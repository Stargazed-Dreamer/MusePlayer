from __future__ import annotations

"""PlayerService 懒加载解码相关 mixin。

包含窗口读取、预读取调度、续播衔接与输出增益策略。
"""

import logging
from pathlib import Path

logger = logging.getLogger("museplayer.player")

class PlayerServiceLazyDecodeMixin:
    def _continue_lazy_window_if_needed(self, *, position: float, duration: float) -> bool:
        """在需要时继续懒加载窗口播放。
        
        在懒加载模式下，当当前窗口快播放完毕时，无缝衔接到下一个窗口。
        优先使用预读取的音频数据，如果没有则重新加载窗口。
        
        Args:
            position: 当前在歌曲中的绝对位置
            duration: 歌曲总时长
            
        Returns:
            bool: 是否成功继续播放
        """
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
        """处理内核运行时错误的回调函数。
        
        Args:
            message: 错误信息
        """
        logger.error("播放内核回调异常: %s", message)
        self.error_occurred.emit(f"播放内核异常: {message}")

    def _safe_position(self) -> float:
        """获取安全的当前位置。
        
        在懒加载模式下，位置需要加上窗口基础秒数。
        
        Returns:
            float: 当前播放位置，如果获取失败返回0.0
        """
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
        """获取安全的当前歌曲时长。
        
        在懒加载模式下，从track对象获取完整时长而不是内核的窗口时长。
        
        Returns:
            float: 歌曲时长，如果获取失败返回0.0
        """
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
        """重新加载懒加载窗口。
        
        加载指定起始位置的音频窗口。如果窗口太小（<=20ms），则加载完整歌曲。
        
        Args:
            target_sec: 目标起始秒数
            keep_playing: 是否继续播放
        """
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
        """可能将懒加载解码升级为完整解码（目前未实现）。
        
        预留接口：在某些情况下（如用户行为表明需要完整播放）
        可以将懒加载模式切换为完整加载模式。
        
        Args:
            position: 当前位置
            playing: 是否正在播放
        """
        return

    def _reset_lazy_prefetch(self, *, cancel: bool) -> None:
        """重置懒加载预取状态。
        
        清理预读取的Future对象和相关状态。
        
        Args:
            cancel: 是否取消正在进行的预读取任务
        """
        future = self._prefetch_future
        if future is not None and cancel and not future.done():
            future.cancel()
        self._prefetch_future = None
        self._prefetch_track_id = None
        self._prefetch_start_sec = 0.0
        self._prefetch_transition_sec = 0.0

    def _schedule_lazy_prefetch(self, track_id: str, next_start_sec: float) -> None:
        """调度懒加载预读取任务。
        
        在后台线程中预解码下一个音频窗口，以实现无缝播放。
        
        Args:
            track_id: 当前音轨ID
            next_start_sec: 下一个窗口的起始秒数
        """
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
        """预读取解码作业（在后台线程执行）。
        
        解码指定时间窗口的音频数据。
        
        Args:
            source: 音频文件路径
            start_sec: 起始秒数
            window_sec: 窗口大小（秒）
            
        Returns:
            dict: 解码结果，包含PCM数据或错误信息
        """
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
        """应用预读取的音频窗口。
        
        将预读取的PCM数据加载到音频内核中继续播放。
        
        Args:
            next_start_sec: 下一个窗口的起始秒数
            absolute_position_sec: 绝对位置秒数，可选
            
        Returns:
            bool: 是否成功应用预读取窗口
        """
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
        """在播放过程中尝试继续懒加载窗口。
        
        在窗口即将结束时（根据_LAZY_SWITCH_AHEAD_SEC配置），
        优先尝试使用预读取结果来实现无缝衔接。
        
        Args:
            position: 当前绝对位置
            duration: 歌曲总时长
            
        Returns:
            bool: 是否成功继续播放
        """
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
        """应用核心音量设置。
        
        将用户音量百分比和增益增强因子应用到音频内核。
        """
        core_gain = (self._gain_percent / 100.0) * self._effective_gain_boost()
        self._core.set_volume(core_gain)

    def _effective_gain_boost(self) -> float:
        """获取有效的增益增强因子。
        
        从getter函数获取增益值，限制在0.5-5.0范围内。
        如果获取失败，使用全局默认值。
        
        Returns:
            float: 增益增强因子
        """
        try:
            value = float(self._gain_boost_getter())
        except Exception:
            value = self._GLOBAL_GAIN_BOOST
        return max(0.5, min(5.0, value))

    def _read_strategy(self) -> str:
        """获取读取策略。
        
        确定使用窗口读取还是完整读取策略。
        
        Returns:
            str: "window" 或 "full"，默认为"window"
        """
        try:
            mode = str(self._read_strategy_getter() or "window").strip().lower()
        except Exception:
            mode = "window"
        if mode not in {"window", "full"}:
            mode = "window"
        return mode

