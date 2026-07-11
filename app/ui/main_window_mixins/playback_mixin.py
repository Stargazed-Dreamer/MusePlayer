from __future__ import annotations

"""MainWindow 播放/歌单/歌词/菜单相关 mixin。

该mixin承载主窗口的所有播放相关功能：
- 播放控制：播放/暂停/上一首/下一首/模式切换
- 歌单管理：歌单切换、歌曲列表刷新、搜索过滤
- 歌词显示：歌词加载、时间同步、高亮滚动
- 媒体信息：歌曲信息显示、封面图片、进度更新
- 菜单操作：文件导入、歌单管理、设置访问
- 主题切换：明暗主题切换和应用
- 音量控制：音量调节、静音切换、滚轮支持

设计特点：
- 功能高度内聚，所有播放相关UI逻辑集中管理
- 通过信号槽与PlayerService和AppController解耦
- 支持快捷键绑定和状态栏消息反馈
- 歌词系统包含自动滚动和用户交互检测

与其他组件关系：
- 依赖 PlayerService 提供播放状态和控制接口
- 依赖 AppController 处理应用级操作（导入、设置等）
- 向 MainWindowWindowingMixin 提供窗口行为支持
"""

import html
import subprocess
from bisect import bisect_right
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QRect, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog, QListWidget, QListWidgetItem, QMessageBox, QSizePolicy, QToolButton, QVBoxLayout

from app.models.entities import Track
from app.services.library_service import ALL_SONGS_ID, FAVORITES_ID
from app.services.player_service import PlayMode
from app.ui.playlist_dialog import PlaylistDialog
from app.ui.settings_dialog import SettingsDialog
from app.ui.shortcut_settings_dialog import ShortcutSettingsDialog
from app.ui.theme import APP_STYLE_DARK, APP_STYLE_LIGHT
from app.ui.main_window_helpers import (
    MultiHintStatusBar,
    TrackItemDelegate,
    _format_lrc_time,
    _format_time,
    _make_crosshair_icon,
    _make_folder_icon,
    _make_compact_icon,
    _make_heart_icon,
    _make_media_icon,
    _make_mode_icon,
    _make_moon_icon,
    _make_plus_icon,
    _make_sun_icon,
    _make_volume_icon,
    _parse_lrc_entries,
    build_structured_lyrics,
)


class MainWindowPlaybackMixin:
    """主窗口播放功能mixin。
    
    提供完整的播放相关用户界面操作：
    
    播放控制：
    - 基本控制：播放、暂停、切歌、进度跳转
    - 模式切换：单曲循环、歌单循环、随机播放
    - 音量管理：音量调节、静音、增益控制
    
    媒体信息：
    - 歌曲信息显示：标题、歌手、专辑、路径
    - 封面图片：自动提取和显示音频文件封面
    - 歌词同步：实时高亮、自动滚动、手动导航
    
    歌单管理：
    - 歌曲列表：显示、搜索、排序、选择
    - 歌单切换：快速切换不同播放列表
    - 播放定位：快速跳转到当前播放歌曲
    
    系统集成：
    - 菜单操作：文件导入、设置访问、统计查看
    - 主题切换：实时的明暗主题切换
    - 状态反馈：多源状态消息的并行显示
    
    快捷键支持：
    - 空格：播放/暂停切换
    - 上下键：音量调节
    - 左右键：进度调整
    - 翻页键：切歌控制
    """

    # ============================================================================
    # 基本播放控制
    # ============================================================================
    
    def _play_previous_track(self) -> None:
        """播放上一首歌曲。"""
        ok = self.player.previous_track()
        if not ok:
            self.statusBar().showMessage("没有上一首可播放", 2000)
            
    def _play_next_track(self) -> None:
        """播放下一首歌曲。"""
        ok = self.player.next_track(user_triggered=True)
        if not ok:
            self.statusBar().showMessage("没有下一首可播放", 2000)
    def _save_stats_now(self) -> None:
        """立即保存播放统计数据到持久化存储。
        
        触发手动统计保存操作，通常在用户请求或特定时机调用。
        捕获并处理保存过程中可能发生的异常，通过状态栏提供用户反馈。
        """
        try:
            self.controller.save_stats_now()
            self.statusBar().showMessage("统计数据已保存", 2200)
        except Exception as exc:
            self.statusBar().showMessage(f"保存统计失败：{exc}", 3500)

    def _export_stats(self) -> None:
        """导出统计数据为 MuseArc 兼容格式。"""
        from PySide6.QtWidgets import QFileDialog
        import hashlib
        import json

        stats_svc = self.controller.playback_stats_service
        library = self.controller.library_service
        if stats_svc is None or library is None:
            self.statusBar().showMessage("服务未就绪，无法导出", 2500)
            return

        entries = stats_svc._entries
        if not entries:
            self.statusBar().showMessage("没有统计数据可导出", 2500)
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出统计数据", "playback_stats.muse_stats.json",
            "MuseArc 统计 (*.muse_stats.json);;JSON (*.json);;所有文件 (*)"
        )
        if not path:
            return

        try:
            tracks_list = []
            for track_id, item in entries.items():
                track = library.tracks.get(track_id)
                entry = {
                    "track_id": f"trk_{track_id}" if not track_id.startswith("trk_") else track_id,
                    "stats": {
                        "play_count": max(0, int(item.play_count)),
                        "manual_play_count": max(0, int(item.active_play_count)),
                        "play_seconds": max(0, int(item.played_seconds_total)),
                        "early_skip_count": max(0, int(item.early_skip_count)),
                    },
                }
                if track:
                    if track.source_sha256:
                        entry["source_sha256"] = track.source_sha256
                    if track.path:
                        entry["storage_relpath"] = str(track.path)
                tracks_list.append(entry)

            content_hash = hashlib.sha256(
                json.dumps(tracks_list, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:16]

            payload = {
                "schema": "musearc_playlist_export_v1",
                "playlist_hash": f"museplayer_stats_{content_hash}",
                "playlist_name": "MusePlayer 播放统计",
                "tracks": tracks_list,
            }

            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            self.statusBar().showMessage(f"已导出 {len(tracks_list)} 首歌曲的统计数据", 3000)
        except Exception as exc:
            self.statusBar().showMessage(f"导出失败：{exc}", 3500)
    def _new_icon_button(self, object_name: str) -> QToolButton:
        """创建新的图标按钮工具。
        
        创建一个标准化的图标按钮，用于播放控制界面。
        统一按钮样式：18x18像素图标，非自动抬起样式。
        
        Args:
            object_name: 按钮的对象名称，用于样式表和调试
            
        Returns:
            配置好的QToolButton实例
        """
        button = QToolButton()
        button.setObjectName(object_name)
        button.setIconSize(QSize(18, 18))
        button.setAutoRaise(False)
        return button
    def _reload_playlist_combo(self) -> None:
        """重新加载歌单下拉框，同步当前可用的所有歌单。
        
        刷新歌单选择下拉框的内容：
        - 清空现有选项并重新从曲库服务获取所有歌单
        - 特殊处理"全部歌曲"歌单的显示名称
        - 保持当前选中的歌单状态
        - 防止重入：使用blockSignals避免触发不必要的事件
        
        通常在以下时机调用：
        - 应用启动时初始化
        - 歌单数据发生变化时
        - 切换播放源之后
        """
        current = self.player.current_playlist_id
        self.playlist_combo.blockSignals(True)
        self.playlist_combo.clear()

        index_to_select = 0
        for idx, playlist in enumerate(self.controller.library_service.list_playlists()):
            if playlist.id == ALL_SONGS_ID:
                display_name = "全部歌曲"
            elif playlist.id == FAVORITES_ID:
                display_name = "我喜欢"
            else:
                display_name = playlist.name
            self.playlist_combo.addItem(display_name, playlist.id)
            if playlist.id == current:
                index_to_select = idx

        self.playlist_combo.setCurrentIndex(index_to_select)
        self.playlist_combo.blockSignals(False)
    def _reload_track_list(self) -> None:
        """刷新当前歌单的歌曲列表显示。
        
        根据搜索关键词过滤并重新构建歌曲列表：
        - 应用搜索过滤条件获取匹配的曲目
        - 批量更新列表控件，优化大列表的性能
        - 保持当前播放曲目的选中状态
        - 自动滚动到当前播放的曲目
        - 更新定位按钮的显示状态
        
        性能优化：
        - 对于大型列表（>600首），分批处理并显示进度
        - 使用setUpdatesEnabled减少UI重绘次数
        - 动态计算项目高度以支持文本换行
        """
        keyword = self.search_edit.text().strip()
        if keyword:
            tracks = self.player.search_playlist_tracks(keyword)
        elif self.player.mode == PlayMode.RANDOM and str(getattr(self.controller.settings, "random_display_order", "original")) == "random":
            ordered_ids = self.player.display_ordered_track_ids()
            tracks = [self.player.library.tracks[tid] for tid in ordered_ids if tid in self.player.library.tracks]
        else:
            tracks = self.player.search_playlist_tracks("")

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
        """同步当前播放曲目在列表中的选中状态和滚动位置。
        
        确保当前正在播放的曲目在列表中可见且被选中：
        - 查找当前播放曲目在列表中的行号
        - 设置该行为选中状态
        - 根据center参数决定滚动策略
        
        Args:
            center: True表示滚动到中央，False表示确保可见即可
        """
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
        """根据文本内容动态计算列表项的高度。
        
        支持长文本的自动换行显示，确保文本完整可见：
        - 考虑移除按钮宽度和边距
        - 使用字体度量计算实际文本高度
        - 支持单词换行和任意位置换行
        - 保证最小高度为24像素
        
        Args:
            text: 需要显示的文本内容
            
        Returns:
            计算得到的列表项高度（像素）
        """
        fm = self.track_list.fontMetrics()
        width = max(80, self.track_list.viewport().width() - TrackItemDelegate.REMOVE_WIDTH - 12)
        bounds = fm.boundingRect(
            QRect(0, 0, width, 2000),
            int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere),
            text,
        )
        return max(24, bounds.height() + 8)
    def _refresh_current_track_ui(self, track: Track | None) -> None:
        """刷新当前曲目相关的所有UI元素。
        
        更新与当前播放曲目相关的所有界面组件：
        - 歌曲信息标签（标题、歌手、专辑、路径）
        - 封面图片显示
        - 歌词内容加载
        - 窗口标题更新
        - 播放定位同步
        
        处理两种状态：
        1. track为None：清空所有显示，显示默认占位符
        2. track有效：更新所有相关的UI元素
        
        Args:
            track: 当前播放的曲目对象，如果为None表示没有选中的曲目
        """
        self._next_track_preview_announced = False
        if track is None:
            self.title_label.setText("未选择歌曲")
            self.artist_label.setText("歌手")
            self.album_label.setText("专辑")
            self.path_label.setText("")
            self._current_track_title = "未选择歌曲"
            self._current_track_artist = "未知歌手"
            self._current_track_album = "未知专辑"
            self._current_track_path = ""
            self.compact_top_title_label.setText(self._current_track_title)
            self.progress_center_label.setText("♪" if self._compact_mode else "")
            self._update_window_title()
            self._set_cover(None)
            self._load_lyrics("")
            self._refresh_now_card_layout()
            self._refresh_random_state_hint()
            self._refresh_favorite_button(None)
            return

        self._current_track_title = track.title or "未知标题"
        self._current_track_artist = (track.artist or "未知歌手").strip() or "未知歌手"
        self._current_track_album = (track.album or "未知专辑").strip() or "未知专辑"
        self._current_track_path = track.path
        self.title_label.setText(self._current_track_title)
        self.artist_label.setText(f"歌手: {self._current_track_artist}")
        self.album_label.setText(f"专辑: {self._current_track_album}")
        self.path_label.setText(self._current_track_path)
        self.compact_top_title_label.setText(self._current_track_title)
        if not self._compact_mode:
            self.progress_center_label.setText("")

        lyrics = self.controller.get_current_lyrics()
        lyrics_filename = self.controller.get_current_lyrics_filename()
        lyrics_extra = self.controller.get_current_lyrics_extra_files()
        self._load_lyrics(lyrics, main_filename=lyrics_filename, extra_files=lyrics_extra)
        self._set_cover(self.controller.get_current_cover())
        self._refresh_now_card_layout()
        self._update_window_title()
        self._refresh_random_state_hint()
        self._refresh_favorite_button(track.id)

        self._sync_current_track_row(center=True)
        self._update_locate_current_button()
        self.statusBar().showMessage(f"播放歌曲：{self._current_track_title}", 3000)
    def _set_cover(self, cover_data: bytes | None) -> None:
        """设置并显示音频文件的封面图片。
        
        处理封面图片的加载、缩放和显示：
        - 处理空数据情况，隐藏封面显示
        - 加载图片数据并验证格式有效性
        - 按比例缩放图片以适应显示区域
        - 平滑转换确保显示质量
        
        Args:
            cover_data: 封面图片的二进制数据，如果为None则隐藏封面
        """
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
    def _reload_current_lyrics(self) -> None:
        track = self.player.current_track()
        if track is None:
            return
        raw = self.controller.get_current_lyrics()
        filename = self.controller.get_current_lyrics_filename()
        extra_files = self.controller.get_current_lyrics_extra_files()
        self._load_lyrics(raw, main_filename=filename, extra_files=extra_files)

    def _load_lyrics(self, raw_lyrics: str, *, main_filename: str = "", extra_files: list[tuple[str, str]] | None = None) -> None:
        """
        加载歌词数据并更新UI显示。

        此方法处理原始歌词字符串，根据用户设置决定是否显示日文和罗马字，
        解析歌词结构（如果提供额外文件），或解析LRC格式歌词，或显示纯文本歌词。

        参数:
        - raw_lyrics (str): 原始歌词字符串。
        - main_filename (str): 主文件名，默认为空字符串，用于结构化歌词构建。
        - extra_files (list[tuple[str, str]] | None): 额外的歌词文件列表，每个元素为(文件名, 内容)的元组，可以为None。

        返回值:
        - None: 此方法不返回任何值，但会更新内部状态和UI元素。
        """
        clean = html.unescape((raw_lyrics or "").replace("\r\n", "\n").replace("\r", "\n"))  # 清理原始歌词：标准化换行符并解码HTML实体

        show_japanese = bool(getattr(self.controller.settings, "show_japanese_lyrics", True))  # 从设置中读取是否显示日文歌词，默认为True
        show_romaji = bool(getattr(self.controller.settings, "show_romaji", True))  # 从设置中读取是否显示罗马字，默认为True

        if extra_files:  # 如果提供了额外的歌词文件
            structured = build_structured_lyrics(clean, main_filename=main_filename, extra_files=extra_files)  # 构建结构化歌词
            if structured:  # 如果成功构建了结构化歌词
                self._lyrics_structured = structured  # 更新结构化歌词数据
                self._lyrics_entries = [(e.timestamp, e.display_text(show_japanese=show_japanese, show_romaji=show_romaji)) for e in structured]  # 生成歌词条目列表，包含时间戳和显示文本
                self._lyrics_times = [e.timestamp for e in structured]  # 提取所有时间戳
                self._lyrics_end_times = self._build_lyrics_end_times(self._lyrics_entries)  # 构建歌词结束时间列表
                self._lyrics_current_index = -1  # 重置当前歌词索引
                self._lyrics_user_scrolling = False  # 重置用户滚动标志
                self._lyrics_auto_adjusting = False  # 重置自动调整标志

                self.lyrics_list.clear()  # 清空歌词列表UI
                self.lyrics_delegate.set_times(self._lyrics_times, self._lyrics_end_times)  # 设置歌词时间数据到委托
                self.lyrics_delegate.set_structured_lyrics(structured)  # 设置结构化歌词到委托
                self.lyrics_delegate.set_hover_row(-1)  # 重置悬停行

                self._has_lyrics_content = True  # 标记有歌词内容
                self.lyrics_list.show()  # 显示歌词列表
                fm = self.lyrics_list.fontMetrics()  # 获取字体度量
                for entry in structured:  # 遍历结构化歌词条目
                    text = entry.display_text(show_japanese=show_japanese, show_romaji=show_romaji)  # 获取条目的显示文本
                    lc = entry.line_count(show_japanese=show_japanese, show_romaji=show_romaji)  # 获取条目的行数
                    base_h = fm.height() + 4  # 计算基础行高
                    furi_h = max(10, int(fm.height() * 0.6) + 2) if entry.furigana else 0  # 计算注音高度（如果有注音）
                    item = QListWidgetItem(text or "♪")  # 创建列表项，文本为空时使用♪替代
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)  # 设置文本对齐方式
                    item.setSizeHint(QSize(0, max(24, base_h * lc + furi_h + 4)))  # 设置项大小提示，确保最小高度
                    self.lyrics_list.addItem(item)  # 添加项到歌词列表
                self._sync_lyrics_with_position(0.0)  # 同步歌词到初始位置
                return  # 提前返回

        entries = _parse_lrc_entries(clean)  # 解析LRC格式歌词条目

        self._lyrics_structured = None  # 清空结构化歌词
        self._lyrics_entries = entries  # 更新歌词条目
        self._lyrics_times = [x[0] for x in entries]  # 提取时间戳
        self._lyrics_end_times = self._build_lyrics_end_times(entries)  # 构建结束时间列表
        self._lyrics_current_index = -1  # 重置当前索引
        self._lyrics_user_scrolling = False  # 重置滚动标志
        self._lyrics_auto_adjusting = False  # 重置调整标志

        self.lyrics_list.clear()  # 清空歌词列表UI
        self.lyrics_delegate.set_times(self._lyrics_times, self._lyrics_end_times)  # 设置时间数据
        self.lyrics_delegate.set_structured_lyrics(None)  # 清空结构化歌词
        self.lyrics_delegate.set_hover_row(-1)  # 重置悬停行

        if entries:  # 如果有LRC条目
            self._has_lyrics_content = True  # 标记有内容
            self.lyrics_list.show()  # 显示列表
            for idx, (_, text) in enumerate(entries):  # 遍历条目，忽略时间戳
                item = QListWidgetItem(text or "♪")  # 创建列表项
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)  # 设置对齐
                fm = self.lyrics_list.fontMetrics()  # 获取字体度量
                item.setSizeHint(QSize(0, max(24, fm.height() + 8)))  # 设置大小提示
                self.lyrics_list.addItem(item)  # 添加项
            self._sync_lyrics_with_position(0.0)  # 同步到初始位置
            return  # 提前返回

        lines = [html.unescape(x.strip()) for x in clean.split("\n") if x.strip()]  # 将清理后的歌词分割成非空行，并解码HTML实体
        if not lines:  # 如果没有歌词行
            self._has_lyrics_content = False  # 标记无内容
            self.lyrics_list.hide()  # 隐藏歌词列表
            if self._compact_mode:  # 如果是紧凑模式
                self.progress_center_label.setText("")  # 清空进度中心标签
            return  # 提前返回

        self._has_lyrics_content = True  # 标记有内容
        self.lyrics_list.show()  # 显示列表
        for line in lines:  # 遍历歌词行
            item = QListWidgetItem(line)  # 创建列表项
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)  # 设置对齐
            fm = self.lyrics_list.fontMetrics()  # 获取字体度量
            item.setSizeHint(QSize(260, max(24, fm.height() + 8)))  # 设置固定宽度和高度提示
            self.lyrics_list.addItem(item)  # 添加项
        if self._compact_mode:  # 如果是紧凑模式
            self.progress_center_label.setText(lines[0] if lines else "(暂无歌词)")  # 在进度中心标签显示第一行歌词或提示信息
    def _build_lyrics_end_times(self, entries: list[tuple[float, str]]) -> list[float]:
        """构建歌词条目的结束时间列表。
        
        为每条歌词计算其显示结束时间：
        - 对于非最后一条：结束时间为下一条歌词的开始时间
        - 对于最后一条：结束时间为歌曲总时长（如果有）或开始时间+3秒
        - 处理边界情况，确保时间顺序正确
        
        Args:
            entries: 歌词条目列表，每个条目包含(开始时间, 文本内容)
            
        Returns:
            对应的结束时间列表
        """
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
        """更新当前卡片的布局。

        根据是否存在封面内容和歌词内容，调整卡片内部各UI元素（如标题、艺术家等标签）的
        对齐方式和间距策略，以实现紧凑居中或默认布局。
        此方法不接收任何参数，也不返回任何值。
        """
        # 判断是否存在封面和歌词内容
        has_cover = bool(self._has_cover_content)
        has_lyrics = bool(self._has_lyrics_content)
        # 如果既没有封面也没有歌词，则启用紧凑居中布局
        compact_center = not has_cover and not has_lyrics

        # 如果存在媒体信息行组件，则根据是否有封面或歌词来决定其可见性
        if hasattr(self, "info_media_row_widget"):
            self.info_media_row_widget.setVisible(has_cover or has_lyrics)

        # 根据是否启用紧凑居中布局，设置标签的对齐方式和上下间距策略
        if compact_center:
            # 紧凑模式：标签水平垂直居中，上下间距为可扩展（占满空间）
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.artist_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.album_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.path_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self._meta_top_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
            self._meta_bottom_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        else:
            # 默认模式：标签水平垂直居中，上下间距为固定高度
            self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.artist_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.album_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self.path_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            self._meta_top_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            self._meta_bottom_spacer.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        # 获取当前卡片的布局管理器
        layout = self.card_now.layout()
        # 如果布局存在，则使其失效并重新激活，以强制应用新的布局设置
        if layout is not None:
            layout.invalidate()
            layout.activate()
    def _on_progress_pressed(self) -> None:
        """处理进度条鼠标按下事件，开始拖拽进度。"""
        self._dragging_progress = True
    def _on_progress_released(self) -> None:
        """处理进度条鼠标释放事件，完成进度跳转。
        
        计算目标位置并跳转到指定播放位置：
        - 根据滑块值计算实际播放时间
        - 调用播放器跳转到目标位置
        - 同步歌词显示到新的播放位置
        """
        self._dragging_progress = False
        duration = max(0.0, self.player.state_snapshot().get("duration_sec", 0.0))
        if duration <= 0:
            return
        position = duration * (self.progress_slider.value() / 1000.0)
        self.player.seek(position)
        self._sync_lyrics_with_position(position)
    def _on_progress_changed(self, position: float, duration: float) -> None:
        """
        功能：当音频进度改变时调用，用于更新UI显示，包括时间标签、任务栏进度，并同步歌词。
        参数：
            position (float): 当前播放位置（以秒为单位）。
            duration (float): 音频总时长（以秒为单位）。
        返回值：None
        """
        # 高频刷新路径：只做轻量 UI 同步，避免阻塞音频线程节奏。
        self.current_time_label.setText(_format_time(position))
        self.total_time_label.setText(_format_time(duration))
        self._update_taskbar_progress(position, duration)
        self._maybe_show_next_track_preview(position, duration)

        # 如果用户没有手动滚动歌词，则自动同步歌词位置。
        if not self._lyrics_user_scrolling:
            self._sync_lyrics_with_position(position)

        # 如果用户正在拖动进度条，避免更新冲突，直接返回。
        if self._dragging_progress:
            return
        # 检查音频时长是否有效，如果无效则重置进度条。
        if duration <= 0:
            self.progress_slider.setValue(0)
            return

        # 计算播放进度比率，确保在0到1之间，然后转换为进度条的整数值（假设范围0-1000）。
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
            if self._lyrics_structured and idx < len(self._lyrics_structured):
                show_japanese = bool(getattr(self.controller.settings, "show_japanese_lyrics", True))
                show_romaji = bool(getattr(self.controller.settings, "show_romaji", True))
                self.progress_center_label.setText(self._lyrics_structured[idx].compact_text(show_japanese=show_japanese, show_romaji=show_romaji))
            else:
                self.progress_center_label.setText(self._lyrics_entries[idx][1] or "♪")
        self._lyrics_auto_adjusting = True
        self.lyrics_list.setCurrentRow(idx)
        item = self.lyrics_list.item(idx)
        if item is not None:
            self.lyrics_list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
        self._lyrics_auto_adjusting = False
    def _on_lyrics_user_interaction(self) -> None:
        """处理歌词列表的用户交互事件。
        
        当用户主动操作歌词列表时（如滚动），暂时禁用自动滚动功能：
        - 设置用户滚动状态标志
        - 启动定时器，在用户操作一段时间后恢复自动滚动
        """
        if not self._lyrics_entries:
            return
        self._lyrics_user_scrolling = True
        self._lyrics_resume_timer.start()
    def _on_lyrics_scroll_changed(self, _value: int) -> None:
        """监听歌词列表滚动位置变化。
        
        检测用户是否正在手动滚动歌词列表：
        - 忽略程序自动调整引起的滚动事件
        - 当鼠标在列表上方时认为是用户操作
        - 触发用户交互处理逻辑
        
        Args:
            _value: 滚动条的新值（本实现中未使用）
        """
        if self._lyrics_auto_adjusting:
            return
        if self.lyrics_list.underMouse():
            self._on_lyrics_user_interaction()
    def _resume_lyrics_auto_scroll(self) -> None:
        """恢复歌词的自动滚动功能。
        
        在用户停止手动操作一段时间后调用：
        - 清除用户滚动状态标志
        - 重新同步歌词显示到当前播放位置
        """
        self._lyrics_user_scrolling = False
        position = float(self.player.state_snapshot().get("position_sec", 0.0))
        self._sync_lyrics_with_position(position)
    def _copy_selected_lyric(self) -> None:
        """复制当前选中的歌词文本到剪贴板。
        
        获取当前选中的歌词列表项文本内容，并复制到系统剪贴板：
        - 检查是否有选中的列表项
        - 提取文本内容并验证非空
        - 使用系统剪贴板API复制文本
        - 通过状态栏提供操作反馈
        """
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
        """处理歌词项双击事件，跳转到对应播放时间点。
        
        根据双击的歌词项，计算对应的时间点并跳转到该位置：
        - 获取歌词项在列表中的行号
        - 验证行号有效性，确保不越界
        - 查找对应的时间戳并跳转到该位置
        - 同步歌词显示到新的播放位置
        - 显示跳转操作的用户反馈
        
        Args:
            item: 被双击的歌词列表项
        """
        row = self.lyrics_list.row(item)
        if row < 0 or row >= len(self._lyrics_times):
            return
        target = float(self._lyrics_times[row])
        self.player.seek(target)
        self._sync_lyrics_with_position(target)
        self.statusBar().showMessage(f"跳转到歌词时间：{_format_lrc_time(target)}", 2500)
    def _on_playback_changed(self, playing: bool) -> None:
        """当播放状态改变时调用此方法，更新播放按钮图标和状态栏消息。

        参数:
            playing (bool): 是否正在播放。True表示播放，False表示暂停。

        返回:
            None: 此方法无返回值。
        """
        icon = _make_media_icon("pause" if playing else "play", color=self._control_icon_color())  # 根据播放状态选择暂停或播放图标
        self.play_btn.setIcon(icon)  # 更新播放按钮的图标
        state = "播放" if playing else "暂停"  # 根据状态设置中文文本
        self.statusBar().showMessage(f"{state}：{self._current_track_title}", 2000)  # 在状态栏显示当前状态和歌曲标题，持续2秒
    def _refresh_mode_order(self) -> None:
        """刷新并更新可用的播放模式顺序列表。
        
        从播放器获取当前可用的播放模式，如果没有可用模式则使用默认列表：
        - 从播放器服务获取可用模式列表
        - 如果列表为空，使用默认模式（单曲循环、随机播放）
        - 确保模式切换逻辑有一致的模式顺序
        """
        self._mode_order = list(self.player.available_modes())
        if not self._mode_order:
            self._mode_order = [PlayMode.SINGLE_LOOP.value, PlayMode.RANDOM.value]
    def _cycle_play_mode(self) -> None:
        """循环切换到下一个播放模式。
        
        按照模式顺序列表切换到下一个播放模式：
        - 刷新当前可用模式列表
        - 查找当前模式在列表中的位置
        - 切换到下一个模式（如果当前模式不在列表中，切换到第一个模式）
        - 更新播放器模式并显示用户反馈
        """
        self._refresh_mode_order()
        current = self.player.mode.value
        try:
            idx = self._mode_order.index(current)
        except ValueError:
            idx = 0
        next_mode = self._mode_order[(idx + 1) % len(self._mode_order)]
        self.player.set_mode(next_mode)
        self._reload_track_list()
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
    def _toggle_current_favorite(self) -> None:
        """切换当前歌曲的喜欢状态。

        本方法会获取当前正在播放的歌曲，并尝试将其加入或移除“我喜欢”列表。
        操作完成后，会更新UI上的喜欢按钮状态，并在状态栏短暂显示操作结果。

        参数:
            self: 类的实例，用于访问实例属性和方法。

        返回值:
            None
        """
        # 从播放器获取当前正在播放的曲目对象。
        track = self.player.current_track()
        # 如果当前没有播放任何曲目，则直接退出方法，不做任何操作。
        if track is None:
            return
        # 调用控制器，切换指定曲目的喜欢状态，并记录切换后的新状态（True为喜欢，False为取消喜欢）。
        now_favorite = self.controller.toggle_track_favorite(track.id)
        # 刷新界面上的喜欢按钮，以反映最新的喜欢状态。
        self._refresh_favorite_button(track.id)
        # 在主窗口状态栏显示操作结果的提示信息，2.2秒后自动消失。
        self.statusBar().showMessage("已加入我喜欢" if now_favorite else "已取消喜欢", 2200)

    def _add_current_to_playlist(self) -> None:
        """将当前播放的歌曲添加到用户选择的歌单中。

        此方法会显示一个对话框，让用户选择目标歌单（排除"所有歌曲"和"收藏"歌单）。
        如果歌曲已存在于歌单中，则会在歌单名后显示"(已存在)"且不可选择。
        用户确认后，将歌曲添加到所选歌单并更新状态栏提示。

        Args:
            self: 实例对象。

        Returns:
            None: 无返回值。
        """
        # 获取当前播放的歌曲信息
        track = self.player.current_track()
        # 如果当前没有播放歌曲，则直接返回
        if track is None:
            return
        # 获取所有歌单列表
        playlists = self.controller.library_service.list_playlists()
        # 准备一个列表，用于存储有效的歌单项（排除特定歌单）
        items = []
        # 遍历所有歌单
        for pl in playlists:
            # 跳过"所有歌曲"和"收藏"歌单
            if pl.id in {ALL_SONGS_ID, FAVORITES_ID}:
                continue
            # 检查当前歌曲是否已存在于该歌单中
            already = track.id in pl.track_ids
            # 将歌单信息（ID、名称、是否已存在）添加到列表中
            items.append((pl.id, pl.name, already))
        # 如果没有可选择的歌单（即排除特定歌单后为空），则显示提示并返回
        if not items:
            self.statusBar().showMessage("没有可添加的歌单", 2200)
            return
        # 创建对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("添加到歌单")
        dialog.setMinimumWidth(280)
        # 创建垂直布局
        layout = QVBoxLayout(dialog)
        # 创建列表部件
        list_widget = QListWidget(dialog)
        # 遍历有效的歌单项
        for pl_id, pl_name, already in items:
            # 创建列表项，并在歌单名后标注"(已存在)"（如果歌曲已存在）
            item = QListWidgetItem(f"{pl_name}{' (已存在)' if already else ''}")
            # 将歌单ID存储在列表项的数据中，以便后续获取
            item.setData(Qt.ItemDataRole.UserRole, pl_id)
            # 如果歌曲已存在，则禁用该列表项的选中状态
            if already:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            # 将列表项添加到列表部件中
            list_widget.addItem(item)
        # 将列表部件添加到布局中
        layout.addWidget(list_widget)
        # 创建按钮框，包含"确定"和"取消"按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        # 连接按钮信号到对话框的接受和拒绝槽函数
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        # 将按钮框添加到布局中
        layout.addWidget(btn_box)
        # 双击列表项时视为确认
        list_widget.itemDoubleClicked.connect(lambda: dialog.accept())
        # 显示对话框并等待用户操作
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # 获取当前选中的列表项
            current = list_widget.currentItem()
            # 如果未选择任何列表项（理论上不太可能，但为安全起见），则返回
            if current is None:
                return
            # 从列表项数据中获取目标歌单ID
            target_id = current.data(Qt.ItemDataRole.UserRole)
            # 将当前歌曲ID添加到目标歌单中
            self.controller.library_service.add_track_ids_to_playlist(target_id, [track.id])
            # 触发音库更新信号
            self.controller.library_changed.emit()
            # 从列表项文本中移除可能的" (已存在)"后缀，以获取纯净的歌单名
            pl_name = current.text().replace(" (已存在)", "")
            # 在状态栏显示添加成功的提示信息
            self.statusBar().showMessage(f"已添加到「{pl_name}」", 2200)
    def _refresh_favorite_button(self, track_id: str | None) -> None:
        if not hasattr(self, "favorite_btn"):
            return
        if not track_id:
            self.favorite_btn.setEnabled(False)
            self.favorite_btn.setIcon(_make_heart_icon(filled=False, color=self._control_icon_color()))
            self.favorite_btn.setToolTip("喜欢当前歌曲")
            return
        self.favorite_btn.setEnabled(True)
        is_fav = self.controller.is_track_favorite(track_id)
        icon = _make_heart_icon(
            filled=is_fav,
            color=self._control_icon_color(),
        )
        self.favorite_btn.setIcon(icon)
        self.favorite_btn.setToolTip("取消喜欢" if is_fav else "喜欢当前歌曲")
    def _toggle_theme(self) -> None:
        """切换界面主题（日间/夜间）。

        该方法切换存储的深色主题标志 `_dark_theme`，并据此更新整个应用程序的样式、
        主题切换按钮的文本、以及用户设置中的主题偏好。切换后会在状态栏短暂显示当前主题。

        参数:
            无。

        返回值:
            无。
        """
        # 将深色主题标志取反，实现切换
        self._dark_theme = not self._dark_theme
        # 根据更新后的标志，应用相应的样式表
        self._apply_theme_stylesheet()
        # 更新主题切换按钮的文本以反映新状态
        self._refresh_theme_button()
        # 设置一个标志，避免在下一次设置加载时覆盖刚刚进行的主题切换
        self._skip_next_settings_reload = True
        # 将用户的新主题偏好持久化保存到设置中
        self.controller.set_theme_preference(self._dark_theme)
        # 根据当前主题类型，在状态栏显示对应的消息（持续1.8秒）
        self.statusBar().showMessage("主题：夜间模式" if self._dark_theme else "主题：日间模式", 1800)
    def _apply_theme_stylesheet(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        app.setStyleSheet(APP_STYLE_DARK if self._dark_theme else APP_STYLE_LIGHT)
        self._refresh_control_icons()
    def _refresh_theme_button(self) -> None:
        """刷新主题切换按钮的图标和提示文本。

        此方法会根据当前的主题模式（深色/浅色），
        更新主题按钮的图标和鼠标悬停提示。

        参数:
            无

        返回:
            None
        """
        # 检查主题按钮是否已经被创建，若不存在则直接返回
        if not hasattr(self, "theme_btn"):
            return
        # 获取当前主题对应的图标颜色
        color = self._control_icon_color()
        # 根据当前是否为暗色主题，设置不同的图标和提示文本
        if self._dark_theme:
            # 当前为暗色主题，按钮图标设为“太阳”，表示点击可切换到日间模式
            self.theme_btn.setIcon(_make_sun_icon(color=color))
            self.theme_btn.setToolTip("切换到日间模式")
        else:
            # 当前为日间主题，按钮图标设为“月亮”，表示点击可切换到夜间模式
            self.theme_btn.setIcon(_make_moon_icon(color=color))
            self.theme_btn.setToolTip("切换到夜间模式")
    def _control_icon_color(self) -> QColor:
        """获取当前主题下控制图标的合适颜色。
        
        根据当前主题模式返回相应的图标颜色：
        - 暗色主题：使用浅色图标（#f4f4f4）
        - 亮色主题：使用深色图标（#1f2521）
        
        Returns:
            适配当前主题的QColor颜色对象
        """
        return QColor("#f4f4f4") if self._dark_theme else QColor("#1f2521")
    def _refresh_control_icons(self) -> None:
        """
        刷新所有控制按钮的图标，根据当前播放状态和模式更新图标显示。
    
        该方法会检查播放控制按钮是否存在，然后根据播放器状态和模式更新各个按钮的图标，
        包括播放/暂停、上一首、下一首、播放模式、收藏按钮、定位按钮等。
    
        Args:
            self: 实例对象本身，通常为播放器界面的主窗口类。
    
        Returns:
            None: 该方法不返回任何值，直接在界面更新图标。
        """
        # 检查播放按钮是否存在，如果不存在则直接返回，避免后续操作出错
        if not hasattr(self, "play_btn"):
            return
        # 获取当前控制图标颜色，确保所有图标使用统一颜色方案
        color = self._control_icon_color()
        # 创建播放模式对应的图标字典，用于快速查找不同播放模式的图标
        self._mode_icons = {
            # 单曲循环模式图标
            PlayMode.SINGLE_LOOP.value: _make_mode_icon(PlayMode.SINGLE_LOOP.value, color=color),
            # 列表循环模式图标
            PlayMode.PLAYLIST_LOOP.value: _make_mode_icon(PlayMode.PLAYLIST_LOOP.value, color=color),
            # 随机播放模式图标
            PlayMode.RANDOM.value: _make_mode_icon(PlayMode.RANDOM.value, color=color),
        }
        # 设置上一首按钮图标
        self.prev_btn.setIcon(_make_media_icon("prev", color=color))
        # 设置下一首按钮图标
        self.next_btn.setIcon(_make_media_icon("next", color=color))
        # 根据播放器是否正在播放，动态设置播放/暂停按钮图标
        # 如果正在播放则显示暂停图标，否则显示播放图标
        self.play_btn.setIcon(_make_media_icon("pause" if self.player.is_playing() else "play", color=color))
        # 设置定位文件夹按钮图标
        self.locate_file_btn.setIcon(_make_folder_icon(color=color))
        # 设置添加到播放列表按钮图标
        self.add_to_playlist_btn.setIcon(_make_plus_icon(color=color))
        # 获取当前播放曲目的ID，用于更新收藏状态
        current_track_id = self.player.current_track_id
        # 根据当前曲目更新收藏按钮状态（已收藏/未收藏）
        self._refresh_favorite_button(current_track_id)
        # 设置紧凑模式切换按钮图标，根据当前紧凑模式状态显示不同图标
        self.compact_btn.setIcon(_make_compact_icon(self._compact_mode, color=color))
        # 设置定位当前播放曲目按钮图标（十字准线图标）
        self.locate_current_btn.setIcon(_make_crosshair_icon(color=color))
        # 刷新富文本标题中的图标（如歌词按钮等）
        self._refresh_rich_title_icons()
        # 刷新主题切换按钮图标
        self._refresh_theme_button()
        # 更新侧边栏切换按钮图标
        self._update_sidebar_toggle_icon()
        # 刷新紧凑模式顶部按钮图标
        self._refresh_compact_top_buttons()
        # 刷新音量控件相关的UI元素
        self._refresh_volume_ui()
        # 根据当前播放模式设置模式按钮图标
        # 从模式图标字典中获取当前模式的图标，如果未找到则默认使用单曲循环模式图标
        self.mode_btn.setIcon(self._mode_icons.get(self.player.mode.value, self._mode_icons[PlayMode.SINGLE_LOOP.value]))
        # 刷新随机播放状态提示（可能显示随机播放的提示信息或图标）
        self._refresh_random_state_hint()
    def _on_random_state_changed(self, seed: int, idx: int) -> None:
        """当随机种子变化时触发，用于更新播放器的随机状态和曲目列表。

        此方法通常作为随机状态变更的回调函数，根据新的随机种子决定是否需要刷新曲目列表。
        只有当播放器处于随机模式且设置要求随机显示顺序时，才会进行后续判断。

        Args:
            seed (int): 新的随机种子。
            idx (int): 索引参数，在当前方法逻辑中未使用。
        """
        self._refresh_random_state_hint()
        # 仅当播放模式为随机且显示顺序设置为随机时，才处理种子变化
        if self.player.mode == PlayMode.RANDOM and str(getattr(self.controller.settings, "random_display_order", "original")) == "random":
            # 检查是否已存在旧种子，并且新种子与旧种子不同，以避免不必要的重载
            if getattr(self, "_last_random_seed", None) is not None and seed != self._last_random_seed:
                self._reload_track_list()
        # 记录当前种子，以便下次比较
        self._last_random_seed = seed
    def _refresh_random_state_hint(self) -> None:
        if not hasattr(self, "random_state_label"):
            return
        if self.player.mode != PlayMode.RANDOM:
            self.random_state_label.setText("")
            self.random_state_label.hide()
            if hasattr(self, "menu_hint_widget"):
                self.menu_hint_widget.updateGeometry()
            return
        self.random_state_label.setText(f"seed:{self.player.random_seed} idx:{self.player.random_index}")
        self.random_state_label.show()
        if hasattr(self, "menu_hint_widget"):
            self.menu_hint_widget.updateGeometry()
        self.menuBar().setCornerWidget(self.menu_hint_widget, Qt.Corner.TopRightCorner)
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
        """
        根据按键调整音量大小。

        参数:
            increase (bool): 为True时增加音量，为False时减少音量。

        返回:
            None
        """
        # 调用播放器的按键音量调整方法，根据increase参数增加或减少音量
        self.player.adjust_gain_by_key(increase)
        # 刷新音量相关的用户界面显示
        self._refresh_volume_ui()
        # 在状态栏显示当前音量百分比，提示信息显示1.5秒（1500毫秒）
        self.statusBar().showMessage(f"音量：{self.player.gain_percent()}%", 1500)
    def _refresh_volume_ui(self) -> None:
        """
        根据当前播放器的音量状态，更新音量UI控件。
    
        功能：
        1. 从播放器获取当前增益百分比和滑块对应的增益百分比。
        2. 更新UI控件（滑块、数值标签、静音按钮图标和提示）。
    
        参数：
        self: 实例对象自身。
    
        返回值：
        None
        """
        # 从播放器获取当前增益百分比和滑块对应的增益百分比
        gain = self.player.gain_percent()
        slider_value = self.player.slider_gain_percent()
    
        # 如果当前增益大于0，则将其记录为“最后的非零增益值”（最小为1）
        if gain > 0:
            self._last_nonzero_gain = max(1, gain)
    
        # 临时阻塞滑块的信号，防止在程序设置值时触发不必要的事件
        self.volume_slider.blockSignals(True)
        # 设置滑块的位置
        self.volume_slider.setValue(slider_value)
        # 取消阻塞信号
        self.volume_slider.blockSignals(False)
    
        # 更新数值标签显示当前的增益百分比
        self.volume_value_label.setText(f"{gain}%")
    
        # 根据增益值判断是否静音（增益 <= 0 视为静音）
        muted = gain <= 0
        # 创建对应静音/非静音状态的音量图标
        icon = _make_volume_icon(muted=muted, color=self._control_icon_color())
        # 设置静音按钮的图标
        self.mute_btn.setIcon(icon)
        # 根据状态设置静音按钮的提示文本
        self.mute_btn.setToolTip("取消静音" if muted else "静音")
    
        # 重新定位数值标签（可能因为文本长度变化需要调整位置）
        self._reposition_volume_value_label()
    def _on_opacity_changed(self, value: int) -> None:
        """
        处理窗口透明度变化事件。

        根据输入的透明度值（0-100的整数）计算最终透明度并更新窗口显示，
        同时在状态栏显示当前透明度百分比信息。

        参数:
            value (int): 用户输入的透明度百分比值，范围0-100。

        返回值:
            None: 此方法不返回任何值。
        """
        # 计算alpha值：将百分比值转换为0.0-1.0的浮点数，并限制在0.35-1.0的安全范围内
        alpha = max(0.35, min(1.0, int(value) / 100.0))
        # 设置窗口透明度
        self.setWindowOpacity(alpha)
        # 在状态栏显示透明度百分比，四舍五入后取整，显示1500毫秒
        self.statusBar().showMessage(f"窗口透明度：{int(round(alpha * 100))}%", 1500)
    def _toggle_compact_lock(self) -> None:
        """切换窗口紧凑模式下的锁定状态。
    
        功能：翻转内部锁定标志，当锁定时清除拖拽偏移量，
             刷新界面按钮，并通过状态栏提示用户当前锁定状态。
        参数：无
        返回值：无
        """
        # 将锁定状态取反：如果原来是锁定则解锁，反之亦然
        self._compact_locked = not self._compact_locked
    
        # 如果当前为锁定状态，则清除拖拽偏移量（因为锁定后不应允许拖动）
        if self._compact_locked:
            self._drag_offset = None
    
        # 根据新的锁定状态刷新紧凑模式顶部的控制按钮
        self._refresh_compact_top_buttons()
    
        # 在状态栏显示当前状态消息，持续2000毫秒
        # 根据锁定状态选择对应的消息文本
        self.statusBar().showMessage("窗口位置已锁定" if self._compact_locked else "窗口位置已解锁", 2000)
    def _toggle_always_on_top(self) -> None:
        """切换当前窗口的置顶状态。
    
        此方法会反转内部的置顶标志，更新窗口属性以使其置顶或取消置顶，
        同时同步更新界面元素（如按钮）的状态，并在状态栏给出提示。
    
        Args:
            无。
        
        Returns:
            None。
        """
        # 切换置顶状态的标志变量
        self._always_on_top = not self._always_on_top
        # 刷新窗口标志以应用置顶设置
        self._refresh_window_flags()
        # 刷新顶部按钮的视觉状态，以反映当前置顶状态
        self._refresh_compact_top_buttons()
        # 在状态栏显示操作反馈消息，持续2秒
        self.statusBar().showMessage("已开启窗口置顶" if self._always_on_top else "已关闭窗口置顶", 2000)
    def _seek_by_seconds(self, delta: float) -> None:
        """根据给定的秒数调整播放进度。
    
        功能：根据 delta 参数的值，快进或后退播放进度。
              调整后会同步更新歌词显示和状态栏消息。
    
        参数：
            delta (float): 要调整的秒数。正值表示快进，负值表示后退。
    
        返回值：
            None
        """
        state = self.player.state_snapshot()  # 获取播放器当前状态的快照
        current = float(state.get("position_sec", 0.0))  # 从状态快照中获取当前播放位置，若无则默认为0.0秒
        duration = max(0.0, float(state.get("duration_sec", 0.0)))  # 获取总时长，确保为非负数
        # 计算目标位置：将当前位置加上 delta，并限制在 [0, duration] 范围内
        target = max(0.0, min(duration, current + float(delta)))
        self.player.seek(target)  # 将播放器跳转到计算出的目标位置
        self._sync_lyrics_with_position(target)  # 同步歌词显示到目标位置
        # 在状态栏显示一条消息，格式化显示目标时间，持续1500毫秒
        self.statusBar().showMessage(f"播放进度：{_format_time(target)}", 1500)
    def _open_current_in_explorer(self) -> None:
        """在系统文件管理器中定位当前播放的文件。
        
        使用Windows资源管理器的"/select"功能高亮显示当前播放的音频文件：
        - 获取当前播放曲目的文件路径
        - 验证文件存在性
        - 使用explorer.exe的/select参数打开并选中该文件
        - 处理可能的异常情况并提供用户反馈
        
        安全性考虑：
        - 保持/select参数和路径分离，避免Unicode或逗号路径的解析问题
        - 使用subprocess.Popen避免阻塞UI线程
        """
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
        """处理播放速度选择变化事件。
        
        当用户选择不同的播放速度时：
        - 从下拉框获取选中的速度值
        - 验证速度值有效性
        - 通知播放器调整播放速度
        - 显示当前播放速度的用户反馈
        
        Args:
            index: 下拉框中选中的项目索引
        """
        rate = self.speed_combo.itemData(index)
        if rate is None:
            return
        self.player.set_playback_rate(float(rate))
        self.statusBar().showMessage(f"播放速度：{float(rate):.2g}x", 2000)
    def _on_playback_rate_changed(self, rate: float) -> None:
        self._sync_speed_combo(rate)
    def _sync_speed_combo(self, rate: float | None = None) -> None:
        """同步速度组合框的选中项到指定或当前播放速率。
    
        此方法根据给定的速率或当前播放器速率，找到组合框中最接近的预设速率并选中。
    
        Args:
            rate (float | None, optional): 目标播放速率。若为 None，则使用当前播放器的播放速率。
    
        Returns:
            None: 此方法无返回值，直接操作UI控件。
        """
        # 如果未提供速率，则使用播放器的当前播放速率；否则将输入值转换为浮点数
        target = self.player.playback_rate() if rate is None else float(rate)
        best_index = 0  # 记录最接近目标速率的项的索引
        best_diff = 999.0  # 初始化一个很大的差值用于比较
        for i in range(self.speed_combo.count()):
            item_rate = float(self.speed_combo.itemData(i))  # 获取组合框中第i项的速率数据
            diff = abs(item_rate - target)  # 计算该项速率与目标速率的绝对差
            if diff < best_diff:  # 如果当前差值小于已记录的最小差值
                best_diff = diff  # 更新最小差值
                best_index = i  # 更新最接近项的索引
        # 暂时阻塞组合框的信号，以避免在设置索引时触发不必要的回调或UI更新
        self.speed_combo.blockSignals(True)
        self.speed_combo.setCurrentIndex(best_index)  # 将组合框的选中项设置为最接近的项
        # 恢复组合框的信号
        self.speed_combo.blockSignals(False)
    def _on_playlist_combo_changed(self, index: int) -> None:
        """
        当播放列表下拉框选择改变时调用的方法。

        功能：根据选择的索引获取播放列表ID，设置当前播放列表，重新加载曲目列表，并在状态栏显示消息。

        参数：
            index (int): 下拉框中选中的项的索引。

        返回值：None
        """
        playlist_id = self.playlist_combo.itemData(index)  # 获取当前选择的播放列表ID
        if not playlist_id:  # 如果播放列表ID为空，则直接返回
            return
        self.player.set_playlist(str(playlist_id))  # 设置播放列表为选中的列表
        self._reload_track_list()  # 重新加载曲目列表
        self.statusBar().showMessage(f"当前歌单：{self.playlist_combo.currentText()}", 2500)  # 在状态栏显示当前播放列表名称，持续2.5秒
    def _on_track_double_clicked(self, item: QListWidgetItem) -> None:
        """
        当轨道项被双击时，播放该轨道。

        参数:
            item (QListWidgetItem): 被双击的轨道项。

        返回值:
            None
        """
        try:
            # 尝试从项数据中获取轨道ID和显示文本
            track_id = item.data(0x0100)
            display_text = self._track_text_of_item(item)
        except RuntimeError:
            # 如果发生RuntimeError，则直接返回
            return
        # 如果轨道ID不存在，则返回
        if not track_id:
            return
        # 计算是否保留随机播放顺序
        preserve_random = (
            self.player.mode == PlayMode.RANDOM
            and str(getattr(self.controller.settings, "random_display_order", "original")) == "random"
        )
        # 调用播放函数播放指定轨道
        self.player.play_track(str(track_id), auto_play=True, manual_select=True, active_request=True, preserve_random=preserve_random)
        # 在状态栏显示播放消息，持续2500毫秒
        self.statusBar().showMessage(f"播放歌曲：{display_text}", 2500)
    def _on_remove_track_clicked(self, track_id: str) -> None:
        """处理从歌单移除音轨的点击事件。

        功能：根据传入的音轨ID，尝试将其从当前播放列表（或默认的“所有歌曲”列表）中移除，并更新UI反馈。
        参数：
            track_id (str): 需要从歌单中移除的音轨的唯一标识ID。
        返回值：
            None
        """
        # 获取当前活动的播放列表ID，如果没有则使用预定义的“所有歌曲”ID
        playlist_id = self.player.current_playlist_id or ALL_SONGS_ID
        try:
            # 尝试通过控制器将指定音轨从播放列表中移除
            self.controller.remove_track_from_playlist(str(playlist_id), str(track_id))
            # 移除成功，在状态栏显示提示信息，持续2.5秒
            self.statusBar().showMessage("已从歌单移除歌曲", 2500)
        except Exception as exc:
            # 捕获并处理移除过程中可能发生的任何异常
            QMessageBox.critical(self, "删除失败", str(exc))
    def _on_queue_changed(self) -> None:
        """当播放队列发生变化时调用的回调方法。
    
        功能：响应播放队列（playlist）的更新事件，用于刷新相关的用户界面元素。
        参数：无。
        返回值：无。
        """
        self._reload_playlist_combo() # 重新加载播放列表组合框的内容，使其与新的队列保持一致。
        self._reload_track_list() # 重新加载曲目列表，以显示更新后的队列内容。
        self._refresh_favorite_button(self.player.current_track_id) # 刷新收藏按钮状态，确保其反映当前播放曲目是否已被收藏。
    def _on_library_changed(self) -> None:
        """当曲库发生变化时调用此方法。
        负责重新加载播放列表、曲目列表，刷新收藏按钮，并在状态栏显示更新消息。

        参数：
            无。

        返回值：
            无。
        """
        self._reload_playlist_combo()  # 重新加载播放列表组合框
        self._reload_track_list()  # 重新加载曲目列表
        self._refresh_favorite_button(self.player.current_track_id)  # 根据当前曲目ID刷新收藏按钮状态
        self.statusBar().showMessage("曲库已更新", 2200)  # 在状态栏显示“曲库已更新”消息，持续2200毫秒
    def _on_favorites_changed(self) -> None:
        self._reload_playlist_combo()
        if self.player.current_playlist_id == FAVORITES_ID:
            self._reload_track_list()
        self._refresh_favorite_button(self.player.current_track_id)

    def _on_settings_changed(self, settings) -> None:
        """
        当设置发生变化时被调用的处理方法。
    
        功能：
        1. 根据新的设置更新播放器的单曲循环和播放列表循环模式。
        2. 检查播放模式是否发生变化，决定是否需要重新加载曲目列表。
        3. 处理主题（深色/浅色）的更新。
        4. 应用窗口大小限制并确保窗口在屏幕内。
    
        参数：
        self: 类实例自身。
        settings: 包含新设置值的对象，预期具有以下属性：
            enable_single_loop_mode: 布尔值，是否启用单曲循环。
            enable_playlist_loop_mode: 布尔值，是否启用播放列表循环。
            dark_theme: 布尔值，是否启用深色主题。
    
        返回值：
        None: 此方法不返回任何值。
        """
        if hasattr(self, "_bind_shortcuts"):
            self._bind_shortcuts()
        # 设置单曲循环模式，将settings中的属性转换为布尔值，并启用或禁用
        self.player.set_single_loop_mode_enabled(bool(getattr(settings, "enable_single_loop_mode", True)))
        # 设置播放列表循环模式，将settings中的属性转换为布尔值，并启用或禁用
        self.player.set_playlist_loop_mode_enabled(bool(getattr(settings, "enable_playlist_loop_mode", False)))
        # 记录更改前的播放模式值，用于后续比较
        old_mode = self.player.mode.value
        # 刷新播放模式顺序（可能是根据当前设置重新排列模式）
        self._refresh_mode_order()
        # 处理播放模式改变事件，传入当前模式值
        self._on_mode_changed(self.player.mode.value)
        # 获取更改后的播放模式值
        new_mode = self.player.mode.value
        # 判断播放模式是否发生变化，以此决定是否需要重新加载曲目
        need_reload = old_mode != new_mode
        # 检查是否设置了跳过下一次设置重新加载的标志（用于避免某些情况下的重复加载）
        if getattr(self, "_skip_next_settings_reload", False):
            # 如果设置了跳过标志，则重置该标志，本次不执行后续的重新加载逻辑
            self._skip_next_settings_reload = False
        else:
            # 获取深色主题设置，如果未提供则使用当前主题设置
            dark = bool(getattr(settings, "dark_theme", self._dark_theme))
            # 如果主题设置发生了变化
            if dark != self._dark_theme:
                # 更新当前主题记录
                self._dark_theme = dark
                # 应用新的主题样式表
                self._apply_theme_stylesheet()
                # 刷新主题按钮的显示状态（如图标或文字）
                self._refresh_theme_button()
            else:
                # 如果主题未变化，但可能因其他原因需要重新加载，此处将need_reload设为True
                # 注意：原逻辑中，如果主题未变化，则将need_reload强制设为True，这可能是一个特定行为
                need_reload = True
        # 如果需要重新加载（播放模式变化或主题未变化但其他条件满足），则重新加载曲目列表
        if need_reload:
            self._reload_track_list()
        # 应用窗口大小限制（可能是最小最大尺寸）
        self._apply_window_size_limits()
        # 确保窗口当前位于屏幕可视区域内
        self._ensure_window_inside_screen()
    def _on_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 7000)
    def _on_runtime_status_changed(self, listening: bool, host: str, port: int) -> None:
        """当运行时状态改变时，更新状态栏消息。

        当控制接口的监听状态发生变化时，此方法被调用，用于在状态栏显示相应的状态信息。

        Args:
            listening (bool): 表示是否正在监听。如果为True，则表示正在监听；如果为False，则表示已停止。
            host (str): 主机地址。
            port (int): 端口号。

        Returns:
            None: 此方法不返回任何值。
        """
        if listening:  # 如果正在监听
            self.statusBar().showMessage(f"控制接口监听中: {host}:{port}", 5000)  # 在状态栏显示监听状态，消息持续5秒
        else:  # 如果已停止监听
            self.statusBar().showMessage(f"控制接口已停止: {host}:{port}", 5000)  # 在状态栏显示停止状态，消息持续5秒
    def _menu_import_folder(self) -> None:
        """从文件夹导入音乐文件到当前播放列表。

        弹出文件夹选择对话框，用户选择后，调用控制器的导入方法，并实时更新状态栏进度。
        导入完成后刷新播放列表。

        Args:
            self: 实例自身

        Returns:
            None
        """
        # 弹出文件夹选择对话框，获取用户选择的路径
        folder = QFileDialog.getExistingDirectory(self, "导入音乐文件夹")
        # 如果用户取消选择（路径为空），则直接返回，不做任何操作
        if not folder:
            return
        # 在状态栏显示开始导入的提示信息，持续4秒
        self.statusBar().showMessage("开始导入，请稍候…", 4000)

        # 定义进度回调函数，供控制器在导入过程中调用以更新进度
        def _progress(done: int, total: int, current: str) -> None:
            # 如果总进度无效（小于等于0），则跳过更新
            if total <= 0:
                return
            # 获取当前正在处理的文件名，若无则显示“完成”
            name = Path(current).name if current else "完成"
            # 在状态栏显示格式化的导入进度信息，持续6秒
            self.statusBar().showMessage(f"导入进度：{done}/{total}  {name}", 6000)
            # 强制处理界面事件（如进度更新），防止界面卡顿
            QCoreApplication.processEvents()

        try:
            # 调用控制器方法执行实际的文件夹导入操作，传入文件夹路径、播放列表ID（None表示当前）和进度回调
            count = self.controller.import_folder(Path(folder), playlist_id=None, progress_callback=_progress)
        except Exception as exc:
            # 如果导入过程中发生任何异常，弹出错误提示框并显示异常信息，然后返回
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        # 导入成功后，在状态栏显示完成信息及导入的文件总数，持续5秒
        self.statusBar().showMessage(f"导入完成，共 {count} 首", 5000)
        # 重新加载播放列表视图，以反映新导入的文件
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
        """
        功能：打开文件对话框选择音频文件，并尝试播放。
        参数：无（self是实例引用）
        返回值：无
        """
        # 调用文件对话框获取选择的音频文件路径
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "播放文件",
            "",
            "音频文件 (*.mp3 *.flac *.wav *.m4a *.aac *.ogg *.opus *.wma)",
        )
        # 如果没有选择文件，直接返回
        if not file_path:
            return
        # 尝试播放选择的音频文件，active_request表示主动请求播放
        ok = self.player.play_file(Path(file_path), active_request=True)
        # 根据播放结果显示状态消息
        if ok:
            self.statusBar().showMessage("已开始播放", 3000)
            self._reload_track_list()  # 重新加载曲目列表
        else:
            self.statusBar().showMessage("播放失败，请查看日志", 5000)
    def _open_playlist_dialog(self) -> None:
        self.statusBar().showMessage("打开歌单管理", 2000)
        dlg = PlaylistDialog(self.controller, self)
        dlg.exec()
        self.statusBar().showMessage("歌单管理已更新", 2500)

    def _copy_current_song_info(self) -> None:
        if not bool(getattr(self.controller.settings, "copy_song_info_enabled", True)):
            self.statusBar().showMessage("歌曲信息复制功能已关闭", 2500)
            return
        if self.player.current_track() is None:
            self.statusBar().showMessage("当前没有可复制的歌曲信息", 2500)
            return
        text = "\n".join(
            (
                f"歌名: {self._current_track_title}",
                f"歌手: {self._current_track_artist} | 专辑: {self._current_track_album}",
                f"路径: {self._current_track_path}",
            )
        )
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("已复制歌曲信息", 2500)

    def _copy_song_info_item(self, item_name: str) -> None:
        if not bool(getattr(self.controller.settings, "copy_song_info_enabled", True)):
            return
        if self.player.current_track() is None:
            self.statusBar().showMessage("当前没有可复制的歌曲信息", 2500)
            return
        values = {
            "title": ("歌名", self._current_track_title),
            "artist": ("歌手", self._current_track_artist),
            "album": ("专辑", self._current_track_album),
            "path": ("路径", self._current_track_path),
        }
        label, value = values.get(item_name, ("歌曲信息", ""))
        if not value:
            return
        QApplication.clipboard().setText(value)
        self.statusBar().showMessage(f"已复制歌曲信息（{label}）", 2500)

    def _open_settings_dialog(self) -> None:
        """打开设置对话框，允许用户修改设置。如果用户接受更改，则更新设置并显示保存消息；否则，直接返回。
        参数：
            self: 当前实例（无其他参数）。
        返回值：
            None（无返回值）。
        """
        self.statusBar().showMessage("打开设置", 1500)  # 在状态栏显示“打开设置”消息，持续1500毫秒
        dlg = SettingsDialog(self.controller.settings, self)  # 创建设置对话框实例，传入当前设置和父窗口
        if dlg.exec() != dlg.DialogCode.Accepted:  # 如果对话框未被接受（例如用户取消）
            return  # 直接返回，不执行后续操作

        new_settings = dlg.output_settings()  # 从对话框获取新的设置
        self.controller.update_settings(new_settings)  # 更新控制器中的设置
        if hasattr(self, "action_copy_song_info"):
            self.action_copy_song_info.setEnabled(bool(new_settings.copy_song_info_enabled))
        if new_settings.logging_enabled and self.controller.log_file_path is not None:  # 检查日志是否启用且日志文件路径存在
            tip = f"设置已保存，日志路径: {self.controller.log_file_path}"  # 构造包含日志路径的提示消息
        else:
            tip = "设置已保存"  # 否则，使用简单提示消息
        self.statusBar().showMessage(tip, 6000)  # 在状态栏显示提示消息，持续6000毫秒

    def _open_shortcut_settings_dialog(self) -> None:
        dialog = ShortcutSettingsDialog(self.controller.settings, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.controller.update_settings(dialog.apply_to_settings())
        self.statusBar().showMessage("按键设置已保存", 3000)
