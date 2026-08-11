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

from __future__ import annotations

import contextlib
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
    QHBoxLayout,
    QLabel,
    QListWidget,
    QSizePolicy,
    QSlider,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSlider,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
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


class AdaptiveInfoLabel(QLabel):
    clicked = Signal()
    _MIDDLE_MARK = " …… "

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._source_text = ""
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(text)

    def sourceText(self) -> str:
        return self._source_text

    def setText(self, text: str) -> None:
        self._source_text = str(text)
        self._refresh_display_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_display_text()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def _refresh_display_text(self) -> None:
        text = self._source_text
        width = max(1, self.contentsRect().width())
        metrics = self.fontMetrics()
        bounds = metrics.boundingRect(
            QRect(0, 0, width, 10000),
            int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere),
            text,
        )
        line_height = max(1, metrics.lineSpacing())
        if bounds.height() <= line_height * 3:
            display_text = text
            word_wrap = True
        else:
            display_text = self._middle_elided_text(text, width)
            word_wrap = False
        if super().text() != display_text:
            super().setText(display_text)
        if self.wordWrap() != word_wrap:
            self.setWordWrap(word_wrap)
        self.setToolTip(text if display_text != text else "")
        self.updateGeometry()

    def _middle_elided_text(self, text: str, width: int) -> str:
        metrics = self.fontMetrics()
        marker = self._MIDDLE_MARK
        if metrics.horizontalAdvance(text) <= width:
            return text
        if metrics.horizontalAdvance(marker) >= width:
            return metrics.elidedText(text, Qt.TextElideMode.ElideRight, width)
        left_count = (len(text) + 1) // 2
        right_count = len(text) - left_count
        while left_count > 0 or right_count > 0:
            candidate = text[:left_count] + marker + (text[-right_count:] if right_count else "")
            if metrics.horizontalAdvance(candidate) <= width:
                return candidate
            if left_count >= right_count and left_count > 0:
                left_count -= 1
            elif right_count > 0:
                right_count -= 1
        return marker.strip()


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
        """计算需要显示的文本行数。

        此方法根据传入的参数以及对象自身存储的内容，
        计算最终输出时需要占用的行数。计算结果确保至少为一行。
        例如，可以选择是否包含日文原文及其注音、罗马字转写和翻译。

        Args:
            show_japanese (bool): 是否在计算中包含日文原文行。默认为 True。
            show_romaji (bool): 是否在计算中包含罗马字转写行。默认为 True。

        Returns:
            int: 计算得出的显示行数，最小值为 1。
        """
        count = 0
        # 如果需要显示日文，且原始日文内容存在，则需要一行来显示
        if show_japanese and self.original:
            count += 1
            # 如果日文内容有注音（furigana），则需要额外一行来显示注音
            if self.furigana:
                count += 1
        # 如果需要显示罗马字，且罗马字内容存在，则需要一行来显示
        if show_romaji and self.romaji:
            count += 1
        # 如果翻译内容存在，则需要一行来显示翻译
        if self.translation:
            count += 1
        # 确保最终返回的行数至少为 1
        return max(1, count)

    def display_text(self, *, show_japanese: bool = True, show_romaji: bool = True) -> str:
        """
        功能：显示文本，包括日语、罗马音和翻译。根据参数决定显示哪些内容，如果没有内容则返回默认值或"♪"。
        参数：
            show_japanese (bool): 是否显示日文原文，默认为True。
            show_romaji (bool): 是否显示罗马音，默认为True。
        返回值：
            str: 显示的文本，由换行符连接各部分；如果没有内容，则返回第一个可用文本或"♪"。
        """
        parts: list[str] = []  # 初始化一个空列表，用于存储要显示的文本部分
        if show_japanese and self.original:  # 如果需要显示日语且存在日文原文
            parts.append(self.original)  # 将日文原文添加到列表
        if show_romaji and self.romaji:  # 如果需要显示罗马音且存在罗马音
            parts.append(self.romaji)  # 将罗马音添加到列表
        if self.translation:  # 如果存在翻译
            parts.append(self.translation)  # 将翻译添加到列表
        if not parts:  # 如果列表为空（即没有添加任何文本部分）
            return self.original or self.romaji or self.translation or "♪"  # 返回第一个可用的文本，如果都没有则返回"♪"
        return "\n".join(parts)  # 用换行符连接所有文本部分并返回

    def compact_text(self, *, show_japanese: bool = True, show_romaji: bool = True) -> str:
        """
        功能：根据参数和对象属性返回压缩后的文本。
        参数：
            show_japanese (bool): 是否显示日文，默认为True。
            show_romaji (bool): 是否显示罗马音，默认为True。
        返回值：str，返回原文、罗马音、翻译或默认符号"♪"。
        """
        if show_japanese and self.original:
            # 如果启用日文且原文存在，返回原文
            return self.original
        if show_romaji and self.romaji:
            # 如果启用罗马音且罗马音存在，返回罗马音
            return self.romaji
        if self.translation:
            # 如果翻译存在，返回翻译
            return self.translation
        # 回退选项：尝试返回原文、罗马音、翻译或默认符号
        return self.original or self.romaji or self.translation or "♪"


TBPF_NOPROGRESS = 0x00000000
TBPF_INDETERMINATE = 0x00000001
TBPF_NORMAL = 0x00000002
TBPF_ERROR = 0x00000003
TBPF_PAUSED = 0x00000004

CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
IID_ITASKBAR_LIST3 = "{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}"


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
            COMMETHOD(
                [],
                HRESULT,
                "SetProgressValue",
                (["in"], c_void_p, "hwnd"),
                (["in"], c_ulonglong, "ullCompleted"),
                (["in"], c_ulonglong, "ullTotal"),
            ),
            COMMETHOD([], HRESULT, "SetProgressState", (["in"], c_void_p, "hwnd"), (["in"], c_int, "tbpFlags")),
            COMMETHOD([], HRESULT, "RegisterTab", (["in"], c_void_p, "h1"), (["in"], c_void_p, "h2")),
            COMMETHOD([], HRESULT, "UnregisterTab", (["in"], c_void_p, "h")),
            COMMETHOD([], HRESULT, "SetTabOrder", (["in"], c_void_p, "h1"), (["in"], c_void_p, "h2")),
            COMMETHOD(
                [], HRESULT, "SetTabActive", (["in"], c_void_p, "h1"), (["in"], c_void_p, "h2"), (["in"], c_int, "f")
            ),
            COMMETHOD(
                [],
                HRESULT,
                "ThumbBarAddButtons",
                (["in"], c_void_p, "h"),
                (["in"], c_uint, "n"),
                (["in"], c_void_p, "p"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "ThumbBarUpdateButtons",
                (["in"], c_void_p, "h"),
                (["in"], c_uint, "n"),
                (["in"], c_void_p, "p"),
            ),
            COMMETHOD([], HRESULT, "ThumbBarSetImageList", (["in"], c_void_p, "h"), (["in"], c_void_p, "p")),
            COMMETHOD(
                [],
                HRESULT,
                "SetOverlayIcon",
                (["in"], c_void_p, "h"),
                (["in"], c_void_p, "p1"),
                (["in"], c_void_p, "p2"),
            ),
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
        """
        显示提示消息。

        此方法根据消息内容推断一个键，然后调用set_hint方法来设置提示信息。如果指定了超时时间，提示将在超时后消失。

        参数：
            message (str): 需要显示的消息文本。
            timeout (int): 超时时间，单位为毫秒。默认为0，表示立即显示或无超时。

        返回值：
            None：此方法不返回任何值。
        """
        key = self._infer_key(message)  # 根据消息推断键
        self.set_hint(key=key, text=message, timeout_ms=timeout)  # 设置提示，包含键、消息和超时

    def clearMessage(self) -> None:
        """清除消息，清空hints并重置状态栏。参数：self - 实例对象。返回值：无。"""
        self._hints.clear()  # 清除hints
        QStatusBar.showMessage(self, "", 0)  # 清空状态栏消息

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
        """根据指定的键值，清除对应的提示信息。

        此方法用于从内部存储的提示信息字典中移除指定键对应的条目，
        并触发界面重新渲染以更新显示。

        Args:
            key (str): 需要清除的提示信息的唯一标识键。

        Returns:
            None: 该方法不返回任何值。
        """
        # 检查传入的 key 是否存在于内部的 _hints 字典中
        if key in self._hints:
            # 使用 pop 方法安全地移除指定键对应的条目，第二个参数 None 用于防止键不存在时引发 KeyError
            self._hints.pop(key, None)
            # 调用内部渲染方法，以反映提示信息被清除后的最新状态
            self._render()

    def _render(self) -> None:
        """根据内部提示信息更新状态栏显示。"""
        # 如果没有提示信息，则清空状态栏并返回
        if not self._hints:
            QStatusBar.showMessage(self, "", 0)
            return
        # 将提示信息按第三个元素（可能是优先级或时间戳）排序
        ordered = sorted(self._hints.items(), key=lambda kv: kv[1][2])
        # 将所有非空提示文本用 " - " 连接起来
        text = " - ".join(v[0] for _, v in ordered if v[0])
        # 在状态栏上显示组合后的提示文本
        QStatusBar.showMessage(self, text, 0)

    def _prune_and_render(self) -> None:
        """
        功能：清理过期的提示并重新渲染。
        参数：无（除了隐含的self）。
        返回值：无。
        """
        now = self._now_sec()  # 获取当前时间（秒）
        expired = [
            k for k, (_, e, _) in self._hints.items() if e is not None and e <= now
        ]  # 从self._hints中筛选出过期键（结束时间e存在且小于等于当前时间）
        if not expired:  # 如果没有过期项
            return  # 提前返回
        for key in expired:  # 遍历所有过期键
            self._hints.pop(key, None)  # 从self._hints中移除该键，如果不存在则忽略
        self._render()  # 重新渲染以更新显示

    def _infer_key(self, message: str) -> str:
        """根据消息文本推断键。如果消息为空，则返回"状态"。否则，尝试使用常见分隔符分割消息，返回分割后的第一部分作为键。如果无法分割，则返回"状态"。"""
        text = (message or "").strip()  # 处理可能为None的消息，并去除首尾空格
        if not text:  # 检查文本是否为空
            return "状态"  # 如果为空，返回默认键"状态"
        for sep in ("：", ":", " - ", "-", " "):  # 遍历多个分隔符，包括中文和英文符号
            if sep in text:  # 如果当前分隔符存在于文本中
                head = text.split(sep, 1)[0].strip()  # 使用分隔符分割文本，取第一部分并去除空格
                if head:  # 如果提取的头部非空
                    return head  # 返回头部作为键
        return "状态"  # 如果所有分隔符都不匹配，返回默认键"状态"

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
        """初始化歌词视图组件。

        Args:
            parent: 父组件对象，默认为None。
        """
        # 调用父类的构造函数
        super().__init__(parent)
        # 初始化鼠标悬停行索引，-1表示当前无悬停行
        self._hover_row = -1
        # 初始化歌词的开始时间列表，存储每段歌词的起始时间
        self._start_times: list[float] = []
        # 初始化歌词的结束时间列表，存储每段歌词的结束时间
        self._end_times: list[float] = []
        # 初始化结构化歌词数据，None表示尚未解析或为空
        self._structured: list[LyricEntry] | None = None

    def set_times(self, starts: list[float], ends: list[float]) -> None:
        """设置实例的开始时间和结束时间。

        参数：
            starts (list[float]): 开始时间列表。
            ends (list[float]): 结束时间列表。

        返回值：
            None
        """
        self._start_times = list(starts)  # 将开始时间列表复制到实例变量
        self._end_times = list(ends)  # 将结束时间列表复制到实例变量

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
            painter.drawText(
                left_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                _format_lrc_time(self._start_times[row]),
            )
            painter.drawText(
                right_rect,
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _format_lrc_time(self._end_times[row]),
            )

        if self._structured and 0 <= row < len(self._structured):
            entry = self._structured[row]
            self._paint_structured_entry(painter, entry, text_rect, opt)
        else:
            text_pen = opt.palette.color(opt.palette.ColorRole.Text)
            painter.setPen(text_pen)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), text)
        painter.restore()

    def _paint_structured_entry(
        self, painter: QPainter, entry: LyricEntry, rect: QRect, opt: QStyleOptionViewItem
    ) -> None:
        """
        此方法用于绘制结构化的歌词条目，包括日文原文、注音、罗马字和翻译。
        参数：
            painter: QPainter对象，用于绘图操作。
            entry: LyricEntry对象，包含歌词条目的各项数据。
            rect: QRect对象，表示绘制区域的矩形范围。
            opt: QStyleOptionViewItem对象，包含视图项的样式选项和字体信息。
        返回值：
            None（无返回值，直接进行绘图）
        """
        text_pen = opt.palette.color(opt.palette.ColorRole.Text)  # 从选项调色板获取文本颜色，用于主要文本绘制
        secondary_pen = QColor(text_pen)  # 基于文本颜色创建副画笔
        secondary_pen.setAlpha(160)  # 设置副画笔透明度为160，用于绘制次要文本如注音和翻译
        fm = opt.fontMetrics  # 获取字体度量对象，用于计算文本尺寸和行高
        base_line_h = fm.height() + 4  # 计算基础行高，为字体高度加4像素，确保文本行间有适当间距
        furi_line_h = max(
            10, int(fm.height() * 0.6) + 2
        )  # 计算注音行高，取最大值10像素或字体高度的60%加2像素，保证最小可读性
        y = rect.top()  # 设置绘制起始y坐标为矩形顶部，用于逐步向下绘制文本行
        has_japanese = bool(entry.original)  # 检查条目是否包含日文原文
        has_furigana = bool(entry.furigana)  # 检查条目是否包含注音
        has_romaji = bool(entry.romaji)  # 检查条目是否包含罗马字
        has_translation = bool(entry.translation)  # 检查条目是否包含翻译文本

        if has_japanese:  # 如果存在日文原文
            if has_furigana:  # 如果存在注音
                self._paint_furigana_line(painter, entry, rect, y, furi_line_h, secondary_pen, fm)  # 调用方法绘制注音行
                y += furi_line_h  # 更新y坐标，为日文原文行腾出空间
            painter.setPen(text_pen)  # 设置画笔为文本颜色，用于绘制主要日文原文
            painter.drawText(
                QRect(rect.left(), y, rect.width(), base_line_h),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                entry.original,
            )  # 绘制日文原文，水平和垂直居中对齐
            y += base_line_h  # 更新y坐标，移动到下一行

        if has_romaji:  # 如果存在罗马字
            painter.setPen(secondary_pen)  # 设置画笔为副画笔（半透明），用于绘制罗马字
            romaji_font = painter.font()  # 获取当前字体对象
            romaji_font.setPointSize(
                max(7, romaji_font.pointSize() - 1)
            )  # 调整字体大小，最小为7点，比当前字号小1点，以区分主次
            painter.setFont(romaji_font)  # 应用调整后的字体
            painter.drawText(
                QRect(rect.left(), y, rect.width(), base_line_h),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                entry.romaji,
            )  # 绘制罗马字，水平和垂直居中对齐
            romaji_font.setPointSize(romaji_font.pointSize() + 1)  # 恢复字体大小，增加1点，避免影响后续绘制
            painter.setFont(romaji_font)  # 应用恢复后的字体
            y += base_line_h  # 更新y坐标，移动到下一行

        if has_translation:  # 如果存在翻译文本
            painter.setPen(secondary_pen)  # 设置画笔为副画笔（半透明），用于绘制翻译
            painter.drawText(
                QRect(rect.left(), y, rect.width(), base_line_h),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                entry.translation,
            )  # 绘制翻译文本，水平和垂直居中对齐

    def _paint_furigana_line(
        self, painter: QPainter, entry: LyricEntry, rect: QRect, y: int, line_h: int, pen: QColor, fm
    ) -> None:
        """绘制歌词条目的注音行。

        该方法负责在指定矩形区域内绘制歌词文本及其对应的注音（ふりがな）。
        注音会根据原始字符的位置进行水平居中排列，并适当调整字符间距以避免注音重叠。

        Args:
            painter (QPainter): 用于绘制的QPainter对象。
            entry (LyricEntry): 包含原始歌词文本和注音信息的歌词条目。
            rect (QRect): 指定绘制区域的矩形。
            y (int): 绘制起始的Y坐标。
            line_h (int): 当前行的高度。
            pen (QColor): 绘制注音使用的画笔颜色。
            fm (QFontMetrics): 用于测量原始文本字符宽度的字体度量对象。

        Returns:
            None: 该方法不返回任何值，直接在painter上绘制内容。
        """
        text = entry.original  # 获取原始歌词文本
        if not text or not entry.furigana:  # 如果没有文本或没有注音数据，则直接返回
            return
        painter.save()  # 保存当前painter状态，以便后续恢复
        furi_font = painter.font()  # 获取当前字体
        furi_font.setPointSize(max(6, furi_font.pointSize() - 2))  # 设置注音字体大小，比主字体小2点，最小为6点
        painter.setFont(furi_font)  # 应用注音字体
        painter.setPen(pen)  # 设置注音绘制颜色

        # 创建字符索引到注音文本的映射字典
        furi_map: dict[int, str] = {f.char_index: f.text for f in entry.furigana}
        char_widths: list[float] = []  # 存储每个字符的宽度
        furi_fm = painter.fontMetrics()  # 获取注音字体的度量对象，用于测量注音文本宽度
        for ch in text:  # 遍历每个字符，计算其宽度
            char_widths.append(float(fm.horizontalAdvance(ch)))  # 使用主字体度量计算字符宽度

        # 计算每个字符对应的注音宽度（如果没有注音则为0）
        furi_widths: list[float] = []
        for i, _ in enumerate(text):
            if i in furi_map:  # 如果该字符有注音
                furi_widths.append(float(furi_fm.horizontalAdvance(furi_map[i])))  # 计算注音宽度
            else:
                furi_widths.append(0.0)  # 无注音时宽度为0

        total_char_w = sum(char_widths)  # 计算所有字符的总宽度
        total_furi_w = sum(furi_widths)  # 计算所有注音的总宽度
        extra = max(0.0, total_furi_w - total_char_w)  # 计算注音超出字符的宽度，确保非负
        extra_per_char = extra / max(1, len(text)) if extra > 0 else 0.0  # 将多余宽度平均分配到每个字符，避免除零

        total_w = total_char_w + extra * 1.0  # 计算总绘制宽度（字符宽度 + 多余宽度）
        start_x = rect.left() + (rect.width() - total_w) / 2.0  # 计算起始X坐标，使内容在矩形内水平居中

        x = start_x  # 初始化当前绘制X坐标
        for i, _ in enumerate(text):  # 遍历每个字符
            if i in furi_map:  # 如果该字符有注音
                furi_text = furi_map[i]  # 获取注音文本
                furi_w = furi_fm.horizontalAdvance(furi_text)  # 计算注音文本宽度
                cell_w = char_widths[i] + extra_per_char  # 计算当前字符的分配宽度（包括额外空间）
                furi_x = x + (cell_w - furi_w) / 2.0  # 计算注音的X坐标，使其在字符单元格内水平居中
                # 在指定位置绘制注音文本，使用左对齐和垂直居中
                painter.drawText(
                    QRectF(furi_x, y, furi_w + 4, line_h),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    furi_text,
                )
            x += char_widths[i] + extra_per_char  # 更新X坐标，移动到下一个字符的位置
        painter.restore()  # 恢复painter到之前保存的状态

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        """
        计算并返回视图项（如列表项或单元格）的推荐尺寸。
        该方法会根据显示的文本内容、行数以及是否包含注音（如日文读音）信息来确定合适的宽度和高度。

        参数：
            option (QStyleOptionViewItem): 包含用于绘制该项的样式和字体度量信息的选项对象。
            index: 一个模型索引（QModelIndex），指向需要计算尺寸的数据项。

        返回值：
            QSize: 一个包含计算出的宽度（width）和高度（height）的尺寸对象。
        """
        # 获取该项要显示的文本内容，如果数据为空则使用空字符串
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        # 计算文本的行数，至少为1行
        line_count = max(1, text.count("\n") + 1)
        # 如果存在结构化数据且当前行索引有效，则使用结构化数据中计算出的行数（可能包含注音等信息）
        if self._structured and 0 <= index.row() < len(self._structured):
            entry = self._structured[index.row()]
            line_count = entry.line_count(show_japanese=True, show_romaji=True)
        # 计算每行基础文本高度（字体高度加上内边距）
        base_h = option.fontMetrics.height() + 4
        # 计算注音（如日文振假名）的高度。如果存在结构化数据、行索引有效且该项有注音，则计算注音高度；否则为0
        furi_h = (
            max(10, int(option.fontMetrics.height() * 0.6) + 2)
            if (
                self._structured and 0 <= index.row() < len(self._structured) and self._structured[index.row()].furigana
            )
            else 0
        )
        # 计算最终高度：取24像素和（基础行高 * 行数 + 注音高度 + 间距）中的较大值
        h = max(24, base_h * line_count + furi_h + 4)
        # 计算宽度：取160像素和（文本首行宽度 + 边距）中的较大值，以适应文本内容
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
            int(
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextWordWrap
                | Qt.TextFlag.TextWrapAnywhere
            ),
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
            with contextlib.suppress(Exception):
                self._shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p("MusePlayer.Desktop"))
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
            with contextlib.suppress(Exception):
                comtypes.CoUninitialize()


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
_QRC_KANA_GROUP_RE = re.compile(r"(\d)(?:(?:\(\d+,\d+\))?[\u3040-\u309f\u30a0-\u30ff]*)*")
_QRC_FURIGANA_BASE_RE = re.compile(r"[\u4e00-\u9fff\uff10-\uff19\uff21-\uff5a\u3005]")


def _parse_qrc_words(text_raw: str) -> list[LyricWord]:
    """解析QRC格式的原始歌词文本，提取其中的歌词词组。

    该函数利用预定义的正则表达式从给定的原始字符串中匹配所有歌词词组，
    并将它们转换为`LyricWord`对象组成的列表。

    Args:
        text_raw (str): 包含QRC格式歌词信息的原始字符串。

    Returns:
        list[LyricWord]: 一个列表，其中每个元素都是一个`LyricWord`对象，
        包含了词组的文本内容、开始时间（毫秒）和持续时间（毫秒）。
    """
    words: list[LyricWord] = []  # 初始化一个空列表，用于存储解析出的歌词词组
    # 使用预定义的正则表达式对象在原始文本中迭代查找所有匹配项
    text_start = 0
    for m in _QRC_WORD_RE.finditer(text_raw):
        # 将每个匹配到的子组（词组文本、开始时间、持续时间）转换为 LyricWord 对象
        # 并添加到列表中。注意：时间值从字符串转换为整数。
        text = text_raw[text_start : m.start()]
        if text:
            words.append(LyricWord(text=text, start_ms=int(m.group(1)), duration_ms=int(m.group(2))))
        text_start = m.end()
    return words  # 返回解析完成的歌词词组列表


def _parse_qrc_entries(raw: str) -> list[tuple[float, str]]:
    """解析 QRC 格式的歌词原始文本，提取时间戳和对应的歌词内容。

    Args:
        raw (str): QRC 格式的歌词原始文本，可能是纯文本或 XML 格式。

    Returns:
        list[tuple[float, str]]: 解析后的歌词列表，每个元素是一个元组，
            包含歌词时间戳（秒，浮点数）和对应的歌词文本（字符串），
            列表按时间戳升序排序。
    """
    content = raw.strip()
    # 检查内容是否为 XML 格式（以特定标签开头）
    if content.startswith("<?xml") or content.startswith("<QrcInfos"):
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(content)
            # 遍历 XML 树，查找包含 LyricContent 属性的元素
            for lyric_elem in root.iter():
                lc = lyric_elem.get("LyricContent", "")
                if lc:
                    content = lc  # 将 content 替换为提取到的歌词内容
                    break
        except Exception:
            pass  # 如果 XML 解析失败，则忽略并继续使用原始 content

    result: list[tuple[float, str]] = []
    # 使用正则表达式匹配 QRC 歌词行（时间戳和歌词内容）
    for m in _QRC_LINE_RE.finditer(content):
        start_ms = int(m.group(1))  # 提取起始时间（毫秒）
        text_raw = m.group(3)  # 提取原始歌词文本
        # 移除歌词中的逐字标记（通过正则替换），并去除首尾空格
        text = _QRC_WORD_RE.sub("", text_raw).strip()
        if not text:
            continue  # 跳过空歌词行
        sec = start_ms / 1000.0  # 将毫秒转换为秒
        result.append((sec, text))
    result.sort(key=lambda x: x[0])  # 按时间戳升序排序
    return result


def _parse_qrc_structured(raw: str, *, is_romaji: bool = False) -> list[tuple[float, str, list[LyricWord]]]:
    """解析QRC格式歌词字符串，返回结构化的歌词数据列表。

    将原始QRC歌词字符串解析为按时间排序的元组列表，每个元组包含：
    - 起始时间（秒，浮点数）
    - 歌词文本（去除时间标签后的纯文本）
    - 歌词单词列表（由LyricWord对象组成）

    如果输入是XML格式的QRC数据，会先提取其中的LyricContent字段。

    Args:
        raw (str): 原始QRC格式歌词字符串。
        is_romaji (bool): 是否为罗马音歌词（此参数在当前实现中未使用）。

    Returns:
        list[tuple[float, str, list[LyricWord]]]: 结构化歌词列表，
        按时间顺序排列，每个元素为(时间秒数, 歌词文本, 单词列表)的元组。
    """
    # 移除字符串首尾空白字符
    content = raw.strip()

    # 检查是否为XML格式的QRC数据
    if content.startswith("<?xml") or content.startswith("<QrcInfos"):
        # 导入XML解析库
        import xml.etree.ElementTree as ET

        try:
            # 解析XML内容
            root = ET.fromstring(content)
            # 遍历XML树寻找包含歌词内容的元素
            for lyric_elem in root.iter():
                # 获取LyricContent属性
                lc = lyric_elem.get("LyricContent", "")
                if lc:
                    # 找到歌词内容，替换content变量
                    content = lc
                    break
        except Exception:
            # XML解析失败，保持原content不变
            pass

    # 初始化结果列表，类型注解为包含(浮点数, 字符串, LyricWord列表)的元组列表
    result: list[tuple[float, str, list[LyricWord]]] = []

    # 使用正则表达式匹配所有歌词行
    for m in _QRC_LINE_RE.finditer(content):
        # 提取起始时间（毫秒）
        start_ms = int(m.group(1))
        # 提取原始文本内容（包含时间标签和歌词）
        text_raw = m.group(3)
        # 移除文本中的时间标签并清理空白
        text = _QRC_WORD_RE.sub("", text_raw).strip()

        # 跳过空文本行
        if not text:
            continue

        # 解析歌词单词
        words = _parse_qrc_words(text_raw)
        # 将毫秒转换为秒
        sec = start_ms / 1000.0
        # 将解析结果添加到结果列表
        result.append((sec, text, words))

    # 按时间顺序排序结果
    result.sort(key=lambda x: x[0])
    return result


def _detect_lyrics_format(raw: str) -> str:
    """检测歌词文本的格式类型。

    通过分析原始字符串的内容特征，判断歌词格式是QRC格式还是LRC格式。

    参数:
        raw (str): 原始歌词文本字符串

    返回值:
        str: 格式标识字符串，"qrc" 或 "lrc"
    """
    # 移除字符串首尾的空白字符
    stripped = raw.strip()

    # 检查是否以XML声明或QrcInfos标签开头（QRC格式特征）
    if stripped.startswith("<?xml") or stripped.startswith("<QrcInfos"):
        return "qrc"

    # 检查原始字符串中是否包含QRC文件扩展名特征
    if "_qm.qrc" in raw or "_qmRoma.qrc" in raw or "_qmts.qrc" in raw:
        return "qrc"

    # 使用预定义的正则表达式检查前500个字符是否符合QRC行格式
    if _QRC_LINE_RE.search(stripped[:500]):
        return "qrc"

    # 默认返回LRC格式
    return "lrc"


def _parse_lyrics_entries(raw: str) -> list[tuple[float, str]]:
    """解析歌词条目，根据检测的格式调用相应的解析函数。

    参数：
    raw (str): 原始歌词文本。

    返回值：
    list[tuple[float, str]]: 解析后的歌词条目列表，每个条目是一个包含时间戳和歌词文本的元组。
    """
    fmt = _detect_lyrics_format(raw)  # 检测歌词格式
    if fmt == "qrc":  # 检查是否为QRC格式
        return _parse_qrc_entries(raw)  # 如果是QRC格式，调用QRC解析函数
    return _parse_lrc_entries(raw)  # 否则，调用LRC解析函数


def _detect_lyrics_lang(filename: str) -> str:
    """功能：检测歌词文件的语言类型，基于文件名中的特定后缀或子串进行判断。
    参数：filename (str): 文件名字符串，可能为None。
    返回值：str: 检测到的语言类型，如'romaji'（罗马字）、'translation'（翻译）、'japanese'（日语）或'original'（原始）。
    """
    name = (filename or "").lower()  # 将文件名转换为小写，确保大小写不敏感；如果filename为None则使用空字符串
    if (
        name.endswith("_qmroma.qrc.txt") or "_qmroma." in name
    ):  # 检查文件名是否以"_qmroma.qrc.txt"结尾或包含"_qmroma."，以识别罗马字歌词
        return "romaji"
    if (
        name.endswith("_qmts.qrc.txt") or "_qmts." in name
    ):  # 检查文件名是否以"_qmts.qrc.txt"结尾或包含"_qmts."，以识别翻译歌词
        return "translation"
    if name.endswith("_qm.qrc.txt") or "_qm." in name:  # 检查文件名是否以"_qm.qrc.txt"结尾或包含"_qm."，以识别日语歌词
        return "japanese"
    return "original"  # 如果以上条件都不匹配，则默认返回原始歌词类型


def _extract_kana_content(raw: str) -> str:
    """从输入的字符串中提取假名内容。

    Args:
        raw (str): 原始字符串。

    Returns:
        str: 提取到的假名内容，如果没有匹配则返回空字符串。
    """
    # 使用预定义的正则表达式对象_KANA_RE在raw字符串中搜索假名内容
    m = _KANA_RE.search(raw)
    # 如果搜索到匹配，则返回匹配的第一个分组（即假名内容）
    if m:
        return m.group(1)
    # 如果没有匹配，则返回空字符串
    return ""


def _parse_kana_to_furigana_list(kana_content: str) -> list[str | None]:
    """
    将假名内容解析为振假名列表。

    参数:
        kana_content (str): 假名内容字符串。

    返回:
        list[str | None]: 振假名列表，其中None表示该位置没有振假名。
    """
    # 使用正则表达式清理输入字符串，移除_QRC_WORD_RE匹配的部分
    groups = list(_QRC_KANA_GROUP_RE.finditer(kana_content))
    # 初始化结果列表
    result: list[str | None] = []
    if groups:
        for match in groups:
            count = int(match.group(1))
            reading = _QRC_WORD_RE.sub("", match.group(0)[1:]) or None
            result.append(reading)
            result.extend([None] * (count - 1))
        return result
    cleaned = _QRC_WORD_RE.sub("", kana_content)
    i = 0
    # 遍历清理后的字符串
    while i < len(cleaned):
        ch = cleaned[i]
        if ch == "1":  # "1"作为标记，表示该位置没有振假名
            result.append(None)  # 追加None到结果
            i += 1  # 移动到下一个字符
        else:
            # 收集假名字符直到遇到"1"或字符串结束
            reading_chars: list[str] = []
            while i < len(cleaned) and cleaned[i] != "1":
                reading_chars.append(cleaned[i])
                i += 1
            # 将收集的字符连接成字符串并追加到结果
            result.append("".join(reading_chars))
    return result


def _parse_kana_timed(kana_content: str) -> list[tuple[str | None, int]]:
    """解析QRC格式的假名内容，提取文本和时间信息。

    该函数遍历输入的假名字符串，根据特定字符（如'1'和'('）识别时间节点，
    并将非节点字符收集为文本。最终返回一个列表，其中每个元素是一个元组，
    包含可选的文本和对应的开始时间（毫秒）。

    Args:
        kana_content: 包含QRC格式假名和时间标记的字符串。

    Returns:
        一个列表，列表中的每个元素是 (文本, 开始时间毫秒) 的元组。
        文本可能为 None，表示该时间节点前没有文本。
    """
    # 初始化结果列表，用于存储解析出的(文本, 时间)元组
    result: list[tuple[str | None, int]] = []
    i = 0
    n = len(kana_content)
    # 主循环，遍历整个输入字符串
    while i < n:
        ch = kana_content[i]
        # 情况1：遇到字符'1'，这通常表示一个换行或段落开始标记
        if ch == "1":
            start_ms = 0
            j = i + 1
            # 检查紧接着'1'后面是否有'('，可能包含时间信息
            if j < n and kana_content[j] == "(":
                # 使用预定义的正则表达式匹配时间模式
                m = _QRC_WORD_RE.match(kana_content, j)
                if m:
                    # 成功匹配，提取捕获组1中的毫秒时间
                    start_ms = int(m.group(1))
                    # 将索引j移动到匹配结束位置
                    j = m.end()
            # 将节点信息（文本为None）添加到结果
            result.append((None, start_ms))
            # 更新主索引i，跳过已处理的部分
            i = j
        # 情况2：单独遇到'('字符（前面没有文本或'1'标记）
        elif ch == "(":
            # 尝试匹配时间模式
            m = _QRC_WORD_RE.match(kana_content, i)
            if m:
                # 匹配成功，则移动索引到匹配结束，跳过这个时间节点
                i = m.end()
            else:
                # 匹配失败（格式不符），则仅跳过这个'('字符
                i += 1
        # 情况3：普通文本字符
        else:
            # 初始化一个列表，用于收集当前文本片段的所有字符
            reading_chars: list[str] = []
            start_ms = 0
            # 循环收集字符，直到遇到下一个节点标记（'1'或'('）或字符串结束
            while i < n and kana_content[i] not in ("1", "("):
                reading_chars.append(kana_content[i])
                i += 1
            # 将收集到的字符列表合并成一个字符串
            reading = "".join(reading_chars)
            # 检查当前索引位置是否是一个时间节点'('（即文本后面跟着时间）
            if i < n and kana_content[i] == "(":
                # 尝试匹配时间模式
                m = _QRC_WORD_RE.match(kana_content, i)
                if m:
                    # 匹配成功，提取开始时间毫秒
                    start_ms = int(m.group(1))
                    # 移动索引到匹配结束
                    i = m.end()
            # 将解析结果添加到列表，如果收集到的文本为空则记为None
            result.append((reading if reading else None, start_ms))
    # 返回最终解析结果
    return result


def _assign_furigana_to_entries(entries: list[LyricEntry], kana_content: str) -> None:
    """将注音内容分配到歌词条目中。

    解析注音字符串并分配给对应的歌词条目，为每个字符添加注音标注。

    参数:
        entries (list[LyricEntry]): 歌词条目列表，包含原始文本和需要添加的注音信息
        kana_content (str): 包含注音信息的字符串，格式为特定解析器能识别的格式

    返回:
        None: 该函数直接修改传入的entries列表，为每个条目添加注音信息
    """
    # 如果注音内容为空，直接返回不做处理
    if not kana_content:
        return

    # 解析注音字符串为注音列表
    furigana_list = _parse_kana_to_furigana_list(kana_content)

    # 如果解析结果为空，直接返回
    if not furigana_list:
        return

    # 当前处理到的注音索引
    kana_idx = 0

    # 遍历每个歌词条目
    for entry in entries:
        # 跳过原始文本为空的条目
        if not entry.original:
            continue

        # 计算当前条目中非空格字符的数量
        base_indexes = [index for index, char in enumerate(entry.original) if _QRC_FURIGANA_BASE_RE.fullmatch(char)]
        char_count = len(base_indexes)

        # 检查剩余注音数量是否足够分配给当前条目
        if kana_idx + char_count > len(furigana_list):
            break

        # 当前字符在原始文本中的索引

        # 初始化当前条目的注音列表
        entry.furigana = []

        # 遍历当前条目的每个字符
        for char_idx in base_indexes:
            # 跳过空格字符（全角和半角空格）

            # 检查注音索引是否超出范围
            if kana_idx >= len(furigana_list):
                break

            # 获取当前注音
            furi = furigana_list[kana_idx]

            # 如果当前注音不为空，则创建注音标注并添加到条目中
            if furi is not None:
                entry.furigana.append(FuriganaAnnotation(char_index=char_idx, text=furi))

            # 更新注音索引和字符索引
            kana_idx += 1


def build_structured_lyrics(
    main_raw: str,
    main_filename: str = "",
    extra_files: list[tuple[str, str]] | None = None,
) -> list[LyricEntry]:
    """从原始歌词文本构建结构化的歌词条目列表，支持合并多个歌词文件。

    Args:
        main_raw (str): 主歌词文件的原始文本内容。
        main_filename (str, optional): 主歌词文件的文件名，用于辅助检测歌词语言。默认为空字符串。
        extra_files (list[tuple[str, str]] | None, optional): 额外的歌词文件列表，每个元素为 (原始文本, 文件名) 的元组。默认为None。

    Returns:
        list[LyricEntry]: 按时间戳排序并填充了内容的歌词条目列表。
    """
    entries_list: list[LyricEntry] = []  # 用于存储最终所有歌词条目的列表
    kana_content = ""  # 用于存储提取的假名（振假名）内容
    _MERGE_TOLERANCE = 0.05  # 合并容差，用于判断两个时间戳是否足够接近以视为同一个歌词条目（单位：秒）

    def _find_or_create(ts: float) -> LyricEntry:
        """根据时间戳查找现有条目，若未找到则创建一个新条目。

        Args:
            ts (float): 歌词的时间戳。

        Returns:
            LyricEntry: 找到的或新创建的歌词条目。
        """
        # 遍历现有条目，检查是否有时间戳足够接近的条目
        for e in entries_list:
            if abs(e.timestamp - ts) < _MERGE_TOLERANCE:  # 使用容差进行比较
                return e
        # 未找到匹配条目，则创建新条目并加入列表
        e = LyricEntry(timestamp=ts)
        entries_list.append(e)
        return e

    def _extract_kana(raw: str) -> None:
        """从原始歌词文本中提取假名（振假名）内容，并更新到外部变量 kana_content。

        Args:
            raw (str): 原始歌词文本。
        """
        nonlocal kana_content  # 声明使用外部函数的 kana_content 变量
        kana = _extract_kana_content(raw)  # 调用外部函数提取假名内容
        # 如果成功提取到假名，并且新提取的内容比已存储的更长，则更新
        if kana and len(kana) > len(kana_content):
            kana_content = kana

    def _merge_lrc(raw: str, lang: str) -> None:
        """合并 LRC 格式歌词。

        Args:
            raw (str): LRC 格式的原始歌词文本。
            lang (str): 歌词的语言类型（如 "translation", "romaji" 等）。
        """
        # 遍历解析出的时间戳和歌词文本对
        for sec, text in _parse_lrc_entries(raw):
            e = _find_or_create(sec)  # 查找或创建对应时间戳的条目
            # 根据语言类型，将文本填充到条目的不同字段
            if lang == "translation":
                e.translation = text
            elif lang == "romaji":
                e.romaji = text
            else:  # 默认情况或其他语言（如原语言）
                e.original = text

    def _merge_qrc(raw: str, lang: str) -> None:
        """合并 QRC 格式歌词（QRC 格式包含逐词时间信息）。

        Args:
            raw (str): QRC 格式的原始歌词文本。
            lang (str): 歌词的语言类型。
        """
        # 遍历解析出的时间戳、歌词文本和单词时间信息
        for sec, text, words in _parse_qrc_structured(raw):
            e = _find_or_create(sec)  # 查找或创建对应时间戳的条目
            # 根据语言类型，将文本和单词时间信息填充到条目的对应字段
            if lang == "romaji":
                e.romaji = text
                e.romaji_words = words  # 逐词罗马音时间信息
            elif lang == "japanese":
                e.original = text
                e.original_words = words  # 逐词原文时间信息
            else:  # 默认情况
                e.original = text
                e.original_words = words

    def _merge(raw: str, filename: str) -> None:
        """合并单个歌词文件的核心逻辑。

        Args:
            raw (str): 歌词文件的原始文本内容。
            filename (str): 歌词文件的文件名，用于辅助检测语言和格式。
        """
        lang = _detect_lyrics_lang(filename)  # 检测歌词语言类型
        fmt = _detect_lyrics_format(raw)  # 检测歌词格式（LRC 或 QRC 等）
        _extract_kana(raw)  # 尝试提取假名内容
        # 根据检测到的格式，调用相应的合并函数
        if fmt == "qrc":
            _merge_qrc(raw, lang)
        else:  # 默认处理为 LRC 格式
            _merge_lrc(raw, lang)

    # 处理主歌词文件
    _merge(main_raw, main_filename)

    # 如果存在额外歌词文件，则逐一处理
    if extra_files:
        for raw, filename in extra_files:
            _merge(raw, filename)

    # 将所有条目按时间戳排序
    entries_list.sort(key=lambda e: e.timestamp)

    # 如果提取到了假名内容，则为所有条目分配振假名
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
    """创建一个紧凑型图标，根据is_compact参数决定绘制两种不同尺寸的矩形图标。

    Args:
        is_compact (bool): 是否为紧凑模式。True时绘制较大矩形（20x16），False时绘制较小矩形（16x10）。
        color (QColor | str, optional): 图标颜色，可以是QColor对象或颜色字符串。默认为"#f4f4f4"。

    Returns:
        QIcon: 根据指定尺寸和颜色创建的图标对象。
    """
    # 创建一个24x24像素的透明画布
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    # 初始化画笔，设置抗锯齿渲染
    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)

    # 创建画笔对象：设置颜色、线宽1.6、实线、圆头笔、圆角连接
    pen = QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    # 设置画刷为完全透明（不填充）
    painter.setBrush(QColor(0, 0, 0, 0))

    # 根据is_compact参数绘制不同尺寸的圆角矩形
    if is_compact:
        # 紧凑模式：在(2,4)位置绘制20x16的圆角矩形，圆角半径2像素
        painter.drawRoundedRect(2, 4, 20, 16, 2, 2)
    else:
        # 非紧凑模式：在(4,7)位置绘制16x10的圆角矩形，圆角半径2像素
        painter.drawRoundedRect(4, 7, 16, 10, 2, 2)

    # 结束绘画操作
    painter.end()

    # 将绘制好的像素图转换为图标并返回
    return QIcon(pix)


def _make_plus_icon(*, color: QColor | str = "#f4f4f4") -> QIcon:
    """创建并返回一个加号图标。

    功能：在24x24像素的透明背景上绘制一个颜色可配置的加号图标。
    参数：
        color (QColor | str): 加号的颜色，支持QColor对象或颜色字符串，默认为浅灰色 "#f4f4f4"。
    返回值：
        QIcon: 包含绘制完成的加号图标的 QIcon 对象。
    """
    # 创建一个24x24像素的QPixmap画布
    pix = QPixmap(24, 24)
    # 用完全透明的颜色填充画布背景
    pix.fill(Qt.GlobalColor.transparent)
    # 初始化画家对象，用于在QPixmap上绘制
    painter = QPainter(pix)
    # 设置渲染提示，启用抗锯齿使线条更平滑
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    # 创建画笔：指定颜色、线宽(2.0)、实线样式、圆角笔帽
    pen = QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    # 将画笔设置给画家
    painter.setPen(pen)
    # 绘制加号的水平线段 (从点(6,12)到点(18,12))
    painter.drawLine(6, 12, 18, 12)
    # 绘制加号的垂直线段 (从点(12,6)到点(12,18))
    painter.drawLine(12, 6, 12, 18)
    # 结束绘图操作，释放相关资源
    painter.end()
    # 将绘制好的QPixmap转换为QIcon并返回
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
    """创建锁头图标。

    锁定态整体使用强调色发亮（与 theme.py 的 ACCENT_STRONG 一致），
    未锁定态使用常规控制色，二者一眼可辨。

    Args:
        locked: True 为已锁定（激活发亮），False 为未锁定（默认）。
        color: 未激活时的图标颜色。

    Returns:
        QIcon: 锁头图标。
    """
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    icon_color = QColor("#81e98b") if locked else QColor(color)
    pen = QPen(icon_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    # 锁体（圆角矩形）
    painter.drawRoundedRect(QRectF(6.0, 11.0, 12.0, 8.5), 2.5, 2.5)

    # 锁扣（U 形弧）：拉长弧线，两端正好嵌在锁体顶部
    shackle = QRectF(7.5, 5.0, 9.0, 12.0)
    if locked:
        # 闭合：上半弧形成穹顶
        painter.drawArc(shackle, 0, 180 * 16)
        # 锁孔（圆点 + 短竖线）强化"已锁"语义
        painter.setBrush(icon_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(10.9, 13.0, 2.2, 2.2))
        painter.setPen(pen)
        painter.drawLine(12.0, 15.0, 12.0, 17.0)
        painter.setBrush(QColor(0, 0, 0, 0))
    else:
        # 张开：以左端为轴抬起 28°，右臂轻微抬起，呈现"已开锁"姿态
        pivot = shackle.bottomLeft()
        painter.save()
        painter.translate(pivot.x(), pivot.y())
        painter.rotate(-28)
        painter.translate(-pivot.x(), -pivot.y())
        painter.drawArc(shackle, 0, 180 * 16)
        painter.restore()

    painter.end()
    return QIcon(pix)


def _make_pin_icon(pinned: bool, *, color: QColor | str = "#f4f4f4") -> QIcon:
    """创建图钉（thumbtack）图标。

    已固定态整体使用强调色发亮（与 theme.py 的 ACCENT_STRONG 一致），
    未固定态使用常规控制色，二者一眼可辨。

    Args:
        pinned: True 为已固定（激活发亮），False 为未固定（默认）。
        color: 未激活时的图标颜色。

    Returns:
        QIcon: 图钉图标。
    """
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    icon_color = QColor("#81e98b") if pinned else QColor(color)
    pen = QPen(icon_color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    # 图钉头部（扁椭圆 thumb pad，实心）
    painter.setBrush(icon_color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(5.5, 2.5, 13.0, 5.5))  # 宽 13、高 5.5，明显扁平

    # 针杆（尖锐三角，向下汇聚到一点）
    painter.setPen(pen)
    painter.setBrush(QColor(0, 0, 0, 0))
    needle = QPainterPath()
    needle.moveTo(9.8, 7.8)
    needle.lineTo(14.2, 7.8)
    needle.lineTo(12.0, 19.5)
    needle.closeSubpath()
    painter.drawPath(needle)

    if pinned:
        # 已固定：针尖下方一道短横线，表示"已钉入"表面
        painter.drawLine(8.0, 20.5, 16.0, 20.5)

    painter.end()
    return QIcon(pix)
