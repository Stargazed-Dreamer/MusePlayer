from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from app.models import LibraryStore, SessionState, SessionStore, Settings, SettingsStore
from app.runtime import ControlServer
from app.services.library_service import LibraryService
from app.services.metadata_service import MetadataService
from app.services.playback_stats_service import PlaybackStatsService
from app.services.player_service import PlayerService
from app.utils import configure_logging, get_logger


class AppController(QObject):
    library_changed = Signal()
    settings_changed = Signal(object)
    runtime_status_changed = Signal(bool, str, int)
    message = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, project_root: Path, parent: QObject | None = None):
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.data_dir = self.project_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.metadata_service = MetadataService()

        self.library_store = LibraryStore(self.data_dir)
        self.session_store = SessionStore(self.data_dir)
        self.settings_store = SettingsStore(self.data_dir)

        self.settings = self.settings_store.load()
        self.log_file_path = configure_logging(self.data_dir, self.settings.logging_enabled)
        self.logger = get_logger("app")
        self.logger.info("MusePlayer 启动")
        self._runtime_error_file = self.data_dir / "runtime_errors.log"

        self.library_service = LibraryService(self.library_store, self.metadata_service)
        self.library_service.load()

        self.playback_stats_service = PlaybackStatsService(self.data_dir)
        self.player_service = PlayerService(
            self.library_service,
            playback_stats_service=self.playback_stats_service,
            collect_stats_getter=lambda: bool(self.settings.collect_playback_data),
            gain_boost_getter=lambda: float(self.settings.global_gain_boost),
            read_strategy_getter=lambda: str(self.settings.read_strategy),
        )
        self.player_service.set_playlist_loop_mode_enabled(self.settings.enable_playlist_loop_mode)
        self.player_service.error_occurred.connect(self.error_occurred)
        self.error_occurred.connect(self._record_runtime_error)

        self.control_server = ControlServer(self.dispatch_command)
        self.control_server.error_occurred.connect(self.error_occurred)
        self.control_server.listening_changed.connect(self.runtime_status_changed)

        self._session_save_timer = QTimer(self)
        self._session_save_timer.timeout.connect(self.save_session)
        self._apply_save_timer_settings()

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
        self.logger.info("准备关闭应用")
        with self.player_service.suspend_stats_collection():
            self.save_session()
            self.playback_stats_service.save_if_dirty()
            self.library_service.save()
            self.settings_store.save(self.settings)
            self.control_server.stop()
            self.player_service.close()

    def save_session(self) -> None:
        state = self.player_service.export_session()
        self.session_store.save(state)
        self.library_service.sync_muse_playlist_stats(self.playback_stats_service)
        self.playback_stats_service.save_if_dirty()

    def save_stats_now(self) -> None:
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
        if not self.settings.control_interface_enabled:
            self.control_server.stop()
            self.runtime_status_changed.emit(False, self.settings.control_host, self.settings.control_port)
            return True
        return self.start_runtime_server()

    def update_settings(self, settings: Settings) -> bool:
        self.settings = settings
        self.settings_store.save(settings)
        self.player_service.set_playlist_loop_mode_enabled(self.settings.enable_playlist_loop_mode)
        self.player_service.refresh_output_gain()
        self._apply_save_timer_settings()
        self.settings_changed.emit(settings)

        self.log_file_path = configure_logging(self.data_dir, self.settings.logging_enabled)
        self.logger = get_logger("app")
        if self.settings.logging_enabled:
            self.logger.info("日志已启用: %s", self.log_file_path)

        ok = self.restart_runtime_server()
        return ok

    def set_theme_preference(self, dark_theme: bool) -> None:
        value = bool(dark_theme)
        if bool(self.settings.dark_theme) == value:
            return
        self.settings.dark_theme = value
        self.settings_store.save(self.settings)
        self.settings_changed.emit(self.settings)

    def persist_window_geometry(self, *, x: int, y: int, width: int, height: int) -> None:
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

    def get_current_cover(self) -> bytes | None:
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
        was_playing = self.player_service.is_playing()
        active_playlist = self.player_service.current_playlist_id
        current_track_id = self.player_service.current_track_id
        removed_current_from_active = active_playlist == playlist_id and current_track_id == track_id

        next_candidate_id: str | None = None
        prev_candidate_id: str | None = None
        if removed_current_from_active:
            active_tracks = self.library_service.get_playlist_tracks(playlist_id)
            ordered_ids = [t.id for t in active_tracks]
            if track_id in ordered_ids:
                idx = ordered_ids.index(track_id)
                if idx + 1 < len(ordered_ids):
                    next_candidate_id = ordered_ids[idx + 1]
                if idx - 1 >= 0:
                    prev_candidate_id = ordered_ids[idx - 1]

        removed_ids = self.library_service.remove_track_from_playlist(playlist_id, track_id)
        for removed_id in removed_ids:
            self.playback_stats_service.remove_track(removed_id)
        if self.player_service.current_playlist_id == playlist_id:
            if removed_current_from_active:
                self.player_service.pause()
            self.player_service.set_playlist(self.player_service.current_playlist_id)

            if removed_current_from_active:
                playlist_track_ids = [t.id for t in self.player_service.playlist_tracks()]
                selected_candidate = None
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
        if self.player_service.current_track_id and self.player_service.current_track_id not in self.library_service.tracks:
            self.player_service.pause()
            self.player_service.set_playlist(self.player_service.current_playlist_id)
        self.library_changed.emit()

    def dispatch_command(self, payload: dict) -> dict:
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

        return {"ok": False, "error": f"unknown cmd: {cmd}"}

    def export_session_for_ui(self) -> SessionState:
        return self.player_service.export_session()

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
