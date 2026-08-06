from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

# 控制服务器与 AppController 依赖 PySide6，缺失时跳过整个模块
pytest.importorskip("PySide6")

from app.services.app_controller import AppController  # noqa: E402


@pytest.fixture
def mock_controller():
    """构造一个 mock 控制器，绑定真实的 dispatch_command 逻辑。

    使用 MagicMock 模拟 AppController 的依赖属性，避免实例化需要 Qt 事件循环
    与音频设备的真实控制器。
    """
    ctrl = MagicMock()
    ctrl.logger = MagicMock()
    ctrl.player_service = MagicMock()
    ctrl.library_service = MagicMock()
    ctrl.playback_stats_service = MagicMock()
    ctrl.library_changed = MagicMock()
    ctrl.favorites_changed = MagicMock()
    ctrl.import_folder = MagicMock(return_value=0)
    ctrl.import_muse_playlist = MagicMock(return_value="pl-1")
    ctrl.import_muse_playlist_data = MagicMock(return_value="pl-1")
    ctrl.create_playlist = MagicMock(return_value="pl-1")

    # player_service.mode 是枚举，模拟 .value 访问
    mode = MagicMock()
    mode.value = "single_loop"
    ctrl.player_service.mode = mode
    ctrl.player_service.volume.return_value = 0.8
    ctrl.player_service.next_track.return_value = True
    ctrl.player_service.previous_track.return_value = True
    ctrl.player_service.play_file.return_value = True
    ctrl.player_service.play_track.return_value = True
    return ctrl


def _dispatch(ctrl, payload):
    """以 mock 控制器为 self 调用真实的 dispatch_command。"""
    return AppController.dispatch_command(ctrl, payload)


# ============ ping ============

def test_dispatch_ping(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "ping"})
    assert result == {"ok": True, "result": "pong"}


# ============ 缺失 / 空 cmd ============

def test_dispatch_missing_cmd(mock_controller):
    result = _dispatch(mock_controller, {})
    assert result["ok"] is False
    assert "missing cmd" in result["error"]


def test_dispatch_empty_cmd(mock_controller):
    result = _dispatch(mock_controller, {"cmd": ""})
    assert result["ok"] is False
    assert "missing cmd" in result["error"]


def test_dispatch_no_cmd_key(mock_controller):
    result = _dispatch(mock_controller, {"foo": "bar"})
    assert result["ok"] is False
    assert "missing cmd" in result["error"]


# ============ 未知命令 ============

def test_dispatch_unknown_cmd(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "does_not_exist"})
    assert result["ok"] is False
    assert "unknown cmd" in result["error"]
    assert "does_not_exist" in result["error"]


# ============ 大小写不敏感 / 去空白 ============

def test_dispatch_case_insensitive(mock_controller):
    # 内部 .strip().lower()，大写 PING 应等价于 ping
    result = _dispatch(mock_controller, {"cmd": "PING"})
    assert result == {"ok": True, "result": "pong"}


def test_dispatch_mixed_case(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "PiNg"})
    assert result == {"ok": True, "result": "pong"}


def test_dispatch_cmd_stripped(mock_controller):
    # 带空白的 cmd 应被 strip
    result = _dispatch(mock_controller, {"cmd": "  ping  "})
    assert result == {"ok": True, "result": "pong"}


# ============ 播放控制路由 ============

def test_dispatch_play(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "play"})
    assert result == {"ok": True}
    mock_controller.player_service.play.assert_called_once()


def test_dispatch_pause(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "pause"})
    assert result == {"ok": True}
    mock_controller.player_service.pause.assert_called_once()


def test_dispatch_toggle(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "toggle"})
    assert result == {"ok": True}
    mock_controller.player_service.toggle_play_pause.assert_called_once()


def test_dispatch_next(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "next"})
    assert result == {"ok": True}
    mock_controller.player_service.next_track.assert_called_once_with(user_triggered=True)


def test_dispatch_previous(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "previous"})
    assert result == {"ok": True}
    mock_controller.player_service.previous_track.assert_called_once()


# ============ seek / set_volume / set_mode ============

def test_dispatch_seek(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "seek", "position_sec": 12.5})
    assert result == {"ok": True}
    mock_controller.player_service.seek.assert_called_once_with(12.5)


def test_dispatch_seek_position_alias(mock_controller):
    # position 是 position_sec 的别名
    result = _dispatch(mock_controller, {"cmd": "seek", "position": 7.0})
    assert result == {"ok": True}
    mock_controller.player_service.seek.assert_called_once_with(7.0)


def test_dispatch_set_volume(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "set_volume", "volume": 0.5})
    assert result["ok"] is True
    assert result["result"]["volume"] == 0.8
    mock_controller.player_service.set_volume.assert_called_once_with(0.5)


def test_dispatch_set_mode(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "set_mode", "mode": "random"})
    assert result["ok"] is True
    assert result["result"]["mode"] == "single_loop"
    mock_controller.player_service.set_mode.assert_called_once_with("random")


# ============ 缺失参数 ============

def test_dispatch_play_file_missing_path(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "play_file"})
    assert result["ok"] is False
    assert "missing path" in result["error"]


def test_dispatch_play_track_missing_id(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "play_track"})
    assert result["ok"] is False
    assert "missing track_id" in result["error"]


def test_dispatch_load_playlist_missing_id(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "load_playlist"})
    assert result["ok"] is False
    assert "missing playlist_id" in result["error"]


def test_dispatch_import_folder_missing_path(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "import_folder"})
    assert result["ok"] is False
    assert "missing path" in result["error"]


# ============ 成功路由（带参数） ============

def test_dispatch_create_playlist(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "create_playlist", "name": "新歌单"})
    assert result["ok"] is True
    assert result["result"]["playlist_id"] == "pl-1"
    mock_controller.create_playlist.assert_called_once_with("新歌单")


def test_dispatch_play_file_with_path(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "play_file", "path": "music/a.flac"})
    assert result["ok"] is True
    mock_controller.player_service.play_file.assert_called_once()
    mock_controller.library_changed.emit.assert_called_once()


def test_dispatch_play_track_with_id(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "play_track", "track_id": "t1"})
    assert result["ok"] is True
    mock_controller.player_service.play_track.assert_called_once()


def test_dispatch_load_playlist_with_id(mock_controller):
    result = _dispatch(mock_controller, {"cmd": "load_playlist", "playlist_id": "p1"})
    assert result["ok"] is True
    mock_controller.player_service.set_playlist.assert_called_once_with("p1")
    mock_controller.library_changed.emit.assert_called_once()


# ============ ControlServer 协议解析层（_handle_line） ============

def _make_qapp():
    """确保存在 QCoreApplication 实例，用于创建 QObject 子类。"""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def test_control_server_handle_line_invalid_json():
    qapp = _make_qapp()
    from app.runtime.control_server import ControlServer

    dispatcher = MagicMock(return_value={"ok": True})
    server = ControlServer(dispatcher)
    response = server._handle_line(b"{not valid json")
    assert response["ok"] is False
    assert "invalid_json" in response["error"]
    dispatcher.assert_not_called()


def test_control_server_handle_line_non_object_payload():
    qapp = _make_qapp()
    from app.runtime.control_server import ControlServer

    dispatcher = MagicMock(return_value={"ok": True})
    server = ControlServer(dispatcher)
    response = server._handle_line(b"[1, 2, 3]")
    assert response["ok"] is False
    assert "object" in response["error"]
    dispatcher.assert_not_called()


def test_control_server_handle_line_dispatches_to_dispatcher():
    qapp = _make_qapp()
    from app.runtime.control_server import ControlServer

    dispatcher = MagicMock(return_value={"ok": True, "result": "pong"})
    server = ControlServer(dispatcher)
    response = server._handle_line(b'{"cmd": "ping"}')
    assert response == {"ok": True, "result": "pong"}
    dispatcher.assert_called_once_with({"cmd": "ping"})


def test_control_server_handle_line_wraps_dict_result_without_ok():
    qapp = _make_qapp()
    from app.runtime.control_server import ControlServer

    # dispatcher 返回不含 ok 的 dict，应被包装为 {"ok": True, "result": ...}
    dispatcher = MagicMock(return_value={"value": 42})
    server = ControlServer(dispatcher)
    response = server._handle_line(b'{"cmd": "something"}')
    assert response == {"ok": True, "result": {"value": 42}}


def test_control_server_handle_line_dispatcher_exception():
    qapp = _make_qapp()
    from app.runtime.control_server import ControlServer

    dispatcher = MagicMock(side_effect=RuntimeError("boom"))
    server = ControlServer(dispatcher)
    response = server._handle_line(b'{"cmd": "ping"}')
    assert response["ok"] is False
    assert "boom" in response["error"]
