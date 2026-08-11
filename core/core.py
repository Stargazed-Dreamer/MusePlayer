from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

try:
    import av
except Exception:  # pragma: no cover - optional dependency
    av = None

import contextlib

from .output import AudioOutputBackend, NullOutputBackend, SoundDeviceOutputBackend
from .types import AudioMeta, PlaybackWindow, PlayerCoreError


class PyAVPlayerCore:
    """Portable playback kernel built on PyAV decode + pluggable output backend.

    This core is UI-agnostic and can be reused by external projects.
    """

    def __init__(
        self,
        output_backend: AudioOutputBackend | None = None,
        target_sample_rate: int = 48_000,
        target_channels: int = 2,
        blocksize: int = 4096,
    ):
        self._lock = threading.RLock()
        self._output = output_backend or self._build_default_output_backend()
        self._target_sample_rate = max(8_000, int(target_sample_rate))
        self._target_channels = max(1, int(target_channels))
        self._blocksize = max(128, int(blocksize))

        self._source_path: Path | None = None
        self._buffer = np.zeros((0, self._target_channels), dtype=np.float32)
        self._sample_rate = self._target_sample_rate
        self._channels = self._target_channels
        self._frame_cursor = 0
        self._segment_end_frame: int | None = None
        self._volume = 1.0
        self._playback_rate = 1.0
        self._playing = False
        self._stream_open = False
        self._last_open_sample_rate = 0
        self._last_open_channels = 0
        self._error_callback: Callable[[str], None] | None = None
        self._last_runtime_error: str | None = None
        # 流式解码状态（full 策略：边读边播）
        self._streaming = False  # 是否处于流式解码模式
        self._streaming_forward_done = False  # 正向解码（start→end）是否完成
        self._streaming_done = False  # 全部解码（含 backfill）是否完成
        self._streaming_thread: threading.Thread | None = None
        self._streaming_stop = threading.Event()  # 通知生产者退出
        self._streaming_generation = 0  # 代际标记，防止旧生产者写入新缓冲
        self._streaming_total_frames = 0  # 整文件总帧数（预分配缓冲大小）
        self._streaming_start_frame = 0  # 流式起始帧偏移
        self._streaming_decoded_end = 0  # 正向已解码到的帧位置
        self._streaming_backfill_end = 0  # 回填已解码到的帧位置
        self._streaming_error: str | None = None

    def set_error_callback(self, callback: Callable[[str], None] | None) -> None:
        with self._lock:
            self._error_callback = callback

    @staticmethod
    def _build_default_output_backend() -> AudioOutputBackend:
        """构建默认的音频输出后端。

        尝试导入sounddevice库并使用它创建输出后端，如果失败则返回空输出后端。

        参数:
            无

        返回:
            AudioOutputBackend: 音频输出后端实例，可能是SoundDeviceOutputBackend或NullOutputBackend。
        """
        try:
            import sounddevice  # noqa: F401  # 尝试导入sounddevice库，忽略未使用警告（仅检查可用性）

            return SoundDeviceOutputBackend()  # 成功导入则使用SoundDeviceOutputBackend
        except Exception:  # 捕获导入或其他任何异常
            return NullOutputBackend()  # 失败时返回空输出后端

    def load(self, source: Path, *, start_sec: float = 0.0, window_sec: float | None = None) -> AudioMeta:
        source = Path(source).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        pcm, sample_rate, channels = self._decode_to_pcm(
            source,
            start_sec=max(0.0, float(start_sec)),
            window_sec=window_sec,
        )
        with self._lock:
            self._stop_streaming_locked()
            self._source_path = source
            self._buffer = pcm
            self._sample_rate = sample_rate
            self._channels = channels
            self._frame_cursor = 0
            self._segment_end_frame = None
            self._playing = False
            self._try_reuse_or_reopen_stream()
            return self.meta()

    def load_streaming(self, source: Path, *, start_sec: float = 0.0, total_duration_sec: float = 0.0) -> AudioMeta:
        """流式加载：边解码边播放，解码完成后整文件驻留内存可任意拖动。

        预分配整文件缓冲，后台线程分块解码写入：
        - Phase 1（正向）：从 start_sec 解码到文件末尾，使播放可尽快开始。
        - Phase 2（回填）：从 0 解码到 start_sec，完成后整文件均在内存，可任意位置拖动。

        Args:
            source: 音频文件路径
            start_sec: 起始播放位置（秒），播放从此处开始，正向解码也从此处开始
            total_duration_sec: 整文件总时长（秒），用于预分配缓冲。为 0 或无法确定时回退到阻塞 load
        """
        source = Path(source).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        start_sec = max(0.0, float(start_sec))
        total_duration_sec = max(0.0, float(total_duration_sec))
        # 时长未知则回退到阻塞完整加载
        if total_duration_sec <= 0.0:
            return self.load(source, start_sec=0.0)
        total_frames = int(round(total_duration_sec * self._target_sample_rate))
        if total_frames <= 0:
            return self.load(source, start_sec=0.0)
        start_frame = min(int(round(start_sec * self._target_sample_rate)), total_frames)
        with self._lock:
            self._stop_streaming_locked()
            self._source_path = source
            self._buffer = np.zeros((total_frames, self._target_channels), dtype=np.float32)
            self._sample_rate = self._target_sample_rate
            self._channels = self._target_channels
            self._frame_cursor = start_frame
            self._segment_end_frame = None
            self._playing = False
            # 初始化流式状态
            self._streaming = True
            self._streaming_forward_done = False
            self._streaming_done = False
            self._streaming_total_frames = total_frames
            self._streaming_start_frame = start_frame
            self._streaming_decoded_end = start_frame
            self._streaming_backfill_end = 0
            self._streaming_error = None
            self._streaming_generation += 1
            gen = self._streaming_generation
            self._streaming_stop = threading.Event()
            stop_event = self._streaming_stop
            self._try_reuse_or_reopen_stream()
            meta = self.meta()
        # 启动后台生产者（不在锁内启动，避免线程调度导致持锁等待）
        self._streaming_thread = threading.Thread(
            target=self._streaming_producer,
            args=(source, start_sec, start_frame, total_frames, stop_event, gen),
            daemon=True,
            name="museplayer-stream-decode",
        )
        self._streaming_thread.start()
        return meta

    def _streaming_producer(
        self,
        source: Path,
        start_sec: float,
        start_frame: int,
        total_frames: int,
        stop_event: threading.Event,
        generation: int,
    ) -> None:
        """后台生产者：分块解码写入预分配缓冲。

        Phase 1 解码 [start_sec, end]，Phase 2 回填 [0, start_sec]。
        通过 generation 标记防止旧生产者向新缓冲写入。
        """
        chunk_sec = 5.0
        try:
            # Phase 1：正向解码 start -> end
            write_cursor = start_frame
            pos = start_sec
            while write_cursor < total_frames:
                if stop_event.is_set():
                    return
                remaining_sec = (total_frames - write_cursor) / self._target_sample_rate
                window = min(chunk_sec, remaining_sec + 0.5)
                pcm, _sr, _ch = self._decode_to_pcm(source, start_sec=pos, window_sec=window)
                if pcm.shape[0] == 0:
                    break
                n = min(pcm.shape[0], total_frames - write_cursor)
                with self._lock:
                    if self._streaming_generation != generation or stop_event.is_set():
                        return
                    self._buffer[write_cursor : write_cursor + n] = pcm[:n]
                    write_cursor += n
                    self._streaming_decoded_end = max(self._streaming_decoded_end, write_cursor)
                pos = start_sec + (write_cursor - start_frame) / self._target_sample_rate
            with self._lock:
                if self._streaming_generation != generation:
                    return
                self._streaming_forward_done = True
            # Phase 2：回填 0 -> start
            if start_frame > 0 and not stop_event.is_set():
                write_cursor = 0
                pos = 0.0
                while write_cursor < start_frame:
                    if stop_event.is_set():
                        return
                    remaining_sec = (start_frame - write_cursor) / self._target_sample_rate
                    window = min(chunk_sec, remaining_sec + 0.5)
                    pcm, _sr, _ch = self._decode_to_pcm(source, start_sec=pos, window_sec=window)
                    if pcm.shape[0] == 0:
                        break
                    n = min(pcm.shape[0], start_frame - write_cursor)
                    with self._lock:
                        if self._streaming_generation != generation or stop_event.is_set():
                            return
                        self._buffer[write_cursor : write_cursor + n] = pcm[:n]
                        write_cursor += n
                        self._streaming_backfill_end = max(self._streaming_backfill_end, write_cursor)
                    pos = write_cursor / self._target_sample_rate
            with self._lock:
                if self._streaming_generation != generation:
                    return
                self._streaming_done = True
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            callback = None
            with self._lock:
                if self._streaming_generation != generation:
                    return
                self._streaming_error = err
                self._streaming_forward_done = True
                self._streaming_done = True
                callback = self._error_callback
            if callback is not None:
                with contextlib.suppress(Exception):
                    callback(err)

    def _stop_streaming_locked(self) -> None:
        """通知流式生产者停止并作废其写入（须持有锁）。

        递增 generation 使在途的旧生产者写入被拒绝；设置 stop event 使其尽快退出。
        不等待线程结束（生产者会在下次检查点退出），避免持锁阻塞。
        """
        self._streaming_stop.set()
        self._streaming_generation += 1
        self._streaming = False
        self._streaming_forward_done = False
        self._streaming_done = False

    def _readable_end_for_cursor(self, cursor: int) -> int:
        """流式模式下给定游标位置的可读末尾帧。"""
        if cursor >= self._streaming_start_frame:
            return self._streaming_decoded_end
        return min(self._streaming_backfill_end, self._streaming_start_frame)

    def _is_seek_in_decoded_region(self, target_frame: int) -> bool:
        """流式模式下目标帧是否已解码（可即时跳转）。

        起始帧和已解码末尾帧均视为可跳转位置：
        - 起始帧：生产者正从此处开始填充，无需重启
        - 已解码末尾：游标追上解码进度时输出静音等待，无需重启
        """
        if self._streaming_done:
            return 0 <= target_frame < self._streaming_total_frames
        if self._streaming_start_frame <= target_frame <= self._streaming_decoded_end:
            return True
        return target_frame < self._streaming_start_frame and target_frame < self._streaming_backfill_end

    def decode_window(
        self, source: Path, *, start_sec: float = 0.0, window_sec: float | None = None
    ) -> tuple[np.ndarray, int, int]:
        source = Path(source).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        return self._decode_to_pcm(
            source,
            start_sec=max(0.0, float(start_sec)),
            window_sec=window_sec,
        )

    def load_decoded_pcm(
        self,
        source: Path,
        pcm: np.ndarray,
        sample_rate: int,
        channels: int,
        *,
        reopen_stream: bool = True,
        resume_sec: float | None = None,
        keep_playing: bool = False,
    ) -> AudioMeta:
        """功能：加载解码后的PCM数据，准备播放音频。
        参数：
        - source: 源文件路径
        - pcm: 解码后的PCM数据，必须是2D numpy数组
        - sample_rate: 采样率
        - channels: 声道数
        - reopen_stream: 是否重新打开音频流（关键字参数）
        - resume_sec: 恢复播放的秒数（关键字参数）
        - keep_playing: 是否保持播放状态（关键字参数）
        返回值：AudioMeta对象，包含音频元数据如源路径、持续时间等。"""
        source = Path(source).resolve()  # 将source转换为绝对路径
        if not source.exists():  # 检查源文件是否存在
            raise FileNotFoundError(source)
        if pcm.ndim != 2:  # 检查PCM数据是否为2D数组
            raise PlayerCoreError("Decoded PCM must be 2D array")
        prepared_pcm = np.ascontiguousarray(pcm.astype(np.float32, copy=False))  # 确保PCM数据为float32类型且内存连续
        with self._lock:  # 使用锁以确保线程安全
            self._stop_streaming_locked()  # 终止可能存在的流式解码
            next_sample_rate = max(8_000, int(sample_rate))  # 确保采样率不低于8000
            next_channels = max(1, int(channels))  # 确保声道数至少为1
            need_reopen = bool(reopen_stream)  # 初始判断是否需要重新打开流
            # 如果流已打开、无需强制重开，但采样率或声道数改变，则需要重新打开流以应用新设置
            if (
                self._stream_open
                and not need_reopen
                and (self._sample_rate != next_sample_rate or self._channels != next_channels)
            ):
                need_reopen = True
            self._source_path = source  # 设置源路径
            self._buffer = prepared_pcm  # 设置PCM缓冲区
            self._sample_rate = next_sample_rate  # 设置采样率
            self._channels = next_channels  # 设置声道数
            if resume_sec is None:  # 如果未指定恢复秒数
                self._frame_cursor = 0  # 帧游标设为0，从头开始
                self._playing = False  # 设置播放状态为False
            else:  # 如果指定恢复秒数
                self._frame_cursor = self._sec_to_frame(float(resume_sec))  # 将秒数转换为帧数并设置游标
                self._playing = bool(keep_playing)  # 根据keep_playing设置播放状态
            self._segment_end_frame = None  # 重置段结束帧
            if need_reopen:  # 如果需要重新打开流
                self._try_reuse_or_reopen_stream()  # 尝试复用或重新打开流
            if self._playing:  # 如果设置为播放
                self._ensure_stream_started()  # 确保流已启动
            return AudioMeta(  # 返回音频元数据对象
                source_path=self._source_path,
                duration_sec=(self._buffer.shape[0] / self._sample_rate)
                if self._sample_rate > 0
                else 0.0,  # 计算持续时间
                sample_rate=self._sample_rate,
                channels=self._channels,
                frame_count=self._buffer.shape[0],
            )

    def unload(self) -> None:
        """卸载当前音频资源，重置播放器状态。

        功能：释放内部资源，将播放器恢复到未加载状态。
        参数：无。
        返回值：None。
        """
        with self._lock:
            # 获取锁，确保线程安全
            self._stop_streaming_locked()  # 终止流式解码生产者
            # 设置播放状态为False
            self._playing = False
            # 设置播放状态为False
            self._source_path = None
            # 清空音频源路径
            self._buffer = np.zeros((0, self._channels), dtype=np.float32)
            # 将音频缓冲区重置为空（形状为(0, 通道数)的零数组）
            self._frame_cursor = 0
            # 重置帧游标到起始位置
            self._segment_end_frame = None
            # 清空当前片段的结束帧位置

    def play(self, start_sec: float = 0.0) -> None:
        """
        开始播放音频。

        参数:
            start_sec (float): 开始播放的秒数，默认为0.0。

        返回:
            None
        """
        with self._lock:  # 获取锁，确保线程安全
            self._require_loaded()  # 检查音频是否已加载
            self._frame_cursor = self._sec_to_frame(start_sec)  # 将秒数转换为帧数，设置播放起始位置
            self._segment_end_frame = None  # 重置段结束帧
            self._playing = True  # 设置播放状态为True
            self._ensure_stream_started()  # 确保音频流已开始

    def play_segment(self, start_sec: float, end_sec: float) -> None:
        """播放从start_sec到end_sec的音频段。

        参数:
            start_sec (float): 开始时间（秒）。
            end_sec (float): 结束时间（秒）。
        返回值:
            None
        """
        with self._lock:  # 获取锁以确保线程安全
            self._require_loaded()  # 确保音频已加载
            start = self._sec_to_frame(start_sec)  # 将开始时间转换为帧号
            end = self._sec_to_frame(end_sec)  # 将结束时间转换为帧号
            if end <= start:  # 检查结束帧是否大于开始帧
                raise PlayerCoreError("end_sec must be greater than start_sec")  # 如果无效，抛出错误
            self._frame_cursor = start  # 设置当前播放位置为开始帧
            self._segment_end_frame = min(end, self._buffer.shape[0])  # 设置段结束帧，确保不超过缓冲区长度
            self._playing = True  # 设置播放状态为真
            self._ensure_stream_started()  # 确保音频流已启动

    def pause(self) -> None:
        with self._lock:
            self._playing = False

    def stop(self) -> None:
        """停止当前播放并重置相关状态。

        该方法停止播放，并将播放状态重置为初始值，确保线程安全。
        使用锁来保护对共享资源的访问，防止并发问题。

        参数：
            无（除了self，表示实例本身）。

        返回值：
            无（返回类型为None）。
        """
        with self._lock:  # 使用锁确保线程安全，防止多线程同时修改状态
            self._playing = False  # 设置播放标志为False，指示停止播放
            self._frame_cursor = 0  # 重置帧光标到起始位置（0），为下次播放做准备
            self._segment_end_frame = None  # 清除段结束帧引用，避免残留数据

    def seek(self, position_sec: float) -> None:
        """将音频播放位置移动到指定的秒数。

        流式模式下，若目标位置尚未解码，则从目标位置重启流式解码
        （保留原播放状态：若正在播放则继续播放，若暂停则保持暂停）。

        Args:
            position_sec (float): 要跳转的目标位置，单位为秒。
        Returns:
            None
        """
        # 先在锁内判断是否需要重启流式解码（目标位置尚未解码）
        restart_source: Path | None = None
        restart_duration_sec = 0.0
        restart_was_playing = False
        with self._lock:
            # 确保音频数据已加载，若未加载则抛出异常
            self._require_loaded()
            # 将目标秒数转换为对应的帧数
            target_frame = self._sec_to_frame(position_sec)
            if self._streaming and not self._is_seek_in_decoded_region(target_frame):
                # 流式模式下目标位置尚未解码，需从目标位置重启流式解码
                restart_source = self._source_path
                if self._streaming_total_frames > 0 and self._target_sample_rate > 0:
                    restart_duration_sec = self._streaming_total_frames / self._target_sample_rate
                restart_was_playing = self._playing
            else:
                # 已解码区域或非流式模式：直接移动游标
                self._frame_cursor = target_frame
                # 检查是否设置了片段结束帧，且新游标位置已达到或超过结束帧
                if self._segment_end_frame is not None and self._frame_cursor >= self._segment_end_frame:
                    # 若已到达或越过预设的片段结束点，则停止播放
                    self._playing = False
                return
        # 释放锁后重启流式解码（load_streaming 内部会获取锁）
        if restart_source is not None and restart_duration_sec > 0.0:
            self.load_streaming(
                restart_source,
                start_sec=max(0.0, float(position_sec)),
                total_duration_sec=restart_duration_sec,
            )
            if restart_was_playing:
                with self._lock:
                    self._playing = True
                    self._ensure_stream_started()

    def set_volume(self, volume: float) -> None:
        """设置音量，将音量值限制在0.0到5.0之间。

        参数:
            volume (float): 要设置的音量值，将被限制在0.0到5.0之间。

        返回:
            None
        """
        with self._lock:  # 使用锁确保线程安全地修改音量
            self._volume = max(0.0, min(5.0, float(volume)))  # 限制音量在0.0到5.0之间，并存储为浮点数

    def set_playback_rate(self, rate: float) -> None:
        with self._lock:
            self._playback_rate = max(0.25, min(4.0, float(rate)))

    def playback_rate(self) -> float:
        """获取播放速率。

        参数：无显式参数（self 是实例引用）。
        返回值：float 类型，表示当前播放速率。
        """
        with self._lock:  # 使用锁以确保线程安全
            return float(self._playback_rate)

    def is_playing(self) -> bool:
        """
        检查当前是否正在播放。

        功能：用于确定播放器的当前播放状态。
        参数：无（仅包含实例方法自身的self参数）。
        返回值：布尔值，True表示正在播放，False表示未播放。
        """
        with self._lock:
            return bool(self._playing)

    def current_time(self) -> float:
        with self._lock:
            if self._sample_rate <= 0:
                return 0.0
            return self._frame_cursor / self._sample_rate

    def duration(self) -> float:
        with self._lock:
            if self._sample_rate <= 0:
                return 0.0
            return self._buffer.shape[0] / self._sample_rate

    def meta(self) -> AudioMeta:
        """获取当前加载音频源的核心元数据。

        此方法在锁的保护下，安全地提取并返回描述当前音频的关键信息。
        如果当前没有加载任何音频源，将引发一个 PlayerCoreError 异常。

        参数:
            无 (除了默认的 self 参数)

        返回:
            AudioMeta: 一个包含音频源路径、时长、采样率、声道数和总帧数的对象。
        """
        with self._lock:  # 获取锁以确保线程安全，防止在读取属性时状态被修改
            if self._source_path is None:
                raise PlayerCoreError("No source loaded")  # 如果未加载音频源，则抛出错误
            # 构建并返回 AudioMeta 对象，从内部状态中提取各项元数据
            return AudioMeta(
                source_path=self._source_path,  # 音频文件的路径
                duration_sec=self.duration(),  # 音频总时长（秒）
                sample_rate=self._sample_rate,  # 采样率
                channels=self._channels,  # 声道数
                frame_count=self._buffer.shape[0],  # 音频缓冲区的总帧数
            )

    def playback_window(self) -> PlaybackWindow:
        """获取播放窗口，返回包含开始和结束时间的 PlaybackWindow 对象。

        参数：无（除了self）。
        返回值：PlaybackWindow 对象。
        """
        with self._lock:  # 使用锁确保线程安全
            start = self.current_time()  # 获取当前时间作为开始时间
            if self._segment_end_frame is None or self._sample_rate <= 0:  # 检查分段结束帧或采样率是否无效
                end = self.duration()  # 若是，则使用总时长作为结束时间
            else:
                end = self._segment_end_frame / self._sample_rate  # 否则，计算结束时间为分段结束帧除以采样率
            return PlaybackWindow(start_sec=start, end_sec=end)  # 返回播放窗口对象

    def close(self) -> None:
        with self._lock:
            self._stop_streaming_locked()
            self._playing = False
            if self._stream_open:
                self._output.close()
                self._stream_open = False

    def set_output_device(self, device: str | int | None) -> None:
        """设置音频输出设备。

        功能：切换音频输出到指定设备，并在必要时重新打开音频流。

        参数：
            device (str | int | None): 目标设备标识符。可以是设备名称（字符串）、设备索引（整数）或None（表示默认设备）。

        返回值：
            None: 该方法不返回任何值。
        """
        with self._lock:  # 获取线程锁，确保操作原子性
            current_device = None
            if isinstance(self._output, SoundDeviceOutputBackend):  # 检查输出后端是否为SoundDevice
                current_device = self._output._device  # 获取当前设备标识
            # 如果当前设备与目标设备相同，则无需更改，直接返回
            if current_device == (device if device else None):
                return
            # 如果输出后端是SoundDevice类型，更新其设备标识
            if isinstance(self._output, SoundDeviceOutputBackend):
                self._output._device = device if device else None  # 设置新设备，如果device为空则使用None
            # 检查是否正在播放且流已打开
            was_playing = bool(self._playing) and self._stream_open
            if self._stream_open:  # 如果流已打开，先停止并关闭
                with contextlib.suppress(Exception):  # 忽略任何异常
                    self._output.stop()  # 尝试停止输出
                self._stream_open = False  # 标记流为关闭
            # 重新打开输出设备，使用当前的采样率和通道数
            self._output.open(
                sample_rate=self._sample_rate,
                channels=self._channels,
                callback=self._audio_callback,
                blocksize=self._blocksize,
            )
            self._stream_open = True  # 标记流为打开
            # 记录最后打开的采样率和通道数
            self._last_open_sample_rate = self._sample_rate
            self._last_open_channels = self._channels
            if was_playing:  # 如果之前正在播放，则重新开始播放
                self._output.start()

    def rebind_output_device(self) -> None:
        """
        重新绑定输出设备。

        此方法用于重新绑定音频输出设备。它会停止当前输出，重新打开设备，并在之前播放的情况下恢复播放。

        参数:
            self: 类实例。

        返回:
            None
        """
        with self._lock:  # 使用锁来确保线程安全操作
            if not self._stream_open:  # 如果音频流未打开，则直接返回
                return
            was_playing = bool(self._playing)  # 记录当前是否正在播放
            with contextlib.suppress(Exception):  # 忽略停止时可能发生的异常
                self._output.stop()  # 尝试停止当前输出
            self._stream_open = False  # 标记音频流为关闭状态
            self._output.open(  # 重新打开音频输出设备
                sample_rate=self._sample_rate,  # 设置采样率
                channels=self._channels,  # 设置通道数
                callback=self._audio_callback,  # 设置音频回调函数
                blocksize=self._blocksize,  # 设置块大小
            )
            self._stream_open = True  # 标记音频流为打开状态
            self._last_open_sample_rate = self._sample_rate  # 更新最后打开的采样率
            self._last_open_channels = self._channels  # 更新最后打开的通道数
            if was_playing:  # 如果之前正在播放，则重新开始播放
                self._output.start()

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()

    def _require_loaded(self) -> None:
        """
        检查是否已加载解码的音频数据，如果没有则抛出异常。
        参数：
            self: 当前实例。
        返回值：
            无。
        """
        if self._source_path is None or self._buffer.size == 0:
            # 检查源路径是否为空或缓冲区大小是否为0，如果条件成立，则没有加载音频数据
            raise PlayerCoreError("No decoded audio loaded")  # 抛出异常，提示没有解码的音频加载

    def _sec_to_frame(self, seconds: float) -> int:
        frame = int(round(max(0.0, float(seconds)) * self._sample_rate))
        return max(0, min(frame, self._buffer.shape[0]))

    def _ensure_stream_started(self) -> None:
        """确保音频流已启动。

        如果流未打开，则尝试打开并设置相关状态，然后启动输出流。

        参数:
            self: 实例自身。

        返回值:
            None
        """
        if not self._stream_open:  # 检查流是否已打开
            try:  # 尝试打开音频流
                self._output.open(
                    sample_rate=self._sample_rate,
                    channels=self._channels,
                    callback=self._audio_callback,
                    blocksize=self._blocksize,
                )
                self._stream_open = True  # 标记流为已打开
                self._last_open_sample_rate = self._sample_rate  # 记录打开时的采样率
                self._last_open_channels = self._channels  # 记录打开时的通道数
            except Exception:  # 处理打开流时的异常
                self._stream_open = False  # 标记流为未打开
                raise  # 重新抛出异常
        self._output.start()  # 启动输出流

    def _try_reuse_or_reopen_stream(self) -> None:
        """尝试重用或重新打开音频流。如果流已打开且参数（采样率和通道数）与上次相同，则停止当前流；否则，异步关闭流以便重新打开。"""
        same_params = (  # 检查是否可以使用相同的流参数：流已打开，且采样率和通道数与上次打开时一致
            self._stream_open
            and self._sample_rate == self._last_open_sample_rate
            and self._channels == self._last_open_channels
        )
        if same_params:  # 参数相同，无需重新打开流
            with contextlib.suppress(Exception):  # 忽略停止过程中可能出现的异常
                self._output.stop()  # 尝试停止当前输出流
            return  # 停止后直接返回，不执行后续关闭操作
        self._async_close_stream()  # 参数不同，异步关闭当前流以准备重新打开

    def _async_close_stream(self) -> None:
        """异步关闭流。

        检查流是否已打开，如果打开则停止旧输出，设置流为关闭状态，并在后台线程中关闭输出。

        参数：
            无（除self外）

        返回：
            None
        """
        if not self._stream_open:  # 如果流未打开，则直接返回
            return
        old_output = self._output  # 保存旧输出引用
        self._stream_open = False  # 设置流为关闭状态
        with contextlib.suppress(Exception):  # 忽略任何异常
            old_output.stop()  # 尝试停止旧输出
        threading.Thread(
            target=self._close_output_in_background, args=(old_output,), daemon=True
        ).start()  # 启动后台线程关闭输出

    @staticmethod
    def _close_output_in_background(backend: AudioOutputBackend) -> None:
        """在后台安全关闭音频输出后端。

        此方法旨在异步或后台环境中执行关闭操作，避免阻塞主线程。
        它会先等待一小段时间，然后尝试关闭后端，并优雅地处理可能出现的异常。

        Args:
            backend (AudioOutputBackend): 需要关闭的音频输出后端实例。

        Returns:
            None: 此方法没有返回值。
        """
        try:
            # 短暂休眠，给可能进行中的操作一个缓冲时间
            time.sleep(0.05)
            # 执行后端关闭操作
            backend.close()
        except Exception:
            # 捕获所有异常并静默忽略，确保后台任务不因关闭错误而中断
            pass

    def _audio_callback(self, outdata, frames, _time_info, _status) -> None:
        try:
            with self._lock:
                outdata.fill(0)
                if not self._playing or self._buffer.shape[0] == 0:
                    return

                end_limit = self._buffer.shape[0] if self._segment_end_frame is None else self._segment_end_frame
                start = self._frame_cursor
                rate = self._playback_rate

                # 流式模式下，限制可读末尾为已解码区域，避免读到未填充的零数据
                readable_end = end_limit
                streaming_underrun = False
                if self._streaming and not self._streaming_done:
                    readable_end = min(end_limit, self._readable_end_for_cursor(start))
                    if start >= readable_end:
                        # 游标已追上解码进度，输出静音等待生产者推进
                        streaming_underrun = True

                if not streaming_underrun:
                    if abs(rate - 1.0) < 1e-6:
                        end = min(start + int(frames), readable_end)
                        if end > start:
                            chunk = self._buffer[start:end]
                            if self._volume != 1.0:
                                chunk = chunk * self._volume
                            outdata[: (end - start), : self._channels] = chunk
                            self._frame_cursor = end
                    else:
                        src_need = int(np.ceil(int(frames) * rate)) + 2
                        end = min(start + src_need, readable_end)
                        src = self._buffer[start:end]
                        if src.shape[0] > 1:
                            src_x = np.arange(src.shape[0], dtype=np.float32)
                            dst_x = np.arange(int(frames), dtype=np.float32) * float(rate)
                            dst_x = np.clip(dst_x, 0.0, float(src.shape[0] - 1))
                            chunk = np.empty((int(frames), self._channels), dtype=np.float32)
                            for ch in range(self._channels):
                                chunk[:, ch] = np.interp(dst_x, src_x, src[:, ch]).astype(np.float32, copy=False)
                            if self._volume != 1.0:
                                chunk = chunk * self._volume
                            outdata[: int(frames), : self._channels] = chunk
                            consumed = max(1, int(np.floor(int(frames) * rate)))
                            self._frame_cursor = min(start + consumed, readable_end)
                        elif not (self._streaming and not self._streaming_done):
                            # 非流式或流式已完成：源数据不足，推进到 readable_end 触发自然结束
                            self._frame_cursor = readable_end
                        # else: 流式未完成且数据不足，保持游标等待解码推进

                # 仅当整文件解码完成（或非流式模式）且到达末尾时才停止播放
                # 流式未完成时即使到达 readable_end 也保持 _playing=True，让回调输出静音等待
                if self._frame_cursor >= end_limit and (not self._streaming or self._streaming_done):
                    self._playing = False
        except Exception as exc:
            with self._lock:
                self._playing = False
                self._last_runtime_error = f"{type(exc).__name__}: {exc}"
                callback = self._error_callback
            outdata.fill(0)
            if callback is not None:
                with contextlib.suppress(Exception):
                    callback(self._last_runtime_error)

    def _decode_to_pcm(
        self,
        source: Path,
        *,
        start_sec: float = 0.0,
        window_sec: float | None = None,
    ) -> tuple[np.ndarray, int, int]:
        if av is None:
            raise PlayerCoreError("PyAV is not installed")

        channels = self._target_channels
        sample_rate = self._target_sample_rate
        layout = "mono" if channels == 1 else "stereo"
        start_sec = max(0.0, float(start_sec))
        decode_window = None if window_sec is None else max(0.05, float(window_sec))
        decode_end = None if decode_window is None else start_sec + decode_window
        seek_target = 0.0
        seek_used = False

        chunks: list[np.ndarray] = []
        try:
            # Use binary stream instead of path string to avoid unicode-path decode issues on Windows.
            with source.open("rb") as source_file, av.open(source_file, mode="r") as container:
                stream = next((s for s in container.streams if s.type == "audio"), None)
                if stream is None:
                    raise PlayerCoreError(f"No audio stream found in {source}")

                if decode_window is not None and start_sec > 0.0:
                    seek_target = max(0.0, start_sec - 0.01)
                    try:
                        if stream.time_base is not None and float(stream.time_base) > 0.0:
                            seek_ts = int(seek_target / float(stream.time_base))
                        else:
                            seek_ts = int(seek_target * av.time_base)
                        container.seek(max(0, seek_ts), stream=stream, backward=True)
                        seek_used = True
                    except Exception:
                        pass

                resampler = av.audio.resampler.AudioResampler(
                    format="flt",
                    layout=layout,
                    rate=sample_rate,
                )
                first_frame_time: float | None = None
                reached_window_end = False
                for packet in container.demux(stream):
                    if packet.stream.type != "audio":
                        continue
                    try:
                        decoded = packet.decode()
                    except Exception:
                        # Some files contain sporadic broken packets; skip and continue decoding.
                        continue
                    for frame in decoded:
                        if frame.time is not None:
                            frame_time = max(0.0, float(frame.time))
                            if first_frame_time is None:
                                first_frame_time = frame_time
                            if decode_end is not None and frame_time > (decode_end + 0.20):
                                reached_window_end = True
                                break
                        self._append_resampled_frame(chunks, resampler, frame, channels)
                    if reached_window_end:
                        break
                flushed = resampler.resample(None)
                if flushed is not None:
                    frames = flushed if isinstance(flushed, list) else [flushed]
                    for frm in frames:
                        arr = np.asarray(frm.to_ndarray())
                        arr = self._normalize_frame_array(arr=arr, channels=channels, planar=bool(frm.format.is_planar))
                        arr = arr.astype(np.float32, copy=False)
                        if arr.shape[1] != channels:
                            if arr.shape[1] > channels:
                                arr = arr[:, :channels]
                            else:
                                pad = np.zeros((arr.shape[0], channels - arr.shape[1]), dtype=np.float32)
                                arr = np.concatenate([arr, pad], axis=1)
                        chunks.append(arr)
        except PlayerCoreError:
            raise
        except Exception as exc:
            raise PlayerCoreError(f"Failed to decode audio via PyAV: {exc}") from exc

        if not chunks:
            return np.zeros((0, channels), dtype=np.float32), sample_rate, channels
        pcm = np.concatenate(chunks, axis=0)

        if decode_window is not None:
            if start_sec > 0.0:
                trim_sec = 0.0
                if "first_frame_time" in locals() and first_frame_time is not None:
                    trim_sec = max(0.0, start_sec - first_frame_time)
                elif seek_used:
                    trim_sec = max(0.0, start_sec - seek_target)
                trim_frames = int(round(trim_sec * sample_rate))
                if 0 < trim_frames < pcm.shape[0]:
                    pcm = pcm[trim_frames:]
            limit_frames = int(round(decode_window * sample_rate))
            if limit_frames > 0 and pcm.shape[0] > limit_frames:
                pcm = pcm[:limit_frames]

        return pcm, sample_rate, channels

    def _append_resampled_frame(
        self,
        chunks: list[np.ndarray],
        resampler,
        frame,
        channels: int,
    ) -> None:
        """将重采样的帧追加到 chunks 列表中。

        参数：
        chunks: list[np.ndarray] - 存储重采样后数组的列表。
        resampler: 重采样器对象，用于对帧进行重采样。
        frame: 原始帧数据。
        channels: int - 目标通道数。

        返回值：
        None
        """
        # 使用 resampler 对帧进行重采样
        resampled = resampler.resample(frame)
        # 如果重采样结果为空，则直接返回
        if resampled is None:
            return
        # 确保 frames 是一个列表，即使 resampled 不是列表
        frames = resampled if isinstance(resampled, list) else [resampled]
        for frm in frames:
            # 将帧转换为 numpy 数组
            arr = np.asarray(frm.to_ndarray())
            # 标准化数组，处理通道数和是否为平面格式
            arr = self._normalize_frame_array(arr=arr, channels=channels, planar=bool(frm.format.is_planar))
            # 将数组数据类型转换为 float32
            arr = arr.astype(np.float32, copy=False)
            # 检查通道数是否匹配
            if arr.shape[1] != channels:
                # 如果通道数过多，则截断
                if arr.shape[1] > channels:
                    arr = arr[:, :channels]
                # 如果通道数不足，则填充零
                else:
                    pad = np.zeros((arr.shape[0], channels - arr.shape[1]), dtype=np.float32)
                    arr = np.concatenate([arr, pad], axis=1)
            # 将处理后的数组添加到 chunks 列表
            chunks.append(arr)

    @staticmethod
    def _normalize_frame_array(arr: np.ndarray, channels: int, planar: bool) -> np.ndarray:
        """将输入数组标准化为帧数组，确保每帧包含指定数量的通道。

        功能：
            将不同维度和排列方式的输入数组转换为统一的二维数组格式，
            每行代表一帧，每帧包含指定数量的通道数据。支持平面排列和平面排列两种模式。

        参数：
            arr (np.ndarray): 输入数组，支持1D、2D或更高维度。
            channels (int): 每帧的通道数。
            planar (bool): 是否为平面排列模式（即通道维度在前，如(C, H, W)）。

        返回值：
            np.ndarray: 标准化后的二维数组，形状为(帧数, 每帧数据点数)。
        """
        if arr.ndim == 1:
            # 一维数组：假设为连续帧数据
            if channels > 1:
                # 计算可整除通道数的可用数据长度
                usable = (arr.size // channels) * channels
                return arr[:usable].reshape((-1, channels))
            return arr[:, None]
        if arr.ndim != 2:
            # 非二维数组：重塑为二维，每行一帧
            return arr.reshape((-1, channels))
        if planar:
            # 平面排列模式：通道维度在前
            if arr.shape[0] == channels:
                # 通道数在第一个维度，需要转置为(帧数, 通道数)
                return arr.T
            if arr.shape[1] == channels:
                # 通道数已在第二个维度，直接返回
                return arr
            return arr.T
        # 平面排列路径（非平面模式，即通道在后）
        if arr.shape[1] == channels:
            # 通道数在第二个维度，直接返回
            return arr
        if arr.shape[0] == 1 and channels > 1:
            # 单行多通道数据：重塑为多行
            row = arr[0]
            # 计算可整除通道数的可用数据长度
            usable = (row.size // channels) * channels
            return row[:usable].reshape((-1, channels))
        if arr.shape[0] == channels:
            # 通道数在第一个维度，需要转置为(帧数, 通道数)
            return arr.T
        return arr
