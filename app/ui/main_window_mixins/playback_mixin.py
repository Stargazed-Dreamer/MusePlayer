from __future__ import annotations

"""MainWindow 播放/歌单/歌词/菜单相关 mixin。"""

import html
import subprocess
from bisect import bisect_right
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QRect, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QListWidget, QListWidgetItem, QMessageBox, QSizePolicy, QToolButton

from app.models.entities import Track
from app.services.player_service import PlayMode
from app.ui.playlist_dialog import PlaylistDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import APP_STYLE_DARK, APP_STYLE_LIGHT
from app.ui.main_window_helpers import (
    MultiHintStatusBar,
    TrackItemDelegate,
    _format_lrc_time,
    _format_time,
    _make_crosshair_icon,
    _make_folder_icon,
    _make_media_icon,
    _make_mode_icon,
    _make_moon_icon,
    _make_plus_minus_icon,
    _make_sun_icon,
    _make_volume_icon,
    _parse_lrc_entries,
)


class MainWindowPlaybackMixin:
    def _play_previous_track(self) -> None:
        ok = self.player.previous_track()
        if not ok:
            self.statusBar().showMessage("没有上一首可播放", 2000)
    def _play_next_track(self) -> None:
        ok = self.player.next_track(user_triggered=True)
        if not ok:
            self.statusBar().showMessage("没有下一首可播放", 2000)
    def _save_stats_now(self) -> None:
        try:
            self.controller.save_stats_now()
            self.statusBar().showMessage("统计数据已保存", 2200)
        except Exception as exc:
            self.statusBar().showMessage(f"保存统计失败：{exc}", 3500)
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
        # 歌词来源可能含 HTML 转义（数据库导出时常见），先统一反转义。
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
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.artist_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.album_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.path_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self._meta_top_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            self._meta_bottom_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        else:
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
        # 高频刷新路径：只做轻量 UI 同步，避免阻塞音频线程节奏。
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
        self.player.play_track(str(track_id), auto_play=True, manual_select=True, active_request=True)
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
    def _menu_import_playlist_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入歌单文件",
            "",
            "MuseArc 歌单 (*.muse_playlist.json);;JSON 文件 (*.json)",
        )
        if not file_path:
            return
        try:
            self.controller.import_muse_playlist(Path(file_path))
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._reload_playlist_combo()
        self._reload_track_list()
        self.statusBar().showMessage("歌单文件已导入", 3000)
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
