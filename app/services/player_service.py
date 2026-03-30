from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
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
    _LAZY_PREFETCH_OVERLAP_SEC = 0.35
    _LAZY_SWITCH_AHEAD_SEC = 0.08
    _GLOBAL_GAIN_BOOST = 1.35

    def __init__(
        self,
        library_service: LibraryService,
        playback_stats_service: PlaybackStatsService | None = None,
        collect_stats_getter: Callable[[], bool] | None = None,
        gain_boost_getter: Callable[[], float] | None = None,
        read_strategy_getter: Callable[[], str] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.library = library_service
        self._stats = playback_stats_service
        self._collect_stats_getter = collect_stats_getter or (lambda: True)
        self._gain_boost_getter = gain_boost_getter or (lambda: self._GLOBAL_GAIN_BOOST)
        self._read_strategy_getter = read_strategy_getter or (lambda: "window")
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
        self._prefetch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="museplayer-prefetch")
        self._prefetch_future: Future | None = None
        self._prefetch_track_id: str | None = None
        self._prefetch_start_sec = 0.0
        self._prefetch_transition_sec = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(30)
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
            self._reset_lazy_prefetch(cancel=True)
            self._prefetch_executor.shutdown(wait=False, cancel_futures=True)
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
        self._reset_lazy_prefetch(cancel=True)
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
                if self._lazy_window_mode and not self._lazy_promoted_to_full:
                    local_start = max(0.0, float(self._core.current_time()))
                    local_duration = max(0.0, float(self._core.duration()))
                    absolute_start = max(0.0, float(self._safe_position()))
                    at_window_end = local_duration > 0.0 and local_start >= max(0.0, local_duration - 0.05)
                    if at_window_end:
                        self._reload_lazy_window(absolute_start, keep_playing=True)
                    else:
                        self._core.play(local_start)
                        self._expecting_natural_end = True
                else:
                    start = self._core.current_time()
                    self._core.play(start)
                    self._expecting_natural_end = True
            except Exception as exc:
                if "No decoded audio loaded" in str(exc):
                    resume_at = max(0.0, float(self._stats_last_position))
                    ok = self._load_current_track(auto_play=True, start_sec=resume_at, active_request=False)
                    if ok:
                        logger.warning("播放前发现解码缓冲丢失，已自动重载: %s", self._current_track_id)
                        self._emit_playback_state(force=True)
                        return
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
            logger.warning("调整进度失败，尝试自动重载后重试: %s", exc)
            keep_playing = self.is_playing()
            ok = self._load_current_track(auto_play=keep_playing, start_sec=target, active_request=False)
            if ok:
                self._stats_skip_next_delta = True
                self._stats_last_position = target
                return
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
            self._apply_core_volume()
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
            step = 5
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

    def refresh_output_gain(self) -> None:
        try:
            self._apply_core_volume()
        except Exception:
            pass

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

    def preview_next_track(self) -> Track | None:
        track_ids = self._playlist_track_ids()
        if not track_ids:
            return None

        current_id = self._current_track_id if self._current_track_id in track_ids else track_ids[0]
        if not current_id:
            return None

        if self._mode == PlayMode.SINGLE_LOOP:
            return self.library.get_track(current_id)

        if self._mode == PlayMode.RANDOM:
            order = list(self._random_order)
            if set(order) != set(track_ids) or len(order) != len(track_ids):
                order = DeterministicShuffle.make_order(track_ids, self._random_seed)
            if not order:
                return None

            if current_id in order:
                idx = order.index(current_id)
            else:
                idx = DeterministicShuffle.clamp_index(order, self._random_index)

            if idx + 1 >= len(order):
                next_seed = self._random_seed + 1
                next_order = DeterministicShuffle.make_order(track_ids, next_seed)
                if not next_order:
                    return None
                target_id = next_order[0]
            else:
                target_id = order[idx + 1]
            return self.library.get_track(target_id)

        if current_id in track_ids:
            idx = track_ids.index(current_id)
        else:
            idx = 0
        target_id = track_ids[(idx + 1) % len(track_ids)]
        return self.library.get_track(target_id)

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
        self._reset_lazy_prefetch(cancel=True)
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
        self._reset_lazy_prefetch(cancel=True)
        track = self.current_track()
        if track is None:
            self.error_occurred.emit("当前没有可播放歌曲")
            return False
        source = Path(track.path)
        target_start = max(0.0, float(start_sec))
        strategy = self._read_strategy()
        try:
            if auto_play and strategy == "window":
                self._core.load(source, start_sec=target_start, window_sec=self._LAZY_WINDOW_SEC)
                loaded_sec = max(0.0, float(self._core.duration()))
                if loaded_sec <= 0.02:
                    self._core.load(source)
                    self._lazy_window_mode = False
                    self._lazy_window_base_sec = 0.0
                    self._lazy_elapsed_play_sec = 0.0
                    self._lazy_promoted_to_full = True
                else:
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
            self._apply_core_volume()
            self._core.set_playback_rate(self._playback_rate)
            if auto_play:
                self._core.play(0.0 if self._lazy_window_mode else target_start)
                self._expecting_natural_end = True
                self._record_play_start(track.id, active_request=active_request)
                if self._lazy_window_mode and not self._lazy_promoted_to_full:
                    local_duration = max(0.0, float(self._core.duration()))
                    self._schedule_lazy_prefetch(track.id, target_start + local_duration)
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
        if playing and self._try_continue_lazy_window_while_playing(position=position, duration=duration):
            duration = self._safe_duration()
            position = self._safe_position()
            playing = self.is_playing()
        self._record_playback_progress(position=position, duration=duration, playing=playing)
        self.progress_changed.emit(position, duration)

        if self._last_playing and not playing:
            if self._continue_lazy_window_if_needed(position=position, duration=duration):
                playing = self.is_playing()
            elif self._expecting_natural_end and (duration <= 0 or position >= max(0.0, duration - 0.10)):
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

    def _continue_lazy_window_if_needed(self, *, position: float, duration: float) -> bool:
        if not self._lazy_window_mode or self._lazy_promoted_to_full:
            return False
        if self._loaded_track_id is None or self._loaded_track_id != self._current_track_id:
            return False
        if not self._expecting_natural_end:
            return False

        try:
            local_duration = max(0.0, float(self._core.duration()))
            local_position = max(0.0, float(self._core.current_time()))
        except Exception:
            local_duration = 0.0
            local_position = 0.0

        if local_duration <= 0.02:
            return False
        if local_position < max(0.0, local_duration - 0.06):
            return False
        if duration > 0 and position >= max(0.0, duration - 0.10):
            return False

        next_start = max(0.0, self._lazy_window_base_sec + local_duration)
        if duration > 0:
            if next_start >= max(0.0, duration - 0.02):
                return False
            next_start = min(next_start, duration)

        try:
            if self._apply_prefetched_window(next_start, absolute_position_sec=max(next_start, position)):
                self._stats_skip_next_delta = True
                self._stats_last_position = max(next_start, position)
                return True
            self._reload_lazy_window(next_start, keep_playing=True)
            self._stats_skip_next_delta = True
            self._stats_last_position = next_start
            return True
        except Exception as exc:
            self.error_occurred.emit(f"窗口续播失败: {exc}")
            return False

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
        self._reset_lazy_prefetch(cancel=False)
        self._core.load(source, start_sec=max(0.0, target_sec), window_sec=self._LAZY_WINDOW_SEC)
        loaded_sec = max(0.0, float(self._core.duration()))
        if loaded_sec <= 0.02:
            self._core.load(source)
            self._apply_core_volume()
            self._core.set_playback_rate(self._playback_rate)
            self._lazy_window_mode = False
            self._lazy_window_base_sec = 0.0
            self._lazy_promoted_to_full = True
            if keep_playing:
                self._core.play(max(0.0, target_sec))
                self._expecting_natural_end = True
            else:
                self._core.seek(max(0.0, target_sec))
                self._expecting_natural_end = False
            return
        self._apply_core_volume()
        self._core.set_playback_rate(self._playback_rate)
        self._lazy_window_mode = True
        self._lazy_window_base_sec = max(0.0, target_sec)
        self._lazy_promoted_to_full = False
        if keep_playing:
            self._core.play(0.0)
            self._expecting_natural_end = True
        else:
            self._expecting_natural_end = False
        if self._current_track_id:
            local_duration = max(0.0, float(self._core.duration()))
            self._schedule_lazy_prefetch(self._current_track_id, self._lazy_window_base_sec + local_duration)

    def _maybe_promote_lazy_full_decode(self, *, position: float, playing: bool) -> None:
        return

    def _reset_lazy_prefetch(self, *, cancel: bool) -> None:
        future = self._prefetch_future
        if future is not None and cancel and not future.done():
            future.cancel()
        self._prefetch_future = None
        self._prefetch_track_id = None
        self._prefetch_start_sec = 0.0
        self._prefetch_transition_sec = 0.0

    def _schedule_lazy_prefetch(self, track_id: str, next_start_sec: float) -> None:
        if not self._lazy_window_mode or self._lazy_promoted_to_full:
            return
        if self._current_track_id != track_id:
            return
        track = self.current_track()
        if track is None:
            return
        duration = float(track.duration_sec)
        if duration > 0 and next_start_sec >= max(0.0, duration - 0.02):
            self._reset_lazy_prefetch(cancel=True)
            return

        transition_sec = max(0.0, float(next_start_sec))
        request_start = max(0.0, transition_sec - self._LAZY_PREFETCH_OVERLAP_SEC)
        if (
            self._prefetch_future is not None
            and not self._prefetch_future.done()
            and self._prefetch_track_id == track_id
            and abs(self._prefetch_start_sec - request_start) < 0.02
            and abs(self._prefetch_transition_sec - transition_sec) < 0.02
        ):
            return

        self._reset_lazy_prefetch(cancel=True)
        source = Path(track.path)
        self._prefetch_track_id = track_id
        self._prefetch_start_sec = request_start
        self._prefetch_transition_sec = transition_sec
        self._prefetch_future = self._prefetch_executor.submit(
            self._prefetch_decode_job,
            source,
            request_start,
            self._LAZY_WINDOW_SEC,
        )

    def _prefetch_decode_job(self, source: Path, start_sec: float, window_sec: float) -> dict:
        try:
            pcm, sample_rate, channels = self._core.decode_window(
                source,
                start_sec=max(0.0, float(start_sec)),
                window_sec=max(0.05, float(window_sec)),
            )
            return {
                "ok": True,
                "source": str(source),
                "start_sec": float(start_sec),
                "pcm": pcm,
                "sample_rate": int(sample_rate),
                "channels": int(channels),
            }
        except Exception as exc:
            return {
                "ok": False,
                "source": str(source),
                "start_sec": float(start_sec),
                "error": str(exc),
            }

    def _apply_prefetched_window(self, next_start_sec: float, *, absolute_position_sec: float | None = None) -> bool:
        future = self._prefetch_future
        if future is None or not future.done():
            return False
        if not self._current_track_id or self._prefetch_track_id != self._current_track_id:
            self._reset_lazy_prefetch(cancel=False)
            return False
        if abs(self._prefetch_transition_sec - float(next_start_sec)) > 0.20:
            self._reset_lazy_prefetch(cancel=False)
            return False

        try:
            result = future.result()
        except Exception:
            self._reset_lazy_prefetch(cancel=False)
            return False

        self._reset_lazy_prefetch(cancel=False)
        if not bool(result.get("ok")):
            logger.debug("预读取失败，回退同步读取: %s", result.get("error", "unknown"))
            return False

        try:
            chunk_start = max(0.0, float(result.get("start_sec", 0.0)))
            sample_rate = int(result["sample_rate"])
            channels = int(result["channels"])
            pcm = result["pcm"]
            chunk_duration = (float(pcm.shape[0]) / float(sample_rate)) if sample_rate > 0 else 0.0
            if chunk_duration <= 0.02:
                return False
            play_abs = float(next_start_sec if absolute_position_sec is None else absolute_position_sec)
            play_from = max(0.0, play_abs - chunk_start)
            if play_from >= max(0.0, chunk_duration - 0.01):
                return False

            self._core.load_decoded_pcm(
                Path(str(result.get("source") or "")),
                pcm,
                sample_rate,
                channels,
                reopen_stream=False,
            )
            self._apply_core_volume()
            self._core.set_playback_rate(self._playback_rate)
            self._lazy_window_mode = True
            self._lazy_window_base_sec = chunk_start
            self._core.play(play_from)
            self._expecting_natural_end = True
            if self._current_track_id:
                self._schedule_lazy_prefetch(self._current_track_id, self._lazy_window_base_sec + chunk_duration)
            return True
        except Exception as exc:
            logger.debug("应用预读取窗口失败: %s", exc)
            return False

    def _try_continue_lazy_window_while_playing(self, *, position: float, duration: float) -> bool:
        if not self._lazy_window_mode or self._lazy_promoted_to_full:
            return False
        if not self._expecting_natural_end:
            return False
        try:
            local_duration = max(0.0, float(self._core.duration()))
            local_position = max(0.0, float(self._core.current_time()))
        except Exception:
            return False
        if local_duration <= 0.02:
            return False

        next_start = max(0.0, self._lazy_window_base_sec + local_duration)
        if duration > 0 and next_start >= max(0.0, duration - 0.10):
            return False
        if local_position < max(0.0, local_duration - self._LAZY_SWITCH_AHEAD_SEC):
            return False

        absolute_now = max(0.0, self._lazy_window_base_sec + local_position)
        if self._apply_prefetched_window(next_start, absolute_position_sec=absolute_now):
            self._stats_skip_next_delta = True
            self._stats_last_position = absolute_now
            return True
        return False

    def _apply_core_volume(self) -> None:
        core_gain = (self._gain_percent / 100.0) * self._effective_gain_boost()
        self._core.set_volume(core_gain)

    def _effective_gain_boost(self) -> float:
        try:
            value = float(self._gain_boost_getter())
        except Exception:
            value = self._GLOBAL_GAIN_BOOST
        return max(0.5, min(5.0, value))

    def _read_strategy(self) -> str:
        try:
            mode = str(self._read_strategy_getter() or "window").strip().lower()
        except Exception:
            mode = "window"
        if mode not in {"window", "full"}:
            mode = "window"
        return mode

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
