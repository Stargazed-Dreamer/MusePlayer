from __future__ import annotations

"""PlayerService 懒加载解码相关 mixin。

包含窗口读取、预读取调度、续播衔接与输出增益策略。
"""

import logging
from pathlib import Path

logger = logging.getLogger("museplayer.player")

class PlayerServiceLazyDecodeMixin:
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
        # 仅在“窗口读取模式 + 当前曲目未变”时调度预读取，避免无效后台任务。
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
            if pcm.size == 0 or pcm.shape[0] <= 0:
                return {
                    "ok": False,
                    "source": str(source),
                    "start_sec": float(start_sec),
                    "error": "empty decoded pcm",
                }
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
                resume_sec=play_from,
                keep_playing=True,
            )
            self._apply_core_volume()
            self._core.set_playback_rate(self._playback_rate)
            self._lazy_window_mode = True
            self._lazy_window_base_sec = chunk_start
            self._expecting_natural_end = True
            if self._current_track_id:
                self._schedule_lazy_prefetch(self._current_track_id, self._lazy_window_base_sec + chunk_duration)
            return True
        except Exception as exc:
            logger.debug("应用预读取窗口失败: %s", exc)
            return False

    def _try_continue_lazy_window_while_playing(self, *, position: float, duration: float) -> bool:
        # 在窗口即将结束时优先切入预读取结果，尽量减小切块卡顿概率。
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

