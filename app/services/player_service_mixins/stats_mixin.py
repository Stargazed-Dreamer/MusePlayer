"""PlayerService 统计相关 mixin。

包含统计开关、早期跳过判定、完播判定、播放增量累计与统计暂停上下文。
"""

from __future__ import annotations

from contextlib import contextmanager, suppress


class PlayerServiceStatsMixin:
    def _collect_stats_enabled(self) -> bool:
        """检查是否启用了统计收集功能。

        判断条件包括：统计对象是否存在、统计收集是否被暂停、用户设置是否开启。

        Returns:
            bool: 如果统计收集功能启用则返回True，否则返回False
        """
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
        """记录播放开始事件。

        记录用户开始播放某个音轨的行为。

        Args:
            track_id: 音轨的唯一标识符
            active_request: 是否是由用户主动发起的播放请求
        """
        if not self._collect_stats_enabled():
            return
        with suppress(Exception):
            self._stats.record_play_start(track_id, active_request=active_request)

    def _record_early_skip_if_needed(
        self,
        *,
        skipped_track_id: str | None,
        position: float,
        duration: float,
        next_track_id: str | None,
    ) -> None:
        """如果需要，记录早期跳过事件。

        早期跳过定义：在歌曲前5%的时间内切换到其他歌曲。
        这个数据用于分析用户的听歌习惯和歌曲质量。

        Args:
            skipped_track_id: 被跳过的音轨ID
            position: 在跳过时已经播放的位置（秒）
            duration: 音轨的总时长（秒）
            next_track_id: 即将播放的下一个音轨ID
        """
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
        with suppress(Exception):
            self._stats.record_early_skip(track_id)

    def _check_and_record_complete_play(
        self,
        *,
        track_id: str | None,
        position: float,
        duration: float,
    ) -> None:
        """检查是否达到完播条件（95%以上），如果是则记录完播。

        在切歌或自然结束时调用，检测上一首歌是否完播。

        Args:
            track_id: 曲目ID
            position: 当前播放位置（秒）
            duration: 音轨总时长（秒）
        """
        if not self._collect_stats_enabled():
            return
        track_id = str(track_id or "").strip()
        if not track_id:
            return
        duration_sec = max(0.0, float(duration))
        if duration_sec <= 0.0:
            return
        played_sec = max(0.0, float(position))
        # 完播定义：播放进度达到 95% 以上
        if played_sec < duration_sec * 0.95:
            return
        with suppress(Exception):
            self._stats.record_complete_play(track_id)

    def _record_playback_progress(self, *, position: float, duration: float, playing: bool) -> None:
        """记录播放进度变化。

        计算自上次记录以来的播放增量，并记录到统计数据中。
        用于计算用户实际收听时长和收听完成度。

        Args:
            position: 当前播放位置（秒）
            duration: 音轨总时长（秒）
            playing: 是否正在播放状态
        """
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

        with suppress(Exception):
            self._stats.record_play_progress(track_id, played_seconds=delta, duration_sec=duration)
