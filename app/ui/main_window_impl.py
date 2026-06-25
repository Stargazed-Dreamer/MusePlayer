from __future__ import annotations

"""主窗口实现（富模式 + 简洁模式）。

设计说明（关键点）：
1. 本类负责界面编排与交互，不直接持久化业务数据；写盘统一经 AppController。
2. 绘制与控件辅助逻辑拆分到 main_window_helpers.py，本文件聚焦主流程。
3. 支持简洁/丰富模式切换，并默认使用系统原生窗口边框。

界面状态管理：
- 丰富模式：展示完整播放器界面，包括歌曲信息、歌词、封面、歌单列表等
- 简洁模式：仅保留基础播放控制和迷你信息栏，适合桌面小窗口场景
- 两种模式间可无缝切换，保持播放状态和用户设置
"""

from PySide6.QtCore import QTimer, Qt, QRect, QSize, QPoint
from PySide6.QtGui import (
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QSizePolicy,
    QSpacerItem,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.services.app_controller import AppController
from app.services.player_service import PlayMode
from app.version import APP_VERSION

from app.ui.main_window_mixins import MainWindowPlaybackMixin, MainWindowWindowingMixin

from app.ui.main_window_helpers import (
    ClickJumpSlider,
    LyricsItemDelegate,
    LyricsListWidget,
    MultiHintStatusBar,
    TrackItemDelegate,
    _WindowsTaskbarProgress,
    _make_compact_icon,
    _make_crosshair_icon,
    _make_folder_icon,
    _make_heart_icon,
    _make_media_icon,
    _make_mode_icon,
    _make_plus_icon,
    _make_rich_title_icon,
)

class MainWindow(MainWindowPlaybackMixin, MainWindowWindowingMixin, QMainWindow):
    """播放器主窗口。

    采用 Mixin 架构分离关注点：
    - MainWindowPlaybackMixin: 处理播放控制、歌单管理、歌词显示等媒体相关功能
    - MainWindowWindowingMixin: 处理窗口行为、模式切换、几何管理等窗口相关功能
    - 本类(MainWindow): 负责控件构建、信号绑定、基础状态维护

    管理两套布局状态：
    - 丰富模式：展示歌单、歌曲信息、歌词和完整控制区
    - 简洁模式：仅保留紧凑控制区与顶部操作栏
    
    核心特性：
    - 默认使用系统原生标题栏与边框，保证窗口缩放稳定
    - 支持窗口吸附、透明度调节、置顶等桌面增强功能
    - 智能歌词同步和滚动，支持歌词搜索定位
    - Windows任务栏进度显示（通过COM接口）
    - 完整的键盘快捷键支持
    """

    def __init__(self, controller: AppController):
        """初始化主窗口。
        
        Args:
            controller: AppController 实例，提供应用级服务和状态管理
        """
        super().__init__()
        
        # 设置自定义状态栏（支持多条消息并行显示）
        self.setStatusBar(MultiHintStatusBar(self))
        
        # 核心依赖注入
        self.controller = controller
        self.player = controller.player_service
        
        # 窗口交互状态
        self._dragging_progress = False  # 是否正在拖拽进度条
        self._compact_mode = False  # 当前是否为简洁模式
        self._compact_locked = False  # 简洁模式是否被锁定（防止意外切换）
        self._always_on_top = False  # 是否置顶显示
        
        # 拖拽相关状态
        self._drag_offset: QPoint | None = None  # 拖拽偏移量
        self._resize_margin = 7  # 边缘调整大小敏感区域宽度
        
        # 侧边栏状态管理
        self._sidebar_collapsed = False  # 侧边栏是否收起
        self._sidebar_was_collapsed_before_compact = False  # 进入简洁模式前的侧边栏状态
        self._sidebar_last_width = 530  # 侧边栏最后宽度
        self._sidebar_min_width = 234  # 侧边栏最小宽度
        self._sidebar_max_width = 936  # 侧边栏最大宽度
        
        # 窗口几何状态（用于模式切换时保存/恢复）
        self._last_window_width = 0  # 上次窗口宽度
        self._resize_adjusting_splitter = False  # 是否正在调整分割器
        self._width_before_compact = 0  # 进入简洁模式前的宽度
        self._height_before_compact = 0  # 进入简洁模式前的高度
        self._min_width_before_compact = self.minimumWidth()  # 进入简洁模式前的最小宽度
        self._max_width_before_compact = self.maximumWidth()  # 进入简洁模式前的最大宽度
        self._min_height_before_compact = self.minimumHeight()  # 进入简洁模式前的最小高度
        self._max_height_before_compact = self.maximumHeight()  # 进入简洁模式前的最大高度

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

        # 播放模式相关状态
        self._mode_order: list[str] = []  # 播放模式循环顺序
        self._mode_titles = {  # 播放模式显示名称映射
            PlayMode.SINGLE_LOOP.value: "单曲循环",
            PlayMode.PLAYLIST_LOOP.value: "歌单循环",
            PlayMode.RANDOM.value: "歌单随机",
        }
        self._mode_icons = {  # 播放模式图标映射
            PlayMode.SINGLE_LOOP.value: _make_mode_icon(PlayMode.SINGLE_LOOP.value),
            PlayMode.PLAYLIST_LOOP.value: _make_mode_icon(PlayMode.PLAYLIST_LOOP.value),
            PlayMode.RANDOM.value: _make_mode_icon(PlayMode.RANDOM.value),
        }

        # 歌词系统状态
        self._lyrics_entries: list[tuple[float, str]] = []
        self._lyrics_times: list[float] = []
        self._lyrics_end_times: list[float] = []
        self._lyrics_current_index = -1
        self._lyrics_user_scrolling = False
        self._lyrics_auto_adjusting = False
        self._lyrics_structured: list | None = None
        
        # 媒体内容状态
        self._has_cover_content = False  # 是否有封面图片
        self._has_lyrics_content = False  # 是否有歌词内容
        self._last_nonzero_gain = max(1, int(self.player.gain_percent())) if self.player else 100  # 最后的非零音量值（用于取消静音）
        
        # 当前播放信息缓存（用于简洁模式显示）
        self._current_track_title = "未选择歌曲"
        self._current_track_artist = "未知歌手"
        self._next_track_preview_announced = False  # 是否已预告下一首歌曲
        
        # 主题和外观
        self._dark_theme = bool(getattr(self.controller.settings, "dark_theme", True))
        
        # Windows任务栏集成
        self._taskbar_progress = _WindowsTaskbarProgress()
        
        # 丰富模式拖拽状态
        self._rich_drag_offset: QPoint | None = None  # 丰富模式拖拽偏移
        self._rich_drag_restore_ratio = 0.5  # 拖拽恢复比例
        self._use_custom_titlebar = False  # 默认关闭无边框模式，避免影响系统缩放
        
        # 窗口吸附状态
        self._snap_docked = False  # 是否已吸附到屏幕边缘
        self._geometry_before_snap: QRect | None = None  # 吸附前的窗口几何信息
        # UI组件引用
        self._top_stack_widget: QWidget | None = None  # 标题栏和菜单栏堆叠容器
        
        # 歌词自动滚动恢复定时器（用户手动滚动后延迟恢复自动滚动）
        self._lyrics_resume_timer = QTimer(self)
        self._lyrics_resume_timer.setSingleShot(True)
        self._lyrics_resume_timer.setInterval(2200)
        self._lyrics_resume_timer.timeout.connect(self._resume_lyrics_auto_scroll)

        # 基础窗口属性设置
        self.setWindowTitle("MusePlayer")
        self.resize(1280, 780)  # 默认窗口大小
        self.setAcceptDrops(True)  # 启用拖拽支持
        self._last_window_width = self.width()

        # 构建界面（按依赖顺序）
        import time as _time
        _t0 = _time.perf_counter()
        self._build_ui()      # 构建控件树
        _t1 = _time.perf_counter()
        self._build_menu()    # 构建菜单栏
        self._bind_signals()  # 绑定信号槽
        self._bind_shortcuts() # 绑定快捷键
        _t2 = _time.perf_counter()
        self._apply_window_size_limits()  # 应用窗口最大尺寸限制
        self._restore_window_geometry()  # 恢复窗口位置和大小

        # 初始化界面状态（歌曲列表和歌单下拉框延迟到窗口显示后加载）
        self._refresh_mode_order()                # 刷新播放模式顺序
        if self.player:
            self._on_mode_changed(self.player.mode.value)     # 同步播放模式
            self._on_playback_changed(self.player.is_playing())  # 同步播放状态
        self._refresh_volume_ui()                 # 刷新音量显示
        self._apply_theme_stylesheet()            # 应用主题样式
        self._refresh_theme_button()              # 刷新主题按钮状态
        self._update_window_title()               # 更新窗口标题
        self._refresh_window_flags()              # 刷新窗口标志
        _t3 = _time.perf_counter()

        print(f"[MainWindow计时] build_ui: {_t1-_t0:.3f}s | menu/signals: {_t2-_t1:.3f}s | "
              f"其余初始化: {_t3-_t2:.3f}s | 总计: {_t3-_t0:.3f}s")

        # 延迟执行的初始化任务
        QTimer.singleShot(0, self._reposition_sidebar_toggle)  # 重定位侧边栏切换按钮
        QTimer.singleShot(0, self._ensure_taskbar_progress_initialized)  # 确保任务栏进度条初始化

    def _build_ui(self) -> None:
        """构建主界面控件树并完成初始布局。
        
        布局结构：
        - 顶级：QWidget + QVBoxLayout
          - RichTitleBar：自定义标题栏（最小化/最大化/关闭）
          - QSplitter：水平分割器
            - 左侧：歌曲信息卡片 + 歌词/封面显示区
            - 右侧：歌单选择 + 搜索 + 歌曲列表
          - 底部控制卡片：进度条 + 时间显示 + 播放控制按钮
        """
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

        self.favorite_btn = self._new_icon_button("ControlIconButton")
        self.favorite_btn.setIcon(_make_heart_icon(filled=False, color=self._control_icon_color()))
        self.favorite_btn.setToolTip("喜欢当前歌曲")

        self.add_to_playlist_btn = self._new_icon_button("ControlIconButton")
        self.add_to_playlist_btn.setIcon(_make_plus_icon(color=self._control_icon_color()))
        self.add_to_playlist_btn.setToolTip("添加到歌单")

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
        self.volume_slider.setValue(self.player.slider_gain_percent() if self.player else 100)
        self.volume_slider.setMinimumWidth(150)

        self.compact_btn = self._new_icon_button("CompactButton")
        self.compact_btn.setIcon(_make_compact_icon(False, color=self._control_icon_color()))
        self.compact_btn.setToolTip("切换到简洁模式")

        self.speed_combo = QComboBox()
        self.speed_combo.setMinimumWidth(88)
        for rate in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            self.speed_combo.addItem(f"{rate:.2g}x", rate)
        self._sync_speed_combo()

        control_row.addWidget(self.theme_btn)
        control_row.addWidget(self.locate_file_btn)
        control_row.addWidget(self.favorite_btn)
        control_row.addWidget(self.add_to_playlist_btn)
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
        self.search_clear_btn = QToolButton(self.search_edit)
        self.search_clear_btn.setObjectName("SearchClearButton")
        self.search_clear_btn.setText("×")
        self.search_clear_btn.setToolTip("清空搜索")
        self.search_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_clear_btn.setAutoRaise(True)
        self.search_clear_btn.hide()
        self.search_edit.setTextMargins(0, 0, 20, 0)
        self.search_edit.installEventFilter(self)
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
        self.compact_top_title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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
        self.compact_top_bar.installEventFilter(self)
        self.controls_layout.insertWidget(0, self.compact_top_bar)
        self.compact_top_bar.hide()
        self._refresh_compact_top_buttons()

        self.statusBar().showMessage("就绪", 1800)
        QTimer.singleShot(0, self._reposition_volume_value_label)
        QTimer.singleShot(0, self._position_search_clear_button)

    def _build_menu(self) -> None:
        """
        构建应用程序的菜单栏，包括文件菜单、歌单、设置菜单，以及提示标签。

        参数：
            self (类实例): 当前对象实例。

        返回值：
            无。
        """
        self.menuBar().setNativeMenuBar(False)  # 设置菜单栏不是原生菜单栏
        self.menuBar().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # 启用样式背景
        self.menuBar().setMouseTracking(True)  # 启用鼠标跟踪

        menu_file = self.menuBar().addMenu("文件")  # 添加“文件”菜单
        action_import_folder = menu_file.addAction("导入文件夹")  # 添加“导入文件夹”动作
        action_import_playlist = menu_file.addAction("导入歌单文件")  # 添加“导入歌单文件”动作
        action_open_file = menu_file.addAction("播放文件")  # 添加“播放文件”动作
        self.action_save_stats = menu_file.addAction("保存统计数据")  # 添加“保存统计数据”动作，并保存为实例变量
        self.action_save_stats.setShortcut(QKeySequence("Ctrl+S"))  # 设置快捷键为Ctrl+S
        self.action_save_stats.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)  # 设置快捷键上下文为应用程序范围
        action_export_stats = menu_file.addAction("导出统计数据")  # 添加“导出统计数据”动作
        menu_file.addSeparator()  # 添加分隔线
        action_exit = menu_file.addAction("退出")  # 添加“退出”动作

        action_playlist = self.menuBar().addAction("歌单")  # 添加“歌单”动作到菜单栏

        action_settings = self.menuBar().addAction("设置")  # 添加“设置”动作到菜单栏

        self.random_state_label = QLabel("")  # 创建随机状态标签，初始为空
        self.random_state_label.setObjectName("RandomStateHintLabel")  # 设置对象名称
        self.random_state_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # 设置对齐方式为右对齐和垂直居中
        self.random_state_label.setMinimumWidth(120)  # 设置最小宽度
        self.version_label = QLabel(f"v{APP_VERSION}")  # 创建版本标签，显示应用版本
        self.version_label.setObjectName("VersionHintLabel")  # 设置对象名称
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # 设置对齐方式
        self.version_label.setMinimumWidth(58)  # 设置最小宽度
        self.version_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)  # 设置大小策略为固定宽度和首选高度

        self.menu_hint_widget = QWidget(self.menuBar())  # 创建提示部件，作为菜单栏的子部件
        hint_layout = QHBoxLayout(self.menu_hint_widget)  # 创建水平布局
        hint_layout.setContentsMargins(0, 0, 0, 0)  # 设置边距为0
        hint_layout.setSpacing(8)  # 设置间距为8像素
        hint_layout.addWidget(self.version_label, 0)  # 添加版本标签到布局，拉伸因子为0
        hint_layout.addWidget(self.random_state_label, 0)  # 添加随机状态标签到布局，拉伸因子为0

        self.menuBar().setCornerWidget(self.menu_hint_widget, Qt.Corner.TopRightCorner)  # 将提示部件设置到菜单栏右上角
        self.random_state_label.hide()  # 隐藏随机状态标签

        for menu in (menu_file,):  # 遍历菜单列表（当前只包含文件菜单）
            menu.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)  # 启用样式背景
            menu.setMouseTracking(True)  # 启用鼠标跟踪

        action_import_folder.triggered.connect(self._menu_import_folder)  # 连接导入文件夹动作到槽函数
        action_import_playlist.triggered.connect(self._menu_import_playlist_file)  # 连接导入歌单文件动作到槽函数
        action_open_file.triggered.connect(self._menu_open_file)  # 连接播放文件动作到槽函数
        self.action_save_stats.triggered.connect(self._save_stats_now)  # 连接保存统计数据动作到槽函数
        action_export_stats.triggered.connect(self._export_stats)  # 连接导出统计数据动作到槽函数
        action_exit.triggered.connect(self.close)  # 连接退出动作到关闭方法
        action_playlist.triggered.connect(self._open_playlist_dialog)  # 连接歌单动作到打开歌单对话框方法
        action_settings.triggered.connect(self._open_settings_dialog)  # 连接设置动作到打开设置对话框方法
        self._stack_title_and_menu()  # 调用堆叠标题和菜单的方法

    def _stack_title_and_menu(self) -> None:
        """
        创建并设置顶部堆叠窗口，包含标题栏和菜单栏。

        此方法检查是否使用自定义标题栏，如果使用，则创建一个新的窗口小部件，
        将标题栏和菜单栏堆叠在一起，并设置为菜单小部件。

        参数：
            无额外参数。

        返回值：
            无。
        """
        # 检查是否使用自定义标题栏
        if not self._use_custom_titlebar:
            # 如果不使用，隐藏标题栏并提前返回
            self.rich_title_bar.hide()
            return
        # 检查顶部堆叠窗口是否已存在，如果存在则直接返回
        if self._top_stack_widget is not None:
            return
        # 创建一个新的QWidget作为顶部堆叠窗口
        stack = QWidget(self)
        # 设置对象名称，便于调试和样式设置
        stack.setObjectName("TopStackWidget")
        # 创建垂直布局管理器
        stack_layout = QVBoxLayout(stack)
        # 设置布局的边距为0，使内容紧贴边缘
        stack_layout.setContentsMargins(0, 0, 0, 0)
        # 设置布局内小部件之间的间距为0
        stack_layout.setSpacing(0)
        # 将标题栏的父级设置为堆叠窗口
        self.rich_title_bar.setParent(stack)
        # 将标题栏添加到布局中，伸缩因子为0
        stack_layout.addWidget(self.rich_title_bar, 0)
        # 将菜单栏添加到布局中，伸缩因子为0
        stack_layout.addWidget(self.menuBar(), 0)
        # 将堆叠窗口设置为菜单小部件，替换默认菜单栏
        self.setMenuWidget(stack)
        # 保存堆叠窗口的引用，以便后续使用或检查
        self._top_stack_widget = stack

    def _bind_signals(self) -> None:
        """绑定所有UI组件的信号到相应的处理函数。

        这个方法将各个按钮、滑块等UI元素的信号连接到对应的槽函数，以实现用户交互的事件处理。

        参数：
        无（self参数为实例对象本身）。

        返回值：
        无（None）。
        """
        self.theme_btn.clicked.connect(self._toggle_theme)
        self.locate_file_btn.clicked.connect(self._open_current_in_explorer)
        self.favorite_btn.clicked.connect(self._toggle_current_favorite)
        self.add_to_playlist_btn.clicked.connect(self._add_current_to_playlist)
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
        self.search_edit.textChanged.connect(lambda _: self._reload_track_list())  # 使用lambda忽略参数，重新加载轨道列表
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        self.search_clear_btn.clicked.connect(self._clear_search_text)
        self.track_list.itemDoubleClicked.connect(self._on_track_double_clicked)
        self.track_list.verticalScrollBar().valueChanged.connect(lambda _: self._update_locate_current_button())  # 使用lambda忽略参数，更新定位当前按钮状态

        if self.player:  # 检查播放器实例是否存在，如果存在则连接播放器相关信号
            self.player.track_changed.connect(self._refresh_current_track_ui)
            self.player.progress_changed.connect(self._on_progress_changed)
            self.player.playback_changed.connect(self._on_playback_changed)
            self.player.mode_changed.connect(self._on_mode_changed)
            self.player.random_state_changed.connect(self._on_random_state_changed)
            self.player.playback_rate_changed.connect(self._on_playback_rate_changed)
            self.player.queue_changed.connect(self._on_queue_changed)

        self.controller.library_changed.connect(self._on_library_changed)
        self.controller.settings_changed.connect(self._on_settings_changed)
        self.controller.message.connect(lambda text: self.statusBar().showMessage(str(text), 2500))  # 使用lambda将消息转换为字符串并显示2500毫秒
        self.controller.error_occurred.connect(self._on_error)
        self.controller.runtime_status_changed.connect(self._on_runtime_status_changed)

        self.main_splitter.splitterMoved.connect(self._on_splitter_moved)

        self.lyrics_list.user_interacted.connect(self._on_lyrics_user_interaction)
        self.lyrics_list.copy_requested.connect(self._copy_selected_lyric)
        self.lyrics_list.itemDoubleClicked.connect(self._on_lyric_double_clicked)
        self.lyrics_list.verticalScrollBar().sliderPressed.connect(self._on_lyrics_user_interaction)
        self.lyrics_list.verticalScrollBar().valueChanged.connect(self._on_lyrics_scroll_changed)

    def _bind_shortcuts(self) -> None:
        if self.player:
            QShortcut(QKeySequence("Space"), self, activated=self.player.toggle_play_pause)
        QShortcut(QKeySequence("PgUp"), self, activated=self._play_previous_track)
        QShortcut(QKeySequence("PgDown"), self, activated=self._play_next_track)
        QShortcut(QKeySequence("Up"), self, activated=lambda: self._adjust_volume_by_key(True))
        QShortcut(QKeySequence("Down"), self, activated=lambda: self._adjust_volume_by_key(False))
        QShortcut(QKeySequence("Left"), self, activated=lambda: self._seek_by_seconds(-5.0))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self._seek_by_seconds(+5.0))
        esc = QShortcut(QKeySequence("Esc"), self, activated=self._clear_search_by_esc)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def _on_search_text_changed(self, _text: str) -> None:
        """
        功能：当搜索文本改变时调用的事件处理器，用于更新清除按钮的状态和位置。
        参数：
            _text (str): 传递的搜索文本参数，但在此方法中未直接使用，具体文本从self.search_edit获取。
        返回值：无
        """
        self._position_search_clear_button()  # 调用方法重新定位清除按钮到搜索框内合适位置
        self.search_clear_btn.setVisible(bool(self.search_edit.text().strip()))  # 判断搜索框文本是否非空，是则显示清除按钮，否则隐藏

    def _position_search_clear_button(self) -> None:
        """设置搜索框清除按钮的位置和大小。

        功能：计算并设置搜索框清除按钮在搜索框内的精确位置和合适大小，确保按钮居中显示且不超出搜索框边界。
        参数：无
        返回值：无返回值
        """
        # 检查是否已初始化所需的UI组件，若未初始化则直接返回
        if not hasattr(self, "search_edit") or not hasattr(self, "search_clear_btn"):
            return
        # 获取搜索框的边框宽度
        frame = self.search_edit.style().pixelMetric(self.search_edit.style().PixelMetric.PM_DefaultFrameWidth)
        # 计算按钮高度，取16和搜索框高度减4的最大值，确保按钮有合适的最小高度
        button_h = max(16, self.search_edit.height() - 4)
        # 调整清除按钮的大小为16（宽）× button_h（高）
        self.search_clear_btn.resize(16, button_h)
        # 计算按钮的x坐标，确保不超出搜索框左侧的边框
        x = max(frame, self.search_edit.width() - self.search_clear_btn.width() - frame - 1)
        # 计算按钮的y坐标，使其在搜索框中垂直居中
        y = max(1, (self.search_edit.height() - self.search_clear_btn.height()) // 2)
        # 将清除按钮移动到计算出的位置 (x, y)
        self.search_clear_btn.move(x, y)
        # 将清除按钮置于顶层，确保其显示在搜索框文本之上
        self.search_clear_btn.raise_()

    def _clear_search_text(self) -> None:
        text = self.search_edit.text()
        if not text:
            return
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.setSelection(0, len(text))
        self.search_edit.del_()

    def _clear_search_by_esc(self) -> None:
        """通过ESC键清除搜索。该方法检查当前窗口是否活动且搜索框有文本，如果条件满足则清除搜索文本。
        参数：self - 类实例。返回值：None。
        """
        if not self.isActiveWindow():  # 检查当前窗口是否活动，如果不活动则返回
            return
        if not self.search_edit.text():  # 检查搜索编辑框是否有文本，如果没有则返回
            return
        self._clear_search_text()  # 调用方法清除搜索文本

