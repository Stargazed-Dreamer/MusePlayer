from __future__ import annotations

"""应用控制器。

该层是 UI 与服务层之间的编排中枢，负责：
1. 初始化与装配（曲库、播放、设置、控制接口）
2. 生命周期管理（启动恢复、定时保存、关闭保存）
3. 对外命令分发（本地 UI 与运行时控制协议共用）
"""

from datetime import datetime
import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QMediaDevices

from app.models import LibraryStore, SessionState, SessionStore, Settings, SettingsStore
from app.runtime import ControlServer
from app.services.library_service import LibraryService
from app.services.metadata_service import MetadataService
from app.services.playback_stats_service import PlaybackStatsService
from app.services.player_service import PlayMode, PlayerService
from app.utils import configure_logging, get_logger


class AppController(QObject):
    library_changed = Signal()
    settings_changed = Signal(object)
    runtime_status_changed = Signal(bool, str, int)
    message = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, project_root: Path, parent: QObject | None = None):
        """应用控制器构造函数。
        
        初始化所有子系统：元数据服务、数据存储、库管理、播放统计、播放器、控制服务等。
        建立服务间的依赖关系，配置事件连接，启动定时保存机制。
        
        Args:
            project_root: 项目根目录路径
            parent: Qt父对象
        """
        super().__init__(parent)
        # 初始化目录结构
        self.project_root = Path(project_root).resolve()
        self.data_dir = self.project_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 创建并装配各服务层
        self.metadata_service = MetadataService()

        # 数据持久化存储层
        self.library_store = LibraryStore(self.data_dir)
        self.session_store = SessionStore(self.data_dir)
        self.settings_store = SettingsStore(self.data_dir)

        # 加载设置并配置日志系统
        self.settings = self.settings_store.load()
        self.log_file_path: Path | None = None
        self.log_file_path = configure_logging(self.data_dir, self.settings.logging_enabled)
        self.logger = get_logger("app")
        self.logger.info("MusePlayer 启动")
        self._runtime_error_file = self.data_dir / "runtime_errors.log"

        # 初始化库服务并加载现有数据
        self.library_service = LibraryService(self.library_store, self.metadata_service)
        self.library_service.set_data_maintenance_logging_enabled(
            bool(getattr(self.settings, "data_maintenance_logging_enabled", True))
        )
        self.library_service.load()

        # 创建播放统计服务和播放器服务
        self.playback_stats_service = PlaybackStatsService(self.data_dir)
        self.player_service = PlayerService(
            self.library_service,
            playback_stats_service=self.playback_stats_service,
            collect_stats_getter=lambda: bool(self.settings.collect_playback_data),
            gain_boost_getter=lambda: float(self.settings.global_gain_boost),
            read_strategy_getter=lambda: str(self.settings.read_strategy),
        )
        self.player_service.set_playlist_loop_sort_getter(lambda: str(getattr(self.settings, "playlist_loop_sort", "default")))
        self.player_service.set_prefer_playlist_order_getter(lambda: bool(getattr(self.settings, "prefer_playlist_order", False)))
        self.player_service.set_random_display_order_getter(lambda: str(getattr(self.settings, "random_display_order", "original")))
        # 配置播放器功能和连接错误处理信号
        self.player_service.set_single_loop_mode_enabled(self.settings.enable_single_loop_mode)
        self.player_service.set_playlist_loop_mode_enabled(self.settings.enable_playlist_loop_mode)
        self.player_service.set_output_device(getattr(self.settings, "output_device", ""))
        self.player_service.error_occurred.connect(self.error_occurred)
        self.error_occurred.connect(self._record_runtime_error)

        # 初始化运行时控制服务器
        self.control_server = ControlServer(self.dispatch_command)
        self.control_server.error_occurred.connect(self.error_occurred)
        self.control_server.listening_changed.connect(self.runtime_status_changed)

        # 配置会话自动保存定时器（默认30秒间隔）
        self._session_save_timer = QTimer(self)
        self._session_save_timer.timeout.connect(self.save_session)
        self._apply_save_timer_settings()

        self._audio_change_timer = QTimer(self)
        self._audio_change_timer.setSingleShot(True)
        self._audio_change_timer.setInterval(150)
        self._audio_change_timer.timeout.connect(self._handle_audio_output_changed)
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)

        if self.settings.auto_restore_session:
            with self.player_service.suspend_stats_collection():
                self.player_service.restore_session(self.session_store.load())
        else:
            self.player_service.set_volume(1.0)

        if self.settings.control_interface_enabled:
            self.start_runtime_server()
        else:
            self.runtime_status_changed.emit(False, self.settings.control_host, self.settings.control_port)

    def shutdown(self) -> None:
        """应用程序关闭流程。
        
        按正确顺序关闭所有服务：暂停统计收集、保存会话状态、
        持久化统计数据、保存库状态、保存设置、停止服务。
        """
        self.logger.info("准备关闭应用")
        # 关闭阶段是系统流程，不应产生任何新增统计计数
        with self.player_service.suspend_stats_collection():
            self.save_session()
            self.playback_stats_service.save_if_dirty()
            self.library_service.save()
            self.settings_store.save(self.settings)
            self.control_server.stop()
            self.player_service.close()

    def save_session(self) -> None:
        """保存当前会话状态。
        
        包括播放器状态、歌单信息、播放位置等所有临时状态。
        同时执行播放统计的同步和保存，确保数据一致性。
        """
        # 会话保存同时承担"统计落盘同步"职责，确保 DB 歌单内统计及时更新
        state = self.player_service.export_session()
        self.session_store.save(state)
        self.playback_stats_service.save_if_dirty()

    def save_stats_now(self) -> None:
        """立即保存统计数据到磁盘。
        
        触发一次完整的会话保存，包括播放器状态和统计信息。
        """
        self.save_session()

    def _apply_save_timer_settings(self) -> None:
        enabled = bool(self.settings.timed_save_enabled)
        minutes = max(1, min(1440, int(self.settings.timed_save_minutes)))
        if enabled:
            self._session_save_timer.setInterval(minutes * 60 * 1000)
            self._session_save_timer.start()
        else:
            self._session_save_timer.stop()

    def start_runtime_server(self) -> bool:
        """启动运行时控制服务器。
        
        根据设置配置启动或停止控制接口。
        
        Returns:
            bool: 是否成功启动或被正确禁用
        """
        if not self.settings.control_interface_enabled:
            self.control_server.stop()
            self.logger.info("控制接口已禁用")
            self.runtime_status_changed.emit(False, self.settings.control_host, self.settings.control_port)
            return True
        ok = self.control_server.start(self.settings.control_host, self.settings.control_port)
        if ok:
            self.logger.info("控制接口监听: %s:%s", self.settings.control_host, self.settings.control_port)
        else:
            self.logger.error("控制接口启动失败")
        return ok

    def restart_runtime_server(self) -> bool:
        """重启运行时控制服务器。
        
        先停止当前服务器然后根据最新设置重新启动。
        
        Returns:
            bool: 是否成功重启
        """
        if not self.settings.control_interface_enabled:
            self.control_server.stop()
            self.runtime_status_changed.emit(False, self.settings.control_host, self.settings.control_port)
            return True
        return self.start_runtime_server()

    def update_settings(self, settings: Settings) -> bool:
        """更新应用设置并应用所有相关变更。
        
        保存新设置、更新播放器配置、重启控制服务器等。
        
        Args:
            settings: 新的设置对象
            
        Returns:
            bool: 所有更新操作是否成功
        """
        self.settings = settings
        self.settings_store.save(settings)
        self.library_service.set_data_maintenance_logging_enabled(
            bool(getattr(self.settings, "data_maintenance_logging_enabled", True))
        )
        self.player_service.set_single_loop_mode_enabled(self.settings.enable_single_loop_mode)
        self.player_service.set_playlist_loop_mode_enabled(self.settings.enable_playlist_loop_mode)
        self.player_service.refresh_output_gain()
        self.player_service.set_output_device(getattr(self.settings, "output_device", ""))
        self._apply_save_timer_settings()
        self.settings_changed.emit(settings)

        self.log_file_path = configure_logging(self.data_dir, self.settings.logging_enabled, self.log_file_path)
        self.logger = get_logger("app")
        if self.settings.logging_enabled:
            self.logger.info("设置已更新")

        ok = self.restart_runtime_server()
        return ok

    def set_theme_preference(self, dark_theme: bool) -> None:
        """设置主题偏好（暗色/亮色）。
        
        Args:
            dark_theme: True为暗色主题，False为亮色主题
        """
        value = bool(dark_theme)
        if bool(self.settings.dark_theme) == value:
            return
        self.settings.dark_theme = value
        self.settings_store.save(self.settings)
        self.settings_changed.emit(self.settings)

    def persist_window_geometry(self, *, x: int, y: int, width: int, height: int) -> None:
        """持久化窗口几何信息。
        
        保存窗口位置和大小，用于应用重启后恢复界面布局。
        
        Args:
            x: 窗口X坐标
            y: 窗口Y坐标  
            width: 窗口宽度
            height: 窗口高度
        """
        self.settings.window_x = int(x)
        self.settings.window_y = int(y)
        self.settings.window_width = max(0, int(width))
        self.settings.window_height = max(0, int(height))
        self.settings_store.save(self.settings)

    def get_current_lyrics(self) -> str:
        track = self.player_service.current_track()
        if track is None:
            return ""
        ext_lyrics = str(getattr(track, "source_lyrics_path", "") or "").strip()
        if ext_lyrics:
            lyric_path = Path(ext_lyrics)
            if lyric_path.exists() and lyric_path.is_file():
                try:
                    return lyric_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    try:
                        return lyric_path.read_text(encoding="gbk")
                    except Exception:
                        pass
                except Exception:
                    pass
        return self.metadata_service.read_lyrics(Path(track.path))

    def get_current_lyrics_extra_files(self) -> list[tuple[str, str]]:
        track = self.player_service.current_track()
        if track is None:
            return []
        extra_raw = str(getattr(track, "extra_lyrics_paths", "") or "").strip()
        if not extra_raw:
            return []
        result: list[tuple[str, str]] = []
        for p in extra_raw.split("|"):
            p = p.strip()
            if not p:
                continue
            lyric_path = Path(p)
            if not lyric_path.exists() or not lyric_path.is_file():
                continue
            try:
                content = lyric_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    content = lyric_path.read_text(encoding="gbk")
                except Exception:
                    continue
            except Exception:
                continue
            result.append((content, lyric_path.name))
        return result

    def get_current_lyrics_filename(self) -> str:
        track = self.player_service.current_track()
        if track is None:
            return ""
        ext_lyrics = str(getattr(track, "source_lyrics_path", "") or "").strip()
        if ext_lyrics:
            return Path(ext_lyrics).name
        return ""

    def get_current_cover(self) -> bytes | None:
        """获取当前播放曲目的封面图像。
        
        从音频文件内嵌的元数据中提取封面图像数据。
        
        Returns:
            bytes: 封面图像的二进制数据，如果没有则返回None
        """
        track = self.player_service.current_track()
        if track is None:
            return None
        return self.metadata_service.read_cover_bytes(Path(track.path))

    def import_folder(
        self,
        folder: Path,
        playlist_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> int:
        """导入文件夹中的音频文件。
        
        递归扫描文件夹，导入所有支持的音频格式文件到曲库。
        
        Args:
            folder: 要导入的文件夹路径
            playlist_id: 目标歌单ID，None表示导入到默认歌单
            progress_callback: 进度回调函数
            
        Returns:
            int: 成功导入的文件数量
        """
        imported = self.library_service.import_folder(
            folder=folder,
            playlist_id=playlist_id,
            recursive=True,
            progress_callback=progress_callback,
        )
        self.library_changed.emit()
        return len(imported)

    def import_muse_playlist(self, file_path: Path) -> str:
        playlist = self.library_service.import_muse_playlist(file_path)
        self.library_changed.emit()
        return playlist.id

    def import_files(self, files: list[Path], playlist_id: str | None = None) -> int:
        imported = 0
        for path in files:
            try:
                self.library_service.import_file(path, playlist_id=playlist_id, skip_save=True)
                imported += 1
            except Exception:
                continue
        if imported > 0:
            self.library_service.save()
            self.library_changed.emit()
        return imported

    def import_muse_playlist_data(self, payload: dict | str, source_hint: str = "runtime_payload") -> str:
        data: dict
        if isinstance(payload, str):
            data = json.loads(payload)
        elif isinstance(payload, dict):
            data = payload
        else:
            raise ValueError("playlist payload must be dict or json string")
        playlist = self.library_service.import_muse_playlist_payload(data, source_hint=source_hint)
        self.library_changed.emit()
        return playlist.id

    def create_playlist(self, name: str) -> str:
        playlist = self.library_service.create_playlist(name)
        self.library_changed.emit()
        return playlist.id

    def toggle_track_favorite(self, track_id: str) -> bool:
        state = self.library_service.toggle_favorite(track_id)
        if state:
            self.playback_stats_service.reset_early_skip_count(track_id)
        self.library_changed.emit()
        return state

    def is_track_favorite(self, track_id: str | None) -> bool:
        return self.library_service.is_favorite(track_id)

    def export_playlist(self, playlist_id: str, out_dir: Path) -> Path:
        file_path = self.library_service.export_playlist_file(
            playlist_id,
            out_dir,
            self.playback_stats_service,
        )
        return file_path

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        self.library_service.rename_playlist(playlist_id, name)
        self.library_changed.emit()

    def copy_playlist(self, source_playlist_id: str, new_name: str | None = None) -> str | None:
        playlist = self.library_service.copy_playlist(source_playlist_id, new_name=new_name)
        if playlist is None:
            return None
        self.library_changed.emit()
        return playlist.id

    def merge_playlist(self, source_playlist_id: str, target_playlist_id: str) -> int:
        merged = self.library_service.merge_playlist(source_playlist_id, target_playlist_id)
        if merged > 0:
            self.library_changed.emit()
        return merged

    def delete_playlist(self, playlist_id: str) -> None:
        track_ids_before = set(self.library_service.tracks.keys())
        self.library_service.delete_playlist(playlist_id)
        removed_ids = track_ids_before - set(self.library_service.tracks.keys())
        for track_id in removed_ids:
            self.playback_stats_service.remove_track(track_id)
        if self.player_service.current_playlist_id == playlist_id:
            self.player_service.set_playlist(self.library_service.active_playlist_id)
        self.library_changed.emit()

    def remove_track_from_playlist(self, playlist_id: str, track_id: str) -> None:
        """从歌单中移除指定曲目，智能处理播放状态切换。
        
        如果移除的是当前播放曲目，会自动选择相邻曲目继续播放
        或将播放状态切换到合适状态，确保用户体验的连续性。
        
        Args:
            playlist_id: 目标歌单ID
            track_id: 要移除的曲目ID
        """
        # 记录当前播放状态用于后续恢复
        was_playing = self.player_service.is_playing()
        active_playlist = self.player_service.current_playlist_id
        current_track_id = self.player_service.current_track_id
        # 判断是否正在从当前活动歌单中移除当前播放的曲目
        removed_current_from_active = active_playlist == playlist_id and current_track_id == track_id

        # 如果需要移除当前播放的曲目，寻找合适的接替候选人
        next_candidate_id: str | None = None
        prev_candidate_id: str | None = None
        if removed_current_from_active:
            active_tracks = self.library_service.get_playlist_tracks(playlist_id)
            ordered_ids = [t.id for t in active_tracks]
            if track_id in ordered_ids:
                idx = ordered_ids.index(track_id)
                # 优先选择下一曲，其次选择上一曲
                if idx + 1 < len(ordered_ids):
                    next_candidate_id = ordered_ids[idx + 1]
                if idx - 1 >= 0:
                    prev_candidate_id = ordered_ids[idx - 1]

        # 执行实际的移除操作
        removed_ids = self.library_service.remove_track_from_playlist(playlist_id, track_id)
        # 清理被移除曲目的统计数据
        for removed_id in removed_ids:
            self.playback_stats_service.remove_track(removed_id)
            
        # 处理播放状态转换逻辑
        if self.player_service.current_playlist_id == playlist_id:
            if removed_current_from_active:
                self.player_service.pause()  # 暂时暂停保证状态一致性
            # 重新加载歌单状态
            self.player_service.set_playlist(self.player_service.current_playlist_id)

            # 选择接替的曲目继续播放
            if removed_current_from_active:
                if self.player_service.mode == PlayMode.RANDOM:
                    target_id = self.player_service.reseed_random_after_current_track_removed()
                    if target_id is not None:
                        self.player_service.play_track(
                            target_id,
                            auto_play=was_playing,
                            start_sec=0.0,
                            manual_select=False,
                            active_request=False,
                        )
                    else:
                        self.player_service.pause()
                    self.library_changed.emit()
                    return
                playlist_track_ids = [t.id for t in self.player_service.playlist_tracks()]
                selected_candidate = None
                # 优先选择下一曲
                if next_candidate_id and next_candidate_id in playlist_track_ids:
                    selected_candidate = next_candidate_id
                elif prev_candidate_id and prev_candidate_id in playlist_track_ids:
                    selected_candidate = prev_candidate_id

                if selected_candidate is not None:
                    self.player_service.play_track(
                        selected_candidate,
                        auto_play=was_playing,
                        start_sec=0.0,
                        manual_select=False,
                        active_request=False,
                    )
                else:
                    self.player_service.pause()
                    
        # 安全清理：如果当前播放曲目已经不存在于库中，需要重置状态
        if self.player_service.current_track_id and self.player_service.current_track_id not in self.library_service.tracks:
            self.player_service.pause()
            self.player_service.set_playlist(self.player_service.current_playlist_id)
            
        self.library_changed.emit()

    def dispatch_command(self, payload: dict) -> dict:
        """运行时控制接口命令分发入口。
        
        处理来自外部控制的命令请求，支持播放控制、库管理、导入等功能。
        这是JSON-RPC风格的API接口，返回标准化的响应格式。
        
        Args:
            payload: 包含cmd字段和其他参数的命令数据
            
        Returns:
            dict: 标准化的响应，包含ok字段和相应数据或错误信息
        """
        cmd = str(payload.get("cmd", "")).strip().lower()
        if cmd:
            self.logger.info("收到控制命令: %s", cmd)
        if not cmd:
            return {"ok": False, "error": "missing cmd"}

        if cmd == "ping":
            return {"ok": True, "result": "pong"}

        if cmd == "state":
            return {
                "ok": True,
                "result": {
                    "player": self.player_service.state_snapshot(),
                    "playlists": [
                        {"id": pl.id, "name": pl.name, "count": len(pl.track_ids)}
                        for pl in self.library_service.list_playlists()
                    ],
                },
            }

        if cmd == "play":
            self.player_service.play()
            return {"ok": True}

        if cmd == "pause":
            self.player_service.pause()
            return {"ok": True}

        if cmd == "toggle":
            self.player_service.toggle_play_pause()
            return {"ok": True}

        if cmd == "seek":
            position = float(payload.get("position_sec", payload.get("position", 0.0)))
            self.player_service.seek(position)
            return {"ok": True}

        if cmd == "set_volume":
            volume = float(payload.get("volume", 1.0))
            self.player_service.set_volume(volume)
            return {"ok": True, "result": {"volume": self.player_service.volume()}}

        if cmd == "next":
            ok = self.player_service.next_track(user_triggered=True)
            return {"ok": ok}

        if cmd == "previous":
            ok = self.player_service.previous_track()
            return {"ok": ok}

        if cmd == "set_mode":
            mode = str(payload.get("mode", "single_loop"))
            self.player_service.set_mode(mode)
            return {"ok": True, "result": {"mode": self.player_service.mode.value}}

        if cmd == "import_folder":
            path = payload.get("path")
            if not path:
                return {"ok": False, "error": "missing path"}
            playlist_id = payload.get("playlist_id")
            count = self.import_folder(Path(path), playlist_id=playlist_id)
            return {"ok": True, "result": {"imported": count}}

        if cmd == "import_playlist_file":
            path = payload.get("path")
            if not path:
                return {"ok": False, "error": "missing path"}
            playlist_id = self.import_muse_playlist(Path(path))
            return {"ok": True, "result": {"playlist_id": playlist_id}}

        if cmd == "import_playlist_data":
            raw = payload.get("playlist")
            if raw is None:
                raw = payload.get("data")
            if raw is None:
                raw = payload.get("content")
            if raw is None:
                return {"ok": False, "error": "missing playlist data"}
            source_hint = str(payload.get("source_hint", "runtime_payload"))
            playlist_id = self.import_muse_playlist_data(raw, source_hint=source_hint)
            return {"ok": True, "result": {"playlist_id": playlist_id}}

        if cmd == "play_file":
            path = payload.get("path")
            if not path:
                return {"ok": False, "error": "missing path"}
            ok = self.player_service.play_file(Path(path), active_request=True)
            self.library_changed.emit()
            return {"ok": ok}

        if cmd == "load_playlist":
            playlist_id = payload.get("playlist_id")
            if not playlist_id:
                return {"ok": False, "error": "missing playlist_id"}
            self.player_service.set_playlist(str(playlist_id))
            self.library_changed.emit()
            return {"ok": True}

        if cmd == "play_playlist":
            playlist_id = payload.get("playlist_id")
            if not playlist_id:
                return {"ok": False, "error": "missing playlist_id"}
            self.player_service.set_playlist(str(playlist_id))
            track_id = payload.get("track_id")
            if track_id:
                ok = self.player_service.play_track(
                    str(track_id),
                    auto_play=True,
                    manual_select=True,
                    active_request=False,
                )
                self.library_changed.emit()
                return {"ok": ok}
            self.player_service.play()
            self.library_changed.emit()
            return {"ok": True}

        if cmd == "play_track":
            track_id = payload.get("track_id")
            if not track_id:
                return {"ok": False, "error": "missing track_id"}
            ok = self.player_service.play_track(
                str(track_id),
                auto_play=True,
                manual_select=True,
                active_request=False,
            )
            return {"ok": ok}

        if cmd == "create_playlist":
            name = str(payload.get("name", "新建歌单"))
            playlist_id = self.create_playlist(name)
            return {"ok": True, "result": {"playlist_id": playlist_id}}

        if cmd == "current_track":
            track = self.player_service.current_track()
            if track is None:
                return {"ok": True, "result": None}
            result = track.to_dict()
            stats = self.playback_stats_service.export_stats_for_track(track.id)
            if stats:
                result["stats"] = stats
            is_fav = self.library_service.is_favorite(track.id)
            result["is_favorite"] = is_fav
            return {"ok": True, "result": result}

        if cmd == "current_playlist":
            playlist_id = self.player_service.current_playlist_id
            if not playlist_id:
                return {"ok": True, "result": None}
            playlist = self.library_service.playlists.get(playlist_id)
            if playlist is None:
                return {"ok": True, "result": None}
            result = playlist.to_dict()
            tracks_info = []
            for tid in playlist.track_ids:
                t = self.library_service.tracks.get(tid)
                if t is not None:
                    tracks_info.append({"id": t.id, "title": t.title, "artist": t.artist, "duration_sec": float(t.duration_sec)})
            result["tracks"] = tracks_info
            return {"ok": True, "result": result}

        if cmd == "get_playlist":
            playlist_id = payload.get("playlist_id")
            if not playlist_id:
                return {"ok": False, "error": "missing playlist_id"}
            playlist = self.library_service.playlists.get(str(playlist_id))
            if playlist is None:
                return {"ok": True, "result": None}
            result = playlist.to_dict()
            tracks_info = []
            for tid in playlist.track_ids:
                t = self.library_service.tracks.get(tid)
                if t is not None:
                    info = {"id": t.id, "title": t.title, "artist": t.artist, "duration_sec": float(t.duration_sec)}
                    if t.source_sha256:
                        info["source_sha256"] = t.source_sha256
                    tracks_info.append(info)
            result["tracks"] = tracks_info
            return {"ok": True, "result": result}

        if cmd == "add_track_to_playlist":
            track_id = payload.get("track_id")
            playlist_id = payload.get("playlist_id")
            if not track_id or not playlist_id:
                return {"ok": False, "error": "missing track_id or playlist_id"}
            self.library_service.add_track_ids_to_playlist(str(playlist_id), [str(track_id)])
            self.library_changed.emit()
            return {"ok": True}

        if cmd == "remove_track_from_playlist":
            track_id = payload.get("track_id")
            playlist_id = payload.get("playlist_id")
            if not track_id or not playlist_id:
                return {"ok": False, "error": "missing track_id or playlist_id"}
            removed_globally = self.library_service.remove_track_from_playlist(str(playlist_id), str(track_id))
            self.library_changed.emit()
            return {"ok": True, "result": {"removed_globally": list(removed_globally)}}

        return {"ok": False, "error": f"unknown cmd: {cmd}"}

    def export_session_for_ui(self) -> SessionState:
        return self.player_service.export_session()

    def _on_audio_outputs_changed(self) -> None:
        # 事件驱动，做短暂去抖后重绑输出设备，避免常驻轮询。
        self._audio_change_timer.start()

    def _handle_audio_output_changed(self) -> None:
        ok = self.player_service.rebind_output_device()
        if ok:
            self.message.emit("音频输出设备已切换")

    def _record_runtime_error(self, message: str) -> None:
        text = str(message).strip()
        if not text:
            return

        try:
            self.logger.error(text)
        except Exception:
            pass

        try:
            self._runtime_error_file.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._runtime_error_file.open("a", encoding="utf-8") as f:
                f.write(f"{stamp} {text}\n")
        except Exception:
            pass
