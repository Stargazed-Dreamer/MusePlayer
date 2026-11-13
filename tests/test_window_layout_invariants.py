"""窗口尺寸不变量测试。

覆盖两个已修复的回归（都是"窗口不该被内容牵着走"的问题）：

1. **状态栏长提示不得参与窗口最小宽度**：`MultiHintStatusBar` 的消息 QLabel
   过去是 `Expanding` 策略，QLabel 的 minimumSizeHint 等于整段文本宽度，一条
   较长的错误提示（如"快捷键已被其他程序占用：…"，30 秒不消失）会把主窗口的
   最小宽度顶到 2400+，表现为"一打开就特别宽、往里拖到一半拖不动"。
2. **拖拽缩放期间不得整表重算行高**：`resizeEvent` 过去每帧遍历全部列表项重算
   行高（3543 首约 40ms），会把 UI 线程占满；现在宽度未变直接跳过，缩放期间只排
   一个 90ms 的单次延迟任务。

运行注意：本文件需要真正的 QApplication。若同一会话里已存在裸 QCoreApplication
（`tests/test_control_server.py` 会创建），Qt 不允许再建 QApplication，这批用例
会自动跳过；验证请单独运行本文件：

    pytest tests/test_window_layout_invariants.py -v
"""

from __future__ import annotations

import os

import pytest

# 必须在导入 PySide6 之前指定无头平台，否则无显示器环境会直接崩。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.main_window_helpers import MultiHintStatusBar  # noqa: E402
from app.ui.main_window_mixins.windowing_mixin import MainWindowWindowingMixin  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """取一个 QApplication；会话里只有 QCoreApplication 时跳过本文件。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    if not isinstance(app, QApplication):
        pytest.skip("会话中已有 QCoreApplication，无法创建 QApplication（请单独运行本文件）")
    return app


def test_long_status_hint_does_not_inflate_min_width(qapp):
    """长提示只应被裁剪，不得把容器（进而主窗口）的最小宽度顶大。"""
    host = QWidget()
    layout = QVBoxLayout(host)
    bar = MultiHintStatusBar(host)
    layout.addWidget(bar)

    bar.set_hint(
        key="shortcut",
        text=(
            "快捷键已被其他程序占用：Ctrl+Alt+Right、Ctrl+Alt+Space、"
            "Ctrl+Alt+D、Ctrl+Alt+Left、Ctrl+Alt+Num+8，请到按键设置里换一组组合"
        ),
        timeout_ms=30000,
        severity="error",
    )
    qapp.processEvents()

    # 未修复时这里会是 700~1000+（等于整段文本宽度）
    assert host.minimumSizeHint().width() < 600
    # 裁掉的完整文本仍可查看
    assert bar._message_label.toolTip()
    assert bar._message_label.minimumWidth() == 0


def test_status_hint_tooltip_follows_messages(qapp):
    bar = MultiHintStatusBar()
    bar.set_hint(key="a", text="播放中", timeout_ms=0)
    qapp.processEvents()
    assert bar._message_label.toolTip() == "播放中"

    bar.clear_hint("a")
    qapp.processEvents()
    assert bar._message_label.toolTip() == ""


class _HeightHarness(MainWindowWindowingMixin, QObject):
    """只带最小依赖的行高重算宿主（复用真实 mixin 实现）。"""

    def __init__(self, list_widget: QListWidget) -> None:
        super().__init__()
        self.track_list = list_widget
        self._track_heights_width = -1
        self._track_height_timer = None
        self.full_passes = 0

    def _track_item_height_for_text(self, _text: str) -> int:
        return 24

    def _apply_track_item_height(self, row: int) -> None:
        self.full_passes += 1


def _make_list(count: int) -> QListWidget:
    widget = QListWidget()
    widget.resize(420, 320)
    widget.show()
    for index in range(count):
        widget.addItem(QListWidgetItem(f"歌名 {index}"))
    return widget


def test_track_height_pass_skips_unchanged_width(qapp):
    """视口宽度没变时必须整表跳过（行高只取决于宽度）。"""
    widget = _make_list(60)
    harness = _HeightHarness(widget)

    harness._update_track_item_heights()
    assert harness.full_passes == 60
    assert harness._track_heights_width == widget.viewport().width()

    harness._update_track_item_heights()
    assert harness.full_passes == 60

    widget.resize(500, 320)
    qapp.processEvents()
    harness._update_track_item_heights()
    assert harness.full_passes == 120


def test_resize_schedules_deferred_height_refresh(qapp):
    """resizeEvent 只排单次延迟任务，立即不做整表重算。"""
    widget = _make_list(60)
    harness = _HeightHarness(widget)

    harness._schedule_track_item_height_refresh()

    assert harness.full_passes == 0
    timer = harness._track_height_timer
    assert timer is not None
    assert timer.isSingleShot()
    assert timer.interval() == 90
