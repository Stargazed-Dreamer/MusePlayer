from __future__ import annotations

"""MainWindow 的可复用辅助组件与绘制工具。

该模块集中承载：
1. 自定义状态栏、歌词列表与歌单列表委托
2. 点击即跳转滑条与滚轮交互细节
3. Windows 任务栏进度条集成（comtypes）
4. 播放控制图标绘制与歌词时间解析工具

设计目的：
- 将高复用 UI 细节从主窗口类中剥离，降低主类复杂度
- 让主窗口更聚焦“流程编排”，辅助模块专注“控件实现”
"""

import ctypes
import html
import re
import sys
import time
from ctypes import HRESULT, c_int, c_uint, c_ulonglong, c_void_p

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QSlider,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSlider,
    QStyleOptionViewItem,
    QWidget,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
)

from app.services.player_service import PlayMode

try:
    import comtypes
    from comtypes import COMMETHOD, GUID, IUnknown
except Exception:
    comtypes = None
    COMMETHOD = None
    GUID = None
    IUnknown = object

_LRC_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")

TBPF_NOPROGRESS = 0x00000000
TBPF_INDETERMINATE = 0x00000001
TBPF_NORMAL = 0x00000002
TBPF_ERROR = 0x00000003
TBPF_PAUSED = 0x00000004

CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
IID_ITASKBAR_LIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"

WM_NCHITTEST = 0x0084
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17


if comtypes is not None and COMMETHOD is not None and GUID is not None:
    class ITaskbarList3(IUnknown):
        _iid_ = GUID(IID_ITASKBAR_LIST3)
        _methods_ = [
            COMMETHOD([], HRESULT, "HrInit"),
            COMMETHOD([], HRESULT, "AddTab", (["in"], c_void_p, "hwnd")),
            COMMETHOD([], HRESULT, "DeleteTab", (["in"], c_void_p, "hwnd")),
            COMMETHOD([], HRESULT, "ActivateTab", (["in"], c_void_p, "hwnd")),
            COMMETHOD([], HRESULT, "SetActiveAlt", (["in"], c_void_p, "hwnd")),
            COMMETHOD([], HRESULT, "MarkFullscreenWindow", (["in"], c_void_p, "hwnd"), (["in"], c_int, "fFullscreen")),
            COMMETHOD([], HRESULT, "SetProgressValue", (["in"], c_void_p, "hwnd"), (["in"], c_ulonglong, "ullCompleted"), (["in"], c_ulonglong, "ullTotal")),
            COMMETHOD([], HRESULT, "SetProgressState", (["in"], c_void_p, "hwnd"), (["in"], c_int, "tbpFlags")),
            COMMETHOD([], HRESULT, "RegisterTab", (["in"], c_void_p, "h1"), (["in"], c_void_p, "h2")),
            COMMETHOD([], HRESULT, "UnregisterTab", (["in"], c_void_p, "h")),
            COMMETHOD([], HRESULT, "SetTabOrder", (["in"], c_void_p, "h1"), (["in"], c_void_p, "h2")),
            COMMETHOD([], HRESULT, "SetTabActive", (["in"], c_void_p, "h1"), (["in"], c_void_p, "h2"), (["in"], c_int, "f")),
            COMMETHOD([], HRESULT, "ThumbBarAddButtons", (["in"], c_void_p, "h"), (["in"], c_uint, "n"), (["in"], c_void_p, "p")),
            COMMETHOD([], HRESULT, "ThumbBarUpdateButtons", (["in"], c_void_p, "h"), (["in"], c_uint, "n"), (["in"], c_void_p, "p")),
            COMMETHOD([], HRESULT, "ThumbBarSetImageList", (["in"], c_void_p, "h"), (["in"], c_void_p, "p")),
            COMMETHOD([], HRESULT, "SetOverlayIcon", (["in"], c_void_p, "h"), (["in"], c_void_p, "p1"), (["in"], c_void_p, "p2")),
            COMMETHOD([], HRESULT, "SetThumbnailTooltip", (["in"], c_void_p, "h"), (["in"], c_void_p, "p")),
            COMMETHOD([], HRESULT, "SetThumbnailClip", (["in"], c_void_p, "h"), (["in"], c_void_p, "p")),
        ]
else:
    class ITaskbarList3:
        pass


class MultiHintStatusBar(QStatusBar):
    """支持多条消息并存的状态栏。

    与 Qt 原生 `showMessage` 不同，本实现允许多来源提示共存，
    并通过 `-` 拼接展示，适合播放器同时显示音量/状态/预告信息。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hints: dict[str, tuple[str, float | None, int]] = {}
        self._order_counter = 0
        self._timer = QTimer(self)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._prune_and_render)
        self._timer.start()

    def showMessage(self, message: str, timeout: int = 0) -> None:
        key = self._infer_key(message)
        self.set_hint(key=key, text=message, timeout_ms=timeout)

    def clearMessage(self) -> None:
        self._hints.clear()
        QStatusBar.showMessage(self, "", 0)

    def set_hint(self, key: str, text: str, timeout_ms: int = 0) -> None:
        if not text:
            self.clear_hint(key)
            return
        now = self._now_sec()
        expire = (now + max(0, int(timeout_ms)) / 1000.0) if timeout_ms > 0 else None
        order = self._hints[key][2] if key in self._hints else self._next_order()
        self._hints[key] = (str(text), expire, order)
        self._render()

    def clear_hint(self, key: str) -> None:
        if key in self._hints:
            self._hints.pop(key, None)
            self._render()

    def _render(self) -> None:
        if not self._hints:
            QStatusBar.showMessage(self, "", 0)
            return
        ordered = sorted(self._hints.items(), key=lambda kv: kv[1][2])
        text = " - ".join(v[0] for _, v in ordered if v[0])
        QStatusBar.showMessage(self, text, 0)

    def _prune_and_render(self) -> None:
        now = self._now_sec()
        expired = [k for k, (_, e, _) in self._hints.items() if e is not None and e <= now]
        if not expired:
            return
        for key in expired:
            self._hints.pop(key, None)
        self._render()

    def _infer_key(self, message: str) -> str:
        text = (message or "").strip()
        if not text:
            return "状态"
        for sep in ("：", ":", " - ", "-", " "):
            if sep in text:
                head = text.split(sep, 1)[0].strip()
                if head:
                    return head
        return "状态"

    def _next_order(self) -> int:
        self._order_counter += 1
        return self._order_counter

    @staticmethod
    def _now_sec() -> float:
        return time.monotonic()


class LyricsListWidget(QListWidget):
    user_interacted = Signal()
    copy_requested = Signal()

    def wheelEvent(self, event):
        self.user_interacted.emit()
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        self.user_interacted.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_requested.emit()
            event.accept()
            return
        self.user_interacted.emit()
        super().keyPressEvent(event)


class LyricLineWidget(QWidget):
    def __init__(self, text: str, start_sec: float, end_sec: float, parent=None):
        super().__init__(parent)
        self.setObjectName("LyricRowWidget")
        self._start_sec = float(start_sec)
        self._end_sec = float(end_sec)

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 1, 4, 1)
        row.setSpacing(4)

        self.start_label = QLabel(_format_lrc_time(self._start_sec), self)
        self.start_label.setObjectName("LyricTimeLabel")
        self.start_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.start_label.setFixedWidth(38)

        self.text_label = QLabel(text or "♪", self)
        self.text_label.setObjectName("LyricTextLabel")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.text_label.setWordWrap(False)

        self.end_label = QLabel(_format_lrc_time(self._end_sec), self)
        self.end_label.setObjectName("LyricTimeLabel")
        self.end_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.end_label.setFixedWidth(38)

        row.addWidget(self.start_label, 0)
        row.addWidget(self.text_label, 1)
        row.addWidget(self.end_label, 0)

        self.start_label.hide()
        self.end_label.hide()

    def enterEvent(self, event) -> None:
        self.start_label.show()
        self.end_label.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.start_label.hide()
        self.end_label.hide()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:
        fm = self.text_label.fontMetrics()
        height = max(24, fm.height() + 8)
        return QSize(260, height)


class ClickJumpSlider(QSlider):
    """可“点击即跳转”的滑条。

    设计目标：
    - 鼠标点击任意位置直接跳到目标值
    - 拖动过程中持续发出 `sliderMoved`
    - 可选接管滚轮用于音量快捷调节
    """

    def __init__(self, orientation: Qt.Orientation, parent=None, *, volume_wheel: bool = False):
        super().__init__(orientation, parent)
        self._mouse_pressed = False
        self._volume_wheel = bool(volume_wheel)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._mouse_pressed = True
            self.setSliderDown(True)
            self.sliderPressed.emit()
            self._set_value_from_position(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._mouse_pressed:
            self._set_value_from_position(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._mouse_pressed and event.button() == Qt.MouseButton.LeftButton:
            self._set_value_from_position(event.position().toPoint())
            self._mouse_pressed = False
            self.setSliderDown(False)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_value_from_position(self, pos: QPoint) -> None:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        if self.orientation() == Qt.Orientation.Horizontal:
            span = max(1, groove.width())
            slider_pos = pos.x() - groove.x()
        else:
            span = max(1, groove.height())
            slider_pos = groove.bottom() - pos.y()

        slider_pos = max(0, min(span, int(slider_pos)))
        value = QStyle.sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            slider_pos,
            span,
            option.upsideDown,
        )
        self.setValue(value)
        self.sliderMoved.emit(value)

    def wheelEvent(self, event):
        if self._volume_wheel:
            win = self.window()
            if hasattr(win, "_adjust_volume_from_wheel_delta"):
                win._adjust_volume_from_wheel_delta(event.angleDelta().y())
                event.accept()
                return
        super().wheelEvent(event)


class LyricsItemDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row = -1
        self._start_times: list[float] = []
        self._end_times: list[float] = []

    def set_times(self, starts: list[float], ends: list[float]) -> None:
        self._start_times = list(starts)
        self._end_times = list(ends)

    def set_hover_row(self, row: int) -> None:
        self._hover_row = int(row)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)

        painter.save()
        row = index.row()
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "♪")
        rect = opt.rect.adjusted(4, 0, -4, 0)
        is_hover = row == self._hover_row and row < len(self._start_times) and row < len(self._end_times)
        time_w = max(36, opt.fontMetrics.horizontalAdvance("00:00") + 6)
        left_rect = QRect(rect.left(), rect.top(), time_w, rect.height())
        right_rect = QRect(rect.right() - time_w + 1, rect.top(), time_w, rect.height())
        text_rect = QRect(left_rect.right() + 4, rect.top(), max(1, rect.width() - time_w * 2 - 8), rect.height())

        if is_hover:
            painter.setPen(QColor("#5f7892"))
            painter.drawText(left_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), _format_lrc_time(self._start_times[row]))
            painter.drawText(right_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), _format_lrc_time(self._end_times[row]))

        text_pen = opt.palette.color(opt.palette.ColorRole.Text)
        painter.setPen(text_pen)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        h = max(24, option.fontMetrics.height() + 8)
        w = max(160, option.fontMetrics.horizontalAdvance(text) + 20)
        return QSize(w, h)


class TrackItemDelegate(QStyledItemDelegate):
    REMOVE_WIDTH = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row = -1

    def set_hover_row(self, row: int) -> None:
        self._hover_row = int(row)

    @classmethod
    def remove_rect(cls, row_rect: QRect) -> QRect:
        return QRect(row_rect.left() + 2, row_rect.top() + max(0, (row_rect.height() - 12) // 2), cls.REMOVE_WIDTH, 12)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)

        painter.save()
        row = index.row()
        rect = opt.rect
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")

        text_rect = rect.adjusted(self.REMOVE_WIDTH + 6, 0, -6, 0)
        if row == self._hover_row:
            rm_rect = self.remove_rect(rect)
            painter.setPen(QColor("#c62f2f"))
            painter.drawText(rm_rect, int(Qt.AlignmentFlag.AlignCenter), "×")

        text_pen = opt.palette.color(opt.palette.ColorRole.Text)
        painter.setPen(text_pen)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere),
            text,
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        h = max(24, option.fontMetrics.height() + 8)
        w = max(180, option.fontMetrics.horizontalAdvance(text) + self.REMOVE_WIDTH + 20)
        return QSize(w, h)


class TrackListItemWidget(QWidget):
    remove_clicked = Signal(str)

    def __init__(self, track_id: str, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("TrackRowWidget")
        self._track_id = str(track_id)

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(4)

        self.remove_slot = QWidget(self)
        self.remove_slot.setFixedWidth(14)
        remove_slot_layout = QVBoxLayout(self.remove_slot)
        remove_slot_layout.setContentsMargins(0, 0, 0, 0)
        remove_slot_layout.setSpacing(0)

        self.remove_btn = QToolButton(self.remove_slot)
        self.remove_btn.setObjectName("TrackDeleteButton")
        self.remove_btn.setText("×")
        self.remove_btn.setToolTip("从当前歌单移除")
        self.remove_btn.setAutoRaise(True)
        self.remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_btn.setFixedSize(12, 12)
        self.remove_btn.hide()
        remove_slot_layout.addWidget(self.remove_btn, 0, Qt.AlignmentFlag.AlignCenter)

        self.text_label = QLabel(text, self)
        self.text_label.setObjectName("TrackTitleLabel")
        self.text_label.setWordWrap(False)

        row.addWidget(self.remove_slot)
        row.addWidget(self.text_label, 1)

        self.remove_btn.clicked.connect(self._emit_remove)

    def enterEvent(self, event) -> None:
        self.remove_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.remove_btn.hide()
        super().leaveEvent(event)

    def _emit_remove(self) -> None:
        self.remove_clicked.emit(self._track_id)

    def sizeHint(self) -> QSize:
        fm = self.text_label.fontMetrics()
        height = max(24, fm.height() + 8)
        return QSize(260, height)

class _WindowsTaskbarProgress:
    """Windows 任务栏进度桥接层（基于 comtypes）。"""

    def __init__(self) -> None:
        self._enabled = bool(sys.platform.startswith("win") and comtypes is not None)
        self._taskbar = None
        self._hwnd = 0
        self._ready = False
        if not self._enabled:
            return
        try:
            self._shell32 = ctypes.WinDLL("shell32")
            try:
                self._shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p("MusePlayer.Desktop"))
            except Exception:
                pass
        except Exception:
            self._enabled = False

    def attach(self, hwnd: int) -> bool:
        if not self._enabled:
            return False
        if hwnd <= 0:
            return False
        self._hwnd = int(hwnd)
        if self._ready:
            return True
        try:
            comtypes.CoInitialize()
        except Exception:
            return False
        try:
            tb = comtypes.CoCreateInstance(
                GUID(CLSID_TASKBAR_LIST),
                interface=ITaskbarList3,
                clsctx=comtypes.CLSCTX_INPROC_SERVER,
            )
            tb.HrInit()
        except Exception:
            return False
        self._taskbar = tb
        self._ready = True
        if self._ready:
            self.clear()
        return self._ready

    def set_progress(self, position: float, duration: float) -> None:
        if not self._ready or self._hwnd <= 0:
            return
        if duration <= 0.0:
            self.clear()
            return
        ratio = max(0.0, min(1.0, float(position) / float(duration)))
        value = int(round(ratio * 1000.0))
        try:
            self._taskbar.SetProgressState(c_void_p(self._hwnd), TBPF_NORMAL)
            self._taskbar.SetProgressValue(c_void_p(self._hwnd), c_ulonglong(value), c_ulonglong(1000))
        except Exception:
            self._ready = False

    def clear(self) -> None:
        if not self._ready or self._hwnd <= 0:
            return
        try:
            self._taskbar.SetProgressState(c_void_p(self._hwnd), TBPF_NOPROGRESS)
        except Exception:
            self._ready = False

    def close(self) -> None:
        self._taskbar = None
        self._ready = False
        if self._enabled and comtypes is not None:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


def _format_time(sec: float) -> str:
    total = max(0, int(sec))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_lrc_time(sec: float) -> str:
    safe = max(0.0, float(sec))
    total = int(safe)
    minutes = total // 60
    seconds = total % 60
    return f"{minutes:02d}:{seconds:02d}"


def _parse_lrc_entries(raw: str) -> list[tuple[float, str]]:
    result: list[tuple[float, str]] = []
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        matches = list(_LRC_RE.finditer(line))
        if not matches:
            continue

        text = html.unescape(_LRC_RE.sub("", line).strip())
        for m in matches:
            mm = int(m.group(1))
            ss = int(m.group(2))
            frac_raw = m.group(3) or "0"
            if len(frac_raw) == 1:
                ms = int(frac_raw) * 100
            elif len(frac_raw) == 2:
                ms = int(frac_raw) * 10
            else:
                ms = int(frac_raw[:3])

            sec = mm * 60 + ss + (ms / 1000.0)
            result.append((sec, text))

    result.sort(key=lambda x: x[0])
    return result


def _make_mode_icon(mode: str, *, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    icon_color = QColor(color)
    pen = QPen(icon_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    if mode == PlayMode.PLAYLIST_LOOP.value:
        painter.drawArc(4, 4, 14, 14, 40 * 16, 290 * 16)
        painter.drawLine(14, 4, 18, 4)
        painter.drawLine(18, 4, 16, 8)
    elif mode == PlayMode.SINGLE_LOOP.value:
        painter.drawArc(4, 4, 14, 14, 40 * 16, 290 * 16)
        painter.drawLine(14, 4, 18, 4)
        painter.drawLine(18, 4, 16, 8)
        font = QFont("Segoe UI", 8, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(12.5, 11.0, 7.0, 8.0), "1")
    else:
        painter.drawLine(4, 7, 18, 17)
        painter.drawLine(15, 17, 18, 17)
        painter.drawLine(16, 14, 18, 17)

        painter.drawLine(4, 17, 9, 12)
        painter.drawLine(9, 12, 18, 7)
        painter.drawLine(15, 7, 18, 7)
        painter.drawLine(16, 10, 18, 7)

    painter.end()
    return QIcon(pix)


def _make_plus_minus_icon(is_plus: bool, *, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)

    painter.drawLine(6, 12, 18, 12)
    if is_plus:
        painter.drawLine(12, 6, 12, 18)

    painter.end()
    return QIcon(pix)


def _make_crosshair_icon(*, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawEllipse(QRectF(6, 6, 12, 12))
    painter.drawLine(12, 4, 12, 8)
    painter.drawLine(12, 16, 12, 20)
    painter.drawLine(4, 12, 8, 12)
    painter.drawLine(16, 12, 20, 12)
    painter.end()
    return QIcon(pix)


def _make_media_icon(kind: str, *, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    icon_color = QColor(color)
    pen = QPen(icon_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(icon_color)

    k = (kind or "").strip().lower()
    if k == "play":
        painter.drawPolygon([QPoint(8, 6), QPoint(18, 12), QPoint(8, 18)])
    elif k == "pause":
        painter.drawRoundedRect(QRectF(7, 6, 3, 12), 1, 1)
        painter.drawRoundedRect(QRectF(13, 6, 3, 12), 1, 1)
    elif k == "next":
        painter.drawPolygon([QPoint(6, 7), QPoint(12, 12), QPoint(6, 17)])
        painter.drawPolygon([QPoint(12, 7), QPoint(18, 12), QPoint(12, 17)])
        painter.drawRect(QRectF(19, 7, 1.8, 10))
    elif k == "prev":
        painter.drawPolygon([QPoint(18, 7), QPoint(12, 12), QPoint(18, 17)])
        painter.drawPolygon([QPoint(12, 7), QPoint(6, 12), QPoint(12, 17)])
        painter.drawRect(QRectF(4.2, 7, 1.8, 10))
    painter.end()
    return QIcon(pix)


def _make_volume_icon(*, muted: bool, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    icon_color = QColor(color)
    pen = QPen(icon_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(icon_color)
    painter.drawPolygon([QPoint(6, 10), QPoint(9, 10), QPoint(13, 6), QPoint(13, 18), QPoint(9, 14), QPoint(6, 14)])
    if muted:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPoint(15, 9), QPoint(20, 15))
        painter.drawLine(QPoint(20, 9), QPoint(15, 15))
    else:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(13, 7, 8, 10, -45 * 16, 90 * 16)
    painter.end()
    return QIcon(pix)


def _make_folder_icon(*, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(4, 8, 16, 11), 2.0, 2.0)
    painter.drawPolyline([QPoint(5, 8), QPoint(9, 5), QPoint(13, 5), QPoint(15, 8)])
    painter.end()
    return QIcon(pix)


def _make_rich_title_icon(kind: str, *, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    k = (kind or "").strip().lower()
    if k == "min":
        # 80% of previous visual length
        painter.drawLine(QPoint(8, 16), QPoint(16, 16))
    elif k == "restore":
        painter.drawRect(QRectF(7.2, 8.2, 9.2, 9.2))
        painter.drawRect(QRectF(9.8, 5.8, 9.2, 9.2))
    elif k == "close":
        # 150% visual size
        painter.drawLine(QPoint(6, 6), QPoint(18, 18))
        painter.drawLine(QPoint(18, 6), QPoint(6, 18))
    else:
        # 160% visual size for maximize square
        painter.drawRect(QRectF(6.4, 6.4, 11.2, 11.2))

    painter.end()
    return QIcon(pix)


def _make_moon_icon(*, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Crescent arc pair (simple curved moon)
    painter.drawArc(5, 4, 13, 16, 65 * 16, 230 * 16)
    painter.drawArc(9, 5, 10, 14, 110 * 16, 190 * 16)
    painter.end()
    return QIcon(pix)


def _make_sun_icon(*, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(8, 8, 8, 8))
    for x1, y1, x2, y2 in (
        (12, 2.5, 12, 5.2),
        (12, 18.8, 12, 21.5),
        (2.5, 12, 5.2, 12),
        (18.8, 12, 21.5, 12),
        (5.1, 5.1, 6.9, 6.9),
        (17.1, 17.1, 18.9, 18.9),
        (5.1, 18.9, 6.9, 17.1),
        (17.1, 6.9, 18.9, 5.1),
    ):
        painter.drawLine(QPoint(int(round(x1)), int(round(y1))), QPoint(int(round(x2)), int(round(y2))))
    painter.end()
    return QIcon(pix)


def _make_sidebar_toggle_icon(*, collapsed: bool, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    # Show next action direction:
    # expanded -> ">" (collapse), collapsed -> "<" (expand)
    if collapsed:
        painter.drawPolyline([QPoint(15, 7), QPoint(9, 12), QPoint(15, 17)])
    else:
        painter.drawPolyline([QPoint(9, 7), QPoint(15, 12), QPoint(9, 17)])
    painter.end()
    return QIcon(pix)


def _make_lock_icon(locked: bool, *, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    painter.drawRoundedRect(QRectF(7, 11, 10, 8), 2.0, 2.0)
    if locked:
        painter.drawArc(7, 5, 10, 8, 0 * 16, 180 * 16)
        painter.drawLine(12, 14, 12, 17)
    else:
        painter.drawArc(7, 5, 10, 8, 35 * 16, 250 * 16)

    painter.end()
    return QIcon(pix)


def _make_pin_icon(pinned: bool, *, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    painter.drawEllipse(QRectF(7.2, 3.6, 9.6, 4.8))
    painter.drawLine(12, 8, 12, 16)
    painter.drawLine(9, 10, 15, 10)
    painter.drawLine(10, 13, 14, 13)
    painter.drawLine(12, 16, 9.2, 20)

    if not pinned:
        strike = QPen(QColor("#9aa69b"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(strike)
        painter.drawLine(6, 18, 18, 6)

    painter.end()
    return QIcon(pix)
