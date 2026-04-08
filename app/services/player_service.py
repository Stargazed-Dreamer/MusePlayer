from __future__ import annotations

"""播放服务层。

职责边界：
1. 对接底层 `PyAVPlayerCore`，提供播放/暂停/切歌/进度/音量等控制。
2. 维护歌单顺序、随机顺序（seed + idx）和播放状态快照。
3. 在“窗口读取”策略下执行分段预读取与无缝续播。
4. 统一记录播放统计（播放次数、主动播放、早期跳过、播放百分比）。
"""

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

from app.services.player_service_mixins import (
    PlayerServiceLazyDecodeMixin,
    PlayerServiceStatsMixin,
)

logger = logging.getLogger("museplayer.player")

class PlayMode(str, Enum):
    SINGLE_LOOP = "single_loop"
    PLAYLIST_LOOP = "playlist_loop"
    RANDOM = "random"

class PlayerService(PlayerServiceStatsMixin, PlayerServiceLazyDecodeMixin, QObject):
    """播放器核心服务。

    结构说明：
    - 本类保留“播放编排 + 歌单/随机队列控制”主流程。
    - 统计相关逻辑下沉到 `PlayerServiceStatsMixin`。
    - 懒加载解码与预读取逻辑下沉到 `PlayerServiceLazyDecodeMixin`。
    """

    # Qt信号定义
    track_changed = Signal(object)  # 当前曲目变化时发出
    queue_changed = Signal()  # 播放队列（歌单）变化时发出
    progress_changed = Signal(float, float)  # 播放进度变化：位置, 时长
    playback_changed = Signal(bool)  # 播放状态变化：是否正在播放
    mode_changed = Signal(str)  # 播放模式变化
    error_occurred = Signal(str)  # 发生错误时发出
    random_state_changed = Signal(int, int)  # 随机播放状态变化：种子, 索引
    playback_rate_changed = Signal(float)  # 播放速率变化
    _LAZY_WINDOW_SEC = 6.2
    # 保留少量重叠窗口，降低切块边界处的解码震荡。
    _LAZY_PREFETCH_OVERLAP_SEC = 0.50
    # 在窗口尾部提前切换，尽量避免“先停再重载”造成的听感卡顿。
    _LAZY_SWITCH_AHEAD_SEC = 0.12
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
        self._single_loop_enabled = True
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
        self._stats_suspended_depth = 0
        self._lazy_window_mode = False
        self._lazy_window_base_sec = 0.0
        self._lazy_promoted_to_full = False
        self._prefetch_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="museplayer-prefetch")
        self._prefetch_future: Future | None = None
        self._prefetch_track_id: str | None = None
        self._prefetch_start_sec = 0.0
        self._prefetch_transition_sec = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(20)
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

    def rebind_output_device(self) -> bool:
        """在系统输出设备变更后重绑音频输出。"""
        try:
            self._core.rebind_output_device()
            logger.info("音频输出设备已重绑定")
            return True
        except Exception as exc:
            msg = f"重绑音频输出失败: {exc}"
            logger.exception(msg)
            self.error_occurred.emit(msg)
            return False

    def set_output_device(self, device_name: str) -> bool:
        try:
            self._core.set_output_device(device_name if device_name else None)
            logger.info("输出设备已切换: %s", device_name or "跟随系统")
            return True
        except Exception as exc:
            msg = f"切换输出设备失败: {exc}"
            logger.exception(msg)
            self.error_occurred.emit(msg)
            return False

    def available_modes(self) -> list[str]:
        modes: list[str] = []
        if self._single_loop_enabled:
            modes.append(PlayMode.SINGLE_LOOP.value)
        if self._playlist_loop_enabled:
            modes.append(PlayMode.PLAYLIST_LOOP.value)
        modes.append(PlayMode.RANDOM.value)
        return modes

    def _fallback_mode_for_disabled_option(self) -> PlayMode:
        if self._single_loop_enabled:
            return PlayMode.SINGLE_LOOP
        if self._playlist_loop_enabled:
            return PlayMode.PLAYLIST_LOOP
        return PlayMode.RANDOM

    def set_single_loop_mode_enabled(self, enabled: bool) -> None:
        self._single_loop_enabled = bool(enabled)
        if not self._single_loop_enabled and self._mode == PlayMode.SINGLE_LOOP:
            self.set_mode(self._fallback_mode_for_disabled_option())
        else:
            self.mode_changed.emit(self._mode.value)

    def set_playlist_loop_mode_enabled(self, enabled: bool) -> None:
        self._playlist_loop_enabled = bool(enabled)
        if not self._playlist_loop_enabled and self._mode == PlayMode.PLAYLIST_LOOP:
            self.set_mode(self._fallback_mode_for_disabled_option())
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

        if parsed == PlayMode.SINGLE_LOOP and not self._single_loop_enabled:
            parsed = self._fallback_mode_for_disabled_option()
        if parsed == PlayMode.PLAYLIST_LOOP and not self._playlist_loop_enabled:
            parsed = self._fallback_mode_for_disabled_option()

        self._mode = parsed
        logger.info("播放模式切换: %s", self._mode.value)
        self.mode_changed.emit(self._mode.value)

        if self._mode == PlayMode.RANDOM:
            self._rebuild_random_order(force=True)
            self._align_random_index_with_current_track()

    def reseed_random_after_current_track_removed(self) -> str | None:
        """当前播放曲目被移除后，重建随机序并切到新序列首曲。"""
        if self._mode != PlayMode.RANDOM:
            return None
        track_ids = self._playlist_track_ids()
        if not track_ids:
            self._current_track_id = None
            self._loaded_track_id = None
            self._random_order = []
            self._random_index = 0
            self.random_state_changed.emit(self._random_seed, self._random_index)
            self.track_changed.emit(None)
            self.queue_changed.emit()
            return None
        self._random_seed += 1
        self._rebuild_random_order(force=True)
        if self._random_order:
            self._random_index = 0
            self._current_track_id = self._random_order[0]
        else:
            self._current_track_id = track_ids[0]
            self._random_index = 0
        self._sequential_index = track_ids.index(self._current_track_id)
        self._align_random_index_with_current_track()
        self.track_changed.emit(self.current_track())
        self.queue_changed.emit()
        return self._current_track_id

    def set_playlist(self, playlist_id: str | None) -> None:
        """切换当前播放歌单，执行完整的状态迁移。
        
        处理播放状态保持、随机序列重构建、早期跳过统计记录等复杂逻辑。
        是一个原子性的状态切换操作。
        
        Args:
            playlist_id: 目标歌单ID，None表示使用当前活动歌单
        """
        # 切歌单是"状态迁移"过程：要同时处理播放状态、随机序和早期跳过统计。
        self._reset_lazy_prefetch(cancel=True)
        was_playing = self.is_playing()
        prev_track_for_skip = self._loaded_track_id or self._current_track_id
        prev_position_for_skip = float(self._safe_position())
        prev_duration_for_skip = float(self._safe_duration())
        playlist = self.library.get_playlist(playlist_id)
        self._current_playlist_id = playlist.id
        self.library.set_active_playlist(playlist.id)

        track_ids = self._playlist_track_ids()
        previous_track_id = self._current_track_id
        if not track_ids:
            self._record_early_skip_if_needed(
                skipped_track_id=prev_track_for_skip,
                position=prev_position_for_skip,
                duration=prev_duration_for_skip,
                next_track_id=None,
            )
            try:
                self._core.pause()
                self._core.unload()
            except Exception:
                pass
            self._current_track_id = None
            self._loaded_track_id = None
            self._stats_last_track_id = None
            self._stats_last_position = 0.0
            self._lazy_window_mode = False
            self._lazy_promoted_to_full = False
            self._lazy_window_base_sec = 0.0
            self._expecting_natural_end = False
            self.track_changed.emit(None)
            self.queue_changed.emit()
            self._emit_playback_state(force=True)
            return

        current_in_target = self._current_track_id in track_ids
        if not current_in_target:
            self._current_track_id = track_ids[0]

        self._rebuild_random_order(force=True)
        if not current_in_target and self._mode == PlayMode.RANDOM and self._random_order:
            self._random_index = 0
            self._current_track_id = self._random_order[0]
            self.random_state_changed.emit(self._random_seed, self._random_index)

        self._sequential_index = track_ids.index(self._current_track_id)
        self._align_random_index_with_current_track()

        if was_playing and not current_in_target and self._current_track_id:
            try:
                self._core.pause()
            except Exception:
                pass
            self._expecting_natural_end = False
            self._load_current_track(auto_play=False, start_sec=0.0, active_request=False)
            self._record_early_skip_if_needed(
                skipped_track_id=prev_track_for_skip,
                position=prev_position_for_skip,
                duration=prev_duration_for_skip,
                next_track_id=self._current_track_id,
            )

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
        """暂停播放。
        
        暂停当前播放并重置"期待自然结束"的状态标志。
        """
        try:
            self._core.pause()
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            return
        self._expecting_natural_end = False
        logger.info("暂停: %s", self._current_track_id)
        self._emit_playback_state()

    def toggle_play_pause(self) -> None:
        """切换播放/暂停状态。
        
        如果正在播放则暂停，如果已暂停则继续播放。
        """
        if self.is_playing():
            self.pause()
        else:
            self.play()

    def seek(self, sec: float) -> None:
        """寻求到指定的播放位置。
        
        在懒加载模式下会触发窗口重新加载，在普通模式下直接调用内核。
        会自动标记统计跳过，避免产生无效的播放增量数据。
        
        Args:
            sec: 目标位置（秒）
        """
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
        """设置播放音量。
        
        Args:
            volume: 音量值(0.0-1.0的浮点数，对应0%-100%)
        """
        percent = int(round(float(volume) * 100.0))
        self.set_gain_percent(percent, allow_boost=True)

    def adjust_volume(self, delta: float) -> float:
        """相对调整音量。
        
        Args:
            delta: 音量变化量（相对于当前值的增量）
            
        Returns:
            调整后的实际音量值
        """
        self.set_volume(self.volume() + delta)
        return self.volume()

    def volume(self) -> float:
        """获取当前音量值。
        
        Returns:
            当前音量(0.0-1.0)
        """
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
        """检查当前是否正在播放。
        
        Returns:
            bool: 是否正在播放状态
        """
        try:
            return self._core.is_playing()
        except Exception:
            return False

    def current_track(self) -> Track | None:
        """获取当前选择的曲目信息。
        
        Returns:
            当前曲目的Track对象，如果没有选择则返回None
        """
        return self.library.get_track(self._current_track_id)

    def playlist_tracks(self) -> list[Track]:
        """获取当前播放列表的所有曲目。
        
        Returns:
            当前播放列表的Track对象列表
        """
        return self.library.get_playlist_tracks(self._current_playlist_id)

    def search_playlist_tracks(self, keyword: str) -> list[Track]:
        """在当前播放列表中搜索曲目。
        
        Args:
            keyword: 搜索关键词
            
        Returns:
            匹配的Track对象列表
        """
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
        """播放指定曲目，处理复杂的播放逻辑和状态转换。
        
        支持多种播放场景：用户手动选择、播放列表切换、随机导航等。
        会自动调整播放列表、随机序列、记录播放统计和早期跳过。
        
        Args:
            track_id: 要播放的曲目ID
            auto_play: 是否自动开始播放
            start_sec: 开始播放的位置（秒）
            manual_select: 是否为用户手动选择
            from_random_navigation: 是否来自随机模式导航
            active_request: 是否为主动播放请求（用于统计）
            
        Returns:
            bool: 是否成功切换到目标曲目
        """
        self._reset_lazy_prefetch(cancel=True)
        track = self.library.get_track(track_id)
        if track is None:
            self.error_occurred.emit(f"歌曲不存在: {track_id}")
            return False

        playlist_ids = self._playlist_track_ids()
        if track_id not in playlist_ids:
            self.set_playlist("all_songs")
            playlist_ids = self._playlist_track_ids()

        previous_track_id = self._loaded_track_id or self._current_track_id
        previous_position = float(self._safe_position())
        previous_duration = float(self._safe_duration())

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

        self._record_early_skip_if_needed(
            skipped_track_id=previous_track_id,
            position=previous_position,
            duration=previous_duration,
            next_track_id=track_id,
        )

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
        """切换到下一首曲目，支持不同播放模式。
        
        在随机模式下会根据随机序列切换到下一首，如果当前曲目无法播放
        会继续尝试后续曲目直到找到可播放的曲目。
        
        Args:
            user_triggered: 是否为用户触切的切换（用于统计记录）
            
        Returns:
            bool: 是否成功切换到下一曲
        """
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
        """切换到上一首曲目。
        
        在随机模式下会向前移动随机序列，在其他模式下向前移动播放列表。
        自动处理循环边界和无效曲目的跳过。
        
        Returns:
            bool: 是否成功切换到上一曲
        """
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
        """导出当前会话状态。
        
        包含播放器状态、歌单信息、播放位置、音量设置等完整状态信息，
        用于应用重启后的状态恢复。
        
        Returns:
            SessionState对象，包含完整的会话状态
        """
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
        """恢复会话状态。
        
        还原播放器设置、歌单、随机序列、当前曲目和播放位置等所有状态。
        
        Args:
            state: 要恢复的会话状态对象
        """
        # 会话恢复属于系统行为，不计入用户行为统计。
        with self.suspend_stats_collection():
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
        """
        生成当前播放状态的快照字典。
        用于记录播放器状态（如播放列表ID、曲目ID、播放位置、播放状态等），便于保存或恢复。
        """
        track = self.current_track()  # 获取当前播放的曲目对象
        duration = self._safe_duration()  # 安全获取当前曲目总时长（避免异常）
        position = self._safe_position()  # 安全获取当前播放位置（避免异常）
        return {
            "playlist_id": self._current_playlist_id,  # 当前播放列表ID
            "track_id": self._current_track_id,        # 当前曲目ID
            "track_title": track.title if track else "",  # 曲目标题（若存在）
            "position_sec": position,                  # 当前播放位置（秒）
            "duration_sec": duration,                  # 曲目总时长（秒）
            "playing": self.is_playing(),              # 是否正在播放
            "mode": self._mode.value,                  # 播放模式（如顺序、随机、单曲循环）
            "volume": self._gain_percent / 100.0,      # 音量（归一化到0.0-1.0）
            "random_seed": self._random_seed,          # 随机播放种子，用于生成确定性随机顺序
            "random_index": self._random_index,        # 当前在随机顺序列表中的索引
            "playback_rate": self._playback_rate,      # 播放速率（如1.0为正常速度）
        }

    def _set_initial_track_for_playlist(self) -> None:
        """
        为当前播放列表设置初始曲目。
        确保当播放列表变更时，能正确设置当前曲目ID，并维护随机顺序和索引。
        """
        track_ids = self._playlist_track_ids()  # 获取当前播放列表中的所有曲目ID
        if not track_ids:  # 如果播放列表为空
            self._current_track_id = None
            self._loaded_track_id = None
            return
        if self._current_track_id not in track_ids:  # 当前曲目不在播放列表中
            self._current_track_id = track_ids[0]     # 设为列表第一首
        self._sequential_index = track_ids.index(self._current_track_id)  # 更新顺序播放索引

        self._rebuild_random_order(force=True)        # 强制重建随机播放顺序
        self._align_random_index_with_current_track()  # 将随机索引与当前曲目对齐

    def _load_current_track(self, *, auto_play: bool, start_sec: float, active_request: bool) -> bool:
        """
        加载当前曲目到播放核心，可选择自动播放。
        Args:
            auto_play: 是否自动开始播放
            start_sec: 起始播放位置（秒）
            active_request: 是否由用户主动请求触发（影响播放记录统计）
        Returns:
            加载成功返回True，失败返回False
        """
        # 根据读取策略加载：
        # - window: 先加载短窗口并预读取后续窗口，降低切歌/跳播瞬时开销
        # - full: 一次性读取完整文件，策略简单直接
        self._reset_lazy_prefetch(cancel=True)  # 重置延迟预读取（取消预取）
        track = self.current_track()
        if track is None:  # 没有当前曲目
            self.error_occurred.emit("当前没有可播放歌曲")
            return False
        source = Path(track.path)
        target_start = max(0.0, float(start_sec))
        strategy = self._read_strategy()
        try:
            if auto_play and strategy == "window":  # 自动播放且策略为窗口模式
                self._core.load(source, start_sec=target_start, window_sec=self._LAZY_WINDOW_SEC)
                loaded_sec = max(0.0, float(self._core.duration()))
                if loaded_sec <= 0.02:  # 加载的窗口太短（可能文件很短或异常）
                    self._core.load(source)  # 回退到完整加载
                    self._lazy_window_mode = False
                    self._lazy_window_base_sec = 0.0
                    self._lazy_promoted_to_full = True
                else:
                    self._lazy_window_mode = True
                    self._lazy_window_base_sec = target_start
                    self._lazy_promoted_to_full = False
            else:  # 非自动播放或策略为完整模式
                self._core.load(source)
                self._lazy_window_mode = False
                self._lazy_window_base_sec = 0.0
                self._lazy_promoted_to_full = True
            self._apply_core_volume()  # 应用当前音量设置
            self._core.set_playback_rate(self._playback_rate)  # 设置播放速率
            if auto_play:  # 需要自动播放
                self._core.play(0.0 if self._lazy_window_mode else target_start)  # 窗口模式从0开始（因为窗口已定位）
                self._expecting_natural_end = True  # 标记期待自然结束（用于自动切歌判断）
                self._record_play_start(track.id, active_request=active_request)  # 记录播放开始事件
                if self._lazy_window_mode and not self._lazy_promoted_to_full:
                    local_duration = max(0.0, float(self._core.duration()))
                    self._schedule_lazy_prefetch(track.id, target_start + local_duration)  # 调度预读下一个窗口
            else:  # 只加载不播放
                self._core.seek(target_start)
                self._expecting_natural_end = False
        except Exception as exc:  # 加载失败处理
            self.error_occurred.emit(f"加载失败: {source} -> {exc}")
            return False

        # 加载成功后的状态更新
        self._loaded_track_id = track.id  # 记录已加载的曲目ID
        self._stats_last_track_id = track.id
        self._stats_last_position = target_start
        self._stats_skip_next_delta = True
        self._emit_playback_state(force=True)  # 强制发射播放状态变更信号
        self.progress_changed.emit(self._safe_position(), self._safe_duration())
        return True

    def _playlist_track_ids(self) -> list[str]:
        """
        获取当前播放列表中存在的所有曲目ID。
        过滤掉曲目库中不存在的ID，确保返回的ID都是有效的。
        """
        playlist = self.library.get_playlist(self._current_playlist_id)
        return [track_id for track_id in playlist.track_ids if track_id in self.library.tracks]

    def _rebuild_random_order(self, *, force: bool) -> None:
        """
        重建随机播放顺序列表。
        当播放列表曲目变更或强制重建时，根据随机种子重新生成确定性的随机顺序。
        """
        track_ids = self._playlist_track_ids()
        if not track_ids:  # 播放列表为空
            self._random_order = []
            self._random_index = 0
            self.random_state_changed.emit(self._random_seed, self._random_index)
            return

        # 强制重建 或 当前随机顺序与播放列表不一致（集合比较）
        if force or set(self._random_order) != set(track_ids) or len(self._random_order) != len(track_ids):
            self._random_order = DeterministicShuffle.make_order(track_ids, self._random_seed)
            self._random_index = DeterministicShuffle.clamp_index(self._random_order, self._random_index)
            self.random_state_changed.emit(self._random_seed, self._random_index)

    def _align_random_index_with_current_track(self) -> None:
        """
        将随机播放索引对齐到当前播放曲目。
        确保随机索引指向的曲目与当前播放曲目一致，若不一致则调整索引或当前曲目。
        """
        if not self._random_order or self._current_track_id is None:  # 无随机顺序或无当前曲目
            self._random_index = 0
            self.random_state_changed.emit(self._random_seed, self._random_index)
            return
        if self._current_track_id in self._random_order:  # 当前曲目在随机列表中
            self._random_index = self._random_order.index(self._current_track_id)
        else:  # 当前曲目不在随机列表中（可能被移除）
            self._random_index = DeterministicShuffle.clamp_index(self._random_order, self._random_index)
            self._current_track_id = self._random_order[self._random_index]  # 将当前曲目设为随机索引指向的曲目
        self.random_state_changed.emit(self._random_seed, self._random_index)

    def _on_tick(self) -> None:
        """
        播放器定时器触发的主更新函数。
        处理进度更新、延迟窗口模式推进、自然结束检测、状态信号发射等。
        """
        duration = self._safe_duration()
        position = self._safe_position()
        playing = self.is_playing()

        # 尝试将延迟窗口模式升级为完整解码（如果接近窗口边界）
        self._maybe_promote_lazy_full_decode(position=position, playing=playing)
        # 如果正在播放，尝试继续推进延迟窗口
        if playing and self._try_continue_lazy_window_while_playing(position=position, duration=duration):
            # 窗口可能已推进，重新获取时长和位置
            duration = self._safe_duration()
            position = self._safe_position()
            playing = self.is_playing()
        # 记录播放进度（用于统计等）
        self._record_playback_progress(position=position, duration=duration, playing=playing)
        # 发射进度变更信号
        self.progress_changed.emit(position, duration)

        # 检测从播放到停止的转变（如播放自然结束）
        if self._last_playing and not playing:
            # 检查是否需要继续延迟窗口（例如暂停后恢复）
            if self._continue_lazy_window_if_needed(position=position, duration=duration):
                playing = self.is_playing()
            # 如果期待自然结束且当前已接近曲目末尾，则处理自然结束逻辑
            elif self._expecting_natural_end and (duration <= 0 or position >= max(0.0, duration - 0.10)):
                self._expecting_natural_end = False
                self._handle_natural_finished()  # 处理自然结束（切歌或单曲循环）

        # 发射播放状态变更信号（如果状态改变或强制）
        self._emit_playback_state(current=playing)
        self._last_playing = playing  # 保存当前播放状态供下次比较

    def _handle_natural_finished(self) -> None:
        """
        处理当前曲目自然播放结束的逻辑。
        根据播放模式（如单曲循环）决定下一步操作。
        """
        if self._mode == PlayMode.SINGLE_LOOP and self._current_track_id:  # 单曲循环模式
            ok = self._load_current_track(auto_play=True, start_sec=0.0, active_request=False)  # 重新从0开始播放当前曲目
            if ok:
                return
        self.next_track(user_triggered=False)  # 否则切换到下一曲（非用户触发）

    def _emit_playback_state(self, *, current: bool | None = None, force: bool = False) -> None:
        """
        发射播放状态（播放/暂停）变更的信号。
        Args:
            current: 指定的当前状态，为None则根据is_playing()获取
            force: 是否强制发射信号（即使状态未变）
        """
        playing = self.is_playing() if current is None else bool(current)
        if force or playing != self._last_playing:  # 状态改变或强制发射
            self.playback_changed.emit(playing)
