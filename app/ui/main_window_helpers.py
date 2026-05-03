from __future__ import annotations

"""MainWindow 的可复用辅助组件与绘制工具。

该模块集中承载：
1. 自定义状态栏、歌词列表与歌单列表委托
2. 点击即跳转滑条与滚轮交互细节
3. Windows 任务栏进度条集成（comtypes）
4. 播放控制图标绘制与歌词时间解析工具

核心组件说明：
- MultiHintStatusBar: 支持多消息并发的状态栏，用于同时显示音量/播放状态/下一首预告等
- LyricsListWidget: 歌词显示列表，支持用户交互检测和复制操作
- ClickJumpSlider: 点击跳转滑条，用于进度条和音量控制，支持滚轮音量调节
- LyricsItemDelegate: 歌词项绘制委托，支持高亮当前歌词和多行歌词显示
- _WindowsTaskbarProgress: Windows任务栏进度集成，通过COM接口实现
- 图标绘制函数: 各种播放控制图标的矢量绘制工具
- LRC/QRC歌词解析: 标准LRC格式和QQ音乐QRC格式歌词的时间戳解析和内容提取

设计目的：
- 将高复用 UI 细节从主窗口类中剥离，降低主类复杂度
- 让主窗口更聚焦"流程编排"，辅助模块专注"控件实现"
- 提供统一的交互模式和视觉风格
- 封装平台特定功能（如Windows任务栏集成）
"""

import ctypes
import html
import re
import sys
import time
from ctypes import HRESULT, c_int, c_uint, c_ulonglong, c_void_p
from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
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
_KANA_RE = re.compile(r"^\[kana:(.*)\]$", re.MULTILINE)


@dataclass(slots=True)
class FuriganaAnnotation:
    char_index: int
    text: str


@dataclass(slots=True)
class LyricWord:
    text: str
    start_ms: int
    duration_ms: int


@dataclass(slots=True)
class LyricEntry:
    timestamp: float
    original: str = ""
    romaji: str = ""
    translation: str = ""
    original_words: list[LyricWord] = field(default_factory=list)
    romaji_words: list[LyricWord] = field(default_factory=list)
    furigana: list[FuriganaAnnotation] = field(default_factory=list)

    def line_count(self, *, show_japanese: bool = True, show_romaji: bool = True) -> int:
        count = 0
        if show_japanese and self.original:
            count += 1
            if self.furigana:
                count += 1
        if show_romaji and self.romaji:
            count += 1
        if self.translation:
            count += 1
        return max(1, count)

    def display_text(self, *, show_japanese: bool = True, show_romaji: bool = True) -> str:
        parts: list[str] = []
        if show_japanese and self.original:
            parts.append(self.original)
        if show_romaji and self.romaji:
            parts.append(self.romaji)
        if self.translation:
            parts.append(self.translation)
        if not parts:
            return self.original or self.romaji or self.translation or "♪"
        return "\n".join(parts)

    def compact_text(self, *, show_japanese: bool = True, show_romaji: bool = True) -> str:
        if show_japanese and self.original:
            return self.original
        if show_romaji and self.romaji:
            return self.romaji
        if self.translation:
            return self.translation
        return self.original or self.romaji or self.translation or "♪"

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
    
    工作机制：
    - 每个消息都有独立的key、过期时间和显示优先级
    - 定时器定期清理过期消息（160ms间隔）
    - 消息按优先级排序后拼接显示，用" - "分隔
    - 支持自动key推断（从消息内容提取前缀）
    
    典型使用场景：
    - key="音量": 显示当前音量值
    - key="状态": 显示播放状态或操作反馈
    - key="预告": 显示下一首/上一首歌曲信息
    """

    def __init__(self, parent=None):
        """初始化多提示状态栏。
        
        Args:
            parent: 父级QWidget
        """
        super().__init__(parent)
        
        # hints字典结构：{key: (text, expire_time, order)}
        # - key: 消息类型标识（如"音量"、"状态"）
        # - text: 实际显示的文本内容
        # - expire_time: 过期时间戳（None表示永不过期）
        # - order: 显示优先级（数字越小优先级越高）
        self._hints: dict[str, tuple[str, float | None, int]] = {}
        
        self._order_counter = 0  # 用于生成唯一的显示优先级
        
        # 创建定时器定期清理过期消息并刷新显示
        self._timer = QTimer(self)
        self._timer.setInterval(160)  # 160ms刷新间隔，平衡性能和响应性
        self._timer.timeout.connect(self._prune_and_render)
        self._timer.start()

    def showMessage(self, message: str, timeout: int = 0) -> None:
        key = self._infer_key(message)
        self.set_hint(key=key, text=message, timeout_ms=timeout)

    def clearMessage(self) -> None:
        self._hints.clear()
        QStatusBar.showMessage(self, "", 0)

    def set_hint(self, key: str, text: str, timeout_ms: int = 0) -> None:
        """设置一条状态提示。
        
        Args:
            key: 提示类型标识（如"音量"、"状态"）
            text: 要显示的文本内容
            timeout_ms: 超时时间（毫秒），0表示永不过期
        """
        if not text:
            self.clear_hint(key)
            return
        
        now = self._now_sec()
        # 计算过期时间戳，如果timeout_ms > 0则设置过期时间，否则为None（永不过期）
        expire = (now + max(0, int(timeout_ms)) / 1000.0) if timeout_ms > 0 else None
        
        # 如果key已存在则保持原有优先级，否则分配新的优先级
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
    """歌词列表小部件.

    继承自QListWidget，提供用户交互信号和复制功能。
    当用户进行鼠标或键盘操作时会发出相应的信号。

    Attributes:
        user_interacted: 用户交互时发出的信号。
        copy_requested: 用户请求复制时发出的信号。
    """
    user_interacted = Signal()
    copy_requested = Signal()

    def wheelEvent(self, event):
        """处理鼠标滚轮事件.

        Args:
            event: 鼠标滚轮事件对象。
        """
        self.user_interacted.emit()
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        """处理鼠标按下事件.

        Args:
            event: 鼠标按下事件对象。
        """
        self.user_interacted.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        """处理键盘按下事件.

        支持复制快捷键，当用户按下复制快捷键时发出copy_requested信号。

        Args:
            event: 键盘按下事件对象。
        """
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_requested.emit()
            event.accept()
            return
        self.user_interacted.emit()
        super().keyPressEvent(event)


class LyricLineWidget(QWidget):
    """歌词行小部件，显示单行歌词及时间戳.

    用于在歌词列表中以可视化方式显示歌词文本和时间信息。
    当鼠标悬停时会显示开始和结束时间。

    Attributes:
        _start_sec: 歌词开始时间（秒）。
        _end_sec: 歌词结束时间（秒）。
    """
    def __init__(self, text: str, start_sec: float, end_sec: float, parent=None):
        """初始化歌词行小部件.

        Args:
            text: 歌词文本内容。
            start_sec: 歌词开始时间（秒）。
            end_sec: 歌词结束时间（秒）。
            parent: 父级小部件。
        """
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
        """鼠标进入事件处理，显示时间标签.

        Args:
            event: 鼠标进入事件对象。
        """
        self.start_label.show()
        self.end_label.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开事件处理，隐藏时间标签.

        Args:
            event: 鼠标离开事件对象。
        """
        self.start_label.hide()
        self.end_label.hide()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:
        """返回歌词行小部件的建议大小.

        根据字体大小计算合适的高度，宽度固定为260像素。

        Returns:
            QSize: 建议的尺寸，宽度260，高度至少24像素。
        """
        fm = self.text_label.fontMetrics()
        height = max(24, fm.height() + 8)
        return QSize(260, height)


class ClickJumpSlider(QSlider):
    """可"点击即跳转"的滑条.

    扩展QSlider，支持鼠标点击任意位置直接跳转到目标值，而不是步进移动。
    支持拖动过程中持续发出sliderMoved信号，并可选择性地接管滚轮事件用于音量调节。

    Attributes:
        _mouse_pressed: 记录鼠标是否被按下的状态。
        _volume_wheel: 是否启用滚轮音量控制功能。
    """

    def __init__(self, orientation: Qt.Orientation, parent=None, *, volume_wheel: bool = False):
        """初始化点击跳转滑条.

        Args:
            orientation: 滑块方向（水平或垂直）。
            parent: 父级小部件。
            volume_wheel: 是否启用滚轮音量控制，默认为False。
        """
        super().__init__(orientation, parent)
        self._mouse_pressed = False
        self._volume_wheel = bool(volume_wheel)

    def mousePressEvent(self, event):
        """处理鼠标按下事件，实现点击跳转功能.

        当左键点击时，直接跳转到鼠标位置对应的值。

        Args:
            event: 鼠标按下事件对象。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self._mouse_pressed = True
            self.setSliderDown(True)
            self.sliderPressed.emit()
            self._set_value_from_position(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """处理鼠标移动事件，实现拖动跳转功能.

        在鼠标按下的状态下移动鼠标时，滑块跟随鼠标位置。

        Args:
            event: 鼠标移动事件对象。
        """
        if self._mouse_pressed:
            self._set_value_from_position(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """处理鼠标释放事件，结束拖动操作.

        释放鼠标按钮时停止拖动操作并发出相应信号。

        Args:
            event: 鼠标释放事件对象。
        """
        if self._mouse_pressed and event.button() == Qt.MouseButton.LeftButton:
            self._set_value_from_position(event.position().toPoint())
            self._mouse_pressed = False
            self.setSliderDown(False)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_value_from_position(self, pos: QPoint) -> None:
        """根据鼠标位置设置滑块值.

        将屏幕坐标转换为滑块值并更新滑块位置，同时发出sliderMoved信号。

        Args:
            pos: 鼠标在滑块上的位置坐标。
        """
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
        """处理鼠标滚轮事件，支持音量快捷调节.

        如果启用了音量控制，将滚轮事件转发给窗口进行音量调节。

        Args:
            event: 鼠标滚轮事件对象。
        """
        if self._volume_wheel:
            win = self.window()
            if hasattr(win, "_adjust_volume_from_wheel_delta"):
                win._adjust_volume_from_wheel_delta(event.angleDelta().y())
                event.accept()
                return
        super().wheelEvent(event)


class LyricsItemDelegate(QStyledItemDelegate):
    """用于显示歌词的自定义列表项委托类.

    此类负责渲染歌词列表，支持显示时间戳、悬停效果和多行歌词。
    当歌词包含日语原文和罗马音时，可以按行对齐显示。

    Attributes:
        _hover_row: 当前鼠标悬停的行号。
        _start_times: 歌词开始时间列表。
        _end_times: 歌词结束时间列表。
        _structured: 结构化歌词数据列表。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row = -1
        self._start_times: list[float] = []
        self._end_times: list[float] = []
        self._structured: list[LyricEntry] | None = None

    def set_times(self, starts: list[float], ends: list[float]) -> None:
        self._start_times = list(starts)
        self._end_times = list(ends)

    def set_structured_lyrics(self, entries: list[LyricEntry] | None) -> None:
        self._structured = entries

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

        if self._structured and 0 <= row < len(self._structured):
            entry = self._structured[row]
            self._paint_structured_entry(painter, entry, text_rect, opt)
        else:
            text_pen = opt.palette.color(opt.palette.ColorRole.Text)
            painter.setPen(text_pen)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), text)
        painter.restore()

    def _paint_structured_entry(self, painter: QPainter, entry: LyricEntry, rect: QRect, opt: QStyleOptionViewItem) -> None:
        text_pen = opt.palette.color(opt.palette.ColorRole.Text)
        secondary_pen = QColor(text_pen)
        secondary_pen.setAlpha(160)
        fm = opt.fontMetrics
        base_line_h = fm.height() + 4
        furi_line_h = max(10, int(fm.height() * 0.6) + 2)
        y = rect.top()
        has_japanese = bool(entry.original)
        has_furigana = bool(entry.furigana)
        has_romaji = bool(entry.romaji)
        has_translation = bool(entry.translation)

        if has_japanese:
            if has_furigana:
                self._paint_furigana_line(painter, entry, rect, y, furi_line_h, secondary_pen, fm)
                y += furi_line_h
            painter.setPen(text_pen)
            painter.drawText(QRect(rect.left(), y, rect.width(), base_line_h), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), entry.original)
            y += base_line_h

        if has_romaji:
            painter.setPen(secondary_pen)
            romaji_font = painter.font()
            romaji_font.setPointSize(max(7, romaji_font.pointSize() - 1))
            painter.setFont(romaji_font)
            painter.drawText(QRect(rect.left(), y, rect.width(), base_line_h), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), entry.romaji)
            romaji_font.setPointSize(romaji_font.pointSize() + 1)
            painter.setFont(romaji_font)
            y += base_line_h

        if has_translation:
            painter.setPen(secondary_pen)
            painter.drawText(QRect(rect.left(), y, rect.width(), base_line_h), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), entry.translation)

    def _paint_furigana_line(self, painter: QPainter, entry: LyricEntry, rect: QRect, y: int, line_h: int, pen: QColor, fm) -> None:
        text = entry.original
        if not text or not entry.furigana:
            return
        painter.save()
        furi_font = painter.font()
        furi_font.setPointSize(max(6, furi_font.pointSize() - 2))
        painter.setFont(furi_font)
        painter.setPen(pen)

        furi_map: dict[int, str] = {f.char_index: f.text for f in entry.furigana}
        char_widths: list[float] = []
        furi_fm = painter.fontMetrics()
        for ch in text:
            char_widths.append(float(fm.horizontalAdvance(ch)))

        furi_widths: list[float] = []
        for i, ch in enumerate(text):
            if i in furi_map:
                furi_widths.append(float(furi_fm.horizontalAdvance(furi_map[i])))
            else:
                furi_widths.append(0.0)

        total_char_w = sum(char_widths)
        total_furi_w = sum(furi_widths)
        extra = max(0.0, total_furi_w - total_char_w)
        extra_per_char = extra / max(1, len(text)) if extra > 0 else 0.0

        total_w = total_char_w + extra * 1.0
        start_x = rect.left() + (rect.width() - total_w) / 2.0

        x = start_x
        for i, ch in enumerate(text):
            if i in furi_map:
                furi_text = furi_map[i]
                furi_w = furi_fm.horizontalAdvance(furi_text)
                cell_w = char_widths[i] + extra_per_char
                furi_x = x + (cell_w - furi_w) / 2.0
                painter.drawText(QRectF(furi_x, y, furi_w + 4, line_h), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), furi_text)
            x += char_widths[i] + extra_per_char

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        line_count = max(1, text.count("\n") + 1)
        if self._structured and 0 <= index.row() < len(self._structured):
            entry = self._structured[index.row()]
            line_count = entry.line_count(show_japanese=True, show_romaji=True)
        base_h = option.fontMetrics.height() + 4
        furi_h = max(10, int(option.fontMetrics.height() * 0.6) + 2) if (self._structured and 0 <= index.row() < len(self._structured) and self._structured[index.row()].furigana) else 0
        h = max(24, base_h * line_count + furi_h + 4)
        w = max(160, option.fontMetrics.horizontalAdvance(text.split("\n")[0] if "\n" in text else text) + 20)
        return QSize(w, h)


class TrackItemDelegate(QStyledItemDelegate):
    """用于显示音轨项的自定义列表项委托类.

    此类负责渲染音轨列表，支持显示删除按钮和悬停效果。
    当鼠标悬停时会显示删除按钮。

    Attributes:
        REMOVE_WIDTH: 删除按钮的宽度。
        _hover_row: 当前鼠标悬停的行号。
    """
    REMOVE_WIDTH = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row = -1

    def set_hover_row(self, row: int) -> None:
        """设置当前悬停的行号.

        Args:
            row: 悬停的行号，如果没有悬停则为-1。
        """
        self._hover_row = int(row)

    @classmethod
    def remove_rect(cls, row_rect: QRect) -> QRect:
        """计算删除按钮的矩形区域.

        Args:
            row_rect: 行项目的矩形区域。

        Returns:
            QRect: 删除按钮的矩形区域。
        """
        return QRect(row_rect.left() + 2, row_rect.top() + max(0, (row_rect.height() - 12) // 2), cls.REMOVE_WIDTH, 12)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """绘制音轨列表项，支持显示删除按钮.

        当鼠标悬停时会显示红色的删除按钮。

        Args:
            painter: Qt绘画对象。
            option: 样式选项，包含绘制所需的信息。
            index: 当前绘制项的模型索引。
        """
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
        """返回音轨列表项的建议大小.

        Args:
            option: 样式选项，包含字体等信息。
            index: 当前项的模型索引。

        Returns:
            QSize: 建议的宽度和高度。
        """
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        h = max(24, option.fontMetrics.height() + 8)
        w = max(180, option.fontMetrics.horizontalAdvance(text) + self.REMOVE_WIDTH + 20)
        return QSize(w, h)


class TrackListItemWidget(QWidget):
    """音轨列表项的自定义小部件.

    此类为每个音轨创建一个包含删除按钮和文本标签的小部件。
    支持鼠标悬停时显示删除按钮。

    Attributes:
        remove_clicked: 当删除按钮被点击时发出的信号，携带音轨ID。
        _track_id: 音轨的唯一标识符。

    Args:
        track_id: 音轨的唯一标识符。
        text: 要显示的文本内容。
        parent: 父级小部件。
    """
    remove_clicked = Signal(str)

    def __init__(self, track_id: str, text: str, parent=None):
        """初始化音轨列表项小部件.

        Args:
            track_id: 音轨的唯一标识符。
            text: 要显示的文本内容。
            parent: 父级小部件。
        """
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
        """鼠标进入事件处理，显示删除按钮.

        Args:
            event: 鼠标进入事件对象。
        """
        self.remove_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开事件处理，隐藏删除按钮.

        Args:
            event: 鼠标离开事件对象。
        """
        self.remove_btn.hide()
        super().leaveEvent(event)

    def _emit_remove(self) -> None:
        """发出删除信号.

        当用户点击删除按钮时调用此方法，发出携带音轨ID的删除信号。
        """
        self.remove_clicked.emit(self._track_id)

    def sizeHint(self) -> QSize:
        """返回小部件的建议大小.

        Returns:
            QSize: 建议的宽度和高度。
        """
        fm = self.text_label.fontMetrics()
        height = max(24, fm.height() + 8)
        return QSize(260, height)

class _WindowsTaskbarProgress:
    """Windows任务栏进度桥接层（基于comtypes）.

    此类用于在Windows操作系统的任务栏中显示播放进度条。
    需要comtypes库来与Windows API进行交互。

    Attributes:
        _enabled: 是否启用任务栏进度功能。
        _taskbar: 任务栏COM对象。
        _hwnd: 窗口句柄。
        _ready: 是否已初始化并准备就绪。
        _shell32: shell32.dll的动态链接库对象。
    """

    def __init__(self) -> None:
        """初始化Windows任务栏进度桥接对象.

        检查系统平台和comtypes库的可用性，设置初始状态。
        """
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
        """将任务栏进度功能附加到指定窗口.

        Args:
            hwnd: 窗口句柄。

        Returns:
            bool: 如果附加成功返回True，否则返回False。
        """
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
        """设置任务栏进度条的位置.

        Args:
            position: 当前播放位置（秒）。
            duration: 总时长（秒）。如果为0或负数则清除进度条。
        """
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
        """清除任务栏进度条.

        停止显示任务栏进度条。
        """
        if not self._ready or self._hwnd <= 0:
            return
        try:
            self._taskbar.SetProgressState(c_void_p(self._hwnd), TBPF_NOPROGRESS)
        except Exception:
            self._ready = False

    def close(self) -> None:
        """关闭并清理任务栏进度桥接对象.

        释放COM对象并重置状态。
        """
        self._taskbar = None
        self._ready = False
        if self._enabled and comtypes is not None:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


def _format_time(sec: float) -> str:
    """格式化时间为MM:SS或HH:MM:SS格式.

    Args:
        sec: 秒数。

    Returns:
        str: 格式化后的时间字符串。
    """
    total = max(0, int(sec))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_lrc_time(sec: float) -> str:
    """格式化LRC歌词时间戳为MM:SS格式.

    Args:
        sec: 秒数。

    Returns:
        str: 格式化后的时间字符串（MM:SS格式）。
    """
    safe = max(0.0, float(sec))
    total = int(safe)
    minutes = total // 60
    seconds = total % 60
    return f"{minutes:02d}:{seconds:02d}"


def _parse_lrc_entries(raw: str) -> list[tuple[float, str]]:
    """解析LRC歌词文件内容.

    从LRC格式的文本中提取时间戳和歌词文本。

    Args:
        raw: LRC格式的原始文本内容。

    Returns:
        list[tuple[float, str]]: 包含（时间戳（秒），歌词文本）的列表，按时间顺序排序。
    """
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


_QRC_LINE_RE = re.compile(r"\[(\d+),(\d+)\]([^\[]*)")
_QRC_WORD_RE = re.compile(r"\((\d+),(\d+)\)")
_QRC_WORD_PART_RE = re.compile(r"([^(]+)\((\d+),(\d+)\)")


def _parse_qrc_words(text_raw: str) -> list[LyricWord]:
    words: list[LyricWord] = []
    for m in _QRC_WORD_PART_RE.finditer(text_raw):
        words.append(LyricWord(text=m.group(1), start_ms=int(m.group(2)), duration_ms=int(m.group(3))))
    return words


def _parse_qrc_entries(raw: str) -> list[tuple[float, str]]:
    content = raw.strip()
    if content.startswith("<?xml") or content.startswith("<QrcInfos"):
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(content)
            for lyric_elem in root.iter():
                lc = lyric_elem.get("LyricContent", "")
                if lc:
                    content = lc
                    break
        except Exception:
            pass
    result: list[tuple[float, str]] = []
    for m in _QRC_LINE_RE.finditer(content):
        start_ms = int(m.group(1))
        text_raw = m.group(3)
        text = _QRC_WORD_RE.sub("", text_raw).strip()
        if not text:
            continue
        sec = start_ms / 1000.0
        result.append((sec, text))
    result.sort(key=lambda x: x[0])
    return result


def _parse_qrc_structured(raw: str, *, is_romaji: bool = False) -> list[tuple[float, str, list[LyricWord]]]:
    content = raw.strip()
    if content.startswith("<?xml") or content.startswith("<QrcInfos"):
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(content)
            for lyric_elem in root.iter():
                lc = lyric_elem.get("LyricContent", "")
                if lc:
                    content = lc
                    break
        except Exception:
            pass
    result: list[tuple[float, str, list[LyricWord]]] = []
    for m in _QRC_LINE_RE.finditer(content):
        start_ms = int(m.group(1))
        text_raw = m.group(3)
        text = _QRC_WORD_RE.sub("", text_raw).strip()
        if not text:
            continue
        words = _parse_qrc_words(text_raw)
        sec = start_ms / 1000.0
        result.append((sec, text, words))
    result.sort(key=lambda x: x[0])
    return result


def _detect_lyrics_format(raw: str) -> str:
    stripped = raw.strip()
    if stripped.startswith("<?xml") or stripped.startswith("<QrcInfos"):
        return "qrc"
    if "_qm.qrc" in raw or "_qmRoma.qrc" in raw or "_qmts.qrc" in raw:
        return "qrc"
    if _QRC_LINE_RE.search(stripped[:500]):
        return "qrc"
    return "lrc"


def _parse_lyrics_entries(raw: str) -> list[tuple[float, str]]:
    fmt = _detect_lyrics_format(raw)
    if fmt == "qrc":
        return _parse_qrc_entries(raw)
    return _parse_lrc_entries(raw)


def _detect_lyrics_lang(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith("_qmroma.qrc.txt") or "_qmroma." in name:
        return "romaji"
    if name.endswith("_qmts.qrc.txt") or "_qmts." in name:
        return "translation"
    if name.endswith("_qm.qrc.txt") or "_qm." in name:
        return "japanese"
    return "original"


def _extract_kana_content(raw: str) -> str:
    m = _KANA_RE.search(raw)
    if m:
        return m.group(1)
    return ""


def _parse_kana_to_furigana_list(kana_content: str) -> list[str | None]:
    cleaned = _QRC_WORD_RE.sub("", kana_content)
    result: list[str | None] = []
    i = 0
    while i < len(cleaned):
        ch = cleaned[i]
        if ch == "1":
            result.append(None)
            i += 1
        else:
            reading_chars: list[str] = []
            while i < len(cleaned) and cleaned[i] != "1":
                reading_chars.append(cleaned[i])
                i += 1
            result.append("".join(reading_chars))
    return result


def _parse_kana_timed(kana_content: str) -> list[tuple[str | None, int]]:
    result: list[tuple[str | None, int]] = []
    i = 0
    n = len(kana_content)
    while i < n:
        ch = kana_content[i]
        if ch == "1":
            start_ms = 0
            j = i + 1
            if j < n and kana_content[j] == "(":
                m = _QRC_WORD_RE.match(kana_content, j)
                if m:
                    start_ms = int(m.group(1))
                    j = m.end()
            result.append((None, start_ms))
            i = j
        elif ch == "(":
            m = _QRC_WORD_RE.match(kana_content, i)
            if m:
                i = m.end()
            else:
                i += 1
        else:
            reading_chars: list[str] = []
            start_ms = 0
            while i < n and kana_content[i] not in ("1", "("):
                reading_chars.append(kana_content[i])
                i += 1
            reading = "".join(reading_chars)
            if i < n and kana_content[i] == "(":
                m = _QRC_WORD_RE.match(kana_content, i)
                if m:
                    start_ms = int(m.group(1))
                    i = m.end()
            result.append((reading if reading else None, start_ms))
    return result


def _assign_furigana_to_entries(entries: list[LyricEntry], kana_content: str) -> None:
    if not kana_content:
        return
    furigana_list = _parse_kana_to_furigana_list(kana_content)
    if not furigana_list:
        return
    kana_idx = 0
    for entry in entries:
        if not entry.original:
            continue
        char_count = sum(1 for ch in entry.original if ch not in (" ", "　"))
        if kana_idx + char_count > len(furigana_list):
            break
        char_idx = 0
        entry.furigana = []
        for ch in entry.original:
            if ch in (" ", "　"):
                char_idx += 1
                continue
            if kana_idx >= len(furigana_list):
                break
            furi = furigana_list[kana_idx]
            if furi is not None:
                entry.furigana.append(FuriganaAnnotation(char_index=char_idx, text=furi))
            kana_idx += 1
            char_idx += 1


def build_structured_lyrics(
    main_raw: str,
    main_filename: str = "",
    extra_files: list[tuple[str, str]] | None = None,
) -> list[LyricEntry]:
    entries_list: list[LyricEntry] = []
    kana_content = ""
    _MERGE_TOLERANCE = 0.05

    def _find_or_create(ts: float) -> LyricEntry:
        for e in entries_list:
            if abs(e.timestamp - ts) < _MERGE_TOLERANCE:
                return e
        e = LyricEntry(timestamp=ts)
        entries_list.append(e)
        return e

    def _extract_kana(raw: str) -> None:
        nonlocal kana_content
        kana = _extract_kana_content(raw)
        if kana and len(kana) > len(kana_content):
            kana_content = kana

    def _merge_lrc(raw: str, lang: str) -> None:
        for sec, text in _parse_lrc_entries(raw):
            e = _find_or_create(sec)
            if lang == "translation":
                e.translation = text
            elif lang == "romaji":
                e.romaji = text
            else:
                e.original = text

    def _merge_qrc(raw: str, lang: str) -> None:
        for sec, text, words in _parse_qrc_structured(raw):
            e = _find_or_create(sec)
            if lang == "romaji":
                e.romaji = text
                e.romaji_words = words
            elif lang == "japanese":
                e.original = text
                e.original_words = words
            else:
                e.original = text
                e.original_words = words

    def _merge(raw: str, filename: str) -> None:
        lang = _detect_lyrics_lang(filename)
        fmt = _detect_lyrics_format(raw)
        _extract_kana(raw)
        if fmt == "qrc":
            _merge_qrc(raw, lang)
        else:
            _merge_lrc(raw, lang)

    _merge(main_raw, main_filename)

    if extra_files:
        for raw, filename in extra_files:
            _merge(raw, filename)

    entries_list.sort(key=lambda e: e.timestamp)

    if kana_content:
        _assign_furigana_to_entries(entries_list, kana_content)
    return entries_list


def _make_mode_icon(mode: str, *, color: QColor | str = "#f4f4f4") -> QIcon:
    """创建播放模式图标.

    Args:
        mode: 播放模式（playlist_loop, single_loop, 或其他）。
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 对应播放模式的图标。
    """
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
    """创建加减图标.

    Args:
        is_plus: True为加号，False为减号。
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 加减图标。
    """
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


def _make_compact_icon(is_compact: bool, *, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QColor(0, 0, 0, 0))
    if is_compact:
        painter.drawRoundedRect(2, 4, 20, 16, 2, 2)
    else:
        painter.drawRoundedRect(4, 7, 16, 10, 2, 2)
    painter.end()
    return QIcon(pix)


def _make_plus_icon(*, color: QColor | str = "#f4f4f4") -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.drawLine(6, 12, 18, 12)
    painter.drawLine(12, 6, 12, 18)
    painter.end()
    return QIcon(pix)


def _make_crosshair_icon(*, color: QColor | str = "#f4f4f4") -> QIcon:
    """创建十字准星图标.

    Args:
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 十字准星图标。
    """
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
    """创建媒体控制图标.

    Args:
        kind: 图标类型（play, pause, next, prev）。
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 媒体控制图标。
    """
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
    """创建音量图标.

    Args:
        muted: True为静音，False为正常音量。
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 音量图标。
    """
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
    """创建文件夹图标.

    Args:
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 文件夹图标。
    """
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


def _make_heart_icon(*, filled: bool, color: QColor | str = "#f4f4f4") -> QIcon:
    """创建喜欢图标（空心/实心）。"""
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)

    pen_color = QColor("#c94141") if filled else QColor(color)
    fill_color = QColor("#e24b4b") if filled else QColor(0, 0, 0, 0)
    pen = QPen(pen_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(fill_color)

    path = QPainterPath()
    path.moveTo(12.0, 19.4)
    path.cubicTo(10.7, 18.1, 6.2, 14.3, 4.7, 11.6)
    path.cubicTo(3.2, 8.9, 4.2, 5.6, 7.0, 4.7)
    path.cubicTo(8.9, 4.1, 10.8, 4.8, 12.0, 6.3)
    path.cubicTo(13.2, 4.8, 15.1, 4.1, 17.0, 4.7)
    path.cubicTo(19.8, 5.6, 20.8, 8.9, 19.3, 11.6)
    path.cubicTo(17.8, 14.3, 13.3, 18.1, 12.0, 19.4)
    path.closeSubpath()
    painter.drawPath(path)
    painter.end()
    return QIcon(pix)


def _make_rich_title_icon(kind: str, *, color: QColor | str = "#f4f4f4") -> QIcon:
    """创建标题栏图标.

    Args:
        kind: 图标类型（min, restore, close, max）。
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 标题栏图标。
    """
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
    """创建月亮图标（夜间模式）.

    Args:
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 月亮图标。
    """
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
    """创建太阳图标（日间模式）.

    Args:
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 太阳图标。
    """
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
    """创建侧边栏切换图标.

    Args:
        collapsed: True为展开状态（<），False为收起状态（>）。
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 侧边栏切换图标。
    """
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
    """创建锁图标.

    Args:
        locked: True为锁定状态，False为解锁状态。
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 锁图标。
    """
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
    """创建图钉图标.

    Args:
        pinned: True为已固定状态，False为未固定状态。
        color: 图标颜色，默认为"#f4f4f4"。

    Returns:
        QIcon: 图钉图标。
    """
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
