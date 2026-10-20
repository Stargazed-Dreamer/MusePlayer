"""主窗口「复制」动作的行为测试。

覆盖三条易回归的口径：
1. 「复制歌曲信息」（Ctrl+C / 菜单 / 点击信息项）**不带文件路径**，且歌名 / 歌手 / 专辑
   各自占一行——早期版本把歌手与专辑挤在一行并用 " | " 分隔，还额外输出本机绝对路径。
2. 专辑封面可点击：有封面时单击发射 `clicked`；无封面占位时不发射、也不呈现手型光标。
3. `copy_song_info_enabled` 开关**只挡界面点击**这条误触路径；Ctrl+C 与【文件】菜单
   里的两个复制项必须照样可用（曾一度把菜单项也置灰）。被拦的点击是**完全静默**的：
   不改剪贴板、不弹状态提示，用户点了就该像什么都没发生。

运行注意：本文件里的控件级 / 剪贴板级用例需要真正的 QApplication。同会话若已存在裸
QCoreApplication（`tests/test_control_server.py` 的 `_make_qapp` 会创建），Qt 不允许再建
QApplication，这批用例会自动跳过；完整回归只保证第 1 类纯逻辑用例执行，验证控件与开关
口径请用 `pytest tests/test_copy_actions.py` 单独运行。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

# 必须在导入 PySide6 之前指定无头平台，否则 CI/无显示器环境会直接崩。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.ui.main_window_mixins.playback_mixin import MainWindowPlaybackMixin  # noqa: E402


class _SongInfoStub:
    """仅持有「复制歌曲信息」所需字段的桩对象（复用真实 mixin 实现）。"""

    def __init__(self, title: str, artist: str, album: str, path: str) -> None:
        self._current_track_title = title
        self._current_track_artist = artist
        self._current_track_album = album
        self._current_track_path = path


def _compose(title: str, artist: str, album: str, path: str) -> str:
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


# ============ copy_song_info_enabled 开关的作用域 ============
#
# 口径（2026-10-21 定）：开关只挡「点一下界面元素就把内容塞进剪贴板」这条误触路径；
# 用户明确表达意图的 Ctrl+C 与【文件】菜单两个复制项必须照样可用。


class _WindowStub(MainWindowPlaybackMixin):
    """最小桩：只提供复制动作真正用到的那几个属性，方法体全部复用真实 mixin 实现。"""

    def __init__(self, copy_enabled: bool, cover_bytes: bytes | None = None, has_track: bool = True) -> None:
        self.controller = SimpleNamespace(
            settings=SimpleNamespace(copy_song_info_enabled=copy_enabled),
            get_current_cover=lambda: cover_bytes,
        )
        self.player = SimpleNamespace(current_track=lambda: object() if has_track else None)
        self._current_track_title = "晴天"
        self._current_track_artist = "周杰伦"
        self._current_track_album = "叶惠美"
        self._current_track_path = r"F:\music\晴天.flac"
        self.status_messages: list[tuple[str, int]] = []
        self.statuses: list[tuple[str, str | None]] = []

    def statusBar(self) -> SimpleNamespace:
        return SimpleNamespace(showMessage=lambda text, timeout: self.status_messages.append((text, timeout)))

    def _show_status(self, text: str, severity: str | None = None, timeout_ms: int = 2500) -> None:
        self.statuses.append((text, severity))


class _LabelStub:
    """替代真实 QWidget，只记录 _refresh_copy_clickable_state 下发了什么。"""

    def __init__(self) -> None:
        self.clickable: bool | None = None

    def setClickable(self, clickable: bool) -> None:
        self.clickable = bool(clickable)


def test_copy_clickable_state_propagates_to_all_info_widgets() -> None:
    """开关下达后：四个信息项标签 + 封面都要同步"能不能点"（纯逻辑，无需 Qt）。"""

    class _StateStub:
        def __init__(self, copy_enabled: bool) -> None:
            self.controller = SimpleNamespace(settings=SimpleNamespace(copy_song_info_enabled=copy_enabled))
            self.title_label = _LabelStub()
            self.artist_label = _LabelStub()
            self.album_label = _LabelStub()
            self.path_label = _LabelStub()
            self.cover_label = _LabelStub()

    stub = _StateStub(copy_enabled=True)
    MainWindowPlaybackMixin._refresh_copy_clickable_state(stub)  # type: ignore[arg-type]
    assert [w.clickable for w in (stub.title_label, stub.artist_label, stub.album_label, stub.path_label)] == [
        True,
        True,
        True,
        True,
    ]
    assert stub.cover_label.clickable is True

    stub.controller.settings.copy_song_info_enabled = False
    MainWindowPlaybackMixin._refresh_copy_clickable_state(stub)  # type: ignore[arg-type]
    assert [w.clickable for w in (stub.title_label, stub.artist_label, stub.album_label, stub.path_label)] == [
        False,
        False,
        False,
        False,
    ]
    assert stub.cover_label.clickable is False, "封面必须跟着变成不可点"


def test_disabled_switch_makes_widgets_look_unclickable() -> None:
    """控件层面：不可点时不该再是手型光标 / 提示文案，点了也不发 clicked。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    _require_qapp()
    # QApplication 已由 _require_qapp() 建好，这里只查控件自身的行为
    from app.ui.main_window_helpers import AdaptiveInfoLabel, CoverLabel

    title = AdaptiveInfoLabel("晴天")
    title.show()
    cover = CoverLabel("暂无封面")
    cover.setFixedSize(170, 170)
    cover.show()

    hits: list[str] = []
    title.clicked.connect(lambda: hits.append("title"))
    cover.clicked.connect(lambda: hits.append("cover"))
    cover.setHasContent(True)

    def _release(widget, x: float, y: float) -> None:
        local = QPointF(x, y)
        widget.mouseReleaseEvent(
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

    # 允许点击：都有手型光标，封面还有 ToolTip
    assert title.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert cover.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert cover.toolTip() == "点击复制专辑封面"

    # 关闭可点：游标回到默认、封面 ToolTip 清空、点击不再发信号
    title.setClickable(False)
    cover.setClickable(False)
    assert title.cursor().shape() != Qt.CursorShape.PointingHandCursor
    assert cover.cursor().shape() != Qt.CursorShape.PointingHandCursor
    assert cover.toolTip() == ""
    _release(title, 5.0, 5.0)
    _release(cover, 10.0, 10.0)
    assert hits == []

    # 恢复可点：立刻回到原来的表现（设置里重新打开要即时生效）
    title.setClickable(True)
    cover.setClickable(True)
    assert title.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert cover.cursor().shape() == Qt.CursorShape.PointingHandCursor
    _release(title, 5.0, 5.0)
    _release(cover, 10.0, 10.0)
    assert hits == ["title", "cover"]


def _sentinel_png(size: int) -> bytes:
    """生成一张纯色 PNG 的原始字节，充当「文件内嵌封面」。"""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(QColor(20, 40, 80))
    payload = QByteArray()
    buf = QBuffer(payload)
    try:
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
    finally:
        buf.close()
    return bytes(payload.data())


def test_click_info_item_is_blocked_by_switch() -> None:
    """关闭开关后点击信息项：既不改写剪贴板，也**不发任何提示**（被拦＝点了跟没点一样）。"""
    from PySide6.QtWidgets import QApplication

    _require_qapp()
    QApplication.clipboard().setText("SENTINEL")

    stub = _WindowStub(copy_enabled=False)
    MainWindowPlaybackMixin._copy_song_info_item(stub, "title")  # type: ignore[arg-type]
    assert QApplication.clipboard().text() == "SENTINEL", "开关关闭后点击信息项仍改写了剪贴板"
    assert stub.statuses == [], f"被拦的点击不该发状态提示：{stub.statuses}"
    assert stub.status_messages == [], f"被拦的点击不该发状态提示：{stub.status_messages}"


def test_menu_and_ctrl_c_copy_ignores_switch() -> None:
    """菜单 / Ctrl+C 复制歌曲信息不受开关影响：开关关着也要照样写入三行文本。"""
    from PySide6.QtWidgets import QApplication

    _require_qapp()
    QApplication.clipboard().setText("")

    stub = _WindowStub(copy_enabled=False)
    MainWindowPlaybackMixin._copy_current_song_info(stub)  # type: ignore[arg-type]
    assert QApplication.clipboard().text().splitlines() == [
        "歌名: 晴天",
        "歌手: 周杰伦",
        "专辑: 叶惠美",
    ]


def test_cover_click_blocked_but_menu_entry_still_works() -> None:
    """同一份封面：点击入口受开关拦截，菜单入口（同一实现）不受拦截。"""
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    _require_qapp()
    mime = QApplication.clipboard()
    sentinel = QImage()
    assert sentinel.loadFromData(_sentinel_png(2))
    mime.setImage(sentinel)

    stub = _WindowStub(copy_enabled=False, cover_bytes=_sentinel_png(9))

    # ① 点击入口：被开关挡住，剪贴板里的哨兵图保持不动，且不留任何提示
    MainWindowPlaybackMixin._on_cover_label_clicked(stub)  # type: ignore[arg-type]
    assert mime.image().size() == sentinel.size(), "开关关闭后点击封面仍改写了剪贴板"
    assert stub.statuses == [], f"被拦的点击不该发状态提示：{stub.statuses}"

    # ② 菜单入口：开关关着也照样把封面（9×9）放进剪贴板
    MainWindowPlaybackMixin._copy_current_album_cover(stub)  # type: ignore[arg-type]
    assert mime.image().size().width() == 9, f"菜单复制封面未生效：{mime.image().size()}"
