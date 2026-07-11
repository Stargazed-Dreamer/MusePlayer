from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShortcutAction:
    action_id: str
    label: str
    interface_default: str
    global_default: str


SHORTCUT_ACTIONS = (
    ShortcutAction("play_pause", "播放/暂停", "Space", "Ctrl+Alt+Space"),
    ShortcutAction("previous_track", "上一首", "PgUp", "Ctrl+Alt+Left"),
    ShortcutAction("next_track", "下一首", "PgDown", "Ctrl+Alt+Right"),
    ShortcutAction("volume_up", "增大音量", "Up", "Ctrl+Alt+Num+8"),
    ShortcutAction("volume_down", "减小音量", "Down", "Ctrl+Alt+Num+2"),
    ShortcutAction("seek_backward", "后退 5 秒", "Left", ""),
    ShortcutAction("seek_forward", "前进 5 秒", "Right", ""),
    ShortcutAction("toggle_favorite", "切换喜欢", "", "Ctrl+Alt+D"),
    ShortcutAction("copy_song_info", "复制歌曲信息", "Ctrl+C", ""),
    ShortcutAction("save_stats", "保存统计数据", "Ctrl+S", ""),
)


def default_interface_shortcuts() -> dict[str, str]:
    return {item.action_id: item.interface_default for item in SHORTCUT_ACTIONS}


def default_global_shortcuts() -> dict[str, str]:
    return {item.action_id: item.global_default for item in SHORTCUT_ACTIONS}


def merge_shortcuts(values: object, defaults: dict[str, str]) -> dict[str, str]:
    result = dict(defaults)
    if isinstance(values, dict):
        for action_id in result:
            value = values.get(action_id)
            if isinstance(value, str):
                result[action_id] = value.strip()
    return result
