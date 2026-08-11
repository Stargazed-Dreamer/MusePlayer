from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from app.version import PROTOCOL_VERSION

logger = logging.getLogger("museplayer.runtime")

# 当前控制服务器实现的协议版本（供外部程序协商使用）
__protocol_version__ = PROTOCOL_VERSION


class ControlServer(QObject):
    """运行时控制服务器。

    提供TCP接口供外部程序控制播放器。
    支持JSON格式的命令和响应。
    """

    error_occurred = Signal(str)
    """错误发生时发出的信号 (错误消息)"""
    listening_changed = Signal(bool, str, int)
    """监听状态变化信号 (是否正在监听, 主机地址, 端口)"""

    def __init__(self, dispatcher: Callable[[dict], dict], parent: QObject | None = None):
        """初始化控制服务器。

        Args:
            dispatcher: 命令分发函数，接收JSON命令字典并返回响应字典
            parent: Qt父对象
        """
        super().__init__(parent)
        self._dispatcher = dispatcher
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)

        # 客户端socket缓冲区，按socket描述符存储
        self._buffers: dict[int, bytearray] = {}
        # 活跃的socket连接
        self._sockets: dict[int, QTcpSocket] = {}
        self._host = "127.0.0.1"
        self._port = 0

    @property
    def host(self) -> str:
        """获取当前监听的主机地址。"""
        return self._host

    @property
    def port(self) -> int:
        """获取当前监听的端口号。"""
        return self._port

    def start(self, host: str, port: int) -> bool:
        """启动控制服务器。

        开始在指定地址和端口监听TCP连接。

        Args:
            host: 监听的主机地址
            port: 监听的端口号

        Returns:
            bool: 启动成功返回True，失败返回False
        """
        self.stop()
        self._host = host
        self._port = int(port)

        address = QHostAddress(host)
        if address.isNull():
            address = QHostAddress(QHostAddress.LocalHost)
            self._host = "127.0.0.1"

        ok = self._server.listen(address, self._port)
        if not ok:
            self.error_occurred.emit(self._server.errorString())
            self.listening_changed.emit(False, self._host, self._port)
            return False

        self.listening_changed.emit(True, self._host, self._port)
        logger.info("控制接口开始监听: %s:%s", self._host, self._port)
        return True

    def stop(self) -> None:
        """停止控制服务器。

        关闭所有客户端连接并停止监听。
        """
        # 关闭所有客户端连接
        for sock in list(self._sockets.values()):
            try:
                sock.disconnectFromHost()
                sock.close()
            except Exception:
                pass
        self._sockets.clear()
        self._buffers.clear()

        # 关闭服务器
        if self._server.isListening():
            self._server.close()
        self.listening_changed.emit(False, self._host, self._port)

    def _on_new_connection(self) -> None:
        """处理新客户端连接。

        为每个新连接分配缓冲区并设置信号槽连接。
        """
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            ptr = int(socket.socketDescriptor())
            self._sockets[ptr] = socket
            self._buffers[ptr] = bytearray()

            socket.readyRead.connect(lambda ptr=ptr: self._on_ready_read(ptr))
            socket.disconnected.connect(lambda ptr=ptr: self._on_disconnected(ptr))

    def _on_disconnected(self, ptr: int) -> None:
        """处理客户端断开连接。

        清理相关资源。

        Args:
            ptr: socket描述符
        """
        sock = self._sockets.pop(ptr, None)
        self._buffers.pop(ptr, None)
        if sock is not None:
            with contextlib.suppress(Exception):
                sock.deleteLater()

    def _on_ready_read(self, ptr: int) -> None:
        """处理socket数据读取。

        按行协议处理命令，每行一个JSON命令。

        Args:
            ptr: socket描述符
        """
        socket = self._sockets.get(ptr)
        if socket is None:
            return

        data = bytes(socket.readAll())
        if not data:
            return

        buf = self._buffers.setdefault(ptr, bytearray())
        buf.extend(data)

        # 按行处理命令，行分隔符为\n
        while True:
            idx = buf.find(b"\n")
            if idx < 0:
                break
            line = bytes(buf[:idx]).strip()
            del buf[: idx + 1]
            if not line:
                continue
            response = self._handle_line(line)
            self._send_response(socket, response)

    def _handle_line(self, line: bytes) -> dict:
        """处理单行JSON命令。

        Args:
            line: 包含JSON命令的字节串

        Returns:
            dict: 命令执行结果
        """
        try:
            payload = json.loads(line.decode("utf-8"))
        except Exception as exc:
            logger.warning("JSON 解析失败: %s", exc)
            return {"ok": False, "error": f"invalid_json: {exc}"}

        if not isinstance(payload, dict):
            return {"ok": False, "error": "payload must be object"}

        try:
            result = self._dispatcher(payload)
        except Exception as exc:
            logger.exception("命令执行失败")
            return {"ok": False, "error": str(exc)}

        if isinstance(result, dict):
            if "ok" in result:
                return result
            return {"ok": True, "result": result}
        return {"ok": True, "result": result}

    @staticmethod
    def _send_response(socket: QTcpSocket, payload: dict) -> None:
        """发送响应给客户端。

        Args:
            socket: 目标socket
            payload: 要发送的响应数据
        """
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        socket.write(raw)
        socket.flush()
