"""播放内核（PyAVPlayerCore）并发与回退行为回归测试。

覆盖 S1/S2 修复：
- 异步关流按"调度时捕获的句柄"关闭，竞态窗口内新开的流不被误杀；
- load_streaming 时长未知的回退路径透传 start_sec；
- load_streaming 的 expected_source 竞态守卫（换曲后旧决策过期）。

全部使用假后端/假流，不依赖真实音频设备；仅用标准库生成合法 WAV。
"""

from __future__ import annotations

import math
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from core import PyAVPlayerCore
from core.output import AudioOutputBackend, NullOutputBackend, SoundDeviceOutputBackend


class FakeStream:
    """模拟 sounddevice.OutputStream 的最小行为面。"""

    def __init__(self) -> None:
        self.active = True
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.stopped = True
        self.active = False

    def close(self) -> None:
        self.closed = True
        self.active = False


class RecordingBackend(AudioOutputBackend):
    """记录每次 open 生成假流的内存后端（与 SoundDevice 后端同构的句柄语义）。"""

    def __init__(self) -> None:
        self._stream: FakeStream | None = None
        self.streams: list[FakeStream] = []

    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: Any,
        blocksize: int = 1024,
    ) -> None:
        self.close()
        stream = FakeStream()
        self._stream = stream
        self.streams.append(stream)

    def start(self) -> None:
        if self._stream is not None and not self._stream.active:
            self._stream.start()

    def stop(self) -> None:
        if self._stream is not None and self._stream.active:
            self._stream.stop()

    def close(self) -> None:
        self.release_stream(self._stream)

    def capture_stream(self) -> Any:
        return self._stream

    def release_stream(self, handle: Any) -> None:
        if handle is None or self._stream is not handle:
            return
        handle.close()
        self._stream = None

    @property
    def current(self) -> FakeStream | None:
        return self._stream


def _write_wav(path: Path, seconds: float = 0.5, rate: int = 22_050) -> None:
    n = int(seconds * rate)
    samples = (np.sin(2.0 * math.pi * 440.0 * np.arange(n) / rate) * 16000.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


class TestReleaseStreamHandleSemantics:
    """S1 后端层：release 只关捕获句柄，绝不误杀新流。"""

    def test_stale_handle_is_noop(self) -> None:
        backend = SoundDeviceOutputBackend()
        old = FakeStream()
        new = FakeStream()
        backend._stream = old

        handle = backend.capture_stream()
        assert handle is old

        # 模拟竞态窗口内 open() 已替换为新流（open 内部会先 close 旧流）
        old.close()
        backend._stream = new

        backend.release_stream(handle)  # 陈旧句柄必须安全跳过
        assert not new.closed
        assert backend._stream is new

        backend.release_stream(new)  # 仍是当前流 → 正常关闭
        assert new.closed
        assert backend._stream is None

    def test_release_none_and_empty_backend(self) -> None:
        backend = SoundDeviceOutputBackend()
        backend.release_stream(None)
        backend.release_stream(FakeStream())  # 非当前流，no-op
        backend.close()  # close 语义保持：无流时静默返回

    def test_null_backend_capture_release(self) -> None:
        backend = NullOutputBackend()
        assert backend.capture_stream() is None
        backend.release_stream(None)


class TestAsyncCloseRace:
    """S1 内核层：异步关流调度后立刻重开，新流不能被后台线程关掉。"""

    def test_reopened_stream_survives_pending_async_close(self) -> None:
        backend = RecordingBackend()
        core = PyAVPlayerCore(output_backend=backend)

        with core._lock:
            backend.open(48_000, 2, core._audio_callback)
            first = backend.current
            assert first is not None
            core._stream_open = True
            core._sample_rate = 48_000
            core._channels = 2
            core._last_open_sample_rate = 44_100  # 制造参数不一致 → 走异步关闭分支
            core._try_reuse_or_reopen_stream()
            assert core._stream_open is False  # 已调度异步关闭

        # 竞态窗口：异步关闭尚未执行（0.05s 睡眠），主线程立刻重开并标记已打开
        backend.open(48_000, 2, core._audio_callback)  # open 内部 close 掉 first
        second = backend.current
        assert second is not None
        assert second is not first
        core._stream_open = True
        core._last_open_sample_rate = 48_000

        deadline = time.time() + 2.0
        while time.time() < deadline and not first.closed:
            time.sleep(0.01)
        assert first.closed  # 旧流最终被回收（由重开的 open 或后台线程）
        assert not second.closed  # 关键断言：新流没有被过期的后台关闭误杀
        assert backend.current is second

    def test_close_stops_stream_for_real_shutdown(self) -> None:
        backend = RecordingBackend()
        core = PyAVPlayerCore(output_backend=backend)
        with core._lock:
            backend.open(48_000, 2, core._audio_callback)
            current = backend.current
            core._stream_open = True
            core.close()
        assert current is not None and current.closed
        assert core._stream_open is False


class TestLoadStreamingGuards:
    """S2：回退透传 start_sec；expected_source 过期守卫。"""

    def test_fallback_keeps_start_sec(self, tmp_path: Path) -> None:
        wav = tmp_path / "tone.wav"
        _write_wav(wav, seconds=0.5)
        core = PyAVPlayerCore(output_backend=NullOutputBackend())

        meta = core.load_streaming(wav, start_sec=0.2, total_duration_sec=0.0)

        assert meta is not None
        # 回退到 load(start_sec=0.2)：只解码剩余 0.3s（修复前为全曲 0.5s）
        assert meta.duration_sec == pytest.approx(0.3, abs=0.1)

    def test_expected_source_abort_keeps_new_track(self, tmp_path: Path) -> None:
        old_wav = tmp_path / "old.wav"
        new_wav = tmp_path / "new.wav"
        _write_wav(old_wav, seconds=0.3)
        _write_wav(new_wav, seconds=0.4)
        core = PyAVPlayerCore(output_backend=NullOutputBackend())

        core.load(new_wav)  # 模拟竞态中另一线程已换曲

        meta = core.load_streaming(
            old_wav,
            start_sec=0.1,
            total_duration_sec=0.3,
            expected_source=Path(old_wav).resolve(),  # 决策时刻的旧源
        )

        assert meta is None  # 过期决策被守卫拒绝
        assert core.meta().source_path == new_wav.resolve()  # 新曲目状态未被覆盖
        assert core.duration() == pytest.approx(0.4, abs=0.1)

    def test_expected_source_match_proceeds(self, tmp_path: Path) -> None:
        wav = tmp_path / "tone.wav"
        _write_wav(wav, seconds=0.4)
        core = PyAVPlayerCore(output_backend=NullOutputBackend())
        core.load(wav)

        meta = core.load_streaming(
            wav,
            start_sec=0.1,
            total_duration_sec=0.4,
            expected_source=wav.resolve(),
        )
        assert meta is not None
        assert meta.source_path == wav.resolve()
        core.close()  # 停掉后台生产者线程，避免测试间残留
