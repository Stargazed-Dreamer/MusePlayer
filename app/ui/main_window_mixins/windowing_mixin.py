from __future__ import annotations

"""MainWindow 窗口行为与交互相关 mixin。

该mixin承载主窗口的所有窗口管理和交互功能：
- 模式切换：富模式与简洁模式间的无缝切换
- 窗口几何：大小调整、位置记忆、屏幕吸附
- 无边框处理：自定义标题栏、边缘拖拽、窗口控制
- 交互增强：透明度调节、置顶显示、锁定模式
- 拖拽支持：文件拖拽导入、进度条拖拽
- 事件处理：鼠标事件、键盘事件、窗口事件

核心特性：
- 双模式界面：适应不同使用场景的完整/简化界面
- 智能吸附：自动吸附屏幕边缘，便于多窗口管理
- 透明度控制：支持窗口半透明效果
- 持久化记忆：自动保存和恢复窗口大小、位置、状态
- Windows集成：任务栏进度显示（COM接口）

设计原则：
- 关注点分离：纯窗口行为逻辑，不涉及播放业务
- 状态管理：完整记录和恢复窗口各个维度的状态
- 用户友好：提供直观的交互反馈和操作提示
- 平台适配：针对不同操作系统特性进行优化

协作关系：
- 与MainWindowPlaybackMixin协作提供完整功能
- 依赖main_window_helpers提供底层窗口能力
- 通过事件过滤机制处理复杂的交互逻辑
"""

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QCursor, QDragEnterEvent, QDropEvent, QGuiApplication
from PySide6.QtWidgets import QComboBox, QLineEdit, QListWidget, QListWidgetItem, QSlider, QToolButton

from app.ui.main_window_helpers import (
    HTBOTTOM,
    HTBOTTOMLEFT,
    HTBOTTOMRIGHT,
    HTLEFT,
    HTRIGHT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    WM_NCHITTEST,
    TrackItemDelegate,
    _make_lock_icon,
    _make_pin_icon,
    _make_plus_minus_icon,
    _make_rich_title_icon,
    _make_sidebar_toggle_icon,
)


class MainWindowWindowingMixin:
    """主窗口窗口行为管理mixin。
    
    提供完整的窗口状态管理和用户交互控制：
    
    双模式界面：
    - 富模式：完整功能界面，包含所有播放控制和媒体信息
    - 简洁模式：最小化界面，仅保留核心播放控制和迷你信息栏
    - 无缝切换：保持播放状态和用户设置的一致性
    
    窗口管理：
    - 几何控制：大小、位置、最小/最大限制
    - 屏幕吸附：智能边缘吸附，支持多窗口布局
    - 状态记忆：自动保存窗口配置到设置文件
    
    交互增强：
    - 透明度调节：支持窗口半透明效果（特别适合简洁模式）
    - 置顶显示：保持在其他窗口之上的显示模式
    - 锁定模式：防止意外退出简洁模式
    - 无边框控制：自定义窗口装饰和控制按钮
    
    事件处理：
    - 鼠标事件：拖拽、调整大小、区域检测
    - 键盘事件：全局快捷键、焦点管理
    - 窗口事件：显示/隐藏、激活/失焦、关闭处理
    
    文件操作：
    - 拖拽导入：支持拖拽音频文件到窗口进行播放
    - 文件关联：与系统文件管理器集成
    """

    # ============================================================================
    # 模式切换管理
    # ============================================================================
    
    def _toggle_compact_mode(self) -> None:
        """在丰富模式与简洁模式之间切换。
        
        模式切换逻辑：
        - 富模式 → 简洁模式：保存当前几何信息，切换到紧凑布局
        - 简洁模式 → 富模式：恢复之前保存的几何信息，切换到完整布局
        
        状态保持：
        - 播放状态、进度、音量等保持不变
        - 歌词显示适配到不同布局空间
        - 侧边栏状态在进入简洁模式时自动收起
        
        界面调整：
        - 富模式：显示完整控制栏、媒体信息、歌词、歌单
        - 简洁模式：仅显示进度条、基础控制按钮、迷你信息栏
        """
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
        """退出简洁模式，切换到富模式界面。
        
        这是一个便捷方法，用于在某些操作后自动退出简洁模式：
        - 检查当前是否处于简洁模式
        - 如果是，则调用模式切换方法
        """
        if not self._compact_mode:
            return
        self._toggle_compact_mode()
    def _toggle_rich_maximize(self) -> None:
        """在富模式下切换窗口最大化/还原状态。
        
        处理窗口的三种状态切换：
        - 最大化 → 正常：还原窗口到正常大小
        - 吸附状态 → 正常：从屏幕吸附状态还原
        - 正常 → 最大化：最大化窗口
        
        简洁模式下此功能不可用。
        """
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
        """检查窗口是否处于需要还原的状态。
        
        判断当前窗口是否需要显示"还原"按钮：
        - 窗口最大化时需要还原
        - 窗口吸附到屏幕边缘时需要还原
        
        Returns:
            True表示需要显示还原按钮，False表示显示最大化按钮
        """
        return bool(self.isMaximized() or self._snap_docked)
    def _remember_geometry_before_snap(self) -> None:
        """保存屏幕吸附前的窗口几何信息。
        
        在窗口即将吸附到屏幕边缘时，保存当前的窗口位置和大小：
        - 只在窗口处于正常状态时保存
        - 用于后续从吸附状态还原时使用
        """
        if self.isMaximized() or self._snap_docked:
            return
        self._geometry_before_snap = QRect(self.geometry())
    def _restore_from_snap(self) -> None:
        """从屏幕吸附状态恢复到正常窗口状态。
        
        使用之前保存的几何信息还原窗口：
        - 清除吸附状态标志
        - 恢复之前的窗口位置和大小
        - 确保窗口在屏幕可见区域内
        """
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
        """刷新窗口标志位，更新窗口行为特性。
        
        配置窗口的核心行为标志：
        - 无边框模式：启用自定义窗口装饰
        - 置顶显示：根据设置决定是否保持在最前端
        - 确保窗口可见性和正确的Z序
        """
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._always_on_top)
        if was_visible:
            self.show()
            if self._always_on_top:
                self.raise_()
    def _refresh_compact_top_buttons(self) -> None:
        """刷新简洁模式顶部控制按钮的图标和提示文本。
        
        根据当前窗口状态更新各个控制按钮的表现：
        - 锁定按钮：显示当前锁定状态和对应图标
        - 置顶按钮：显示当前置顶状态和对应图标  
        - 关闭按钮：保持一致的样式和颜色
        """
        color = self._control_icon_color()
        self.lock_btn.setIcon(_make_lock_icon(self._compact_locked, color=color))
        self.lock_btn.setToolTip("已锁定窗口位置" if self._compact_locked else "锁定窗口位置")
        self.pin_btn.setIcon(_make_pin_icon(self._always_on_top, color=color))
        self.pin_btn.setToolTip("取消置顶" if self._always_on_top else "置顶窗口")
        self.compact_close_btn.setIcon(_make_rich_title_icon("close", color=color))
    def _layout_compact_top_bar(self) -> None:
        """布局简洁模式顶部控制栏的各个组件。
        
        动态计算并设置顶部栏各元素的尺寸和位置：
        - 计算可用空间，考虑左右控制按钮的宽度
        - 动态调整标题标签的宽度和位置
        - 确保标题在可用空间内居中显示
        - 提升标题标签的层级确保可见性
        """
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
        """确保窗口始终位于屏幕可见区域内。
        
        如果窗口位置或大小超出屏幕边界，自动调整到合适位置：
        - 获取当前屏幕的可用几何区域（排除任务栏等系统区域）
        - 检测窗口是否超出边界
        - 自动调整位置确保窗口完全可见
        - 保持窗口的合理性（不超出屏幕范围）
        """
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
        """从设置中恢复之前保存的窗口几何信息。
        
        在应用启动时调用，恢复用户上次关闭时的窗口状态：
        - 检查是否启用了窗口几何记忆功能
        - 从设置中读取保存的窗口大小和位置
        - 验证几何信息的有效性（正数范围）
        - 恢复窗口大小和位置
        - 确保恢复后的窗口在屏幕可见区域内
        """
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
        """将当前窗口几何信息保存到设置中。
        
        在应用关闭或窗口发生变化时调用，记住用户偏好的窗口状态：
        - 检查是否启用了窗口几何记忆功能
        - 获取当前窗口的框架几何信息（包含边框的完整尺寸）
        - 将位置和大小信息转换为整型并保存
        - 供下次启动时恢复使用
        """
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
        """确保Windows任务栏进度显示功能已初始化。
        
        在Windows平台上初始化任务栏进度显示：
        - 获取窗口句柄（只支持Windows平台）
        - 验证句柄有效性
        - 将任务栏进度组件绑定到窗口
        """
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
        """切换侧边栏的展开/收起状态。
        
        控制主窗口侧边栏的显示和隐藏：
        - 简洁模式下不可用（此时侧边栏已自动隐藏）
        - 展开→收起：保存当前宽度，完全隐藏侧边栏
        - 收起→展开：恢复到之前保存的宽度
        - 更新切换按钮图标和位置
        - 通过状态栏提供用户反馈
        """
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
        """约束侧边栏宽度到合理的范围内。
        
        确保侧边栏宽度适配可用空间且符合最小/最大限制：
        - 考虑主窗口的总宽度
        - 保留最小主内容区域宽度（252像素）
        - 应用配置的最小/最大侧边栏宽度限制
        - 将期望宽度调整到有效范围内
        
        Args:
            total_width: 主窗口的总宽度
            preferred: 期望的侧边栏宽度
            
        Returns:
            符合约束条件的实际宽度
        """
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
        """优先调整侧边栏大小的窗口resize处理。
        
        当窗口大小改变时，优先调整侧边栏的宽度而不是主内容区域。
        这样可以在窗口resize时保持更好的用户体验。
        
        Args:
            delta_width: 宽度变化量
            old_sidebar_width: 调整前的侧边栏宽度，如果为None则使用上次的记录值
            total_width: 新的总宽度，如果为None则使用当前splitter的宽度
        """
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
        """检测鼠标位置是否在窗口边缘的可调整区域内。
        
        用于实现自定义的窗口边缘拖动调整大小功能。
        
        Args:
            pos: 相对于窗口的本地坐标位置
            
        Returns:
            Qt.Edge的组合值，表示鼠标在哪个边缘，如果不在边缘则返回None
        """
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
    def _cursor_for_resize_edges(edges) -> Qt.CursorShape:
        """根据边缘类型返回对应的鼠标光标形状。
        
        Args:
            edges: Qt.Edge的组合值，表示可调整的边缘
            
        Returns:
            对应的鼠标光标形状
        """
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
        """获取指定全局坐标所在屏幕对象。
        
        优先尝试通过坐标查找，如果找不到则使用窗口的屏幕或主屏幕。
        
        Args:
            global_pos: 全局屏幕坐标
            
        Returns:
            QScreen对象
        """
        screen = QGuiApplication.screenAt(global_pos)
        if screen is not None:
            return screen
        handle = self.windowHandle()
        return handle.screen() if handle is not None else QGuiApplication.primaryScreen()
    def _apply_titlebar_snap(self, global_pos: QPoint) -> None:
        """应用标题栏拖动吸附效果。
        
        模拟Windows系统的标题栏吸附行为：
        - 拖动到屏幕顶部：最大化窗口
        - 拖动到屏幕左侧：窗口占据左半屏
        - 拖动到屏幕右侧：窗口占据右半屏
        
        Args:
            global_pos: 鼠标的全局屏幕坐标
        """
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
        """处理原生窗口事件，主要用于Windows平台的无边框窗口边缘调整。
        
        通过拦截WM_NCHITTEST消息，为无边框窗口提供系统级的边缘拖动调整大小功能。
        只有在Windows平台上且不是紧凑模式或最大化状态下才生效。
        
        Args:
            eventType: 事件类型
            message: 事件消息指针
            
        Returns:
            (handled, result) 元组，handled表示是否处理了该事件，result为处理结果
        """
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
        """事件过滤器，处理各种UI组件的交互事件。
        
        监控并处理以下事件：
        1. 自定义标题栏的鼠标交互（双击最大化，拖动等）
        2. 播放列表的鼠标悬停和删除操作
        3. 歌词列表的鼠标悬停效果
        
        Args:
            watched: 被监控的对象
            event: Qt事件对象
            
        Returns:
            True表示事件已被处理，False交由父类处理
        """
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
        """根据鼠标滚轮调整音量。
        
        滚轮向上增加音量，向下减少音量。使用与键盘快捷键相同的音量调整策略，
        确保用户的操作预期一致。
        
        Args:
            delta: 滚轮变化量，正值表示向上滚动，负值表示向下滚动
        """
        if delta == 0:
            return
        increase = delta > 0
        self.player.adjust_gain_by_key(increase)
        self._refresh_volume_ui()
        self.statusBar().showMessage(f"音量：{self.player.gain_percent()}%", 1200)
    def _is_inside_playback_controls(self, pos: QPoint) -> bool:
        """检查指定位置是否在播放控制区域内。
        
        用于确定鼠标滚轮事件是否应该用于调整音量。
        
        Args:
            pos: 相对于窗口的本地坐标位置
            
        Returns:
            如果位置在播放控制区域内则返回True，否则返回False
        """
        child = self.childAt(pos)
        while child is not None and child is not self:
            if child is self.card_controls:
                return True
            child = child.parentWidget()
        return False
    def wheelEvent(self, event) -> None:
        """处理鼠标滚轮事件。
        
        当鼠标在播放控制区域内时，滚轮用于调整音量；
        在其他区域则传递给父类处理默认行为。
        
        Args:
            event: 鼠标滚轮事件
        """
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
        """处理鼠标按下事件。
        
        支持两种鼠标拖动操作：
        1. 非紧凑模式下：检测是否在窗口边缘，如果是则开始系统级窗口调整
        2. 紧凑模式下：检测是否在可拖动区域，如果是则开始窗口拖动
        
        Args:
            event: 鼠标按下事件
        """
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
        """处理鼠标移动事件。
        
        支持以下功能：
        1. 拖动窗口（包括自定义标题栏和紧凑模式下的拖动）
        2. 根据鼠标位置设置合适的调整大小光标
        
        Args:
            event: 鼠标移动事件
        """
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
        """处理鼠标释放事件。
        
        主要功能：
        1. 释放窗口拖动状态
        2. 应用标题栏吸附效果（如果适用）
        3. 恢复正常的鼠标光标显示
        
        Args:
            event: 鼠标释放事件
        """
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
        """处理鼠标离开窗口事件。
        
        当鼠标离开窗口时，恢复默认的光标形状。
        
        Args:
            event: 鼠标离开事件
        """
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """处理拖放进入事件。
        
        检查拖放的内容是否包含本地文件URL，如果是则接受拖放操作。
        
        Args:
            event: 拖放进入事件
        """
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
        """处理文件拖放事件。
        
        用户可以直接将音频文件拖拽到播放器窗口中进行播放。
        只处理第一个有效的本地文件。
        
        Args:
            event: 拖放事件
        """
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
        """检查指定位置是否在交互控件上。
        
        用于确定在紧凑模式下是否可以开始窗口拖拽。
        如果点击在按钮、滑块等交互控件上，则不应触发窗口拖拽。
        
        Args:
            pos: 相对于窗口的本地坐标位置
            
        Returns:
            如果位置在交互控件上则返回True，否则返回False
        """
        child = self.childAt(pos)
        interactive_types = (QToolButton, QSlider, QComboBox, QLineEdit, QListWidget)
        while child is not None and child is not self:
            if isinstance(child, interactive_types):
                return True
            child = child.parentWidget()
        return False
    def resizeEvent(self, event) -> None:
        """处理窗口大小调整事件。
        
        在窗口大小改变时执行以下操作：
        1. 优先调整侧边栏大小而不是主内容区域
        2. 重新定位和调整各个UI组件
        3. 更新播放列表中项目的高度
        4. 更新定位当前播放歌曲的按钮状态
        
        Args:
            event: 窗口大小调整事件
        """
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
        """处理窗口显示事件。
        
        窗口显示时初始化任务栏进度条显示。
        
        Args:
            event: 窗口显示事件
        """
        super().showEvent(event)
        QTimer.singleShot(0, self._ensure_taskbar_progress_initialized)
    def changeEvent(self, event) -> None:
        """处理窗口状态改变事件。
        
        主要用于在窗口最大化状态改变时更新自定义标题栏的图标显示。
        
        Args:
            event: 状态改变事件
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "rich_max_btn"):
            if self.isMaximized():
                self._snap_docked = False
            self._refresh_rich_title_icons()
    def _update_track_item_heights(self) -> None:
        """更新播放列表中所有项目的高度。
        
        根据项目的文本内容计算合适的高度，确保文本能够完整显示。
        """
        for row in range(self.track_list.count()):
            item = self.track_list.item(row)
            if item is None:
                continue
            item.setSizeHint(QSize(0, self._track_item_height_for_text(item.text() or "")))
    def _position_locate_current_button(self) -> None:
        """定位"跳转到当前播放"按钮的位置。
        
        将按钮放置在播放列表视图的右下角，确保按钮始终可见。
        """
        if not hasattr(self, "locate_current_btn"):
            return
        vp = self.track_list.viewport()
        x = max(2, vp.width() - self.locate_current_btn.width() - 4)
        y = max(2, vp.height() - self.locate_current_btn.height() - 4)
        self.locate_current_btn.move(x, y)
        self.locate_current_btn.raise_()
    def _find_current_track_row(self) -> int:
        """查找当前正在播放的歌曲在播放列表中的行号。
        
        Returns:
            当前播放歌曲的行号，如果未找到则返回-1
        """
        current_id = self.player.current_track_id
        if not current_id:
            return -1
        for row in range(self.track_list.count()):
            item = self.track_list.item(row)
            if item is not None and item.data(0x0100) == current_id:
                return row
        return -1
    def _is_track_row_visible(self, row: int) -> bool:
        """检查指定行的项目是否在播放列表的可视区域内。
        
        Args:
            row: 要检查的行号
            
        Returns:
            如果项目完全可见则返回True，否则返回False
        """
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
        """更新"跳转到当前播放"按钮的显示状态。
        
        只有当当前播放的歌曲不在可视区域内时才显示按钮。
        """
        if not hasattr(self, "locate_current_btn"):
            return
        row = self._find_current_track_row()
        should_show = row >= 0 and not self._is_track_row_visible(row)
        self.locate_current_btn.setVisible(bool(should_show))
        self._position_locate_current_button()
    def _locate_current_track_in_list(self) -> None:
        """在播放列表中定位并滚动到当前播放的歌曲。
        
        将当前播放的歌曲设置为选中状态，并滚动到可视区域中央。
        然后更新定位按钮的显示状态。
        """
        row = self._find_current_track_row()
        if row < 0:
            return
        self.track_list.setCurrentRow(row)
        item = self.track_list.item(row)
        if item is not None:
            self.track_list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
        self._update_locate_current_button()
    def minimumSizeHint(self) -> QSize:
        """返回窗口的最小尺寸提示。
        
        在紧凑模式下，窗口可以缩小到非常小的尺寸（理论上为0,0），
        在其他模式下使用父类的最小尺寸。
        
        Returns:
            最小尺寸QSize对象
        """
        if self._compact_mode:
            return QSize(0, 0)
        return super().minimumSizeHint()
    def _lyric_text_of_item(self, item: QListWidgetItem) -> str:
        """获取歌词列表中项目的纯文本内容。
        
        去除首尾空白字符，确保返回干净的歌词文本。
        
        Args:
            item: 歌词列表中的列表项
            
        Returns:
            清理后的歌词文本
        """
        return (item.text() or "").strip()
    def _track_text_of_item(self, item: QListWidgetItem) -> str:
        """获取播放列表项目中歌曲的显示名称。
        
        如果项目名称为空或只有空白字符，则返回"未知歌曲"作为默认值。
        
        Args:
            item: 播放列表中的列表项
            
        Returns:
            歌曲名称，如果为空则返回"未知歌曲"
        """
        text = (item.text() or "").strip()
        return text or "未知歌曲"
    def closeEvent(self, event: QCloseEvent) -> None:
        """处理窗口关闭事件。
        
        在窗口关闭前执行清理工作：
        1. 保存窗口几何信息以便下次启动时恢复
        2. 清理任务栏进度条
        3. 关闭应用程序控制器
        4. 最后调用父类的关闭处理
        
        Args:
            event: 窗口关闭事件
        """
        try:
            self._persist_window_geometry()
            self._taskbar_progress.clear()
            self._taskbar_progress.close()
            self.controller.shutdown()
        finally:
            super().closeEvent(event)
