"""应用控制器。

该层是 UI 与服务层之间的编排中枢，负责：
1. 初始化与装配（曲库、播放、设置、控制接口）
2. 生命周期管理（启动恢复、定时保存、关闭保存）
3. 对外命令分发（本地 UI 与运行时控制协议共用）
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import QMediaDevices

from app.models import LibraryStore, Playlist, SessionState, SessionStore, Settings, SettingsStore, Track
from app.runtime import ControlServer
from app.services.library_service import ALL_SONGS_ID, LibraryService
from app.services.metadata_service import MetadataService
from app.services.playback_stats_service import PlaybackStatsService
from app.services.player_service import PlayerService, PlayMode
from app.utils import configure_logging, get_logger


class AppController(QObject):
    library_changed = Signal()
    favorites_changed = Signal()
    settings_changed = Signal(object)
    runtime_status_changed = Signal(bool, str, int)
    message = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, project_root: Path, parent: QObject | None = None):
        """应用控制器构造函数（轻量模式）。

        仅初始化设置和存储层，不加载库和播放器。
        调用 initialize_services() 完成完整初始化。
        """
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.data_dir = self.project_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

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

        # 占位属性，initialize_services() 中赋值
        self.metadata_service = None
        self.library_service = None
        self.playback_stats_service = None
        self.player_service = None
        self.control_server = None
        self._session_restored = False
        self._services_initialized = False
        self._library_loaded = False

    def initialize_services(self) -> None:
        """初始化库服务、播放器、控制服务器等重服务。

        在窗口显示后调用，避免阻塞窗口出现。
        分两步：先初始化播放器（~0.3s，可立即播放），再加载库（~0.9s）。
        """
        if self._services_initialized:
            return
        self._services_initialized = True
        import time as _time

        _t0 = _time.perf_counter()

        self.metadata_service = MetadataService()

        # 第一步：创建库服务（不加载）和播放器
        self.library_service = LibraryService(self.library_store, self.metadata_service)
        self.library_service.set_data_maintenance_logging_enabled(
            bool(getattr(self.settings, "data_maintenance_logging_enabled", True))
        )

        self.playback_stats_service = PlaybackStatsService(self.data_dir)
        self.player_service = PlayerService(
            self.library_service,
            playback_stats_service=self.playback_stats_service,
            collect_stats_getter=lambda: bool(self.settings.collect_playback_data),
            gain_boost_getter=lambda: float(self.settings.global_gain_boost),
            read_strategy_getter=lambda: str(self.settings.read_strategy),
        )
        self.player_service.set_playlist_loop_sort_getter(
            lambda: str(getattr(self.settings, "playlist_loop_sort", "default"))
        )
        self.player_service.set_prefer_playlist_order_getter(
            lambda: bool(getattr(self.settings, "prefer_playlist_order", False))
        )
        self.player_service.set_random_display_order_getter(
            lambda: str(getattr(self.settings, "random_display_order", "original"))
        )
        self.player_service.set_single_loop_mode_enabled(self.settings.enable_single_loop_mode)
        self.player_service.set_playlist_loop_mode_enabled(self.settings.enable_playlist_loop_mode)
        self.player_service.set_output_device(getattr(self.settings, "output_device", ""))
        self.player_service.error_occurred.connect(self.error_occurred)
        self.error_occurred.connect(self._record_runtime_error)
        _t1 = _time.perf_counter()
        print(f"[Services计时] 播放器初始化: {_t1 - _t0:.3f}s")

    def restore_session_preview(self, state: SessionState) -> bool:
        if self.library_service is None or self.player_service is None:
            return False
        track_id = str(state.current_track_id or "").strip()
        track_path = str(state.current_track_path or "").strip()
        if not track_id or not track_path or not Path(track_path).is_file():
            return False

        playlist_id = str(state.current_playlist_id or ALL_SONGS_ID).strip() or ALL_SONGS_ID
        stored_track, stored_playlist = self.library_store.load_track_and_playlist(track_id, playlist_id)
        track = stored_track or Track(
            id=track_id,
            path=track_path,
            title=str(state.current_track_title or Path(track_path).stem),
            artist=str(state.current_track_artist or "未知歌手"),
        )

        playlists = {
            ALL_SONGS_ID: Playlist(id=ALL_SONGS_ID, name="全部歌曲", track_ids=[track_id]),
        }
        if stored_playlist is not None:
            if track_id not in stored_playlist.track_ids:
                stored_playlist.track_ids.insert(0, track_id)
            playlists[playlist_id] = stored_playlist
        elif playlist_id != ALL_SONGS_ID:
            playlists[playlist_id] = Playlist(id=playlist_id, name="恢复中的歌单", track_ids=[track_id])

        self.library_service.load_preloaded(
            {track_id: track},
            playlists,
            playlist_id,
            quick=True,
        )
        with self.player_service.suspend_stats_collection():
            self.player_service.set_mode(state.play_mode)
            self.player_service.set_volume(state.volume)
            self.library_service.active_playlist_id = playlist_id
            self.player_service._current_playlist_id = playlist_id
            return self.player_service.play_track(
                track_id,
                auto_play=False,
                start_sec=max(0.0, state.position_sec),
                manual_select=False,
            )

    def prepare_library_load(self):
        tracks, playlists, active = self.library_store.load()
        indexes = self.library_service.build_indexes_for_tracks(tracks)
        return tracks, playlists, active, indexes

    def load_library(self) -> None:
        if self.library_service is None:
            return
        self.finish_library_load(self.prepare_library_load())

    def finish_library_load(self, payload) -> None:
        if self.library_service is None:
            return
        import time as _time

        _t0 = _time.perf_counter()

        tracks, playlists, active, indexes = payload
        self.library_service.load_preloaded(tracks, playlists, active, indexes, quick=True)
        _t1 = _time.perf_counter()
        self._library_loaded = True

        if self.player_service:
            self.player_service._set_initial_track_for_playlist()

        self.control_server = ControlServer(self.dispatch_command)
        self.control_server.error_occurred.connect(self.error_occurred)
        self.control_server.listening_changed.connect(self.runtime_status_changed)

        self._session_save_timer = QTimer(self)
        self._session_save_timer.timeout.connect(self.save_session)
        self._apply_save_timer_settings()

        self._audio_change_timer = QTimer(self)
        self._audio_change_timer.setSingleShot(True)
        self._audio_change_timer.setInterval(150)
        self._audio_change_timer.timeout.connect(self._handle_audio_output_changed)
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)

        if self.settings.control_interface_enabled:
            self.start_runtime_server()
        else:
            self.runtime_status_changed.emit(False, self.settings.control_host, self.settings.control_port)

        _t2 = _time.perf_counter()
        print(f"[Services计时] 库加载: {_t1 - _t0:.3f}s | 服务: {_t2 - _t1:.3f}s | 总计: {_t2 - _t0:.3f}s")

    def restore_session(self) -> None:
        """恢复上次播放会话（在窗口显示后调用以加速启动）。"""
        if self._session_restored:
            return
        self._session_restored = True
        if self.settings.auto_restore_session:
            with self.player_service.suspend_stats_collection():
                self.player_service.restore_session(self.session_store.load())
        else:
            self.player_service.set_volume(1.0)

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
            if self._library_loaded:
                self.library_service.save()
            self.settings_store.save(self.settings)
            if self.control_server is not None:
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
        """应用保存定时器设置。

        根据self.settings中的配置，启动或停止会话保存定时器。
        功能：如果定时保存启用，则启动定时器；否则停止定时器。
        参数：无（直接使用self.settings）
        返回值：无
        """
        # 从设置中获取定时保存是否启用，并转换为布尔值
        enabled = bool(self.settings.timed_save_enabled)
        # 获取定时保存的分钟数，限制在1-1440分钟范围内（24小时）
        minutes = max(1, min(1440, int(self.settings.timed_save_minutes)))
        if enabled:
            # 如果启用，设置定时器间隔为分钟数转换成的毫秒数
            self._session_save_timer.setInterval(minutes * 60 * 1000)
            # 启动定时器
            self._session_save_timer.start()
        else:
            # 如果未启用，停止定时器
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
        """获取当前歌词的文件名。
        参数：无（self 为实例自身）。
        返回值：字符串，表示歌词文件名；若无则返回空字符串。
        """
        track = self.player_service.current_track()  # 获取当前播放的轨道
        if track is None:
            return ""  # 如果没有轨道，返回空字符串
        # 获取轨道的歌词路径属性，转换为字符串，若为空则使用空字符串，并去除首尾空白
        ext_lyrics = str(getattr(track, "source_lyrics_path", "") or "").strip()
        if ext_lyrics:
            return Path(ext_lyrics).name  # 返回路径中的文件名部分
        return ""  # 如果歌词路径为空，返回空字符串

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
        """导入并处理Muse格式的播放列表文件，将其存入资料库并返回该播放列表的标识符。

        Args:
            self: 实例对象自身。
            file_path (Path): 待导入的Muse播放列表文件路径。

        Returns:
            str: 成功导入的播放列表在资料库中的唯一ID。
        """
        # 调用资料库服务的核心方法，解析文件并导入播放列表数据
        playlist = self.library_service.import_muse_playlist(file_path)
        # 发射资料库已变更信号，通知UI或其他监听组件刷新数据
        self.library_changed.emit()
        # 返回新导入播放列表的ID
        return playlist.id

    def import_files(self, files: list[Path], playlist_id: str | None = None) -> int:
        """
        将指定文件列表导入媒体库。

        功能：
            遍历给定的文件路径列表，将每个文件尝试导入到媒体库中。
            可选择将导入的文件关联到一个已有的播放列表。
            导入过程中，单个文件的失败不会中断整个流程。
            如果有任何文件成功导入，将自动保存媒体库的更改并发出`library_changed`信号。

        参数：
            files (list[Path]): 包含待导入文件路径的列表。
            playlist_id (str | None): 可选参数。如果提供，则所有成功导入的文件将被添加到此ID对应的播放列表中。默认为None。

        返回值：
            int: 成功导入的文件数量。
        """
        imported = 0  # 初始化成功导入的文件计数器
        for path in files:
            try:
                # 尝试调用核心服务方法导入单个文件，并临时跳过保存操作
                self.library_service.import_file(path, playlist_id=playlist_id, skip_save=True)
                imported += 1  # 导入成功，计数器加1
            except Exception:
                # 捕获并忽略任何异常，跳过当前文件，继续处理下一个
                continue
        if imported > 0:  # 如果至少有一个文件被成功导入
            self.library_service.save()  # 一次性保存所有成功的导入结果到持久化存储
            self.library_changed.emit()  # 发出信号，通知UI或其他组件媒体库已更新
        return imported  # 返回成功导入的文件总数

    def import_muse_playlist_data(self, payload: dict | str, source_hint: str = "runtime_payload") -> str:
        """
        导入Muse播放列表数据。

        功能：将Muse播放列表数据导入到音乐库中，并返回导入后的播放列表ID。

        参数：
            payload (dict | str): 播放列表数据，可以是字典对象或JSON格式的字符串。
            source_hint (str): 数据来源提示，默认为"runtime_payload"，用于标识数据来源。

        返回值：
            str: 导入成功的播放列表ID。
        """
        data: dict
        # 判断输入数据类型并进行相应转换
        if isinstance(payload, str):
            # 如果输入是JSON字符串，解析为字典
            data = json.loads(payload)
        elif isinstance(payload, dict):
            # 如果输入已经是字典，直接使用
            data = payload
        else:
            # 输入类型不支持时抛出异常
            raise ValueError("playlist payload must be dict or json string")

        # 调用库服务导入播放列表数据，传入数据和来源提示
        playlist = self.library_service.import_muse_playlist_payload(data, source_hint=source_hint)

        # 发出库变更信号，通知UI或其他组件库已更新
        self.library_changed.emit()

        # 返回导入后的播放列表ID
        return playlist.id

    def create_playlist(self, name: str) -> str:
        """
        创建一个新的播放列表。

        参数:
            name (str): 播放列表的名称。

        返回:
            str: 新创建播放列表的ID。
        """
        playlist = self.library_service.create_playlist(name)  # 调用库服务创建播放列表
        self.library_changed.emit()  # 发出库已更改的信号
        return playlist.id  # 返回新播放列表的ID

    def toggle_track_favorite(self, track_id: str) -> bool:
        """切换曲目的收藏状态。

        功能：切换指定曲目的收藏状态，并在收藏时重置早期跳过计数。
            该方法通过库服务切换收藏状态，如果状态变为收藏，则重置播放统计中的早期跳过计数，
            最后发出库变化信号。

        参数：
            track_id (str): 需要切换收藏状态的曲目ID。

        返回值：
            bool: 切换后的收藏状态，True表示已收藏，False表示未收藏。
        """
        state = self.library_service.toggle_favorite(track_id)  # 调用库服务切换收藏状态，获取新状态
        if state:  # 如果状态为True，表示曲目已被收藏
            self.playback_stats_service.reset_early_skip_count(track_id)  # 重置该曲目的早期跳过计数
        self.favorites_changed.emit()
        return state  # 返回切换后的收藏状态

    def is_track_favorite(self, track_id: str | None) -> bool:
        return self.library_service.is_favorite(track_id)

    def export_playlist(self, playlist_id: str, out_dir: Path) -> Path:
        """导出指定ID的播放列表到指定目录。

        Args:
            playlist_id (str): 需要导出的播放列表的唯一标识符。
            out_dir (Path): 播放列表文件的输出目录路径。

        Returns:
            Path: 导出的播放列表文件的完整路径。
        """
        # 调用库服务的导出功能，传入播放列表ID、输出目录和播放统计服务以附加播放数据
        file_path = self.library_service.export_playlist_file(
            playlist_id,
            out_dir,
            self.playback_stats_service,
        )
        # 返回生成的文件路径
        return file_path

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        self.library_service.rename_playlist(playlist_id, name)
        self.library_changed.emit()

    def copy_playlist(self, source_playlist_id: str, new_name: str | None = None) -> str | None:
        """
        复制一个现有的播放列表。

        参数:
            source_playlist_id (str): 要复制的源播放列表的ID。
            new_name (str | None, optional): 新播放列表的名称。如果为 None，则会自动生成一个名称。默认为 None。

        返回值:
            str | None: 新创建的播放列表的ID。如果复制操作失败（例如，源播放列表不存在），则返回 None。
        """
        # 调用库服务的方法来实际执行播放列表的复制操作
        playlist = self.library_service.copy_playlist(source_playlist_id, new_name=new_name)
        # 检查复制操作是否成功，如果返回的播放列表对象为 None，说明复制失败
        if playlist is None:
            return None
        # 复制成功后，发出库已更改的信号，以通知UI或其他监听组件进行更新
        self.library_changed.emit()
        # 返回新创建播放列表的ID
        return playlist.id

    def merge_playlist(self, source_playlist_id: str, target_playlist_id: str) -> int:
        """
        合并播放列表的方法。

        该方法将源播放列表中的内容合并到目标播放列表中。

        参数：
        - source_playlist_id: str, 源播放列表的ID。
        - target_playlist_id: str, 目标播放列表的ID。

        返回值：
        - int, 返回合并的项目数量或其他合并结果指标。
        """
        # 调用库服务执行播放列表合并操作，并将结果存储在变量中
        merged = self.library_service.merge_playlist(source_playlist_id, target_playlist_id)
        # 如果合并操作成功（即合并项目数大于0）
        if merged > 0:
            # 发出库变化信号，通知其他部分库已更新
            self.library_changed.emit()
        # 返回合并结果
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
        if (
            self.player_service.current_track_id
            and self.player_service.current_track_id not in self.library_service.tracks
        ):
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
                    tracks_info.append(
                        {"id": t.id, "title": t.title, "artist": t.artist, "duration_sec": float(t.duration_sec)}
                    )
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
        """记录运行时错误消息到日志和文件中。

        参数:
            message (str): 错误消息字符串。

        返回:
            None
        """
        text = str(message).strip()  # 将消息转换为字符串并去除首尾空白
        if not text:  # 如果消息为空字符串，则直接返回，不记录
            return

        with contextlib.suppress(Exception):  # 尝试使用logger记录错误，如果记录失败则忽略异常
            self.logger.error(text)

        with contextlib.suppress(
            Exception
        ):  # 尝试将错误消息写入文件，包括创建目录、生成时间戳和写入内容，如果任何步骤失败则忽略异常
            self._runtime_error_file.parent.mkdir(parents=True, exist_ok=True)  # 创建日志文件的父目录，如果已存在则跳过
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 生成当前时间的格式化字符串
            with self._runtime_error_file.open("a", encoding="utf-8") as f:  # 以追加模式打开文件，使用UTF-8编码
                f.write(f"{stamp} {text}\n")  # 写入时间戳和错误消息，后跟换行符
