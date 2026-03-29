from __future__ import annotations

import json
import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

logger = logging.getLogger("museplayer.runtime")


class ControlServer(QObject):
    error_occurred = Signal(str)
    listening_changed = Signal(bool, str, int)

    def __init__(self, dispatcher: Callable[[dict], dict], parent: QObject | None = None):
        super().__init__(parent)
        self._dispatcher = dispatcher
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)

        self._buffers: dict[int, bytearray] = {}
        self._sockets: dict[int, QTcpSocket] = {}
        self._host = "127.0.0.1"
        self._port = 0

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    def start(self, host: str, port: int) -> bool:
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
        for sock in list(self._sockets.values()):
            try:
                sock.disconnectFromHost()
                sock.close()
            except Exception:
                pass
        self._sockets.clear()
        self._buffers.clear()

        if self._server.isListening():
            self._server.close()
        self.listening_changed.emit(False, self._host, self._port)

    def _on_new_connection(self) -> None:
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
        sock = self._sockets.pop(ptr, None)
        self._buffers.pop(ptr, None)
        if sock is not None:
            try:
                sock.deleteLater()
            except Exception:
                pass

    def _on_ready_read(self, ptr: int) -> None:
        socket = self._sockets.get(ptr)
        if socket is None:
            return

        data = bytes(socket.readAll())
        if not data:
            return

        buf = self._buffers.setdefault(ptr, bytearray())
        buf.extend(data)

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
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        socket.write(raw)
        socket.flush()