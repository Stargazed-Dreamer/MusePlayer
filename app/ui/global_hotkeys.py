from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000


class GlobalHotkeyManager:
    def __init__(self, window):
        self._window = window
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._registered_ids: list[int] = []
        # 上一次 register() 里失败的项：action_id -> 失败原因（已注册成功的项不在其中）。
        # unregister_all() 会清空它，因此"全局快捷键被关闭"时不会留下过期标记。
        self.conflicts: dict[str, str] = {}

    def register(self, shortcuts: dict[str, str], callbacks: dict[str, Callable[[], None]]) -> dict[str, str]:
        """注册一组全局快捷键。

        Args:
            shortcuts: action_id -> 快捷键文本（如 "Ctrl+Alt+Right"），值为空表示不注册。
            callbacks: action_id -> 触发时执行的回调。

        Returns:
            失败项字典：action_id -> 失败原因文案。空字典表示全部注册成功。
        """
        self.unregister_all()
        if sys.platform != "win32":
            return {"__unsupported__": "当前系统不支持全局快捷键"}
        errors: dict[str, str] = {}
        for hotkey_id, (action_id, shortcut) in enumerate(shortcuts.items(), start=1):
            if not shortcut or action_id not in callbacks:
                continue
            native = self._to_native(shortcut)
            if native is None:
                errors[action_id] = f"无法识别快捷键：{shortcut}"
                continue
            modifiers, virtual_key = native
            ok = ctypes.windll.user32.RegisterHotKey(
                wintypes.HWND(int(self._window.winId())),
                hotkey_id,
                modifiers | MOD_NOREPEAT,
                virtual_key,
            )
            if not ok:
                errors[action_id] = f"快捷键已被其他程序占用：{shortcut}"
                continue
            self._registered_ids.append(hotkey_id)
            self._callbacks[hotkey_id] = callbacks[action_id]
        self.conflicts = dict(errors)
        return errors

    def unregister_all(self) -> None:
        if sys.platform == "win32":
            handle = wintypes.HWND(int(self._window.winId()))
            for hotkey_id in self._registered_ids:
                ctypes.windll.user32.UnregisterHotKey(handle, hotkey_id)
        self._registered_ids.clear()
        self._callbacks.clear()
        # 全部注销后冲突标记自然作废（例如用户刚刚关掉了全局快捷键开关）
        self.conflicts.clear()

    def handle_native_event(self, event_type, message) -> bool:
        if sys.platform != "win32":
            return False
        # Qt6 传入的 event_type 是 QByteArray：它与 str/bytes 比较相等但哈希不同，
        # 直接写 `event_type not in {b"windows_generic_MSG", ...}` 会恒为真，
        # 从而静默吞掉所有 WM_HOTKEY（注册成功却毫无反应）。统一归一化成 str 再判成员。
        try:
            event_name = bytes(event_type).decode("ascii", "replace")
        except TypeError:
            event_name = str(event_type)
        if event_name not in {"windows_generic_MSG", "windows_dispatcher_MSG"}:
            return False
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != WM_HOTKEY:
            return False
        callback = self._callbacks.get(int(msg.wParam))
        if callback is None:
            return False
        callback()
        return True

    @staticmethod
    def _to_native(shortcut: str) -> tuple[int, int] | None:
        sequence = QKeySequence(shortcut)
        if sequence.isEmpty():
            return None
        combination = sequence[0]
        modifiers = combination.keyboardModifiers()
        native_modifiers = 0
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            native_modifiers |= MOD_CONTROL
        if modifiers & Qt.KeyboardModifier.AltModifier:
            native_modifiers |= MOD_ALT
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            native_modifiers |= MOD_SHIFT
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            native_modifiers |= MOD_WIN

        key = combination.key()
        if modifiers & Qt.KeyboardModifier.KeypadModifier and Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return native_modifiers, 0x60 + int(key - Qt.Key.Key_0)
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return native_modifiers, ord("A") + int(key - Qt.Key.Key_A)
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return native_modifiers, ord("0") + int(key - Qt.Key.Key_0)
        key_map = {
            Qt.Key.Key_Space: 0x20,
            Qt.Key.Key_Left: 0x25,
            Qt.Key.Key_Up: 0x26,
            Qt.Key.Key_Right: 0x27,
            Qt.Key.Key_Down: 0x28,
            Qt.Key.Key_PageUp: 0x21,
            Qt.Key.Key_PageDown: 0x22,
        }
        virtual_key = key_map.get(key)
        if virtual_key is None:
            return None
        return native_modifiers, virtual_key
