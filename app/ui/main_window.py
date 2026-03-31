from __future__ import annotations

import ctypes
import html
import re
import subprocess
import sys
import time
from bisect import bisect_right
from ctypes import HRESULT, c_int, c_uint, c_ulonglong, c_void_p
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt, QRect, QRectF, QSize, Signal, QPoint
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QCursor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSpacerItem,
    QStatusBar,
    QSlider,
    QSplitter,
    QStyle,
    QStyleOptionSlider,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models.entities import Track
from app.services.app_controller import AppController
from app.services.player_service import PlayMode
from app.ui.playlist_dialog import PlaylistDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import APP_STYLE_DARK, APP_STYLE_LIGHT
from app.version import APP_VERSION

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


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController):
        super().__init__()
        self.setStatusBar(MultiHintStatusBar(self))
        self.controller = controller
        self.player = controller.player_service

        self._dragging_progress = False
        self._compact_mode = False
        self._compact_locked = False
        self._always_on_top = False
        self._drag_offset: QPoint | None = None
        self._resize_margin = 7
        self._sidebar_collapsed = False
        self._sidebar_was_collapsed_before_compact = False
        self._sidebar_last_width = 530
        self._sidebar_min_width = 234
        self._sidebar_max_width = 936
        self._last_window_width = 0
        self._resize_adjusting_splitter = False
        self._width_before_compact = 0
        self._height_before_compact = 0
        self._min_width_before_compact = self.minimumWidth()
        self._max_width_before_compact = self.maximumWidth()
        self._min_height_before_compact = self.minimumHeight()
        self._max_height_before_compact = self.maximumHeight()

        self._mode_order: list[str] = []
        self._mode_titles = {
            PlayMode.SINGLE_LOOP.value: "单曲循环",
            PlayMode.PLAYLIST_LOOP.value: "歌单循环",
            PlayMode.RANDOM.value: "歌单随机",
        }
        self._mode_icons = {
            PlayMode.SINGLE_LOOP.value: _make_mode_icon(PlayMode.SINGLE_LOOP.value),
            PlayMode.PLAYLIST_LOOP.value: _make_mode_icon(PlayMode.PLAYLIST_LOOP.value),
            PlayMode.RANDOM.value: _make_mode_icon(PlayMode.RANDOM.value),
        }

        self._lyrics_entries: list[tuple[float, str]] = []
        self._lyrics_times: list[float] = []
        self._lyrics_end_times: list[float] = []
        self._lyrics_current_index = -1
        self._lyrics_user_scrolling = False
        self._lyrics_auto_adjusting = False
        self._has_cover_content = False
        self._has_lyrics_content = False
        self._last_nonzero_gain = max(1, int(self.player.gain_percent()))
        self._current_track_title = "未选择歌曲"
        self._current_track_artist = "未知歌手"
        self._next_track_preview_announced = False
        self._dark_theme = bool(getattr(self.controller.settings, "dark_theme", True))
        self._taskbar_progress = _WindowsTaskbarProgress()
        self._rich_drag_offset: QPoint | None = None
        self._rich_drag_restore_ratio = 0.5
        self._snap_docked = False
        self._geometry_before_snap: QRect | None = None
        self._top_stack_widget: QWidget | None = None
        self._lyrics_resume_timer = QTimer(self)
        self._lyrics_resume_timer.setSingleShot(True)
        self._lyrics_resume_timer.setInterval(2200)
        self._lyrics_resume_timer.timeout.connect(self._resume_lyrics_auto_scroll)

        self.setWindowTitle("MusePlayer")
        self.resize(1280, 780)
        self.setAcceptDrops(True)
        self._last_window_width = self.width()

        self._build_ui()
        self._build_menu()
        self._bind_signals()
        self._bind_shortcuts()
        self._restore_window_geometry()

        self._reload_playlist_combo()
        self._reload_track_list()
        self._refresh_current_track_ui(self.player.current_track())
        self._refresh_mode_order()
        self._on_mode_changed(self.player.mode.value)
        self._on_playback_changed(self.player.is_playing())
        self._refresh_volume_ui()
        self._apply_theme_stylesheet()
        self._refresh_theme_button()
        self._refresh_random_state_hint()
        self._update_window_title()
        self._refresh_window_flags()

        QTimer.singleShot(0, self._reposition_sidebar_toggle)
        QTimer.singleShot(0, self._ensure_taskbar_progress_initialized)

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(8, 6, 8, 4)
        main_layout.setSpacing(6)

        self.rich_title_bar = QFrame(root)
        self.rich_title_bar.setObjectName("RichTitleBar")
        title_row = QHBoxLayout(self.rich_title_bar)
        title_row.setContentsMargins(8, 4, 8, 4)
        title_row.setSpacing(6)
        self.rich_title_label = QLabel("MusePlayer")
        self.rich_title_label.setObjectName("RichTitleLabel")
        self.rich_title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.rich_min_btn = self._new_icon_button("RichTitleButton")
        self.rich_max_btn = self._new_icon_button("RichTitleButton")
        self.rich_close_btn = self._new_icon_button("RichTitleButton")
        self.rich_min_btn.setToolTip("最小化")
        self.rich_max_btn.setToolTip("最大化 / 还原")
        self.rich_close_btn.setToolTip("关闭")
        self.rich_min_btn.setText("")
        self.rich_max_btn.setText("")
        self.rich_close_btn.setText("")
        self.rich_min_btn.setIconSize(QSize(14, 14))
        self.rich_max_btn.setIconSize(QSize(14, 14))
        self.rich_close_btn.setIconSize(QSize(14, 14))
        title_row.addWidget(self.rich_title_label, 1)
        title_row.addWidget(self.rich_min_btn, 0)
        title_row.addWidget(self.rich_max_btn, 0)
        title_row.addWidget(self.rich_close_btn, 0)
        self.rich_title_bar.installEventFilter(self)
        self.rich_title_label.installEventFilter(self)
        self.rich_min_btn.clicked.connect(self.showMinimized)
        self.rich_max_btn.clicked.connect(self._toggle_rich_maximize)
        self.rich_close_btn.clicked.connect(self.close)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, root)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(6)
        main_layout.addWidget(self.main_splitter, 1)

        left_container = QWidget(self.main_splitter)
        left_col = QVBoxLayout(left_container)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(0)

        self.card_now = QFrame(left_container)
        self.card_now.setObjectName("Card")
        now_layout = QVBoxLayout(self.card_now)
        now_layout.setContentsMargins(14, 12, 14, 10)
        now_layout.setSpacing(6)

        self.title_label = QLabel("未选择歌曲")
        self.title_label.setObjectName("TitleLabel")
        self.title_label.setWordWrap(True)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.artist_label = QLabel("歌手")
        self.artist_label.setObjectName("MetaLabel")
        self.album_label = QLabel("专辑")
        self.album_label.setObjectName("MetaLabel")
        self.path_label = QLabel("")
        self.path_label.setObjectName("CaptionLabel")
        self.path_label.setWordWrap(True)
        self.path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._meta_top_spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._meta_bottom_spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        self.cover_label = QLabel("暂无封面")
        self.cover_label.setFixedSize(170, 170)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet("")

        self.lyrics_list = LyricsListWidget()
        self.lyrics_list.setObjectName("lyrics_list")
        self.lyrics_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.lyrics_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.lyrics_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lyrics_list.setSpacing(2)
        self.lyrics_list.setMouseTracking(True)
        self.lyrics_delegate = LyricsItemDelegate(self.lyrics_list)
        self.lyrics_list.setItemDelegate(self.lyrics_delegate)
        self.lyrics_list.viewport().installEventFilter(self)

        self.info_media_row_widget = QWidget(self.card_now)
        media_row = QHBoxLayout(self.info_media_row_widget)
        media_row.setContentsMargins(0, 0, 0, 0)
        media_row.setSpacing(12)
        media_row.addWidget(self.cover_label, 0, Qt.AlignmentFlag.AlignVCenter)
        media_row.addWidget(self.lyrics_list, 1)

        now_layout.addItem(self._meta_top_spacer)
        now_layout.addWidget(self.title_label)
        now_layout.addWidget(self.artist_label)
        now_layout.addWidget(self.album_label)
        now_layout.addWidget(self.path_label)
        now_layout.addItem(self._meta_bottom_spacer)
        now_layout.addWidget(self.info_media_row_widget, 1)

        left_col.addWidget(self.card_now, 1)

        self.card_controls = QFrame(root)
        self.card_controls.setObjectName("Card")
        self._controls_normal_margins = (14, 8, 14, 10)
        self._controls_compact_margins = (12, 6, 12, 8)
        self.controls_layout = QVBoxLayout(self.card_controls)
        self.controls_layout.setContentsMargins(*self._controls_normal_margins)
        self.controls_layout.setSpacing(8)

        self.compact_info_widget = QWidget(self.card_controls)
        compact_info_layout = QVBoxLayout(self.compact_info_widget)
        compact_info_layout.setContentsMargins(0, 0, 0, 0)
        compact_info_layout.setSpacing(0)
        self.compact_song_label = QLabel("")
        self.compact_song_label.setObjectName("CompactLyricLineLabel")
        self.compact_song_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        compact_info_layout.addWidget(self.compact_song_label)
        self.compact_info_widget.hide()

        self.progress_slider = ClickJumpSlider(Qt.Orientation.Horizontal, volume_wheel=True)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.sliderPressed.connect(self._on_progress_pressed)
        self.progress_slider.sliderReleased.connect(self._on_progress_released)

        time_row = QHBoxLayout()
        self.current_time_label = QLabel("00:00")
        self.current_time_label.setObjectName("CaptionLabel")
        self.progress_center_label = QLabel("")
        self.progress_center_label.setObjectName("CompactLyricLineLabel")
        self.progress_center_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_center_label.hide()
        self.total_time_label = QLabel("00:00")
        self.total_time_label.setObjectName("CaptionLabel")
        time_row.addWidget(self.current_time_label)
        time_row.addStretch(1)
        time_row.addWidget(self.progress_center_label, 1)
        time_row.addStretch(1)
        time_row.addWidget(self.total_time_label)

        self.controls_layout.addWidget(self.compact_info_widget)
        self.controls_layout.addWidget(self.progress_slider)
        self.controls_layout.addLayout(time_row)

        control_row = QHBoxLayout()
        control_row.setSpacing(8)

        self.theme_btn = self._new_icon_button("ControlIconButton")
        self.theme_btn.setToolTip("切换到日间模式")

        self.locate_file_btn = self._new_icon_button("ControlIconButton")
        self.locate_file_btn.setIcon(_make_folder_icon(color=self._control_icon_color()))
        self.locate_file_btn.setToolTip("在资源管理器中定位当前文件")

        self.mode_btn = self._new_icon_button("ModeButton")

        self.prev_btn = self._new_icon_button("ControlIconButton")
        self.prev_btn.setIcon(_make_media_icon("prev", color=self._control_icon_color()))
        self.prev_btn.setToolTip("上一首")

        self.play_btn = self._new_icon_button("ControlIconButton")
        self.play_btn.setToolTip("播放 / 暂停")

        self.next_btn = self._new_icon_button("ControlIconButton")
        self.next_btn.setIcon(_make_media_icon("next", color=self._control_icon_color()))
        self.next_btn.setToolTip("下一首")

        self.volume_panel = QWidget()
        self.volume_panel.setObjectName("VolumePanel")
        volume_layout = QVBoxLayout(self.volume_panel)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(0)
        self.mute_btn = self._new_icon_button("VolumeIconButton")
        self.mute_btn.setToolTip("静音 / 取消静音")
        volume_layout.addWidget(self.mute_btn, 0, Qt.AlignmentFlag.AlignCenter)
        self.volume_panel.setFixedSize(32, 32)

        self.volume_value_label = QLabel("100%", self.card_controls)
        self.volume_value_label.setObjectName("VolumeValueLabel")
        self.volume_value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.volume_value_label.setFixedHeight(18)
        self.volume_value_label.setFixedWidth(self.volume_value_label.fontMetrics().horizontalAdvance("500%") + 8)

        self.volume_slider = ClickJumpSlider(Qt.Orientation.Horizontal, volume_wheel=True)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.player.slider_gain_percent())
        self.volume_slider.setMinimumWidth(150)

        self.compact_btn = self._new_icon_button("CompactButton")
        self.compact_btn.setIcon(_make_plus_minus_icon(False, color=self._control_icon_color()))
        self.compact_btn.setToolTip("切换到简洁模式")

        self.speed_combo = QComboBox()
        self.speed_combo.setMinimumWidth(88)
        for rate in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            self.speed_combo.addItem(f"{rate:.2g}x", rate)
        self._sync_speed_combo()

        control_row.addWidget(self.theme_btn)
        control_row.addWidget(self.locate_file_btn)
        control_row.addWidget(self.mode_btn)
        control_row.addWidget(self.prev_btn)
        control_row.addWidget(self.play_btn)
        control_row.addWidget(self.next_btn)
        control_row.addSpacing(6)
        control_row.addWidget(self.volume_panel)
        control_row.addWidget(self.volume_slider, 1)
        control_row.addWidget(self.speed_combo)
        control_row.addWidget(self.compact_btn)

        self.controls_layout.addLayout(control_row)

        self.side_card = QFrame(self.main_splitter)
        self.side_card.setObjectName("Card")
        side_layout = QVBoxLayout(self.side_card)
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.setSpacing(8)

        side_title = QLabel("当前歌单")
        side_title.setObjectName("MetaLabel")
        self.playlist_combo = QComboBox()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索当前歌单（标题 / 歌手 / 专辑）")
        self.track_list = QListWidget()
        self.track_list.setObjectName("track_list")
        self.track_list.setMouseTracking(True)
        self.track_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.track_list.setUniformItemSizes(False)
        self.track_delegate = TrackItemDelegate(self.track_list)
        self.track_list.setItemDelegate(self.track_delegate)
        self.track_list.viewport().installEventFilter(self)
        self.locate_current_btn = self._new_icon_button("LocateCurrentButton")
        self.locate_current_btn.setParent(self.track_list.viewport())
        self.locate_current_btn.setIcon(_make_crosshair_icon(color=self._control_icon_color()))
        self.locate_current_btn.setToolTip("定位到当前播放歌曲")
        self.locate_current_btn.clicked.connect(self._locate_current_track_in_list)
        self.locate_current_btn.hide()

        side_layout.addWidget(side_title)
        side_layout.addWidget(self.playlist_combo)
        side_layout.addWidget(self.search_edit)
        side_layout.addWidget(self.track_list, 1)

        self.main_splitter.setSizes([720, self._sidebar_last_width])
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)

        main_layout.addWidget(self.card_controls, 0)

        self.sidebar_toggle_btn = QToolButton(root)
        self.sidebar_toggle_btn.setObjectName("SidebarToggle")
        self.sidebar_toggle_btn.setToolTip("收起 / 展开快捷侧边栏")
        self.sidebar_toggle_btn.clicked.connect(self._toggle_sidebar)
        self._update_sidebar_toggle_icon()

        self.compact_top_bar = QFrame(self.card_controls)
        self.compact_top_bar.setObjectName("CompactTopBar")
        compact_bar_layout = QHBoxLayout(self.compact_top_bar)
        compact_bar_layout.setContentsMargins(6, 3, 6, 3)
        compact_bar_layout.setSpacing(6)

        self.opacity_slider = ClickJumpSlider(Qt.Orientation.Horizontal, self.compact_top_bar)
        self.opacity_slider.setRange(35, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setFixedWidth(84)
        self.opacity_slider.setToolTip("调整窗口透明度")

        self.lock_btn = self._new_icon_button("CompactTopButton")
        self.pin_btn = self._new_icon_button("CompactTopButton")
        self.compact_top_title_label = QLabel("未选择歌曲", self.compact_top_bar)
        self.compact_top_title_label.setObjectName("CompactTopTitle")
        self.compact_top_title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.compact_top_title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.compact_close_btn = self._new_icon_button("CompactTopButton")
        self.compact_close_btn.setIcon(_make_rich_title_icon("close", color=self._control_icon_color()))
        self.compact_close_btn.setToolTip("返回丰富模式")
        self.compact_close_btn.clicked.connect(self._exit_compact_mode)
        compact_bar_layout.addWidget(self.opacity_slider, 0, Qt.AlignmentFlag.AlignLeft)
        compact_bar_layout.addStretch(1)
        compact_bar_layout.addWidget(self.lock_btn)
        compact_bar_layout.addWidget(self.pin_btn)
        compact_bar_layout.addWidget(self.compact_close_btn)
        self.compact_top_bar.setMinimumHeight(30)
        self.controls_layout.insertWidget(0, self.compact_top_bar)
        self.compact_top_bar.hide()
        self._refresh_compact_top_buttons()

        self.statusBar().showMessage("就绪", 1800)
        QTimer.singleShot(0, self._reposition_volume_value_label)

    def _build_menu(self) -> None:
        self.menuBar().setNativeMenuBar(False)
        self.menuBar().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.menuBar().setMouseTracking(True)

        menu_file = self.menuBar().addMenu("文件")
        action_import_folder = menu_file.addAction("导入文件夹")
        action_open_file = menu_file.addAction("播放文件")
        menu_file.addSeparator()
        action_exit = menu_file.addAction("退出")

        action_playlist = self.menuBar().addAction("歌单")

        action_settings = self.menuBar().addAction("设置")

        self.random_state_label = QLabel("")
        self.random_state_label.setObjectName("RandomStateHintLabel")
        self.random_state_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.random_state_label.setMinimumWidth(120)
        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setObjectName("VersionHintLabel")
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.version_label.setMinimumWidth(58)
        self.version_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        self.menu_hint_widget = QWidget(self.menuBar())
        hint_layout = QHBoxLayout(self.menu_hint_widget)
        hint_layout.setContentsMargins(0, 0, 0, 0)
        hint_layout.setSpacing(8)
        hint_layout.addWidget(self.version_label, 0)
        hint_layout.addWidget(self.random_state_label, 0)

        self.menuBar().setCornerWidget(self.menu_hint_widget, Qt.Corner.TopRightCorner)
        self.random_state_label.hide()

        for menu in (menu_file,):
            menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            menu.setMouseTracking(True)

        action_import_folder.triggered.connect(self._menu_import_folder)
        action_open_file.triggered.connect(self._menu_open_file)
        action_exit.triggered.connect(self.close)
        action_playlist.triggered.connect(self._open_playlist_dialog)
        action_settings.triggered.connect(self._open_settings_dialog)
        self._stack_title_and_menu()

    def _stack_title_and_menu(self) -> None:
        if self._top_stack_widget is not None:
            return
        stack = QWidget(self)
        stack.setObjectName("TopStackWidget")
        stack_layout = QVBoxLayout(stack)
        stack_layout.setContentsMargins(0, 0, 0, 0)
        stack_layout.setSpacing(0)
        self.rich_title_bar.setParent(stack)
        stack_layout.addWidget(self.rich_title_bar, 0)
        stack_layout.addWidget(self.menuBar(), 0)
        self.setMenuWidget(stack)
        self._top_stack_widget = stack

    def _bind_signals(self) -> None:
        self.theme_btn.clicked.connect(self._toggle_theme)
        self.locate_file_btn.clicked.connect(self._open_current_in_explorer)
        self.prev_btn.clicked.connect(self._play_previous_track)
        self.play_btn.clicked.connect(self.player.toggle_play_pause)
        self.next_btn.clicked.connect(self._play_next_track)

        self.mode_btn.clicked.connect(self._cycle_play_mode)
        self.mute_btn.clicked.connect(self._toggle_mute)
        self.volume_slider.valueChanged.connect(self._on_volume_slider_changed)
        self.compact_btn.clicked.connect(self._toggle_compact_mode)
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self.lock_btn.clicked.connect(self._toggle_compact_lock)
        self.pin_btn.clicked.connect(self._toggle_always_on_top)

        self.playlist_combo.currentIndexChanged.connect(self._on_playlist_combo_changed)
        self.search_edit.textChanged.connect(lambda _: self._reload_track_list())
        self.track_list.itemDoubleClicked.connect(self._on_track_double_clicked)
        self.track_list.verticalScrollBar().valueChanged.connect(lambda _: self._update_locate_current_button())

        self.player.track_changed.connect(self._refresh_current_track_ui)
        self.player.progress_changed.connect(self._on_progress_changed)
        self.player.playback_changed.connect(self._on_playback_changed)
        self.player.mode_changed.connect(self._on_mode_changed)
        self.player.random_state_changed.connect(self._on_random_state_changed)
        self.player.playback_rate_changed.connect(self._on_playback_rate_changed)
        self.player.queue_changed.connect(self._on_queue_changed)

        self.controller.library_changed.connect(self._on_library_changed)
        self.controller.settings_changed.connect(self._on_settings_changed)
        self.controller.error_occurred.connect(self._on_error)
        self.controller.runtime_status_changed.connect(self._on_runtime_status_changed)

        self.main_splitter.splitterMoved.connect(self._on_splitter_moved)

        self.lyrics_list.user_interacted.connect(self._on_lyrics_user_interaction)
        self.lyrics_list.copy_requested.connect(self._copy_selected_lyric)
        self.lyrics_list.itemDoubleClicked.connect(self._on_lyric_double_clicked)
        self.lyrics_list.verticalScrollBar().sliderPressed.connect(self._on_lyrics_user_interaction)
        self.lyrics_list.verticalScrollBar().valueChanged.connect(self._on_lyrics_scroll_changed)

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Space"), self, activated=self.player.toggle_play_pause)
        QShortcut(QKeySequence("PgUp"), self, activated=self._play_previous_track)
        QShortcut(QKeySequence("PgDown"), self, activated=self._play_next_track)
        QShortcut(QKeySequence("Up"), self, activated=lambda: self._adjust_volume_by_key(True))
        QShortcut(QKeySequence("Down"), self, activated=lambda: self._adjust_volume_by_key(False))
        QShortcut(QKeySequence("Left"), self, activated=lambda: self._seek_by_seconds(-5.0))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self._seek_by_seconds(+5.0))

    def _play_previous_track(self) -> None:
        ok = self.player.previous_track()
        if not ok:
            self.statusBar().showMessage("没有上一首可播放", 2000)

    def _play_next_track(self) -> None:
        ok = self.player.next_track(user_triggered=True)
        if not ok:
            self.statusBar().showMessage("没有下一首可播放", 2000)

    def _new_icon_button(self, object_name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName(object_name)
        button.setIconSize(QSize(18, 18))
        button.setAutoRaise(False)
        return button

    def _reload_playlist_combo(self) -> None:
        current = self.player.current_playlist_id
        self.playlist_combo.blockSignals(True)
        self.playlist_combo.clear()

        index_to_select = 0
        for idx, playlist in enumerate(self.controller.library_service.list_playlists()):
            display_name = "全部歌曲" if playlist.id == "all_songs" else playlist.name
            self.playlist_combo.addItem(display_name, playlist.id)
            if playlist.id == current:
                index_to_select = idx

        self.playlist_combo.setCurrentIndex(index_to_select)
        self.playlist_combo.blockSignals(False)

    def _reload_track_list(self) -> None:
        keyword = self.search_edit.text().strip()
        tracks = self.player.search_playlist_tracks(keyword)

        self.track_delegate.set_hover_row(-1)
        self.track_list.setUpdatesEnabled(False)
        self.track_list.clear()
        current_id = self.player.current_track_id
        row_to_select = -1
        total = len(tracks)

        for idx, track in enumerate(tracks):
            text = f"{track.title}  -  {track.artist}"
            item = QListWidgetItem(text)
            item.setData(0x0100, track.id)
            item.setSizeHint(QSize(0, self._track_item_height_for_text(text)))
            self.track_list.addItem(item)
            if track.id == current_id:
                row_to_select = idx
            if total > 600 and idx > 0 and idx % 300 == 0:
                self.statusBar().showMessage(f"列表加载中：{idx}/{total}", 1500)
                QCoreApplication.processEvents()

        if row_to_select >= 0:
            self.track_list.setCurrentRow(row_to_select)
            current_item = self.track_list.item(row_to_select)
            if current_item is not None:
                self.track_list.scrollToItem(current_item, QListWidget.ScrollHint.PositionAtCenter)
        self.track_list.setUpdatesEnabled(True)
        self._position_locate_current_button()
        self._update_locate_current_button()

    def _sync_current_track_row(self, *, center: bool) -> None:
        row = self._find_current_track_row()
        if row < 0:
            return
        self.track_list.setCurrentRow(row)
        item = self.track_list.item(row)
        if item is None:
            return
        hint = QListWidget.ScrollHint.PositionAtCenter if center else QListWidget.ScrollHint.EnsureVisible
        self.track_list.scrollToItem(item, hint)

    def _track_item_height_for_text(self, text: str) -> int:
        fm = self.track_list.fontMetrics()
        width = max(80, self.track_list.viewport().width() - TrackItemDelegate.REMOVE_WIDTH - 12)
        bounds = fm.boundingRect(
            QRect(0, 0, width, 2000),
            int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere),
            text,
        )
        return max(24, bounds.height() + 8)

    def _refresh_current_track_ui(self, track: Track | None) -> None:
        self._next_track_preview_announced = False
        if track is None:
            self.title_label.setText("未选择歌曲")
            self.artist_label.setText("歌手")
            self.album_label.setText("专辑")
            self.path_label.setText("")
            self._current_track_title = "未选择歌曲"
            self._current_track_artist = "未知歌手"
            self.compact_top_title_label.setText(self._current_track_title)
            self.progress_center_label.setText("♪" if self._compact_mode else "")
            self._update_window_title()
            self._set_cover(None)
            self._load_lyrics("")
            self._refresh_now_card_layout()
            self._refresh_random_state_hint()
            return

        self._current_track_title = track.title or "未知标题"
        self._current_track_artist = (track.artist or "未知歌手").strip() or "未知歌手"
        self.title_label.setText(self._current_track_title)
        self.artist_label.setText(f"歌手: {self._current_track_artist}")
        self.album_label.setText(f"专辑: {track.album or '未知专辑'}")
        self.path_label.setText(track.path)
        self.compact_top_title_label.setText(self._current_track_title)
        if not self._compact_mode:
            self.progress_center_label.setText("")

        lyrics = self.controller.get_current_lyrics()
        self._load_lyrics(lyrics)
        self._set_cover(self.controller.get_current_cover())
        self._refresh_now_card_layout()
        self._update_window_title()
        self._refresh_random_state_hint()

        self._sync_current_track_row(center=True)
        self._update_locate_current_button()
        self.statusBar().showMessage(f"播放歌曲：{self._current_track_title}", 3000)

    def _set_cover(self, cover_data: bytes | None) -> None:
        if not cover_data:
            self._has_cover_content = False
            self.cover_label.hide()
            return

        pixmap = QPixmap()
        ok = pixmap.loadFromData(cover_data)
        if not ok:
            self._has_cover_content = False
            self.cover_label.hide()
            return

        scaled = pixmap.scaled(
            self.cover_label.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._has_cover_content = True
        self.cover_label.show()
        self.cover_label.setText("")
        self.cover_label.setPixmap(scaled)

    def _load_lyrics(self, raw_lyrics: str) -> None:
        clean = html.unescape((raw_lyrics or "").replace("\r\n", "\n").replace("\r", "\n"))
        entries = _parse_lrc_entries(clean)

        self._lyrics_entries = entries
        self._lyrics_times = [x[0] for x in entries]
        self._lyrics_end_times = self._build_lyrics_end_times(entries)
        self._lyrics_current_index = -1
        self._lyrics_user_scrolling = False
        self._lyrics_auto_adjusting = False

        self.lyrics_list.clear()
        self.lyrics_delegate.set_times(self._lyrics_times, self._lyrics_end_times)
        self.lyrics_delegate.set_hover_row(-1)

        if entries:
            self._has_lyrics_content = True
            self.lyrics_list.show()
            for idx, (_, text) in enumerate(entries):
                item = QListWidgetItem(text or "♪")
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                fm = self.lyrics_list.fontMetrics()
                item.setSizeHint(QSize(0, max(24, fm.height() + 8)))
                self.lyrics_list.addItem(item)
            self._sync_lyrics_with_position(0.0)
            return

        lines = [html.unescape(x.strip()) for x in clean.split("\n") if x.strip()]
        if not lines:
            self._has_lyrics_content = False
            self.lyrics_list.hide()
            if self._compact_mode:
                self.progress_center_label.setText("")
            return

        self._has_lyrics_content = True
        self.lyrics_list.show()
        for line in lines:
            item = QListWidgetItem(line)
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            fm = self.lyrics_list.fontMetrics()
            item.setSizeHint(QSize(260, max(24, fm.height() + 8)))
            self.lyrics_list.addItem(item)
        if self._compact_mode:
            self.progress_center_label.setText(lines[0] if lines else "(暂无歌词)")

    def _build_lyrics_end_times(self, entries: list[tuple[float, str]]) -> list[float]:
        if not entries:
            return []
        duration = float(self.player.state_snapshot().get("duration_sec", 0.0))
        end_times: list[float] = []
        for idx, (start_sec, _) in enumerate(entries):
            if idx + 1 < len(entries):
                end_times.append(max(start_sec, entries[idx + 1][0]))
            else:
                fallback = duration if duration > start_sec else start_sec + 3.0
                end_times.append(fallback)
        return end_times

    def _refresh_now_card_layout(self) -> None:
        has_cover = bool(self._has_cover_content)
        has_lyrics = bool(self._has_lyrics_content)
        compact_center = not has_cover and not has_lyrics

        if hasattr(self, "info_media_row_widget"):
            self.info_media_row_widget.setVisible(has_cover or has_lyrics)

        if compact_center:
            self.path_label.hide()
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.artist_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.album_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.path_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self._meta_top_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            self._meta_bottom_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        else:
            self.path_label.show()
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.artist_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.album_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.path_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self._meta_top_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            self._meta_bottom_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        layout = self.card_now.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()

    def _on_progress_pressed(self) -> None:
        self._dragging_progress = True

    def _on_progress_released(self) -> None:
        self._dragging_progress = False
        duration = max(0.0, self.player.state_snapshot().get("duration_sec", 0.0))
        if duration <= 0:
            return
        position = duration * (self.progress_slider.value() / 1000.0)
        self.player.seek(position)
        self._sync_lyrics_with_position(position)

    def _on_progress_changed(self, position: float, duration: float) -> None:
        self.current_time_label.setText(_format_time(position))
        self.total_time_label.setText(_format_time(duration))
        self._update_taskbar_progress(position, duration)
        self._maybe_show_next_track_preview(position, duration)

        if not self._lyrics_user_scrolling:
            self._sync_lyrics_with_position(position)

        if self._dragging_progress:
            return
        if duration <= 0:
            self.progress_slider.setValue(0)
            return

        ratio = max(0.0, min(1.0, position / duration))
        self.progress_slider.setValue(int(round(ratio * 1000)))

    def _sync_lyrics_with_position(self, position_sec: float) -> None:
        if not self._lyrics_entries:
            return

        idx = bisect_right(self._lyrics_times, position_sec) - 1
        if idx < 0:
            idx = 0
        idx = min(idx, len(self._lyrics_entries) - 1)

        if idx == self._lyrics_current_index:
            return

        self._lyrics_current_index = idx
        if self._compact_mode:
            self.progress_center_label.setText(self._lyrics_entries[idx][1] or "♪")
        self._lyrics_auto_adjusting = True
        self.lyrics_list.setCurrentRow(idx)
        item = self.lyrics_list.item(idx)
        if item is not None:
            self.lyrics_list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
        self._lyrics_auto_adjusting = False

    def _on_lyrics_user_interaction(self) -> None:
        if not self._lyrics_entries:
            return
        self._lyrics_user_scrolling = True
        self._lyrics_resume_timer.start()

    def _on_lyrics_scroll_changed(self, _value: int) -> None:
        if self._lyrics_auto_adjusting:
            return
        if self.lyrics_list.underMouse():
            self._on_lyrics_user_interaction()

    def _resume_lyrics_auto_scroll(self) -> None:
        self._lyrics_user_scrolling = False
        position = float(self.player.state_snapshot().get("position_sec", 0.0))
        self._sync_lyrics_with_position(position)

    def _copy_selected_lyric(self) -> None:
        item = self.lyrics_list.currentItem()
        if item is None:
            return
        text = self._lyric_text_of_item(item)
        if not text:
            return
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(text)
        self.statusBar().showMessage(f"已复制歌词：{text}", 2000)

    def _on_lyric_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.lyrics_list.row(item)
        if row < 0 or row >= len(self._lyrics_times):
            return
        target = float(self._lyrics_times[row])
        self.player.seek(target)
        self._sync_lyrics_with_position(target)
        self.statusBar().showMessage(f"跳转到歌词时间：{_format_lrc_time(target)}", 2500)

    def _on_playback_changed(self, playing: bool) -> None:
        icon = _make_media_icon("pause" if playing else "play", color=self._control_icon_color())
        self.play_btn.setIcon(icon)
        state = "播放" if playing else "暂停"
        self.statusBar().showMessage(f"{state}：{self._current_track_title}", 2000)

    def _refresh_mode_order(self) -> None:
        self._mode_order = list(self.player.available_modes())
        if not self._mode_order:
            self._mode_order = [PlayMode.SINGLE_LOOP.value, PlayMode.RANDOM.value]

    def _cycle_play_mode(self) -> None:
        self._refresh_mode_order()
        current = self.player.mode.value
        try:
            idx = self._mode_order.index(current)
        except ValueError:
            idx = 0
        next_mode = self._mode_order[(idx + 1) % len(self._mode_order)]
        self.player.set_mode(next_mode)
        title = self._mode_titles.get(next_mode, next_mode)
        self.statusBar().showMessage(f"播放模式：{title}", 2500)

    def _on_mode_changed(self, mode: str) -> None:
        self._refresh_mode_order()
        fallback = PlayMode.SINGLE_LOOP.value
        self.mode_btn.setIcon(self._mode_icons.get(mode, self._mode_icons[fallback]))
        title = self._mode_titles.get(mode, self._mode_titles[fallback])
        self.mode_btn.setToolTip(f"播放模式: {title}（点击切换）")
        self._next_track_preview_announced = False
        self._refresh_random_state_hint()

    def _toggle_theme(self) -> None:
        self._dark_theme = not self._dark_theme
        self._apply_theme_stylesheet()
        self._refresh_theme_button()
        self.controller.set_theme_preference(self._dark_theme)
        self.statusBar().showMessage("主题：夜间模式" if self._dark_theme else "主题：日间模式", 1800)

    def _apply_theme_stylesheet(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        app.setStyleSheet(APP_STYLE_DARK if self._dark_theme else APP_STYLE_LIGHT)
        self._refresh_control_icons()

    def _refresh_theme_button(self) -> None:
        if not hasattr(self, "theme_btn"):
            return
        color = self._control_icon_color()
        if self._dark_theme:
            self.theme_btn.setIcon(_make_sun_icon(color=color))
            self.theme_btn.setToolTip("切换到日间模式")
        else:
            self.theme_btn.setIcon(_make_moon_icon(color=color))
            self.theme_btn.setToolTip("切换到夜间模式")

    def _control_icon_color(self) -> QColor:
        return QColor("#f4f4f4") if self._dark_theme else QColor("#1f2521")

    def _refresh_control_icons(self) -> None:
        if not hasattr(self, "play_btn"):
            return
        color = self._control_icon_color()
        self._mode_icons = {
            PlayMode.SINGLE_LOOP.value: _make_mode_icon(PlayMode.SINGLE_LOOP.value, color=color),
            PlayMode.PLAYLIST_LOOP.value: _make_mode_icon(PlayMode.PLAYLIST_LOOP.value, color=color),
            PlayMode.RANDOM.value: _make_mode_icon(PlayMode.RANDOM.value, color=color),
        }
        self.prev_btn.setIcon(_make_media_icon("prev", color=color))
        self.next_btn.setIcon(_make_media_icon("next", color=color))
        self.play_btn.setIcon(_make_media_icon("pause" if self.player.is_playing() else "play", color=color))
        self.locate_file_btn.setIcon(_make_folder_icon(color=color))
        self.compact_btn.setIcon(_make_plus_minus_icon(self._compact_mode, color=color))
        self.locate_current_btn.setIcon(_make_crosshair_icon(color=color))
        self._refresh_rich_title_icons()
        self._refresh_theme_button()
        self._update_sidebar_toggle_icon()
        self._refresh_compact_top_buttons()
        self._refresh_volume_ui()
        self._on_mode_changed(self.player.mode.value)

    def _on_random_state_changed(self, seed: int, idx: int) -> None:
        _ = seed, idx
        self._refresh_random_state_hint()

    def _refresh_random_state_hint(self) -> None:
        if not hasattr(self, "random_state_label"):
            return
        if self.player.mode != PlayMode.RANDOM:
            self.random_state_label.setText("")
            self.random_state_label.hide()
            return
        self.random_state_label.setText(f"seed:{self.player.random_seed} idx:{self.player.random_index}")
        self.random_state_label.show()

    def _update_window_title(self) -> None:
        title = (self._current_track_title or "").strip()
        if not title or title == "未选择歌曲":
            self.setWindowTitle("MusePlayer")
            if hasattr(self, "rich_title_label"):
                self.rich_title_label.setText("MusePlayer")
            return
        artist = (self._current_track_artist or "").strip() or "未知歌手"
        title_text = f"{title} - {artist}"
        self.setWindowTitle(f"{title_text} - MusePlayer")
        if hasattr(self, "rich_title_label"):
            self.rich_title_label.setText(title_text)

    def _maybe_show_next_track_preview(self, position: float, duration: float) -> None:
        status = self.statusBar()
        if duration <= 0.0 or not self.player.is_playing():
            if isinstance(status, MultiHintStatusBar):
                status.clear_hint("下一首")
            return
        remaining = max(0.0, float(duration) - float(position))
        if remaining > 5.0:
            if isinstance(status, MultiHintStatusBar):
                status.clear_hint("下一首")
            return
        next_track = self.player.preview_next_track()
        if next_track is None:
            if isinstance(status, MultiHintStatusBar):
                status.clear_hint("下一首")
            return
        title = (next_track.title or "未知标题").strip() or "未知标题"
        left = max(0, int(round(remaining)))
        if isinstance(status, MultiHintStatusBar):
            status.set_hint("下一首", f"下一首：{title}（{left}s）", 1200)
        else:
            self.statusBar().showMessage(f"下一首：{title}（{left}s）", 1200)

    def _toggle_mute(self) -> None:
        gain = self.player.gain_percent()
        if gain <= 0:
            target = max(1, self._last_nonzero_gain)
            self.player.set_gain_percent(target, allow_boost=True)
        else:
            self._last_nonzero_gain = max(1, gain)
            self.player.set_gain_percent(0, allow_boost=True)
        self._refresh_volume_ui()
        self.statusBar().showMessage(f"音量：{self.player.gain_percent()}%", 2000)

    def _on_volume_slider_changed(self, value: int) -> None:
        self.player.set_gain_percent(int(value), allow_boost=False)
        self._refresh_volume_ui()
        self.statusBar().showMessage(f"音量：{self.player.gain_percent()}%", 1500)

    def _adjust_volume_by_key(self, increase: bool) -> None:
        self.player.adjust_gain_by_key(increase)
        self._refresh_volume_ui()
        self.statusBar().showMessage(f"音量：{self.player.gain_percent()}%", 1500)

    def _refresh_volume_ui(self) -> None:
        gain = self.player.gain_percent()
        slider_value = self.player.slider_gain_percent()
        if gain > 0:
            self._last_nonzero_gain = max(1, gain)
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(slider_value)
        self.volume_slider.blockSignals(False)
        self.volume_value_label.setText(f"{gain}%")
        muted = gain <= 0
        icon = _make_volume_icon(muted=muted, color=self._control_icon_color())
        self.mute_btn.setIcon(icon)
        self.mute_btn.setToolTip("取消静音" if muted else "静音")
        self._reposition_volume_value_label()

    def _on_opacity_changed(self, value: int) -> None:
        alpha = max(0.35, min(1.0, int(value) / 100.0))
        self.setWindowOpacity(alpha)
        self.statusBar().showMessage(f"窗口透明度：{int(round(alpha * 100))}%", 1500)

    def _toggle_compact_lock(self) -> None:
        self._compact_locked = not self._compact_locked
        if self._compact_locked:
            self._drag_offset = None
        self._refresh_compact_top_buttons()
        self.statusBar().showMessage("窗口位置已锁定" if self._compact_locked else "窗口位置已解锁", 2000)

    def _toggle_always_on_top(self) -> None:
        self._always_on_top = not self._always_on_top
        self._refresh_window_flags()
        self._refresh_compact_top_buttons()
        self.statusBar().showMessage("已开启窗口置顶" if self._always_on_top else "已关闭窗口置顶", 2000)

    def _seek_by_seconds(self, delta: float) -> None:
        state = self.player.state_snapshot()
        current = float(state.get("position_sec", 0.0))
        duration = max(0.0, float(state.get("duration_sec", 0.0)))
        target = max(0.0, min(duration, current + float(delta)))
        self.player.seek(target)
        self._sync_lyrics_with_position(target)
        self.statusBar().showMessage(f"播放进度：{_format_time(target)}", 1500)

    def _open_current_in_explorer(self) -> None:
        track = self.player.current_track()
        if track is None:
            self.statusBar().showMessage("当前没有可定位的歌曲文件", 3000)
            return
        source = Path(track.path).resolve()
        if not source.exists():
            self.statusBar().showMessage("歌曲文件不存在，无法定位", 3000)
            return
        try:
            # Keep /select and path as separate args to avoid parser issues with unicode/comma paths.
            subprocess.Popen(["explorer.exe", "/select,", str(source)])
            self.statusBar().showMessage("已在资源管理器定位文件", 2500)
        except Exception as exc:
            self.statusBar().showMessage(f"打开资源管理器失败: {exc}", 5000)

    def _on_speed_changed(self, index: int) -> None:
        rate = self.speed_combo.itemData(index)
        if rate is None:
            return
        self.player.set_playback_rate(float(rate))
        self.statusBar().showMessage(f"播放速度：{float(rate):.2g}x", 2000)

    def _on_playback_rate_changed(self, rate: float) -> None:
        self._sync_speed_combo(rate)

    def _sync_speed_combo(self, rate: float | None = None) -> None:
        target = self.player.playback_rate() if rate is None else float(rate)
        best_index = 0
        best_diff = 999.0
        for i in range(self.speed_combo.count()):
            item_rate = float(self.speed_combo.itemData(i))
            diff = abs(item_rate - target)
            if diff < best_diff:
                best_diff = diff
                best_index = i
        self.speed_combo.blockSignals(True)
        self.speed_combo.setCurrentIndex(best_index)
        self.speed_combo.blockSignals(False)

    def _on_playlist_combo_changed(self, index: int) -> None:
        playlist_id = self.playlist_combo.itemData(index)
        if not playlist_id:
            return
        self.player.set_playlist(str(playlist_id))
        self._reload_track_list()
        self.statusBar().showMessage(f"当前歌单：{self.playlist_combo.currentText()}", 2500)

    def _on_track_double_clicked(self, item: QListWidgetItem) -> None:
        try:
            track_id = item.data(0x0100)
            display_text = self._track_text_of_item(item)
        except RuntimeError:
            return
        if not track_id:
            return
        active_search = bool(self.search_edit.text().strip())
        self.player.play_track(str(track_id), auto_play=True, manual_select=True, active_request=active_search)
        self.statusBar().showMessage(f"播放歌曲：{display_text}", 2500)

    def _on_remove_track_clicked(self, track_id: str) -> None:
        playlist_id = self.player.current_playlist_id or "all_songs"
        try:
            self.controller.remove_track_from_playlist(str(playlist_id), str(track_id))
            self.statusBar().showMessage("已从歌单移除歌曲", 2500)
        except Exception as exc:
            QMessageBox.critical(self, "删除失败", str(exc))

    def _on_queue_changed(self) -> None:
        self._reload_playlist_combo()
        self._reload_track_list()

    def _on_library_changed(self) -> None:
        self._reload_playlist_combo()
        self._reload_track_list()
        self.statusBar().showMessage("曲库已更新", 2200)

    def _on_settings_changed(self, settings) -> None:
        self.player.set_playlist_loop_mode_enabled(bool(getattr(settings, "enable_playlist_loop_mode", False)))
        self._refresh_mode_order()
        self._on_mode_changed(self.player.mode.value)
        dark = bool(getattr(settings, "dark_theme", self._dark_theme))
        if dark != self._dark_theme:
            self._dark_theme = dark
            self._apply_theme_stylesheet()
            self._refresh_theme_button()

    def _on_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 7000)

    def _on_runtime_status_changed(self, listening: bool, host: str, port: int) -> None:
        if listening:
            self.statusBar().showMessage(f"控制接口监听中: {host}:{port}", 5000)
        else:
            self.statusBar().showMessage(f"控制接口已停止: {host}:{port}", 5000)

    def _menu_import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "导入音乐文件夹")
        if not folder:
            return
        self.statusBar().showMessage("开始导入，请稍候…", 4000)

        def _progress(done: int, total: int, current: str) -> None:
            if total <= 0:
                return
            name = Path(current).name if current else "完成"
            self.statusBar().showMessage(f"导入进度：{done}/{total}  {name}", 6000)
            QCoreApplication.processEvents()

        try:
            count = self.controller.import_folder(Path(folder), playlist_id=None, progress_callback=_progress)
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self.statusBar().showMessage(f"导入完成，共 {count} 首", 5000)
        self._reload_track_list()

    def _menu_open_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "播放文件",
            "",
            "音频文件 (*.mp3 *.flac *.wav *.m4a *.aac *.ogg *.opus *.wma)",
        )
        if not file_path:
            return
        ok = self.player.play_file(Path(file_path), active_request=True)
        if ok:
            self.statusBar().showMessage("已开始播放", 3000)
            self._reload_track_list()
        else:
            self.statusBar().showMessage("播放失败，请查看日志", 5000)

    def _open_playlist_dialog(self) -> None:
        self.statusBar().showMessage("打开歌单管理", 2000)
        dlg = PlaylistDialog(self.controller, self)
        dlg.exec()
        self._reload_playlist_combo()
        self._reload_track_list()
        self.statusBar().showMessage("歌单管理已更新", 2500)

    def _open_settings_dialog(self) -> None:
        self.statusBar().showMessage("打开设置", 1500)
        dlg = SettingsDialog(self.controller.settings, self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        new_settings = dlg.output_settings()
        self.controller.update_settings(new_settings)
        if new_settings.logging_enabled and self.controller.log_file_path is not None:
            tip = f"设置已保存，日志路径: {self.controller.log_file_path}"
        else:
            tip = "设置已保存"
        self.statusBar().showMessage(tip, 6000)

    def _toggle_compact_mode(self) -> None:
        self._compact_mode = not self._compact_mode

        if self._compact_mode:
            controls_pos_before = self.card_controls.mapToGlobal(QPoint(0, 0))
            self._width_before_compact = self.width()
            self._height_before_compact = self.height()
            self._min_width_before_compact = self.minimumWidth()
            self._max_width_before_compact = self.maximumWidth()
            self._min_height_before_compact = self.minimumHeight()
            self._max_height_before_compact = self.maximumHeight()
            self._sidebar_was_collapsed_before_compact = self._sidebar_collapsed

            sizes = self.main_splitter.sizes()
            if len(sizes) == 2:
                self._sidebar_last_width = max(self._sidebar_min_width, sizes[1])
                self.main_splitter.setSizes([sizes[0] + sizes[1], 0])

            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.controls_layout.setContentsMargins(*self._controls_compact_margins)
            self.controls_layout.setSpacing(4)
            self.card_now.hide()
            self.side_card.hide()
            self.main_splitter.hide()
            self.sidebar_toggle_btn.hide()
            if self._top_stack_widget is not None:
                self._top_stack_widget.hide()
            self.statusBar().hide()
            self.compact_info_widget.hide()
            self.progress_center_label.show()
            self.compact_top_bar.show()
            self.compact_top_title_label.setText(self._current_track_title)
            if 0 <= self._lyrics_current_index < len(self._lyrics_entries):
                self.progress_center_label.setText(self._lyrics_entries[self._lyrics_current_index][1] or "♪")
            elif self._lyrics_entries:
                self.progress_center_label.setText(self._lyrics_entries[0][1] or "♪")
            else:
                self.progress_center_label.setText("♪")
            self._refresh_window_flags()
            self._on_opacity_changed(self.opacity_slider.value())
            self._layout_compact_top_bar()

            self.compact_btn.setIcon(_make_plus_minus_icon(True, color=self._control_icon_color()))
            self.compact_btn.setToolTip("切换到丰富模式")

            self.centralWidget().layout().activate()
            self.controls_layout.activate()
            m = self.centralWidget().layout().contentsMargins()
            control_height = self.card_controls.sizeHint().height()
            control_width = self.card_controls.sizeHint().width()
            client_height = m.top() + control_height + m.bottom()
            client_width = m.left() + control_width + m.right()
            frame_height = self.frameGeometry().height() - self.geometry().height()
            frame_width = self.frameGeometry().width() - self.geometry().width()
            target_height = client_height + frame_height
            target_width = client_width + frame_width

            screen = self.windowHandle().screen() if self.windowHandle() else QGuiApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                target_width = min(max(1, target_width), avail.width())
                target_height = min(max(1, target_height), avail.height())

            self.resize(target_width, target_height)
            controls_pos_after = self.card_controls.mapToGlobal(QPoint(0, 0))
            self.move(
                self.x() + (controls_pos_before.x() - controls_pos_after.x()),
                self.y() + (controls_pos_before.y() - controls_pos_after.y()),
            )
            self._layout_compact_top_bar()
            self._ensure_window_inside_screen()
            self.statusBar().showMessage("已进入简洁模式", 2500)
            return

        rich_anchor = self.frameGeometry().topLeft()

        self.compact_info_widget.hide()
        self.progress_center_label.hide()
        self.compact_top_bar.hide()
        self.controls_layout.setContentsMargins(*self._controls_normal_margins)
        self.controls_layout.setSpacing(8)
        self.setWindowOpacity(1.0)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self._refresh_window_flags()

        self.card_now.show()
        self.side_card.show()
        self.main_splitter.show()
        self.sidebar_toggle_btn.show()
        if self._top_stack_widget is not None:
            self._top_stack_widget.show()
        self.statusBar().show()

        self.compact_btn.setIcon(_make_plus_minus_icon(False, color=self._control_icon_color()))
        self.compact_btn.setToolTip("切换到简洁模式")

        self.centralWidget().layout().activate()
        self.controls_layout.activate()
        min_hint = self.minimumSizeHint()
        min_w = max(int(min_hint.width()), max(0, int(self._min_width_before_compact)))
        min_h = max(int(min_hint.height()), max(0, int(self._min_height_before_compact)))
        self.setMinimumWidth(min_w)
        self.setMinimumHeight(min_h)
        self.setMaximumWidth(16777215)
        self.setMaximumHeight(16777215)
        restore_width = self._width_before_compact if self._width_before_compact > 0 else self.width()
        restore_height = self._height_before_compact if self._height_before_compact > 0 else self.height()
        screen = self.windowHandle().screen() if self.windowHandle() else QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            restore_width = min(max(restore_width, min_w), max(480, avail.width()))
            restore_height = min(max(restore_height, min_h), max(240, avail.height()))
        self.resize(restore_width, restore_height)
        self.move(rich_anchor)

        total = max(1, sum(self.main_splitter.sizes()))
        if self._sidebar_was_collapsed_before_compact:
            self.main_splitter.setSizes([total, 0])
            self._sidebar_collapsed = True
        else:
            target = self._clamp_sidebar_width(total, self._sidebar_last_width)
            self.main_splitter.setSizes([total - target, target])
            self._sidebar_collapsed = False

        self._update_sidebar_toggle_icon()
        self._reposition_sidebar_toggle()
        self._sync_current_track_row(center=True)
        self._update_locate_current_button()
        self._ensure_window_inside_screen()
        self.statusBar().showMessage("已退出简洁模式", 3000)

    def _exit_compact_mode(self) -> None:
        if not self._compact_mode:
            return
        self._toggle_compact_mode()

    def _toggle_rich_maximize(self) -> None:
        if self._compact_mode:
            return
        if self.isMaximized():
            self.showNormal()
            self._snap_docked = False
        elif self._snap_docked:
            self._restore_from_snap()
        else:
            self.showMaximized()
            self._snap_docked = False
        self._refresh_rich_title_icons()

    def _is_rich_restore_state(self) -> bool:
        return bool(self.isMaximized() or self._snap_docked)

    def _remember_geometry_before_snap(self) -> None:
        if self.isMaximized() or self._snap_docked:
            return
        self._geometry_before_snap = QRect(self.geometry())

    def _restore_from_snap(self) -> None:
        if not self._snap_docked:
            return
        geo = QRect(self._geometry_before_snap) if self._geometry_before_snap is not None else None
        self._snap_docked = False
        self._geometry_before_snap = None
        if geo is not None and geo.isValid():
            self.setGeometry(geo)
        self._ensure_window_inside_screen()

    def _refresh_rich_title_icons(self) -> None:
        if not hasattr(self, "rich_min_btn"):
            return
        color = self._control_icon_color()
        self.rich_min_btn.setIcon(_make_rich_title_icon("min", color=color))
        self.rich_max_btn.setIcon(_make_rich_title_icon("restore" if self._is_rich_restore_state() else "max", color=color))
        self.rich_close_btn.setIcon(_make_rich_title_icon("close", color=color))

    def _refresh_window_flags(self) -> None:
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._always_on_top)
        if was_visible:
            self.show()
            if self._always_on_top:
                self.raise_()

    def _refresh_compact_top_buttons(self) -> None:
        color = self._control_icon_color()
        self.lock_btn.setIcon(_make_lock_icon(self._compact_locked, color=color))
        self.lock_btn.setToolTip("已锁定窗口位置" if self._compact_locked else "锁定窗口位置")
        self.pin_btn.setIcon(_make_pin_icon(self._always_on_top, color=color))
        self.pin_btn.setToolTip("取消置顶" if self._always_on_top else "置顶窗口")
        self.compact_close_btn.setIcon(_make_rich_title_icon("close", color=color))

    def _layout_compact_top_bar(self) -> None:
        if not hasattr(self, "compact_top_bar"):
            return
        if not self.compact_top_bar.isVisible():
            return
        width = max(220, self.compact_top_bar.width())
        height = max(self.compact_top_bar.minimumHeight(), self.compact_top_bar.height())
        title_h = self.compact_top_title_label.sizeHint().height()
        right_controls_width = (
            self.lock_btn.width() + self.pin_btn.width() + self.compact_close_btn.width() + 12
        )
        left_controls_width = self.opacity_slider.width() + 12
        max_title_width = max(80, width - left_controls_width - right_controls_width - 16)
        title_w = min(max_title_width, self.compact_top_title_label.sizeHint().width())
        self.compact_top_title_label.resize(title_w, title_h)
        self.compact_top_title_label.move((width - title_w) // 2, (height - title_h) // 2)
        self.compact_top_title_label.raise_()

    def _reposition_volume_value_label(self) -> None:
        if not hasattr(self, "mute_btn") or not hasattr(self, "volume_value_label"):
            return
        if not self.volume_panel.isVisible():
            self.volume_value_label.hide()
            return
        self.volume_value_label.show()
        panel_center = self.volume_panel.mapTo(self.card_controls, self.volume_panel.rect().center())
        panel_bottom = self.volume_panel.mapTo(self.card_controls, self.volume_panel.rect().bottomLeft()).y()
        x = panel_center.x() - (self.volume_value_label.width() // 2)
        y = panel_bottom + 1
        x = max(0, min(x, self.card_controls.width() - self.volume_value_label.width()))
        y = max(0, min(y, self.card_controls.height() - self.volume_value_label.height()))
        self.volume_value_label.move(x, y)
        self.volume_value_label.raise_()

    def _ensure_window_inside_screen(self) -> None:
        handle = self.windowHandle()
        screen = handle.screen() if handle is not None else QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        geo = self.frameGeometry()

        target_x = geo.x()
        target_y = geo.y()

        if geo.width() > available.width():
            target_x = available.x()
        else:
            target_x = max(available.left(), min(target_x, available.right() - geo.width() + 1))

        if geo.height() > available.height():
            target_y = available.y()
        else:
            target_y = max(available.top(), min(target_y, available.bottom() - geo.height() + 1))

        if target_x != geo.x() or target_y != geo.y():
            self.move(target_x, target_y)

    def _restore_window_geometry(self) -> None:
        settings = self.controller.settings
        if not bool(getattr(settings, "remember_window_geometry", True)):
            return
        width = max(0, int(getattr(settings, "window_width", 0)))
        height = max(0, int(getattr(settings, "window_height", 0)))
        x = int(getattr(settings, "window_x", -1))
        y = int(getattr(settings, "window_y", -1))

        if width > 0 and height > 0:
            self.resize(width, height)
        if x >= 0 and y >= 0:
            self.move(x, y)
        self._ensure_window_inside_screen()

    def _persist_window_geometry(self) -> None:
        settings = self.controller.settings
        if not bool(getattr(settings, "remember_window_geometry", True)):
            return
        geo = self.frameGeometry()
        self.controller.persist_window_geometry(
            x=int(geo.x()),
            y=int(geo.y()),
            width=int(geo.width()),
            height=int(geo.height()),
        )

    def _ensure_taskbar_progress_initialized(self) -> None:
        hwnd = int(self.winId()) if sys.platform.startswith("win") else 0
        if hwnd <= 0:
            return
        self._taskbar_progress.attach(hwnd)

    def _update_taskbar_progress(self, position: float, duration: float) -> None:
        if not sys.platform.startswith("win"):
            return
        self._ensure_taskbar_progress_initialized()
        self._taskbar_progress.set_progress(position, duration)

    def _toggle_sidebar(self) -> None:
        if self._compact_mode:
            return

        sizes = self.main_splitter.sizes()
        if len(sizes) != 2:
            return
        total = max(1, sizes[0] + sizes[1])

        if not self._sidebar_collapsed and sizes[1] > 0:
            self._sidebar_last_width = max(self._sidebar_min_width, sizes[1])
            self.main_splitter.setSizes([total, 0])
            self._sidebar_collapsed = True
        else:
            target = self._clamp_sidebar_width(total, self._sidebar_last_width)
            self.main_splitter.setSizes([total - target, target])
            self._sidebar_collapsed = False

        self._update_sidebar_toggle_icon()
        self._reposition_sidebar_toggle()
        self.statusBar().showMessage("已收起快捷侧边栏" if self._sidebar_collapsed else "已展开快捷侧边栏", 1800)

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        sizes = self.main_splitter.sizes()
        if len(sizes) == 2:
            if sizes[1] <= 1:
                self._sidebar_collapsed = True
            else:
                self._sidebar_collapsed = False
                self._sidebar_last_width = max(self._sidebar_min_width, sizes[1])
        self._update_sidebar_toggle_icon()
        self._reposition_sidebar_toggle()

    def _update_sidebar_toggle_icon(self) -> None:
        self.sidebar_toggle_btn.setIcon(
            _make_sidebar_toggle_icon(collapsed=self._sidebar_collapsed, color=self._control_icon_color())
        )

    def _reposition_sidebar_toggle(self) -> None:
        if not hasattr(self, "sidebar_toggle_btn"):
            return
        if not self.sidebar_toggle_btn.isVisible():
            return

        geo = self.main_splitter.geometry()
        sizes = self.main_splitter.sizes()
        if len(sizes) != 2:
            return

        split_x = geo.x() + sizes[0]
        x = split_x - self.sidebar_toggle_btn.width() // 2
        x = min(x, geo.right() - self.sidebar_toggle_btn.width())
        x = max(geo.x(), x)

        y = geo.y() + (geo.height() - self.sidebar_toggle_btn.height()) // 2
        self.sidebar_toggle_btn.move(x, y)
        self.sidebar_toggle_btn.raise_()

    def _clamp_sidebar_width(self, total_width: int, preferred: int) -> int:
        total = max(1, int(total_width))
        hard_max = max(self._sidebar_min_width, total - 252)
        upper = min(self._sidebar_max_width, hard_max)
        return max(self._sidebar_min_width, min(int(preferred), upper))

    def _prefer_resize_to_sidebar(
        self,
        delta_width: int,
        *,
        old_sidebar_width: int | None = None,
        total_width: int | None = None,
    ) -> None:
        if delta_width == 0 or self._compact_mode or self._sidebar_collapsed:
            return
        sizes = self.main_splitter.sizes()
        if len(sizes) != 2:
            return
        total = max(1, int(total_width) if total_width is not None else sizes[0] + sizes[1])
        base_side = self._sidebar_last_width if old_sidebar_width is None else int(old_sidebar_width)
        previous_side = max(self._sidebar_min_width, base_side)
        target_side = self._clamp_sidebar_width(total, previous_side + int(delta_width))
        if target_side == sizes[1]:
            return
        self.main_splitter.setSizes([max(0, total - target_side), target_side])
        self._sidebar_last_width = target_side

    def _hit_test_resize_edges(self, pos: QPoint):
        if self._compact_mode or self.isMaximized():
            return None
        margin = max(4, int(self._resize_margin))
        x = int(pos.x())
        y = int(pos.y())
        w = max(1, self.width())
        h = max(1, self.height())
        if x < 0 or y < 0 or x > w or y > h:
            return None

        edges = Qt.Edge(0)
        if x <= margin:
            edges |= Qt.Edge.LeftEdge
        elif x >= w - margin:
            edges |= Qt.Edge.RightEdge
        if y <= margin:
            edges |= Qt.Edge.TopEdge
        elif y >= h - margin:
            edges |= Qt.Edge.BottomEdge
        return edges if edges else None

    @staticmethod
    def _cursor_for_resize_edges(edges) -> Qt.CursorShape:
        if edges is None:
            return Qt.CursorShape.ArrowCursor
        has_left = bool(edges & Qt.Edge.LeftEdge)
        has_right = bool(edges & Qt.Edge.RightEdge)
        has_top = bool(edges & Qt.Edge.TopEdge)
        has_bottom = bool(edges & Qt.Edge.BottomEdge)
        if (has_left and has_top) or (has_right and has_bottom):
            return Qt.CursorShape.SizeFDiagCursor
        if (has_right and has_top) or (has_left and has_bottom):
            return Qt.CursorShape.SizeBDiagCursor
        if has_left or has_right:
            return Qt.CursorShape.SizeHorCursor
        if has_top or has_bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _screen_for_global_pos(self, global_pos: QPoint):
        screen = QGuiApplication.screenAt(global_pos)
        if screen is not None:
            return screen
        handle = self.windowHandle()
        return handle.screen() if handle is not None else QGuiApplication.primaryScreen()

    def _apply_titlebar_snap(self, global_pos: QPoint) -> None:
        if self._compact_mode:
            return
        screen = self._screen_for_global_pos(global_pos)
        if screen is None:
            return
        avail = screen.availableGeometry()
        threshold = 10
        if global_pos.y() <= avail.top() + threshold:
            self.showMaximized()
            self._snap_docked = False
            self._refresh_rich_title_icons()
            return
        if global_pos.x() <= avail.left() + threshold:
            self._remember_geometry_before_snap()
            self.setGeometry(avail.left(), avail.top(), avail.width() // 2, avail.height())
            self._snap_docked = True
            self._refresh_rich_title_icons()
            return
        if global_pos.x() >= avail.right() - threshold:
            self._remember_geometry_before_snap()
            self.setGeometry(avail.left() + avail.width() // 2, avail.top(), avail.width() - avail.width() // 2, avail.height())
            self._snap_docked = True
            self._refresh_rich_title_icons()

    def nativeEvent(self, eventType, message):
        if not sys.platform.startswith("win"):
            return super().nativeEvent(eventType, message)
        if self._compact_mode or self.isMaximized():
            return super().nativeEvent(eventType, message)

        if str(eventType) not in {"windows_generic_MSG", "windows_dispatcher_MSG"}:
            return super().nativeEvent(eventType, message)
        try:
            msg_ptr = int(message)
            msg = ctypes.wintypes.MSG.from_address(msg_ptr)  # type: ignore[attr-defined]
        except Exception:
            return super().nativeEvent(eventType, message)
        if int(msg.message) != WM_NCHITTEST:
            return super().nativeEvent(eventType, message)

        local = self.mapFromGlobal(QCursor.pos())
        w = self.width()
        h = self.height()
        if local.x() < 0 or local.y() < 0 or local.x() >= w or local.y() >= h:
            return super().nativeEvent(eventType, message)

        margin = max(4, int(self._resize_margin))
        left = local.x() <= margin
        right = local.x() >= (w - margin - 1)
        top = local.y() <= margin
        bottom = local.y() >= (h - margin - 1)

        if top and left:
            return True, HTTOPLEFT
        if top and right:
            return True, HTTOPRIGHT
        if bottom and left:
            return True, HTBOTTOMLEFT
        if bottom and right:
            return True, HTBOTTOMRIGHT
        if left:
            return True, HTLEFT
        if right:
            return True, HTRIGHT
        if top:
            return True, HTTOP
        if bottom:
            return True, HTBOTTOM
        return super().nativeEvent(eventType, message)

    def eventFilter(self, watched, event):
        track_view = self.track_list.viewport() if hasattr(self, "track_list") else None
        lyric_view = self.lyrics_list.viewport() if hasattr(self, "lyrics_list") else None
        rich_title_targets = {getattr(self, "rich_title_bar", None), getattr(self, "rich_title_label", None)}

        if watched in rich_title_targets and watched is not None:
            if self._compact_mode:
                return False
            if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._toggle_rich_maximize()
                return True
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                gpos = event.globalPosition().toPoint()
                if self.isMaximized() or self._snap_docked:
                    frame = self.frameGeometry()
                    if frame.width() > 0:
                        ratio = (gpos.x() - frame.x()) / frame.width()
                        self._rich_drag_restore_ratio = max(0.0, min(1.0, float(ratio)))
                    if self.isMaximized():
                        self.showNormal()
                    else:
                        self._restore_from_snap()
                    self._snap_docked = False
                    self._refresh_rich_title_icons()
                    frame = self.frameGeometry()
                    offset_x = max(0, min(frame.width() - 1, int(round(frame.width() * self._rich_drag_restore_ratio))))
                    offset_y = max(1, min(self.rich_title_bar.height() - 1, self.rich_title_bar.height() // 2))
                    self._rich_drag_offset = QPoint(offset_x, offset_y)
                    self.move(gpos - self._rich_drag_offset)
                else:
                    self._rich_drag_offset = gpos - self.frameGeometry().topLeft()
                return True

        if track_view is not None and watched is track_view:
            if event.type() == QEvent.Type.MouseMove:
                idx = self.track_list.indexAt(event.pos())
                self.track_delegate.set_hover_row(idx.row() if idx.isValid() else -1)
                self.track_list.viewport().update()
            elif event.type() == QEvent.Type.Leave:
                self.track_delegate.set_hover_row(-1)
                self.track_list.viewport().update()
            elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                idx = self.track_list.indexAt(event.pos())
                if idx.isValid():
                    row_rect = self.track_list.visualRect(idx)
                    if TrackItemDelegate.remove_rect(row_rect).contains(event.pos()):
                        item = self.track_list.item(idx.row())
                        if item is not None:
                            track_id = item.data(0x0100)
                            if track_id:
                                self._on_remove_track_clicked(str(track_id))
                                return True
        elif lyric_view is not None and watched is lyric_view:
            if event.type() == QEvent.Type.MouseMove:
                idx = self.lyrics_list.indexAt(event.pos())
                self.lyrics_delegate.set_hover_row(idx.row() if idx.isValid() else -1)
                self.lyrics_list.viewport().update()
            elif event.type() == QEvent.Type.Leave:
                self.lyrics_delegate.set_hover_row(-1)
                self.lyrics_list.viewport().update()
        return super().eventFilter(watched, event)

    def _adjust_volume_from_wheel_delta(self, delta: int) -> None:
        if delta == 0:
            return
        increase = delta > 0
        self.player.adjust_gain_by_key(increase)
        self._refresh_volume_ui()
        self.statusBar().showMessage(f"音量：{self.player.gain_percent()}%", 1200)

    def _is_inside_playback_controls(self, pos: QPoint) -> bool:
        child = self.childAt(pos)
        while child is not None and child is not self:
            if child is self.card_controls:
                return True
            child = child.parentWidget()
        return False

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        if self._is_inside_playback_controls(event.position().toPoint()):
            self._adjust_volume_from_wheel_delta(delta)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self._compact_mode:
            edges = self._hit_test_resize_edges(event.position().toPoint())
            if edges is not None:
                handle = self.windowHandle()
                if handle is not None and handle.startSystemResize(edges):
                    event.accept()
                    return
        if (
            self._compact_mode
            and not self._compact_locked
            and event.button() == Qt.MouseButton.LeftButton
            and not self._is_interactive_widget_at(event.position().toPoint())
        ):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._rich_drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._rich_drag_offset)
            event.accept()
            return
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self.setCursor(self._cursor_for_resize_edges(self._hit_test_resize_edges(event.position().toPoint())))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._rich_drag_offset is not None and event.button() == Qt.MouseButton.LeftButton:
            self._apply_titlebar_snap(event.globalPosition().toPoint())
            self._rich_drag_offset = None
            event.accept()
            return
        if self._drag_offset is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(self._cursor_for_resize_edges(self._hit_test_resize_edges(event.position().toPoint())))
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if not mime.hasUrls():
            event.ignore()
            return
        for url in mime.urls():
            if url.isLocalFile():
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if not mime.hasUrls():
            event.ignore()
            return
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            ok = self.player.play_file(Path(url.toLocalFile()), active_request=True)
            if ok:
                self.statusBar().showMessage("已播放拖入文件", 3000)
            event.acceptProposedAction()
            return
        event.ignore()

    def _is_interactive_widget_at(self, pos: QPoint) -> bool:
        child = self.childAt(pos)
        interactive_types = (QToolButton, QSlider, QComboBox, QLineEdit, QListWidget)
        while child is not None and child is not self:
            if isinstance(child, interactive_types):
                return True
            child = child.parentWidget()
        return False

    def resizeEvent(self, event) -> None:
        old_sizes = self.main_splitter.sizes() if hasattr(self, "main_splitter") else []
        old_total = old_sizes[0] + old_sizes[1] if len(old_sizes) == 2 else 0
        old_sidebar_width = old_sizes[1] if len(old_sizes) == 2 else None
        super().resizeEvent(event)
        new_sizes = self.main_splitter.sizes() if hasattr(self, "main_splitter") else []
        new_total = new_sizes[0] + new_sizes[1] if len(new_sizes) == 2 else 0
        if old_total > 0 and new_total > 0:
            delta_width = int(new_total - old_total)
        else:
            delta_width = int(event.size().width() - event.oldSize().width())
        if not self._resize_adjusting_splitter:
            self._resize_adjusting_splitter = True
            try:
                self._prefer_resize_to_sidebar(
                    delta_width,
                    old_sidebar_width=old_sidebar_width,
                    total_width=new_total if new_total > 0 else None,
                )
            finally:
                self._resize_adjusting_splitter = False
        self._reposition_sidebar_toggle()
        self._layout_compact_top_bar()
        self._reposition_volume_value_label()
        self._update_track_item_heights()
        self._position_locate_current_button()
        self._update_locate_current_button()
        self._last_window_width = self.width()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._ensure_taskbar_progress_initialized)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "rich_max_btn"):
            if self.isMaximized():
                self._snap_docked = False
            self._refresh_rich_title_icons()

    def _update_track_item_heights(self) -> None:
        for row in range(self.track_list.count()):
            item = self.track_list.item(row)
            if item is None:
                continue
            item.setSizeHint(QSize(0, self._track_item_height_for_text(item.text() or "")))

    def _position_locate_current_button(self) -> None:
        if not hasattr(self, "locate_current_btn"):
            return
        vp = self.track_list.viewport()
        x = max(2, vp.width() - self.locate_current_btn.width() - 4)
        y = max(2, vp.height() - self.locate_current_btn.height() - 4)
        self.locate_current_btn.move(x, y)
        self.locate_current_btn.raise_()

    def _find_current_track_row(self) -> int:
        current_id = self.player.current_track_id
        if not current_id:
            return -1
        for row in range(self.track_list.count()):
            item = self.track_list.item(row)
            if item is not None and item.data(0x0100) == current_id:
                return row
        return -1

    def _is_track_row_visible(self, row: int) -> bool:
        if row < 0:
            return False
        item = self.track_list.item(row)
        if item is None:
            return False
        rect = self.track_list.visualItemRect(item)
        if not rect.isValid():
            return False
        vp = self.track_list.viewport().rect()
        return rect.top() >= vp.top() and rect.bottom() <= vp.bottom()

    def _update_locate_current_button(self) -> None:
        if not hasattr(self, "locate_current_btn"):
            return
        row = self._find_current_track_row()
        should_show = row >= 0 and not self._is_track_row_visible(row)
        self.locate_current_btn.setVisible(bool(should_show))
        self._position_locate_current_button()

    def _locate_current_track_in_list(self) -> None:
        row = self._find_current_track_row()
        if row < 0:
            return
        self.track_list.setCurrentRow(row)
        item = self.track_list.item(row)
        if item is not None:
            self.track_list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
        self._update_locate_current_button()

    def minimumSizeHint(self) -> QSize:
        if self._compact_mode:
            return QSize(0, 0)
        return super().minimumSizeHint()

    def _lyric_text_of_item(self, item: QListWidgetItem) -> str:
        return (item.text() or "").strip()

    def _track_text_of_item(self, item: QListWidgetItem) -> str:
        text = (item.text() or "").strip()
        return text or "未知歌曲"

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            self._persist_window_geometry()
            self._taskbar_progress.clear()
            self._taskbar_progress.close()
            self.controller.shutdown()
        finally:
            super().closeEvent(event)


class _WindowsTaskbarProgress:
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
