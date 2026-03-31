from __future__ import annotations

"""PlayerService 统计相关 mixin。

包含统计开关、早期跳过判定、播放增量累计与统计暂停上下文。
"""

from contextlib import contextmanager

class PlayerServiceStatsMixin:
    def _collect_stats_enabled(self) -> bool:
        if self._stats is None:
            return False
        if self._stats_suspended_depth > 0:
            return False
        try:
            return bool(self._collect_stats_getter())
        except Exception:
            return False

    @contextmanager
    def suspend_stats_collection(self):
        """临时暂停统计收集（支持嵌套）。

        用于会话恢复、应用退出等系统流程，避免污染用户行为统计。
        """
        self._stats_suspended_depth += 1
        try:
            yield
        finally:
            self._stats_suspended_depth = max(0, self._stats_suspended_depth - 1)

    def _record_play_start(self, track_id: str, *, active_request: bool) -> None:
        if not self._collect_stats_enabled():
            return
        try:
            self._stats.record_play_start(track_id, active_request=active_request)
        except Exception:
            pass

    def _record_early_skip_if_needed(
        self,
        *,
        skipped_track_id: str | None,
        position: float,
        duration: float,
        next_track_id: str | None,
    ) -> None:
        if not self._collect_stats_enabled():
            return
        track_id = str(skipped_track_id or "").strip()
        if not track_id:
            return
        if next_track_id and str(next_track_id) == track_id:
            return
        duration_sec = max(0.0, float(duration))
        if duration_sec <= 0.0:
            return
        played_sec = max(0.0, float(position))
        # 早期跳过定义：在歌曲前 5% 被切走（且切到其他歌曲）。
        if played_sec >= max(0.0, duration_sec * 0.05):
            return
        try:
            self._stats.record_early_skip(track_id)
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

