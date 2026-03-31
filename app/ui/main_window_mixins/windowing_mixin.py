from __future__ import annotations

"""MainWindow 窗口行为与交互相关 mixin。"""

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
    def _toggle_compact_mode(self) -> None:
        """在丰富/简洁模式之间切换并维护窗口几何信息。"""
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
        # 模拟系统标题栏吸附行为：顶部最大化，左右半屏。
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
        # 无边框窗口通过 WM_NCHITTEST 暴露系统级边缘拉伸能力。
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
        # 滚轮步进与键盘步进共享同一档位策略，确保用户预期一致。
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
