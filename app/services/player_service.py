from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from core import PyAVPlayerCore

from app.models.entities import SessionState, Track
from app.services.library_service import LibraryService
from app.services.playback_stats_service import PlaybackStatsService
from app.services.random_order import DeterministicShuffle

logger = logging.getLogger("museplayer.player")


class PlayMode(str, Enum):
    SINGLE_LOOP = "single_loop"
    PLAYLIST_LOOP = "playlist_loop"
    RANDOM = "random"


class PlayerService(QObject):
    track_changed = Signal(object)
    queue_changed = Signal()
    progress_changed = Signal(float, float)
    playback_changed = Signal(bool)
    mode_changed = Signal(str)
    error_occurred = Signal(str)
    random_state_changed = Signal(int, int)
    playback_rate_changed = Signal(float)
    _LAZY_PLAY_THRESHOLD_SEC = 5.0
    _LAZY_WINDOW_SEC = 6.2

    def __init__(
        self,
        library_service: LibraryService,
        playback_stats_service: PlaybackStatsService | None = None,
        collect_stats_getter: Callable[[], bool] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.library = library_service
        self._stats = playback_stats_service
        self._collect_stats_getter = collect_stats_getter or (lambda: True)
        self._core = PyAVPlayerCore()

        self._mode = PlayMode.SINGLE_LOOP
        self._playlist_loop_enabled = False
        self._gain_percent = 80
        self._playback_rate = 1.0

        self._current_playlist_id = self.library.active_playlist_id
        self._current_track_id: str | None = None
        self._loaded_track_id: str | None = None

        self._sequential_index = 0
        self._random_seed = 1
        self._random_index = 0
        self._random_order: list[str] = []

        self._expecting_natural_end = False
        self._last_playing = False
        self._stats_last_track_id: str | None = None
        self._stats_last_position = 0.0
        self._stats_skip_next_delta = False
        self._lazy_window_mode = False
        self._lazy_window_base_sec = 0.0
        self._lazy_elapsed_play_sec = 0.0
        self._lazy_promoted_to_full = False

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

        self._core.set_error_callback(self._on_core_runtime_error)
        self._set_initial_track_for_playlist()

    @property
    def mode(self) -> PlayMode:
        return self._mode

    @property
    def current_playlist_id(self) -> str | None:
        return self._current_playlist_id

    @property
    def current_track_id(self) -> str | None:
        return self._current_track_id

    @property
    def random_seed(self) -> int:
        return self._random_seed

    @property
    def random_index(self) -> int:
        return self._random_index

    def close(self) -> None:
        try:
            self._timer.stop()
        finally:
            self._core.close()

    def available_modes(self) -> list[str]:
        modes = [PlayMode.SINGLE_LOOP.value]
        if self._playlist_loop_enabled:
            modes.append(PlayMode.PLAYLIST_LOOP.value)
        modes.append(PlayMode.RANDOM.value)
        return modes

    def set_playlist_loop_mode_enabled(self, enabled: bool) -> None:
        self._playlist_loop_enabled = bool(enabled)
        if not self._playlist_loop_enabled and self._mode == PlayMode.PLAYLIST_LOOP:
            self.set_mode(PlayMode.SINGLE_LOOP)
        else:
            self.mode_changed.emit(self._mode.value)

    def set_mode(self, mode: str | PlayMode) -> None:
        if isinstance(mode, str):
            cleaned = mode.strip().lower()
            if cleaned in {"single_loop", "repeat_one", "single", "one", "loop"}:
                parsed = PlayMode.SINGLE_LOOP
            elif cleaned in {"playlist_loop", "list_loop", "sequential"}:
                parsed = PlayMode.PLAYLIST_LOOP
            elif cleaned in {"random", "shuffle"}:
                parsed = PlayMode.RANDOM
            else:
                parsed = PlayMode.SINGLE_LOOP
        else:
            parsed = mode

        if parsed == PlayMode.PLAYLIST_LOOP and not self._playlist_loop_enabled:
            parsed = PlayMode.SINGLE_LOOP

        self._mode = parsed
        logger.info("播放模式切换: %s", self._mode.value)
        self.mode_changed.emit(self._mode.value)

        if self._mode == PlayMode.RANDOM:
            self._rebuild_random_order(force=True)
            self._align_random_index_with_current_track()

    def set_playlist(self, playlist_id: str | None) -> None:
        playlist = self.library.get_playlist(playlist_id)
        self._current_playlist_id = playlist.id
        self.library.set_active_playlist(playlist.id)

        track_ids = self._playlist_track_ids()
        previous_track_id = self._current_track_id
        if not track_ids:
            self._current_track_id = None
            self._loaded_track_id = None
            self._stats_last_track_id = None
            self._stats_last_position = 0.0
            self._lazy_window_mode = False
            self._lazy_promoted_to_full = False
            self._lazy_window_base_sec = 0.0
            self._lazy_elapsed_play_sec = 0.0
            self.track_changed.emit(None)
            self.queue_changed.emit()
            return

        if self._current_track_id not in track_ids:
            self._current_track_id = track_ids[0]
        self._sequential_index = track_ids.index(self._current_track_id)

        self._rebuild_random_order(force=True)
        self._align_random_index_with_current_track()

        if previous_track_id != self._current_track_id:
            self.track_changed.emit(self.current_track())
        self.queue_changed.emit()

    def play(self) -> None:
        if self._current_track_id is None:
            self._set_initial_track_for_playlist()
            if self._current_track_id is None:
                return

        if self._loaded_track_id != self._current_track_id:
            ok = self._load_current_track(auto_play=True, start_sec=0.0, active_request=False)
            if not ok:
                return
        else:
            try:
                start = self._core.current_time()
                self._core.play(start)
                self._expecting_natural_end = True
            except Exception as exc:
                self.error_occurred.emit(str(exc))
                return

        logger.info("播放: %s", self._current_track_id)
        self._emit_playback_state()

    def pause(self) -> None:
        try:
            self._core.pause()
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return
        self._expecting_natural_end = False
        logger.info("暂停: %s", self._current_track_id)
        self._emit_playback_state()

    def toggle_play_pause(self) -> None:
        if self.is_playing():
            self.pause()
        else:
            self.play()

    def seek(self, sec: float) -> None:
        if self._loaded_track_id is None:
            return
        target = max(0.0, float(sec))
        try:
            if self._lazy_window_mode and not self._lazy_promoted_to_full:
                self._reload_lazy_window(target, keep_playing=self.is_playing())
            else:
                self._core.seek(target)
            self._stats_skip_next_delta = True
            self._stats_last_position = target
            logger.info("调整进度: %.3f", target)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return

    def set_volume(self, volume: float) -> None:
        percent = int(round(float(volume) * 100.0))
        self.set_gain_percent(percent, allow_boost=True)

    def adjust_volume(self, delta: float) -> float:
        self.set_volume(self.volume() + delta)
        return self.volume()

    def volume(self) -> float:
        return self._gain_percent / 100.0

    def gain_percent(self) -> int:
        return int(self._gain_percent)

    def slider_gain_percent(self) -> int:
        return max(0, min(100, int(self._gain_percent)))

    def set_gain_percent(self, percent: int, *, allow_boost: bool) -> int:
        max_value = 500 if allow_boost else 100
        value = max(0, min(max_value, int(percent)))
        self._gain_percent = value
        try:
            self._core.set_volume(self._gain_percent / 100.0)
        except Exception:
            pass
        return self._gain_percent

    def adjust_gain_by_key(self, increase: bool) -> int:
        cur = int(self._gain_percent)
        if cur < 10:
            step = 1
        elif cur < 20:
            step = 2
        elif cur < 100:
            step = 10
        else:
            step = 20

        target = cur + (step if increase else -step)
        return self.set_gain_percent(target, allow_boost=True)

    def set_playback_rate(self, rate: float) -> None:
        value = max(0.25, min(4.0, float(rate)))
        self._playback_rate = value
        try:
            self._core.set_playback_rate(value)
        except Exception:
            pass
        self.playback_rate_changed.emit(self._playback_rate)

    def playback_rate(self) -> float:
        return float(self._playback_rate)

    def is_playing(self) -> bool:
        try:
            return self._core.is_playing()
        except Exception:
            return False

    def current_track(self) -> Track | None:
        return self.library.get_track(self._current_track_id)

    def playlist_tracks(self) -> list[Track]:
        return self.library.get_playlist_tracks(self._current_playlist_id)

    def search_playlist_tracks(self, keyword: str) -> list[Track]:
        return self.library.search_playlist_tracks(self._current_playlist_id, keyword)

    def play_track(
        self,
        track_id: str,
        *,
        auto_play: bool = True,
        start_sec: float = 0.0,
        manual_select: bool = False,
        from_random_navigation: bool = False,
        active_request: bool = False,
    ) -> bool:
        track = self.library.get_track(track_id)
        if track is None:
            self.error_occurred.emit(f"歌曲不存在: {track_id}")
            return False

        playlist_ids = self._playlist_track_ids()
        if track_id not in playlist_ids:
            self.set_playlist("all_songs")
            playlist_ids = self._playlist_track_ids()

        self._current_track_id = track_id
        if track_id in playlist_ids:
            self._sequential_index = playlist_ids.index(track_id)

        if self._mode == PlayMode.RANDOM:
            if manual_select:
                self._random_seed += 1
                self._rebuild_random_order(force=True)
                self._align_random_index_with_current_track()
            elif from_random_navigation:
                pass
            else:
                self._rebuild_random_order(force=False)
                self._align_random_index_with_current_track()

        ok = self._load_current_track(auto_play=auto_play, start_sec=start_sec, active_request=active_request)
        if not ok:
            return False

        logger.info("切换歌曲: %s", track_id)
        self.track_changed.emit(self.current_track())
        self.queue_changed.emit()
        return True

    def play_file(self, file_path: Path, *, active_request: bool = True) -> bool:
        try:
            track = self.library.import_file(file_path=file_path, playlist_id=self._current_playlist_id)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return False

        self.queue_changed.emit()
        return self.play_track(
            track.id,
            auto_play=True,
            start_sec=0.0,
            manual_select=True,
            active_request=active_request,
        )

    def next_track(self, *, user_triggered: bool = True) -> bool:
        track_ids = self._playlist_track_ids()
        if not track_ids:
            return False

        if self._mode == PlayMode.RANDOM:
            self._rebuild_random_order(force=False)
            if not self._random_order:
                return False

            attempts = len(self._random_order)
            while attempts > 0:
                if self._current_track_id in self._random_order:
                    self._random_index = self._random_order.index(self._current_track_id)
                else:
                    self._random_index = DeterministicShuffle.clamp_index(self._random_order, self._random_index)

                if self._random_index + 1 >= len(self._random_order):
                    self._random_seed += 1
                    self._rebuild_random_order(force=True)
                    self._random_index = 0
                else:
                    self._random_index += 1

                self.random_state_changed.emit(self._random_seed, self._random_index)
                target_id = self._random_order[self._random_index]
                if self.play_track(
                    target_id,
                    auto_play=True,
                    start_sec=0.0,
                    from_random_navigation=True,
                    active_request=False,
                ):
                    return True
                attempts -= 1

            self.error_occurred.emit("歌单内歌曲均无法播放")
            return False

        if self._current_track_id in track_ids:
            idx = track_ids.index(self._current_track_id)
        else:
            idx = 0

        attempts = len(track_ids)
        while attempts > 0:
            idx = (idx + 1) % len(track_ids)
            target_id = track_ids[idx]
            if self.play_track(
                target_id,
                auto_play=True,
                start_sec=0.0,
                manual_select=False,
                active_request=False,
            ):
                return True
            attempts -= 1

        self.error_occurred.emit("歌单内歌曲均无法播放")
        return False

    def previous_track(self) -> bool:
        track_ids = self._playlist_track_ids()
        if not track_ids:
            return False

        if self._mode == PlayMode.RANDOM:
            self._rebuild_random_order(force=False)
            if not self._random_order:
                return False

            attempts = len(self._random_order)
            while attempts > 0:
                if self._current_track_id in self._random_order:
                    self._random_index = self._random_order.index(self._current_track_id)
                if self._random_index > 0:
                    self._random_index -= 1
                self.random_state_changed.emit(self._random_seed, self._random_index)
                target_id = self._random_order[self._random_index]
                if self.play_track(
                    target_id,
                    auto_play=True,
                    start_sec=0.0,
                    from_random_navigation=True,
                    active_request=False,
                ):
                    return True
                attempts -= 1

            self.error_occurred.emit("歌单内歌曲均无法播放")
            return False

        if self._current_track_id in track_ids:
            idx = track_ids.index(self._current_track_id)
        else:
            idx = 0
        attempts = len(track_ids)
        while attempts > 0:
            idx = (idx - 1) % len(track_ids)
            target_id = track_ids[idx]
            if self.play_track(
                target_id,
                auto_play=True,
                start_sec=0.0,
                manual_select=False,
                active_request=False,
            ):
                return True
            attempts -= 1

        self.error_occurred.emit("歌单内歌曲均无法播放")
        return False

    def export_session(self) -> SessionState:
        position = 0.0
        if self._loaded_track_id is not None:
            try:
                position = self._core.current_time()
            except Exception:
                position = 0.0

        return SessionState(
            current_playlist_id=self._current_playlist_id,
            current_track_id=self._current_track_id,
            position_sec=position,
            volume=self._gain_percent / 100.0,
            play_mode=self._mode.value,
            random_seed=self._random_seed,
            random_index=self._random_index,
        )

    def restore_session(self, state: SessionState) -> None:
        self.set_mode(state.play_mode)
        self.set_volume(state.volume)

        playlist_id = state.current_playlist_id or self.library.active_playlist_id
        self.set_playlist(playlist_id)

        self._random_seed = max(0, int(state.random_seed))
        self._random_index = max(0, int(state.random_index))
        self.random_state_changed.emit(self._random_seed, self._random_index)

        if state.current_track_id:
            track_id = state.current_track_id
            if self._mode == PlayMode.RANDOM:
                self._rebuild_random_order(force=True)
                if track_id in self._random_order:
                    self._random_index = self._random_order.index(track_id)
                elif self._random_order:
                    self._random_index = DeterministicShuffle.clamp_index(self._random_order, state.random_index)
                    track_id = self._random_order[self._random_index]

            self.play_track(
                track_id,
                auto_play=False,
                start_sec=max(0.0, state.position_sec),
                manual_select=False,
            )

    def state_snapshot(self) -> dict:
        track = self.current_track()
        duration = self._safe_duration()
        position = self._safe_position()
        return {
            "playlist_id": self._current_playlist_id,
            "track_id": self._current_track_id,
            "track_title": track.title if track else "",
            "position_sec": position,
            "duration_sec": duration,
            "playing": self.is_playing(),
            "mode": self._mode.value,
            "volume": self._gain_percent / 100.0,
            "random_seed": self._random_seed,
            "random_index": self._random_index,
            "playback_rate": self._playback_rate,
        }

    def _set_initial_track_for_playlist(self) -> None:
        track_ids = self._playlist_track_ids()
        if not track_ids:
            self._current_track_id = None
            self._loaded_track_id = None
            return
        if self._current_track_id not in track_ids:
            self._current_track_id = track_ids[0]
        self._sequential_index = track_ids.index(self._current_track_id)

        self._rebuild_random_order(force=True)
        self._align_random_index_with_current_track()

    def _load_current_track(self, *, auto_play: bool, start_sec: float, active_request: bool) -> bool:
        track = self.current_track()
        if track is None:
            self.error_occurred.emit("当前没有可播放歌曲")
            return False
        source = Path(track.path)
        target_start = max(0.0, float(start_sec))
        try:
            if auto_play:
                self._core.load(source, start_sec=target_start, window_sec=self._LAZY_WINDOW_SEC)
                self._lazy_window_mode = True
                self._lazy_window_base_sec = target_start
                self._lazy_elapsed_play_sec = 0.0
                self._lazy_promoted_to_full = False
            else:
                self._core.load(source)
                self._lazy_window_mode = False
                self._lazy_window_base_sec = 0.0
                self._lazy_elapsed_play_sec = 0.0
                self._lazy_promoted_to_full = True
            self._core.set_volume(self._gain_percent / 100.0)
            self._core.set_playback_rate(self._playback_rate)
            if auto_play:
                self._core.play(0.0 if self._lazy_window_mode else target_start)
                self._expecting_natural_end = True
                self._record_play_start(track.id, active_request=active_request)
            else:
                self._core.seek(target_start)
                self._expecting_natural_end = False
        except Exception as exc:
            self.error_occurred.emit(f"加载失败: {source} -> {exc}")
            return False

        self._loaded_track_id = track.id
        self._stats_last_track_id = track.id
        self._stats_last_position = target_start
        self._stats_skip_next_delta = True
        self._emit_playback_state(force=True)
        self.progress_changed.emit(self._safe_position(), self._safe_duration())
        return True

    def _playlist_track_ids(self) -> list[str]:
        playlist = self.library.get_playlist(self._current_playlist_id)
        return [track_id for track_id in playlist.track_ids if track_id in self.library.tracks]

    def _rebuild_random_order(self, *, force: bool) -> None:
        track_ids = self._playlist_track_ids()
        if not track_ids:
            self._random_order = []
            self._random_index = 0
            self.random_state_changed.emit(self._random_seed, self._random_index)
            return

        if force or set(self._random_order) != set(track_ids) or len(self._random_order) != len(track_ids):
            self._random_order = DeterministicShuffle.make_order(track_ids, self._random_seed)
            self._random_index = DeterministicShuffle.clamp_index(self._random_order, self._random_index)
            self.random_state_changed.emit(self._random_seed, self._random_index)

    def _align_random_index_with_current_track(self) -> None:
        if not self._random_order or self._current_track_id is None:
            self._random_index = 0
            self.random_state_changed.emit(self._random_seed, self._random_index)
            return
        if self._current_track_id in self._random_order:
            self._random_index = self._random_order.index(self._current_track_id)
        else:
            self._random_index = DeterministicShuffle.clamp_index(self._random_order, self._random_index)
            self._current_track_id = self._random_order[self._random_index]
        self.random_state_changed.emit(self._random_seed, self._random_index)

    def _on_tick(self) -> None:
        duration = self._safe_duration()
        position = self._safe_position()
        playing = self.is_playing()

        self._maybe_promote_lazy_full_decode(position=position, playing=playing)
        self._record_playback_progress(position=position, duration=duration, playing=playing)
        self.progress_changed.emit(position, duration)

        if self._last_playing and not playing:
            if self._expecting_natural_end and duration > 0 and position >= max(0.0, duration - 0.10):
                self._expecting_natural_end = False
                self._handle_natural_finished()

        self._emit_playback_state(current=playing)
        self._last_playing = playing

    def _handle_natural_finished(self) -> None:
        if self._mode == PlayMode.SINGLE_LOOP and self._current_track_id:
            ok = self._load_current_track(auto_play=True, start_sec=0.0, active_request=False)
            if ok:
                return
        self.next_track(user_triggered=False)

    def _on_core_runtime_error(self, message: str) -> None:
        logger.error("播放内核回调异常: %s", message)
        self.error_occurred.emit(f"播放内核异常: {message}")

    def _safe_position(self) -> float:
        if self._loaded_track_id is None:
            return 0.0
        try:
            current = float(self._core.current_time())
            if self._lazy_window_mode and not self._lazy_promoted_to_full:
                return self._lazy_window_base_sec + current
            return current
        except Exception:
            return 0.0

    def _safe_duration(self) -> float:
        if self._loaded_track_id is None:
            track = self.current_track()
            return float(track.duration_sec if track else 0.0)
        if self._lazy_window_mode and not self._lazy_promoted_to_full:
            track = self.current_track()
            return float(track.duration_sec if track else 0.0)
        try:
            return float(self._core.duration())
        except Exception:
            track = self.current_track()
            return float(track.duration_sec if track else 0.0)

    def _emit_playback_state(self, *, current: bool | None = None, force: bool = False) -> None:
        playing = self.is_playing() if current is None else bool(current)
        if force or playing != self._last_playing:
            self.playback_changed.emit(playing)

    def _reload_lazy_window(self, target_sec: float, *, keep_playing: bool) -> None:
        track = self.current_track()
        if track is None:
            return
        source = Path(track.path)
        self._core.load(source, start_sec=max(0.0, target_sec), window_sec=self._LAZY_WINDOW_SEC)
        self._core.set_volume(self._gain_percent / 100.0)
        self._core.set_playback_rate(self._playback_rate)
        self._lazy_window_mode = True
        self._lazy_window_base_sec = max(0.0, target_sec)
        if keep_playing:
            self._core.play(0.0)
            self._expecting_natural_end = True
        else:
            self._core.seek(0.0)
            self._expecting_natural_end = False

    def _maybe_promote_lazy_full_decode(self, *, position: float, playing: bool) -> None:
        if not self._lazy_window_mode or self._lazy_promoted_to_full:
            return
        if not playing:
            return

        self._lazy_elapsed_play_sec += self._timer.interval() / 1000.0
        if self._lazy_elapsed_play_sec < self._LAZY_PLAY_THRESHOLD_SEC:
            return

        track = self.current_track()
        if track is None:
            return
        source = Path(track.path)

        keep_playing = self.is_playing()
        target = max(0.0, float(position))
        try:
            self._core.load(source)
            self._core.set_volume(self._gain_percent / 100.0)
            self._core.set_playback_rate(self._playback_rate)
            self._core.seek(target)
            if keep_playing:
                self._core.play(target)
        except Exception as exc:
            self.error_occurred.emit(f"提升为完整读取失败: {source} -> {exc}")
            return

        self._lazy_promoted_to_full = True
        self._lazy_window_mode = False
        self._lazy_window_base_sec = 0.0
        self._stats_skip_next_delta = True
        self._stats_last_position = target

    def _collect_stats_enabled(self) -> bool:
        if self._stats is None:
            return False
        try:
            return bool(self._collect_stats_getter())
        except Exception:
            return False

    def _record_play_start(self, track_id: str, *, active_request: bool) -> None:
        if not self._collect_stats_enabled():
            return
        try:
            self._stats.record_play_start(track_id, active_request=active_request)
        except Exception:
            pass

    def _record_playback_progress(self, *, position: float, duration: float, playing: bool) -> None:
        if not playing:
            self._stats_last_position = max(0.0, float(position))
            return

        track_id = self._loaded_track_id
        if not track_id:
            return

        if self._stats_last_track_id != track_id:
            self._stats_last_track_id = track_id
            self._stats_last_position = max(0.0, float(position))
            self._stats_skip_next_delta = False
            return

        delta = float(position) - float(self._stats_last_position)
        self._stats_last_position = max(0.0, float(position))

        if self._stats_skip_next_delta:
            self._stats_skip_next_delta = False
            return

        if delta <= 0.0 or delta > 30.0:
            return

        if not self._collect_stats_enabled():
            return

        try:
            self._stats.record_play_progress(track_id, played_seconds=delta, duration_sec=duration)
        except Exception:
            pass
