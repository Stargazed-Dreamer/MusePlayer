"""播放服务层。

职责边界：
1. 对接底层 `PyAVPlayerCore`，提供播放/暂停/切歌/进度/音量等控制。
2. 维护歌单顺序、随机顺序（seed + idx）和播放状态快照。
3. 在“窗口读取”策略下执行分段预读取与无缝续播。
4. 统一记录播放统计（播放次数、主动播放、早期跳过、播放百分比）。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from app.models.entities import SessionState, Track
from app.services.library_service import LibraryService
from app.services.playback_stats_service import PlaybackStatsService
from app.services.player_service_mixins import (
    PlayerServiceLazyDecodeMixin,
    PlayerServiceStatsMixin,
)
from app.services.random_order import DeterministicShuffle
from core import PyAVPlayerCore

logger = logging.getLogger("museplayer.player")


class PlayMode(StrEnum):
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
        """
        初始化媒体播放器控制器的核心状态和依赖。

        该方法负责设置播放器所需的各种服务、配置回调函数、初始化播放状态、
        创建播放内核以及启动定时器来维持播放状态更新。

        参数:
            library_service (LibraryService): 图书馆服务实例，用于管理媒体库和播放列表。
            playback_stats_service (PlaybackStatsService | None, optional): 播放统计服务实例。默认为None。
            collect_stats_getter (Callable[[], bool] | None, optional): 获取是否收集统计数据的回调函数。默认为None。
            gain_boost_getter (Callable[[], float] | None, optional): 获取增益提升值的回调函数。默认为None。
            read_strategy_getter (Callable[[], str] | None, optional): 获取文件读取策略的回调函数。默认为None。
            parent (QObject | None, optional): 父QObject对象，用于Qt对象树管理。默认为None。

        返回值:
            None: 初始化方法无返回值。
        """
        super().__init__(parent)  # 调用父类QObject的初始化方法
        self.library = library_service  # 保存图书馆服务实例
        self._stats = playback_stats_service  # 保存播放统计服务实例
        # 设置回调函数，如果未提供则使用默认值
        self._collect_stats_getter = collect_stats_getter or (lambda: True)  # 默认收集统计
        self._gain_boost_getter = gain_boost_getter or (lambda: self._GLOBAL_GAIN_BOOST)  # 使用全局默认增益
        self._read_strategy_getter = read_strategy_getter or (lambda: "window")  # 默认使用“窗口”读取策略
        self._core = PyAVPlayerCore()  # 创建底层的PyAV播放器核心实例

        # 初始化播放模式相关状态
        self._mode = PlayMode.SINGLE_LOOP  # 初始播放模式为单曲循环
        self._single_loop_enabled = True  # 启用单曲循环
        self._playlist_loop_enabled = False  # 禁用播放列表循环
        self._playlist_loop_sort_getter: Callable[[], str] = lambda: "default"  # 获取播放列表循环排序方式的回调
        self._prefer_playlist_order_getter: Callable[[], bool] = lambda: False  # 获取是否优先使用播放列表顺序的回调
        self._random_display_order_getter: Callable[[], str] = lambda: "original"  # 获取随机显示顺序的回调
        self._gain_percent = 80  # 初始增益百分比
        self._playback_rate = 1.0  # 初始播放速率

        # 初始化当前播放的播放列表和曲目ID
        self._current_playlist_id = self.library.active_playlist_id  # 从图书馆服务获取当前活动播放列表ID
        self._current_track_id: str | None = None  # 当前正在播放或请求播放的曲目ID
        self._loaded_track_id: str | None = None  # 已加载到播放器核心的曲目ID

        # 初始化顺序播放索引
        self._sequential_index = 0  # 顺序播放模式下的当前索引
        # 初始化随机播放相关状态
        self._random_seed = 1  # 随机种子
        self._random_index = 0  # 随机播放模式下的当前索引
        self._random_order: list[str] = []  # 随机播放顺序列表
        self._RANDOM_SEED_MAX = 2_000_000_000  # 随机种子的最大值

        # 初始化播放控制和统计相关的标志位
        self._expecting_natural_end = False  # 是否正在等待曲目自然结束
        self._last_playing = False  # 上一个tick时的播放状态
        self._stats_last_track_id: str | None = None  # 上次统计记录的曲目ID
        self._stats_last_position = 0.0  # 上次统计记录的位置
        self._stats_skip_next_delta = False  # 是否跳过下一次的位置增量统计
        self._stats_suspended_depth = 0  # 统计暂停的嵌套深度
        # 初始化延迟窗口模式相关状态
        self._lazy_window_mode = False  # 是否处于延迟窗口模式
        self._lazy_window_base_sec = 0.0  # 延迟窗口模式的基准时间（秒）
        self._lazy_promoted_to_full = False  # 是否已从延迟窗口提升为完整加载
        # 初始化预加载执行器
        self._prefetch_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="museplayer-prefetch"
        )  # 单线程的预加载线程池
        self._prefetch_future: Future | None = None  # 当前预加载任务的Future对象
        self._prefetch_track_id: str | None = None  # 正在预加载的曲目ID
        self._prefetch_start_sec = 0.0  # 预加载开始的时间点（秒）
        self._prefetch_transition_sec = 0.0  # 预加载过渡时间点（秒）

        # 创建并配置定时器，用于定期更新播放状态
        self._timer = QTimer(self)
        self._timer.setInterval(20)  # 设置定时器间隔为20毫秒（约50FPS）
        self._timer.timeout.connect(self._on_tick)  # 连接超时信号到更新槽函数
        self._timer.start()  # 启动定时器

        # 设置播放器核心的错误回调
        self._core.set_error_callback(self._on_core_runtime_error)
        # 为当前播放列表设置初始曲目
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
        """设置输出设备。

        参数:
            device_name (str): 要设置的输出设备名称。如果为空或None，则使用系统默认设备。

        返回:
            bool: 如果设置成功返回True，否则返回False。
        """
        try:
            self._core.set_output_device(
                device_name if device_name else None
            )  # 设置输出设备，如果设备名为空则使用None表示跟随系统
            logger.info("输出设备已切换: %s", device_name or "跟随系统")  # 记录日志，显示设备名或"跟随系统"
            return True
        except Exception as exc:
            msg = f"切换输出设备失败: {exc}"  # 构造错误消息字符串
            logger.exception(msg)  # 记录异常信息到日志
            self.error_occurred.emit(msg)  # 发出错误信号
            return False

    def available_modes(self) -> list[str]:
        """
        返回当前实例中启用的可用播放模式列表。

        参数：
            self: 类实例。

        返回值：
            list[str]: 可用播放模式的字符串列表。
        """
        modes: list[str] = []  # 初始化一个空列表来存储可用模式
        if self._single_loop_enabled:  # 检查是否启用了单曲循环模式
            modes.append(PlayMode.SINGLE_LOOP.value)  # 如果启用，将单曲循环模式添加到列表
        if self._playlist_loop_enabled:  # 检查是否启用了列表循环模式
            modes.append(PlayMode.PLAYLIST_LOOP.value)  # 如果启用，将列表循环模式添加到列表
        modes.append(PlayMode.RANDOM.value)  # 总是添加随机模式
        return modes  # 返回可用模式列表

    def _fallback_mode_for_disabled_option(self) -> PlayMode:
        if self._single_loop_enabled:
            return PlayMode.SINGLE_LOOP
        if self._playlist_loop_enabled:
            return PlayMode.PLAYLIST_LOOP
        return PlayMode.RANDOM

    def set_single_loop_mode_enabled(self, enabled: bool) -> None:
        """
        设置单循环模式是否启用。

        参数:
            enabled (bool): 是否启用单循环模式。

        返回值:
            None
        """
        self._single_loop_enabled = bool(enabled)  # 将参数转换为布尔值并设置单循环启用状态
        if (
            not self._single_loop_enabled and self._mode == PlayMode.SINGLE_LOOP
        ):  # 如果单循环模式被禁用且当前模式是单循环模式
            self.set_mode(self._fallback_mode_for_disabled_option())  # 设置为后备模式
        else:
            self.mode_changed.emit(self._mode.value)  # 发射模式改变信号，传递当前模式的值

    def set_playlist_loop_mode_enabled(self, enabled: bool) -> None:
        """设置播放列表循环模式的启用状态。

        参数:
            enabled (bool): 是否启用播放列表循环模式。

        返回:
            None: 此方法不返回任何值。
        """
        self._playlist_loop_enabled = bool(enabled)  # 将输入参数转换为布尔值并存储循环模式启用状态
        if (
            not self._playlist_loop_enabled and self._mode == PlayMode.PLAYLIST_LOOP
        ):  # 如果循环模式被禁用且当前播放模式是循环播放列表
            self.set_mode(self._fallback_mode_for_disabled_option())  # 则将播放模式切换为禁用时的回退模式
        else:  # 否则
            self.mode_changed.emit(self._mode.value)  # 发出模式改变信号，通知其他组件

    def set_playlist_loop_sort_getter(self, getter: Callable[[], str]) -> None:
        self._playlist_loop_sort_getter = getter

    def set_prefer_playlist_order_getter(self, getter: Callable[[], bool]) -> None:
        self._prefer_playlist_order_getter = getter

    def set_random_display_order_getter(self, getter: Callable[[], str]) -> None:
        self._random_display_order_getter = getter

    def _increment_random_seed(self) -> None:
        self._random_seed += 1
        if self._random_seed > self._RANDOM_SEED_MAX:
            self._random_seed = 1

    def set_mode(self, mode: str | PlayMode) -> None:
        """设置播放模式。

        本方法用于切换音乐播放器的当前模式。
        支持通过字符串或直接传入PlayMode枚举值来指定目标模式。

        Args:
            mode (str | PlayMode): 指定的播放模式。
                - 当传入str时，会进行标准化（去除首尾空格并转为小写）并匹配为PlayMode枚举。
                  已知的字符串别名包括：'single_loop', 'repeat_one', 'single', 'one', 'loop',
                  'playlist_loop', 'list_loop', 'sequential', 'random', 'shuffle'。
                  若字符串无法匹配任何已知模式，则默认回退到PlayMode.SINGLE_LOOP。
                - 当直接传入PlayMode枚举值时，将直接使用该值。

        Returns:
            None: 此方法无返回值。
        """
        # 如果传入的是字符串，需要解析并转换为对应的PlayMode枚举值
        if isinstance(mode, str):
            # 对字符串进行清理：去除首尾空白并转为小写，以便标准化匹配
            cleaned = mode.strip().lower()
            # 根据清理后的字符串匹配预定义的模式
            if cleaned in {"single_loop", "repeat_one", "single", "one", "loop"}:
                # 解析为单曲循环模式
                parsed = PlayMode.SINGLE_LOOP
            elif cleaned in {"playlist_loop", "list_loop", "sequential"}:
                # 解析为列表循环模式
                parsed = PlayMode.PLAYLIST_LOOP
            elif cleaned in {"random", "shuffle"}:
                # 解析为随机播放模式
                parsed = PlayMode.RANDOM
            else:
                # 当传入的字符串无法识别时，默认回退到单曲循环模式
                parsed = PlayMode.SINGLE_LOOP
        else:
            # 如果传入的已经是PlayMode枚举，则直接使用
            parsed = mode

        # 检查目标模式是否被系统启用，若未启用则使用回退模式
        # 如果目标是单曲循环但该功能未启用，则切换到回退模式
        if parsed == PlayMode.SINGLE_LOOP and not self._single_loop_enabled:
            parsed = self._fallback_mode_for_disabled_option()
        # 如果目标是列表循环但该功能未启用，则切换到回退模式
        if parsed == PlayMode.PLAYLIST_LOOP and not self._playlist_loop_enabled:
            parsed = self._fallback_mode_for_disabled_option()

        # 将最终解析后的模式设置为当前模式
        self._mode = parsed
        # 记录模式切换的日志信息
        logger.info("播放模式切换: %s", self._mode.value)
        # 发射模式改变信号，通知其他组件
        self.mode_changed.emit(self._mode.value)

        # 如果最终模式是随机播放，则需要重新构建播放顺序并同步索引
        if self._mode == PlayMode.RANDOM:
            # 强制重建随机播放队列
            self._rebuild_random_order(force=True)
            # 将随机播放索引与当前播放曲目对齐
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
        self._increment_random_seed()
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
        if playlist is None:
            return
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
            with contextlib.suppress(Exception):
                self._core.pause()
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
        """开始播放当前曲目或从暂停状态恢复播放。

        功能说明：
        - 如果当前没有设置曲目ID，则先为播放列表设置初始曲目
        - 如果已加载的曲目与当前曲目不同，则加载新曲目
        - 如果曲目相同，则根据播放模式（正常模式/懒加载窗口模式）恢复播放
        - 播放过程中会处理可能的异常情况

        参数：
        无参数

        返回值：
        无返回值（None）
        """
        if self._current_track_id is None:
            # 如果当前没有播放曲目，尝试为播放列表设置初始曲目
            self._set_initial_track_for_playlist()
            # 如果设置后仍然没有曲目ID，则直接返回
            if self._current_track_id is None:
                return

        # 检查当前加载的曲目是否与要播放的曲目相同
        if self._loaded_track_id != self._current_track_id:
            # 如果不同，加载当前曲目（自动播放，从0秒开始，非活跃请求）
            ok = self._load_current_track(auto_play=True, start_sec=0.0, active_request=False)
            # 如果加载失败，直接返回
            if not ok:
                return
        else:
            # 如果已加载的曲目与当前曲目相同，则尝试从当前位置继续播放
            try:
                # 检查是否处于懒加载窗口模式且尚未提升为完整播放
                if self._lazy_window_mode and not self._lazy_promoted_to_full:
                    # 获取本地播放的起始位置（当前播放时间，最小为0）
                    local_start = max(0.0, float(self._core.current_time()))
                    # 获取本地播放的总时长（最小为0）
                    local_duration = max(0.0, float(self._core.duration()))
                    # 获取绝对播放位置（安全位置，最小为0）
                    absolute_start = max(0.0, float(self._safe_position()))
                    # 判断是否已到达懒加载窗口的末尾（窗口时长大于0且当前位置接近窗口末尾）
                    at_window_end = local_duration > 0.0 and local_start >= max(0.0, local_duration - 0.05)

                    if at_window_end:
                        # 如果到达窗口末尾，重新加载懒加载窗口（保持播放状态）
                        self._reload_lazy_window(absolute_start, keep_playing=True)
                    else:
                        # 否则，从本地开始位置继续播放
                        self._core.play(local_start)
                        # 设置标志表示预期自然结束
                        self._expecting_natural_end = True
                else:
                    # 在正常模式下，从当前位置继续播放
                    start = self._core.current_time()
                    self._core.play(start)
                    # 设置标志表示预期自然结束
                    self._expecting_natural_end = True
            except Exception as exc:
                # 捕获播放过程中可能出现的异常
                # 如果是特定解码缓冲丢失错误
                if "No decoded audio loaded" in str(exc):
                    # 获取上次记录的播放位置作为恢复位置
                    resume_at = max(0.0, float(self._stats_last_position))
                    # 尝试重新加载当前曲目（从恢复位置开始自动播放）
                    ok = self._load_current_track(auto_play=True, start_sec=resume_at, active_request=False)
                    if ok:
                        # 记录警告日志
                        logger.warning("播放前发现解码缓冲丢失，已自动重载: %s", self._current_track_id)
                        # 强制更新播放状态
                        self._emit_playback_state(force=True)
                        return
                # 对于其他异常，发出错误信号并返回
                self.error_occurred.emit(str(exc))
                return

        # 记录播放信息日志
        logger.info("播放: %s", self._current_track_id)
        # 更新播放状态
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
        """设置增益百分比。

        参数:
            percent (int): 增益百分比，整数。
            allow_boost (bool): 是否允许提升，布尔值。

        返回:
            int: 设置后的增益百分比。
        """
        max_value = 500 if allow_boost else 100  # 根据是否允许提升，设置最大值
        value = max(0, min(max_value, int(percent)))  # 将百分比转换为整数，并限制在0到最大值之间
        self._gain_percent = value
        with contextlib.suppress(Exception):  # 忽略任何异常
            self._apply_core_volume()  # 尝试应用核心音量
        return self._gain_percent  # 返回设置后的增益百分比

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
        """
        设置播放速率。

        该方法用于调整媒体的播放速度。
        输入值会被限制在0.25倍到4.0倍之间。
        如果底层媒体核心设置失败，则静默忽略异常。
        无论成功与否，都会发射播放速率变化的信号。

        参数:
            rate (float): 目标播放速率。

        返回:
            None: 此方法不返回任何值。
        """
        # 将输入的播放速率限制在0.25到4.0的范围内，并确保其为浮点数类型。
        value = max(0.25, min(4.0, float(rate)))
        # 存储经过范围限制的播放速率值。
        self._playback_rate = value
        with contextlib.suppress(
            Exception
        ):  # 如果设置过程中发生任何异常（例如底层播放器不支持），则静默忽略，不中断程序执行。
            # 尝试在底层媒体播放核心中设置播放速率。
            self._core.set_playback_rate(value)
        # 向外部发送播放速率已更改的通知信号，传递当前的播放速率值。
        self.playback_rate_changed.emit(self._playback_rate)

    def playback_rate(self) -> float:
        return float(self._playback_rate)

    def refresh_output_gain(self) -> None:
        """刷新输出增益。通过调用内部方法 _apply_core_volume() 来应用核心音量设置。如果调用失败，静默忽略异常。

        参数：无（除了隐含的 self）。
        返回值：None。
        """
        with contextlib.suppress(Exception):  # 如果发生异常，静默忽略以防止程序崩溃
            self._apply_core_volume()  # 尝试应用核心音量

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

        idx = track_ids.index(current_id) if current_id in track_ids else 0
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
        preserve_random: bool = False,
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
            if manual_select and not preserve_random:
                self._increment_random_seed()
                self._rebuild_random_order(force=True)
                self._align_random_index_with_current_track()
            elif preserve_random:
                self._rebuild_random_order(force=False)
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

        # 检查上一首歌是否完播（95%以上）
        self._check_and_record_complete_play(
            track_id=previous_track_id,
            position=previous_position,
            duration=previous_duration,
        )

        logger.info("切换歌曲: %s", track_id)
        self.track_changed.emit(self.current_track())
        return True

    def play_file(self, file_path: Path, *, active_request: bool = True) -> bool:
        """播放指定路径的文件。

        此方法尝试将给定文件导入到当前播放列表，然后播放该文件。
        如果导入过程中发生错误，会发射错误信号并返回 False。
        成功导入后，会发射队列变更信号并播放该音轨。

        Args:
            file_path (Path): 要播放的音频文件的路径。
            active_request (bool, optional): 是否作为主动请求播放。默认为 True。

        Returns:
            bool: 如果文件导入并开始播放成功，返回 True；如果导入失败，返回 False。
        """
        try:
            # 尝试将文件导入到当前播放列表库中，并获取对应的音轨对象
            track = self.library.import_file(file_path=file_path, playlist_id=self._current_playlist_id)
        except Exception as exc:
            # 如果导入过程中发生任何异常，发射错误信号并返回失败
            self.error_occurred.emit(str(exc))
            return False

        # 导入成功，发射信号通知队列状态已更新
        self.queue_changed.emit()
        # 调用播放音轨方法，并传递相关参数
        return self.play_track(
            track.id,
            auto_play=True,  # 导入后自动开始播放
            start_sec=0.0,  # 从音轨起始位置开始播放
            manual_select=True,  # 标记为手动选择播放（可能用于区分自动播放）
            active_request=active_request,  # 传递是否为活跃播放请求的标志
        )

    def next_track(self, *, user_triggered: bool = True) -> bool:
        """
        切换到播放列表中的下一个音轨。

        参数：
            user_triggered (bool, 可选): 是否由用户触发，默认为 True。当由系统自动切换时，可以设置为 False。

        返回值：
            bool: 如果成功切换到下一个音轨，返回 True；否则返回 False。
        """
        # 获取播放列表中所有音轨的ID列表
        track_ids = self._playlist_track_ids()
        # 如果播放列表为空，无法切换音轨，返回 False
        if not track_ids:
            return False

        # 检查播放模式是否为随机模式
        if self._mode == PlayMode.RANDOM:
            # 重建随机播放顺序，仅在必要时强制重建
            self._rebuild_random_order(force=False)
            # 如果随机顺序为空，无法播放，返回 False
            if not self._random_order:
                return False

            # 设置最大尝试次数，防止无限循环
            attempts = len(self._random_order)
            while attempts > 0:
                # 尝试找到当前音轨在随机顺序中的索引
                if self._current_track_id in self._random_order:
                    self._random_index = self._random_order.index(self._current_track_id)
                else:
                    # 如果当前音轨不在随机顺序中，调整索引到有效范围
                    self._random_index = DeterministicShuffle.clamp_index(self._random_order, self._random_index)

                # 检查是否已到随机顺序的末尾，如果是，增加随机种子并重建顺序
                if self._random_index + 1 >= len(self._random_order):
                    self._increment_random_seed()
                    self._rebuild_random_order(force=True)
                    self._random_index = 0
                else:
                    # 否则，移动到下一个索引
                    self._random_index += 1

                # 发出随机状态变化信号
                self.random_state_changed.emit(self._random_seed, self._random_index)
                # 获取目标音轨ID
                target_id = self._random_order[self._random_index]
                # 尝试播放目标音轨
                if self.play_track(
                    target_id,
                    auto_play=True,
                    start_sec=0.0,
                    from_random_navigation=True,
                    active_request=False,
                ):
                    # 播放成功，返回 True
                    return True
                # 播放失败，减少尝试次数
                attempts -= 1

            # 所有尝试均失败，发出错误信号
            self.error_occurred.emit("歌单内歌曲均无法播放")
            return False

        # 如果播放模式为播放列表循环
        if self._mode == PlayMode.PLAYLIST_LOOP:
            # 获取排序模式和是否优先使用播放列表顺序
            sort_mode = self._playlist_loop_sort_getter()
            prefer_order = self._prefer_playlist_order_getter()
            # 优先使用播放列表顺序则直接用原始列表，否则按排序模式排序
            sorted_ids = track_ids if prefer_order else self._sorted_playlist_track_ids(sort_mode)
        else:
            # 其他模式，使用原始播放列表
            sorted_ids = track_ids

        # 尝试找到当前音轨在排序列表中的索引，不在则从起始位置开始
        idx = sorted_ids.index(self._current_track_id) if self._current_track_id in sorted_ids else -1

        # 设置最大尝试次数
        attempts = len(sorted_ids)
        while attempts > 0:
            # 循环到下一个音轨索引，使用模运算实现循环
            idx = (idx + 1) % len(sorted_ids)
            target_id = sorted_ids[idx]
            # 尝试播放目标音轨
            if self.play_track(
                target_id,
                auto_play=True,
                start_sec=0.0,
                manual_select=False,
                active_request=False,
            ):
                # 播放成功，返回 True
                return True
            # 播放失败，减少尝试次数
            attempts -= 1

        # 所有尝试均失败，发出错误信号
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

        idx = track_ids.index(self._current_track_id) if self._current_track_id in track_ids else 0
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
        position = self._safe_position()
        track = self.current_track()

        return SessionState(
            current_playlist_id=self._current_playlist_id,
            current_track_id=self._current_track_id,
            position_sec=position,
            volume=self._gain_percent / 100.0,
            play_mode=self._mode.value,
            random_seed=self._random_seed,
            random_index=self._random_index,
            current_track_path=str(track.path) if track else "",
            current_track_title=str(track.title) if track else "",
            current_track_artist=str(track.artist) if track else "",
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
            "track_id": self._current_track_id,  # 当前曲目ID
            "track_title": track.title if track else "",  # 曲目标题（若存在）
            "position_sec": position,  # 当前播放位置（秒）
            "duration_sec": duration,  # 曲目总时长（秒）
            "playing": self.is_playing(),  # 是否正在播放
            "mode": self._mode.value,  # 播放模式（如顺序、随机、单曲循环）
            "volume": self._gain_percent / 100.0,  # 音量（归一化到0.0-1.0）
            "random_seed": self._random_seed,  # 随机播放种子，用于生成确定性随机顺序
            "random_index": self._random_index,  # 当前在随机顺序列表中的索引
            "playback_rate": self._playback_rate,  # 播放速率（如1.0为正常速度）
        }

    def _set_initial_track_for_playlist(self) -> None:
        """
        为当前播放列表设置初始曲目。
        确保当播放列表变更时，能正确设置当前曲目ID，并维护随机顺序和索引。
        """
        track_ids = self._playlist_track_ids()  # 获取当前播放列表中的所有曲目ID
        if not track_ids:  # 如果播放列表为空（库未加载或确实为空）
            self._current_track_id = None
            self._loaded_track_id = None
            return
        if self._current_track_id not in track_ids:  # 当前曲目不在播放列表中
            self._current_track_id = track_ids[0]  # 设为列表第一首
        self._sequential_index = track_ids.index(self._current_track_id)  # 更新顺序播放索引

        self._rebuild_random_order(force=True)  # 强制重建随机播放顺序
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
        # - full: 流式加载（边读边播），解码完成后整文件驻留内存可任意拖动
        self._reset_lazy_prefetch(cancel=True)  # 重置延迟预读取（取消预取）
        track = self.current_track()
        if track is None:  # 没有当前曲目
            self.error_occurred.emit("当前没有可播放歌曲")
            return False
        source = Path(track.path)
        target_start = max(0.0, float(start_sec))
        strategy = self._read_strategy()
        try:
            if strategy == "window":  # 窗口策略下始终只加载当前位置附近的数据
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
            else:  # 完整模式：流式加载（边读边播）
                total_duration_sec = max(0.0, float(track.duration_sec))
                if total_duration_sec > 0.0:
                    # 流式加载：预分配整文件缓冲，后台分块解码，播放可立即开始
                    self._core.load_streaming(
                        source,
                        start_sec=target_start,
                        total_duration_sec=total_duration_sec,
                    )
                else:
                    # 时长未知，回退到阻塞完整加载
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
                self._core.seek(0.0 if self._lazy_window_mode else target_start)
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
        """获取当前播放列表的 track IDs，只返回在库中存在的 track IDs。
        参数：无（self 是实例参数）。
        返回值：list[str] - 包含字符串类型的 track IDs 列表。
        """
        playlist = self.library.get_playlist(self._current_playlist_id)  # 获取指定ID的播放列表
        if playlist is None:  # 如果播放列表不存在
            return []  # 返回空列表
        return [
            track_id for track_id in playlist.track_ids if track_id in self.library.tracks
        ]  # 过滤出存在于库中的 track IDs

    def display_ordered_track_ids(self) -> list[str]:
        track_ids = self._playlist_track_ids()
        if self._mode == PlayMode.RANDOM and self._random_display_order_getter() == "random":
            self._rebuild_random_order(force=False)
            if self._random_order:
                return list(self._random_order)
        return track_ids

    def _sorted_playlist_track_ids(self, sort_mode: str = "default") -> list[str]:
        """
        根据指定的排序模式返回播放列表中曲目ID的排序列表。

        参数:
            sort_mode (str): 排序模式，默认为"default"。
                - "default": 返回原始顺序。
                - "title": 按曲目标题排序。
                - "artist": 按艺术家排序。
                - 其他值: 与"default"相同，返回原始顺序。

        返回:
            list[str]: 排序后的曲目ID列表。
        """
        track_ids = self._playlist_track_ids()  # 获取当前播放列表的曲目ID列表
        if sort_mode == "default":  # 检查是否为默认排序模式
            return track_ids  # 直接返回原始曲目ID列表
        tracks = self.library.tracks  # 从库中获取所有曲目的字典，键为曲目ID
        if sort_mode == "title":  # 如果排序模式为按标题排序
            return sorted(
                track_ids, key=lambda tid: (tracks[tid].title or "").lower()
            )  # 按曲目标题的字母顺序排序，忽略大小写
        if sort_mode == "artist":  # 如果排序模式为按艺术家排序
            return sorted(
                track_ids, key=lambda tid: (tracks[tid].artist or "").lower()
            )  # 按艺术家名称的字母顺序排序，忽略大小写
        return track_ids  # 对于其他排序模式，返回原始曲目ID列表

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
                playing = self.is_playing()

        # 发射播放状态变更信号（如果状态改变或强制）
        self._emit_playback_state(current=playing)
        self._last_playing = playing  # 保存当前播放状态供下次比较

    def _handle_natural_finished(self) -> None:
        """
        处理当前曲目自然播放结束的逻辑。
        根据播放模式（如单曲循环）决定下一步操作。
        """
        # 自然结束时，当前曲目一定完播（95%以上），记录完播
        self._check_and_record_complete_play(
            track_id=self._loaded_track_id,
            position=float(self._safe_position()),
            duration=float(self._safe_duration()),
        )
        if self._mode == PlayMode.SINGLE_LOOP and self._current_track_id:  # 单曲循环模式
            ok = self._load_current_track(
                auto_play=True, start_sec=0.0, active_request=False
            )  # 重新从0开始播放当前曲目
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
