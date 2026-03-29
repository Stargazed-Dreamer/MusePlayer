from __future__ import annotations

import html
import re
import subprocess
from bisect import bisect_right
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QRectF, QSize, Signal, QPoint
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
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
    QSlider,
    QSplitter,
    QStyle,
    QStyleOptionSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.models.entities import Track
from app.services.app_controller import AppController
from app.services.player_service import PlayMode
from app.ui.playlist_dialog import PlaylistDialog
from app.ui.settings_dialog import SettingsDialog

_LRC_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


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
        self._start_sec = float(start_sec)
        self._end_sec = float(end_sec)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(6)

        self.start_label = QLabel(_format_lrc_time(self._start_sec), self)
        self.start_label.setObjectName("LyricTimeLabel")
        self.start_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.text_label = QLabel(text or "♪", self)
        self.text_label.setObjectName("LyricTextLabel")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        self.end_label = QLabel(_format_lrc_time(self._end_sec), self)
        self.end_label.setObjectName("LyricTimeLabel")
        self.end_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

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


class ClickJumpSlider(QSlider):
    def __init__(self, orientation: Qt.Orientation, parent=None):
        super().__init__(orientation, parent)
        self._mouse_pressed = False

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


class TrackListItemWidget(QWidget):
    remove_clicked = Signal(str)

    def __init__(self, track_id: str, text: str, parent=None):
        super().__init__(parent)
        self._track_id = str(track_id)

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 2, 0)
        row.setSpacing(8)

        self.remove_slot = QWidget(self)
        self.remove_slot.setFixedWidth(20)
        remove_slot_layout = QVBoxLayout(self.remove_slot)
        remove_slot_layout.setContentsMargins(0, 0, 0, 0)
        remove_slot_layout.setSpacing(0)

        self.remove_btn = QToolButton(self.remove_slot)
        self.remove_btn.setObjectName("TrackDeleteButton")
        self.remove_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical))
        self.remove_btn.setToolTip("从当前歌单移除")
        self.remove_btn.setAutoRaise(True)
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


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController):
        super().__init__()
        self.controller = controller
        self.player = controller.player_service

        self._dragging_progress = False
        self._compact_mode = False
        self._compact_locked = False
        self._always_on_top = False
        self._drag_offset: QPoint | None = None
        self._sidebar_collapsed = False
        self._sidebar_was_collapsed_before_compact = False
        self._sidebar_last_width = 240
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
        self._last_nonzero_gain = max(1, int(self.player.gain_percent()))
        self._current_track_title = "未选择歌曲"
        self._lyrics_resume_timer = QTimer(self)
        self._lyrics_resume_timer.setSingleShot(True)
        self._lyrics_resume_timer.setInterval(2200)
        self._lyrics_resume_timer.timeout.connect(self._resume_lyrics_auto_scroll)

        self.setWindowTitle("MusePlayer")
        self.resize(1280, 780)
        self.setAcceptDrops(True)

        self._build_ui()
        self._build_menu()
        self._bind_signals()
        self._bind_shortcuts()

        self._reload_playlist_combo()
        self._reload_track_list()
        self._refresh_current_track_ui(self.player.current_track())
        self._refresh_mode_order()
        self._on_mode_changed(self.player.mode.value)
        self._on_playback_changed(self.player.is_playing())
        self._refresh_volume_ui()

        QTimer.singleShot(0, self._reposition_sidebar_toggle)

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(0)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, root)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(6)
        main_layout.addWidget(self.main_splitter)

        left_container = QWidget(self.main_splitter)
        left_col = QVBoxLayout(left_container)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(12)

        self.card_now = QFrame(left_container)
        self.card_now.setObjectName("Card")
        now_layout = QHBoxLayout(self.card_now)
        now_layout.setContentsMargins(16, 16, 16, 16)
        now_layout.setSpacing(16)

        self.cover_label = QLabel("暂无封面")
        self.cover_label.setFixedSize(230, 230)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet(
            "background:#d9e6f5; border-radius:12px; color:#406180; font-weight:600;"
        )
        now_layout.addWidget(self.cover_label)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(6)
        self.title_label = QLabel("未选择歌曲")
        self.title_label.setObjectName("TitleLabel")
        self.artist_label = QLabel("歌手")
        self.artist_label.setObjectName("MetaLabel")
        self.album_label = QLabel("专辑")
        self.album_label.setObjectName("MetaLabel")
        self.path_label = QLabel("")
        self.path_label.setObjectName("CaptionLabel")
        self.path_label.setWordWrap(True)

        self.lyrics_list = LyricsListWidget()
        self.lyrics_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.lyrics_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.lyrics_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lyrics_list.setSpacing(2)

        meta_col.addWidget(self.title_label)
        meta_col.addWidget(self.artist_label)
        meta_col.addWidget(self.album_label)
        meta_col.addWidget(self.path_label)
        meta_col.addWidget(self.lyrics_list, 1)
        now_layout.addLayout(meta_col, 1)

        left_col.addWidget(self.card_now, 1)

        self.card_controls = QFrame(left_container)
        self.card_controls.setObjectName("Card")
        self._controls_normal_margins = (14, 12, 14, 12)
        self._controls_compact_margins = (14, 58, 14, 12)
        self.controls_layout = QVBoxLayout(self.card_controls)
        self.controls_layout.setContentsMargins(*self._controls_normal_margins)
        self.controls_layout.setSpacing(8)

        self.compact_info_widget = QWidget(self.card_controls)
        compact_info_layout = QVBoxLayout(self.compact_info_widget)
        compact_info_layout.setContentsMargins(0, 0, 0, 0)
        compact_info_layout.setSpacing(2)
        self.compact_song_label = QLabel("♪")
        self.compact_song_label.setObjectName("CompactLyricLineLabel")
        self.compact_song_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        compact_info_layout.addWidget(self.compact_song_label)
        self.compact_info_widget.hide()

        self.progress_slider = ClickJumpSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.sliderPressed.connect(self._on_progress_pressed)
        self.progress_slider.sliderReleased.connect(self._on_progress_released)

        time_row = QHBoxLayout()
        self.current_time_label = QLabel("00:00")
        self.current_time_label.setObjectName("CaptionLabel")
        self.progress_center_label = QLabel("")
        self.progress_center_label.setObjectName("CompactTitleLabel")
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

        self.locate_file_btn = self._new_icon_button("ControlIconButton")
        self.locate_file_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.locate_file_btn.setToolTip("在资源管理器中定位当前文件")

        self.mode_btn = self._new_icon_button("ModeButton")

        self.prev_btn = self._new_icon_button("ControlIconButton")
        self.prev_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipBackward))
        self.prev_btn.setToolTip("上一首")

        self.play_btn = self._new_icon_button("ControlIconButton")
        self.play_btn.setToolTip("播放 / 暂停")

        self.next_btn = self._new_icon_button("ControlIconButton")
        self.next_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
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

        self.volume_slider = ClickJumpSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.player.slider_gain_percent())
        self.volume_slider.setMinimumWidth(150)

        self.compact_btn = self._new_icon_button("CompactButton")
        self.compact_btn.setIcon(_make_plus_minus_icon(False))
        self.compact_btn.setToolTip("切换到简洁模式")

        self.speed_combo = QComboBox()
        self.speed_combo.setMinimumWidth(88)
        for rate in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            self.speed_combo.addItem(f"{rate:.2g}x", rate)
        self._sync_speed_combo()

        control_row.addWidget(self.locate_file_btn)
        control_row.addWidget(self.mode_btn)
        control_row.addWidget(self.prev_btn)
        control_row.addWidget(self.play_btn)
        control_row.addWidget(self.next_btn)
        control_row.addSpacing(6)
        control_row.addWidget(self.volume_panel)
        control_row.addWidget(self.volume_slider, 1)
        control_row.addWidget(self.compact_btn)
        control_row.addWidget(self.speed_combo)

        self.controls_layout.addLayout(control_row)
        left_col.addWidget(self.card_controls, 0)

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

        side_layout.addWidget(side_title)
        side_layout.addWidget(self.playlist_combo)
        side_layout.addWidget(self.search_edit)
        side_layout.addWidget(self.track_list, 1)

        self.main_splitter.setSizes([980, self._sidebar_last_width])
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)

        self.sidebar_toggle_btn = QToolButton(root)
        self.sidebar_toggle_btn.setObjectName("SidebarToggle")
        self.sidebar_toggle_btn.setToolTip("收起 / 展开快捷侧边栏")
        self.sidebar_toggle_btn.clicked.connect(self._toggle_sidebar)
        self._update_sidebar_toggle_icon()

        self.compact_top_bar = QWidget(self)
        self.compact_top_bar.setObjectName("CompactTopBar")
        compact_bar_layout = QHBoxLayout(self.compact_top_bar)
        compact_bar_layout.setContentsMargins(8, 8, 8, 8)
        compact_bar_layout.setSpacing(8)

        self.opacity_slider = ClickJumpSlider(Qt.Orientation.Horizontal, self.compact_top_bar)
        self.opacity_slider.setRange(35, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setFixedWidth(84)
        self.opacity_slider.setToolTip("调整窗口透明度")

        self.lock_btn = self._new_icon_button("CompactTopButton")
        self.pin_btn = self._new_icon_button("CompactTopButton")
        self._compact_top_right_spacer = QWidget(self.compact_top_bar)
        self._compact_top_right_spacer.setFixedWidth(self.opacity_slider.width())
        compact_bar_layout.addWidget(self.opacity_slider, 0, Qt.AlignmentFlag.AlignLeft)
        compact_bar_layout.addStretch(1)
        compact_bar_layout.addWidget(self.lock_btn)
        compact_bar_layout.addWidget(self.pin_btn)
        compact_bar_layout.addStretch(1)
        compact_bar_layout.addWidget(self._compact_top_right_spacer, 0, Qt.AlignmentFlag.AlignRight)
        self.compact_top_bar.setMinimumHeight(42)
        self.compact_top_bar.hide()
        self._refresh_compact_top_buttons()

        self.statusBar().showMessage("就绪")
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

        menu_playlist = self.menuBar().addMenu("歌单")
        action_manage_playlist = menu_playlist.addAction("歌单管理")

        action_settings = self.menuBar().addAction("设置")

        for menu in (menu_file, menu_playlist):
            menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            menu.setMouseTracking(True)

        action_import_folder.triggered.connect(self._menu_import_folder)
        action_open_file.triggered.connect(self._menu_open_file)
        action_exit.triggered.connect(self.close)
        action_manage_playlist.triggered.connect(self._open_playlist_dialog)
        action_settings.triggered.connect(self._open_settings_dialog)

    def _bind_signals(self) -> None:
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

        self.player.track_changed.connect(self._refresh_current_track_ui)
        self.player.progress_changed.connect(self._on_progress_changed)
        self.player.playback_changed.connect(self._on_playback_changed)
        self.player.mode_changed.connect(self._on_mode_changed)
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

        self.track_list.clear()
        current_id = self.player.current_track_id
        row_to_select = -1

        for idx, track in enumerate(tracks):
            text = f"{track.title}  -  {track.artist}"
            item = QListWidgetItem(text)
            item.setData(0x0100, track.id)
            item.setSizeHint(QSize(280, 34))
            self.track_list.addItem(item)
            widget = TrackListItemWidget(track.id, text, self.track_list)
            widget.remove_clicked.connect(self._on_remove_track_clicked)
            self.track_list.setItemWidget(item, widget)
            if track.id == current_id:
                row_to_select = idx

        if row_to_select >= 0:
            self.track_list.setCurrentRow(row_to_select)

    def _refresh_current_track_ui(self, track: Track | None) -> None:
        if track is None:
            self.title_label.setText("未选择歌曲")
            self.artist_label.setText("歌手")
            self.album_label.setText("专辑")
            self.path_label.setText("")
            self._current_track_title = "未选择歌曲"
            self.compact_song_label.setText("♪")
            self.progress_center_label.setText(self._current_track_title if self._compact_mode else "")
            self._set_cover(None)
            self._load_lyrics("")
            return

        self._current_track_title = track.title or "未知标题"
        self.title_label.setText(self._current_track_title)
        self.artist_label.setText(f"歌手: {track.artist or '未知歌手'}")
        self.album_label.setText(f"专辑: {track.album or '未知专辑'}")
        self.path_label.setText(track.path)
        self.progress_center_label.setText(self._current_track_title if self._compact_mode else "")

        lyrics = self.controller.get_current_lyrics()
        self._load_lyrics(lyrics)
        self._set_cover(self.controller.get_current_cover())

        self._reload_track_list()
        self.statusBar().showMessage(f"播放歌曲：{self._current_track_title}", 3000)

    def _set_cover(self, cover_data: bytes | None) -> None:
        if not cover_data:
            self.cover_label.hide()
            return

        pixmap = QPixmap()
        ok = pixmap.loadFromData(cover_data)
        if not ok:
            self.cover_label.hide()
            return

        scaled = pixmap.scaled(
            self.cover_label.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
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

        if entries:
            for idx, (_, text) in enumerate(entries):
                item = QListWidgetItem(text or "♪")
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                item.setSizeHint(QSize(300, 28))
                self.lyrics_list.addItem(item)
                widget = LyricLineWidget(
                    text=text,
                    start_sec=self._lyrics_times[idx],
                    end_sec=self._lyrics_end_times[idx],
                    parent=self.lyrics_list,
                )
                self.lyrics_list.setItemWidget(item, widget)
            self._sync_lyrics_with_position(0.0)
            return

        lines = [html.unescape(x.strip()) for x in clean.split("\n") if x.strip()]
        if not lines:
            lines = ["(暂无歌词)"]

        for line in lines:
            item = QListWidgetItem(line)
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.lyrics_list.addItem(item)
        if self._compact_mode:
            self.compact_song_label.setText(lines[0] if lines else "(暂无歌词)")

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
            self.compact_song_label.setText(self._lyrics_entries[idx][1] or "♪")
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
        text = (item.text() or "").strip()
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
        icon = self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay
        )
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
        icon = self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaVolumeMuted if muted else QStyle.StandardPixmap.SP_MediaVolume
        )
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
            subprocess.Popen(["explorer", f"/select,{source}"])
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
        track_id = item.data(0x0100)
        if not track_id:
            return
        active_search = bool(self.search_edit.text().strip())
        self.player.play_track(str(track_id), auto_play=True, manual_select=True, active_request=active_search)
        self.statusBar().showMessage(f"播放歌曲：{item.text()}", 2500)

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
        try:
            count = self.controller.import_folder(Path(folder), playlist_id=None)
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
                self._sidebar_last_width = max(180, sizes[1])
                self.main_splitter.setSizes([sizes[0] + sizes[1], 0])

            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.controls_layout.setContentsMargins(*self._controls_compact_margins)
            self.card_now.hide()
            self.side_card.hide()
            self.sidebar_toggle_btn.hide()
            self.menuBar().hide()
            self.statusBar().hide()
            self.compact_info_widget.show()
            self.progress_center_label.show()
            self.compact_top_bar.show()
            self.progress_center_label.setText(self._current_track_title)
            if 0 <= self._lyrics_current_index < len(self._lyrics_entries):
                self.compact_song_label.setText(self._lyrics_entries[self._lyrics_current_index][1] or "♪")
            elif self._lyrics_entries:
                self.compact_song_label.setText(self._lyrics_entries[0][1] or "♪")
            else:
                self.compact_song_label.setText("♪")
            self._refresh_window_flags()
            self._on_opacity_changed(self.opacity_slider.value())
            self._layout_compact_top_bar()

            self.compact_btn.setIcon(_make_plus_minus_icon(True))
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
            target_height = max(220, client_height + frame_height)
            target_width = max(460, client_width + frame_width)

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

        controls_pos_before = self.card_controls.mapToGlobal(QPoint(0, 0))

        self.compact_info_widget.hide()
        self.progress_center_label.hide()
        self.compact_top_bar.hide()
        self.controls_layout.setContentsMargins(*self._controls_normal_margins)
        self.setWindowOpacity(1.0)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self._refresh_window_flags()

        self.card_now.show()
        self.side_card.show()
        self.sidebar_toggle_btn.show()
        self.menuBar().show()
        self.statusBar().show()

        self.compact_btn.setIcon(_make_plus_minus_icon(False))
        self.compact_btn.setToolTip("切换到简洁模式")

        self.setMinimumWidth(max(0, int(self._min_width_before_compact)))
        self.setMaximumWidth(max(16777215 if self._max_width_before_compact <= 0 else int(self._max_width_before_compact), self.minimumWidth()))
        self.setMinimumHeight(max(0, int(self._min_height_before_compact)))
        self.setMaximumHeight(max(16777215 if self._max_height_before_compact <= 0 else int(self._max_height_before_compact), self.minimumHeight()))
        restore_width = self._width_before_compact if self._width_before_compact > 0 else self.width()
        restore_height = self._height_before_compact if self._height_before_compact > 0 else self.height()
        screen = self.windowHandle().screen() if self.windowHandle() else QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            restore_width = min(restore_width, max(480, avail.width()))
            restore_height = min(restore_height, max(240, avail.height()))
        self.resize(restore_width, restore_height)
        controls_pos_after = self.card_controls.mapToGlobal(QPoint(0, 0))
        self.move(
            self.x() + (controls_pos_before.x() - controls_pos_after.x()),
            self.y() + (controls_pos_before.y() - controls_pos_after.y()),
        )

        total = max(1, sum(self.main_splitter.sizes()))
        if self._sidebar_was_collapsed_before_compact:
            self.main_splitter.setSizes([total, 0])
            self._sidebar_collapsed = True
        else:
            target = min(max(180, self._sidebar_last_width), max(180, total - 360))
            self.main_splitter.setSizes([total - target, target])
            self._sidebar_collapsed = False

        self._update_sidebar_toggle_icon()
        self._reposition_sidebar_toggle()
        self._ensure_window_inside_screen()
        self.statusBar().showMessage("已退出简洁模式", 3000)

    def _refresh_window_flags(self) -> None:
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, self._compact_mode)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._always_on_top)
        if was_visible:
            self.show()
            if self._always_on_top:
                self.raise_()

    def _refresh_compact_top_buttons(self) -> None:
        self.lock_btn.setIcon(_make_lock_icon(self._compact_locked))
        self.lock_btn.setToolTip("已锁定窗口位置" if self._compact_locked else "锁定窗口位置")
        self.pin_btn.setIcon(_make_pin_icon(self._always_on_top))
        self.pin_btn.setToolTip("取消置顶" if self._always_on_top else "置顶窗口")

    def _layout_compact_top_bar(self) -> None:
        if not hasattr(self, "compact_top_bar"):
            return
        if not self.compact_top_bar.isVisible():
            return
        margin = 8
        width = max(220, self.width() - margin * 2)
        height = max(self.compact_top_bar.minimumHeight(), self.compact_top_bar.sizeHint().height())
        self.compact_top_bar.resize(width, height)
        self.compact_top_bar.move(margin, margin)
        self.compact_top_bar.raise_()

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

    def _toggle_sidebar(self) -> None:
        if self._compact_mode:
            return

        sizes = self.main_splitter.sizes()
        if len(sizes) != 2:
            return
        total = max(1, sizes[0] + sizes[1])

        if not self._sidebar_collapsed and sizes[1] > 0:
            self._sidebar_last_width = max(180, sizes[1])
            self.main_splitter.setSizes([total, 0])
            self._sidebar_collapsed = True
        else:
            target = min(max(180, self._sidebar_last_width), max(180, total - 360))
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
                self._sidebar_last_width = max(180, sizes[1])
        self._update_sidebar_toggle_icon()
        self._reposition_sidebar_toggle()

    def _update_sidebar_toggle_icon(self) -> None:
        icon = (
            QStyle.StandardPixmap.SP_ArrowLeft
            if self._sidebar_collapsed
            else QStyle.StandardPixmap.SP_ArrowRight
        )
        self.sidebar_toggle_btn.setIcon(self.style().standardIcon(icon))

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

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        steps = max(1, abs(int(delta)) // 120)
        increase = delta > 0
        for _ in range(steps):
            self.player.adjust_gain_by_key(increase)
        self._refresh_volume_ui()
        self.statusBar().showMessage(f"音量：{self.player.gain_percent()}%", 1200)
        event.accept()

    def mousePressEvent(self, event) -> None:
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
        if self._drag_offset is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_offset is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
        super().resizeEvent(event)
        self._reposition_sidebar_toggle()
        self._layout_compact_top_bar()
        self._reposition_volume_value_label()

    def minimumSizeHint(self) -> QSize:
        if self._compact_mode:
            return QSize(0, 0)
        return super().minimumSizeHint()

    def closeEvent(self, event: QCloseEvent) -> None:
        try:
            self.controller.shutdown()
        finally:
            super().closeEvent(event)


def _format_time(sec: float) -> str:
    total = max(0, int(sec))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_lrc_time(sec: float) -> str:
    safe = max(0.0, float(sec))
    minutes = int(safe // 60)
    seconds = safe - minutes * 60
    return f"{minutes:02d}:{seconds:05.2f}"


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


def _make_mode_icon(mode: str) -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#1e5899"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
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


def _make_plus_minus_icon(is_plus: bool) -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#1e5899"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)

    painter.drawLine(6, 12, 18, 12)
    if is_plus:
        painter.drawLine(12, 6, 12, 18)

    painter.end()
    return QIcon(pix)


def _make_lock_icon(locked: bool) -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#1e5899"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    painter.drawRoundedRect(QRectF(7, 11, 10, 8), 2.0, 2.0)
    if locked:
        painter.drawArc(7, 5, 10, 8, 0 * 16, 180 * 16)
        painter.drawLine(12, 14, 12, 17)
    else:
        painter.drawArc(7, 5, 10, 8, 35 * 16, 250 * 16)

    painter.end()
    return QIcon(pix)


def _make_pin_icon(pinned: bool) -> QIcon:
    pix = QPixmap(24, 24)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHints(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#1e5899"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)

    painter.drawEllipse(QRectF(8.2, 4.0, 7.6, 4.6))
    painter.drawLine(12, 8, 12, 15)
    painter.drawLine(9, 10, 15, 10)
    painter.drawLine(12, 15, 9.5, 19)

    if not pinned:
        strike = QPen(QColor("#9cb6d4"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(strike)
        painter.drawLine(6, 18, 18, 6)

    painter.end()
    return QIcon(pix)
