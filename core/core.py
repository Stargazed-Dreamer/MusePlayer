from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import numpy as np

try:
    import av
except Exception:  # pragma: no cover - optional dependency
    av = None

from .output import AudioOutputBackend, NullOutputBackend, SoundDeviceOutputBackend
from .types import AudioMeta, PlayerCoreError, PlaybackWindow


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
        self._error_callback: Callable[[str], None] | None = None
        self._last_runtime_error: str | None = None

    def set_error_callback(self, callback: Callable[[str], None] | None) -> None:
        with self._lock:
            self._error_callback = callback

    @staticmethod
    def _build_default_output_backend() -> AudioOutputBackend:
        try:
            import sounddevice  # noqa: F401

            return SoundDeviceOutputBackend()
        except Exception:
            return NullOutputBackend()

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
            self._source_path = source
            self._buffer = pcm
            self._sample_rate = sample_rate
            self._channels = channels
            self._frame_cursor = 0
            self._segment_end_frame = None
            self._playing = False
            self._reopen_stream()
            return self.meta()

    def decode_window(self, source: Path, *, start_sec: float = 0.0, window_sec: float | None = None) -> tuple[np.ndarray, int, int]:
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
        source = Path(source).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        if pcm.ndim != 2:
            raise PlayerCoreError("Decoded PCM must be 2D array")
        prepared_pcm = np.ascontiguousarray(pcm.astype(np.float32, copy=False))
        with self._lock:
            next_sample_rate = max(8_000, int(sample_rate))
            next_channels = max(1, int(channels))
            need_reopen = bool(reopen_stream)
            if self._stream_open and not need_reopen:
                if self._sample_rate != next_sample_rate or self._channels != next_channels:
                    need_reopen = True
            self._source_path = source
            self._buffer = prepared_pcm
            self._sample_rate = next_sample_rate
            self._channels = next_channels
            if resume_sec is None:
                self._frame_cursor = 0
                self._playing = False
            else:
                self._frame_cursor = self._sec_to_frame(float(resume_sec))
                self._playing = bool(keep_playing)
            self._segment_end_frame = None
            if need_reopen:
                self._reopen_stream()
            if self._playing:
                self._ensure_stream_started()
            return AudioMeta(
                source_path=self._source_path,
                duration_sec=(self._buffer.shape[0] / self._sample_rate) if self._sample_rate > 0 else 0.0,
                sample_rate=self._sample_rate,
                channels=self._channels,
                frame_count=self._buffer.shape[0],
            )

    def unload(self) -> None:
        with self._lock:
            self._playing = False
            self._source_path = None
            self._buffer = np.zeros((0, self._channels), dtype=np.float32)
            self._frame_cursor = 0
            self._segment_end_frame = None

    def play(self, start_sec: float = 0.0) -> None:
        with self._lock:
            self._require_loaded()
            self._frame_cursor = self._sec_to_frame(start_sec)
            self._segment_end_frame = None
            self._playing = True
            self._ensure_stream_started()

    def play_segment(self, start_sec: float, end_sec: float) -> None:
        with self._lock:
            self._require_loaded()
            start = self._sec_to_frame(start_sec)
            end = self._sec_to_frame(end_sec)
            if end <= start:
                raise PlayerCoreError("end_sec must be greater than start_sec")
            self._frame_cursor = start
            self._segment_end_frame = min(end, self._buffer.shape[0])
            self._playing = True
            self._ensure_stream_started()

    def pause(self) -> None:
        with self._lock:
            self._playing = False

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._frame_cursor = 0
            self._segment_end_frame = None

    def seek(self, position_sec: float) -> None:
        with self._lock:
            self._require_loaded()
            self._frame_cursor = self._sec_to_frame(position_sec)
            if self._segment_end_frame is not None and self._frame_cursor >= self._segment_end_frame:
                self._playing = False

    def set_volume(self, volume: float) -> None:
        with self._lock:
            self._volume = max(0.0, min(5.0, float(volume)))

    def set_playback_rate(self, rate: float) -> None:
        with self._lock:
            self._playback_rate = max(0.25, min(4.0, float(rate)))

    def playback_rate(self) -> float:
        with self._lock:
            return float(self._playback_rate)

    def is_playing(self) -> bool:
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
        with self._lock:
            if self._source_path is None:
                raise PlayerCoreError("No source loaded")
            return AudioMeta(
                source_path=self._source_path,
                duration_sec=self.duration(),
                sample_rate=self._sample_rate,
                channels=self._channels,
                frame_count=self._buffer.shape[0],
            )

    def playback_window(self) -> PlaybackWindow:
        with self._lock:
            start = self.current_time()
            if self._segment_end_frame is None or self._sample_rate <= 0:
                end = self.duration()
            else:
                end = self._segment_end_frame / self._sample_rate
            return PlaybackWindow(start_sec=start, end_sec=end)

    def close(self) -> None:
        with self._lock:
            self._playing = False
            if self._stream_open:
                self._output.close()
                self._stream_open = False

    def set_output_device(self, device: str | int | None) -> None:
        with self._lock:
            if isinstance(self._output, SoundDeviceOutputBackend):
                self._output._device = device if device else None
            was_playing = bool(self._playing) and self._stream_open
            if self._stream_open:
                try:
                    self._output.close()
                finally:
                    self._stream_open = False
                self._output.open(
                    sample_rate=self._sample_rate,
                    channels=self._channels,
                    callback=self._audio_callback,
                    blocksize=self._blocksize,
                )
                self._stream_open = True
                if was_playing:
                    self._output.start()

    def rebind_output_device(self) -> None:
        """在不轮询的前提下重绑定输出设备（用于系统设备切换事件）。"""
        with self._lock:
            if not self._stream_open:
                return
            was_playing = bool(self._playing)
            try:
                self._output.close()
            finally:
                self._stream_open = False
            self._output.open(
                sample_rate=self._sample_rate,
                channels=self._channels,
                callback=self._audio_callback,
                blocksize=self._blocksize,
            )
            self._stream_open = True
            if was_playing:
                self._output.start()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _require_loaded(self) -> None:
        if self._source_path is None or self._buffer.size == 0:
            raise PlayerCoreError("No decoded audio loaded")

    def _sec_to_frame(self, seconds: float) -> int:
        frame = int(round(max(0.0, float(seconds)) * self._sample_rate))
        return max(0, min(frame, self._buffer.shape[0]))

    def _ensure_stream_started(self) -> None:
        if not self._stream_open:
            self._output.open(
                sample_rate=self._sample_rate,
                channels=self._channels,
                callback=self._audio_callback,
                blocksize=self._blocksize,
            )
            self._stream_open = True
        self._output.start()

    def _reopen_stream(self) -> None:
        if self._stream_open:
            self._output.close()
            self._stream_open = False

    def _audio_callback(self, outdata, frames, _time_info, _status) -> None:
        try:
            with self._lock:
                outdata.fill(0)
                if not self._playing or self._buffer.shape[0] == 0:
                    return

                end_limit = self._buffer.shape[0] if self._segment_end_frame is None else self._segment_end_frame
                start = self._frame_cursor
                rate = self._playback_rate

                if abs(rate - 1.0) < 1e-6:
                    end = min(start + int(frames), end_limit)
                    if end > start:
                        chunk = self._buffer[start:end]
                        if self._volume != 1.0:
                            chunk = chunk * self._volume
                        outdata[: (end - start), : self._channels] = chunk
                        self._frame_cursor = end
                else:
                    src_need = int(np.ceil(int(frames) * rate)) + 2
                    end = min(start + src_need, end_limit)
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
                    self._frame_cursor = min(start + consumed, end_limit)

                if self._frame_cursor >= end_limit:
                    self._playing = False
        except Exception as exc:
            with self._lock:
                self._playing = False
                self._last_runtime_error = f"{type(exc).__name__}: {exc}"
                callback = self._error_callback
            outdata.fill(0)
            if callback is not None:
                try:
                    callback(self._last_runtime_error)
                except Exception:
                    pass

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
                if trim_frames > 0:
                    if trim_frames < pcm.shape[0]:
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
        resampled = resampler.resample(frame)
        if resampled is None:
            return
        frames = resampled if isinstance(resampled, list) else [resampled]
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

    @staticmethod
    def _normalize_frame_array(arr: np.ndarray, channels: int, planar: bool) -> np.ndarray:
        if arr.ndim == 1:
            if channels > 1:
                usable = (arr.size // channels) * channels
                return arr[:usable].reshape((-1, channels))
            return arr[:, None]
        if arr.ndim != 2:
            return arr.reshape((-1, channels))
        if planar:
            if arr.shape[0] == channels:
                return arr.T
            if arr.shape[1] == channels:
                return arr
            return arr.T
        # packed path
        if arr.shape[1] == channels:
            return arr
        if arr.shape[0] == 1 and channels > 1:
            row = arr[0]
            usable = (row.size // channels) * channels
            return row[:usable].reshape((-1, channels))
        if arr.shape[0] == channels:
            return arr.T
        return arr
