"""主窗口「复制」动作的行为测试。

覆盖两条易回归的口径：
1. 「复制歌曲信息」（Ctrl+C / 菜单 / 点击信息项）**不带文件路径**，且歌名 / 歌手 / 专辑
   各自占一行——早期版本把歌手与专辑挤在一行并用 " | " 分隔，还额外输出本机绝对路径。
2. 专辑封面可点击：有封面时单击发射 `clicked`；无封面占位时不发射、也不呈现手型光标。

运行注意：本文件里的控件级用例需要真正的 QApplication。同会话若已存在裸 QCoreApplication
（`tests/test_control_server.py` 的 `_make_qapp` 会创建），Qt 不允许再建 QApplication，
该用例会自动跳过；完整回归只保证第 1 类纯逻辑用例执行，验证控件请用
`pytest tests/test_copy_actions.py` 单独运行。
"""

from __future__ import annotations

import os

import pytest

# 必须在导入 PySide6 之前指定无头平台，否则 CI/无显示器环境会直接崩。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _SongInfoStub:
    """仅持有「复制歌曲信息」所需字段的桩对象（复用真实 mixin 实现）。"""

    def __init__(self, title: str, artist: str, album: str, path: str) -> None:
        self._current_track_title = title
        self._current_track_artist = artist
        self._current_track_album = album
        self._current_track_path = path


def _compose(title: str, artist: str, album: str, path: str) -> str:
    from app.ui.main_window_mixins.playback_mixin import MainWindowPlaybackMixin

    stub = _SongInfoStub(title, artist, album, path)
    return MainWindowPlaybackMixin._compose_song_info_text(stub)  # type: ignore[arg-type]


def test_song_info_text_has_no_path() -> None:
    """复制文本里不得出现本机文件路径（隐私：路径属于本机私有信息）。"""
    text = _compose("晴天", "周杰伦", "叶惠美", r"F:\secret\music\晴天.flac")
    assert r"F:\secret\music\晴天.flac" not in text
    assert "路径" not in text
    assert "secret" not in text


def test_song_info_text_is_three_lines() -> None:
    """歌名 / 歌手 / 专辑三行，顺序固定，不带 " | " 这类同行拼接符。"""
    text = _compose("晴天", "周杰伦", "叶惠美", r"F:\music\晴天.flac")
    lines = text.splitlines()
    assert lines == [
        "歌名: 晴天",
        "歌手: 周杰伦",
        "专辑: 叶惠美",
    ]
    assert " | " not in text


def test_song_info_text_uses_placeholders_when_fields_missing() -> None:
    """字段缺失时仍保持三行结构（上层已把空值兜底成「未知歌手 / 未知专辑」等）。"""
    text = _compose("未选择歌曲", "未知歌手", "未知专辑", "")
    assert text.splitlines() == [
        "歌名: 未选择歌曲",
        "歌手: 未知歌手",
        "专辑: 未知专辑",
    ]


def _require_qapp():
    """取可用的 QApplication。

    注意：Qt 会话里一旦先创建了裸 `QCoreApplication`（`test_control_server.py` 的
    `_make_qapp` 就这么干），再建 `QApplication` 会直接让进程硬崩（无 traceback）。
    因此这里发现已存在非 GUI 的应用实例就跳过控件级用例，避免整轮测试被拖崩。
    """
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    existing = QCoreApplication.instance()
    if existing is not None and not isinstance(existing, QApplication):
        pytest.skip(
            "当前会话已存在裸 QCoreApplication，Qt 不允许再创建 QApplication；"
            "单独运行 `pytest tests/test_copy_actions.py` 才会执行该控件级用例"
        )
    try:
        return QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover - 环境无 Qt 平台插件
        pytest.skip(f"无法创建 QApplication：{exc}")


def test_cover_label_click_emits_only_with_content() -> None:
    """封面标签：有封面时单击发 clicked；无封面占位时不发、且不留手型光标。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    _require_qapp()
    from app.ui.main_window_helpers import CoverLabel

    cover = CoverLabel("暂无封面")
    cover.setFixedSize(170, 170)
    cover.show()

    hits: list[int] = []
    cover.clicked.connect(lambda: hits.append(1))

    def _release(local_x: float, local_y: float) -> None:
        # Qt6 推荐构造签名：(type, localPos, scenePos, globalPos, button, buttons, modifiers)
        local = QPointF(local_x, local_y)
        cover.mouseReleaseEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease,
                local,
                local,
                local,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    # 无内容时的点击不应触发（占位状态不该表现为可点）
    _release(10.0, 10.0)
    assert hits == []
    assert cover.cursor().shape() != Qt.CursorShape.PointingHandCursor

    # 装上封面后同一位置的点击才触发
    cover.setHasContent(True)
    assert cover.cursor().shape() == Qt.CursorShape.PointingHandCursor
    _release(10.0, 10.0)
    assert hits == [1]

    # 控件矩形外的释放（按下后拖出再松手）不触发
    _release(500.0, 500.0)
    assert hits == [1]
