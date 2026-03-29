from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Any


StreamCallback = Callable[[Any, int, Any, Any], None]


class AudioOutputBackend(ABC):
    """Platform output backend abstraction."""

    @abstractmethod
    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: StreamCallback,
        blocksize: int = 1024,
    ) -> None:
        """Open output stream."""

    @abstractmethod
    def start(self) -> None:
        """Start output stream."""

    @abstractmethod
    def stop(self) -> None:
        """Stop output stream."""

    @abstractmethod
    def close(self) -> None:
        """Close and release output stream."""


class NullOutputBackend(AudioOutputBackend):
    """No-op backend used when no audio device backend is available."""

    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: StreamCallback,
        blocksize: int = 1024,
    ) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None


class SoundDeviceOutputBackend(AudioOutputBackend):
    """sounddevice-based realtime output backend."""

    def __init__(self, latency: str | float = "low"):
        self._latency = latency
        self._stream = None

    def open(
        self,
        sample_rate: int,
        channels: int,
        callback: StreamCallback,
        blocksize: int = 1024,
    ) -> None:
        import sounddevice as sd

        self.close()
        self._stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            callback=callback,
            blocksize=blocksize,
            latency=self._latency,
        )

    def start(self) -> None:
        if self._stream is not None and not self._stream.active:
            self._stream.start()

    def stop(self) -> None:
        if self._stream is not None and self._stream.active:
            self._stream.stop()

    def close(self) -> None:
        if self._stream is None:
            return
        try:
            if self._stream.active:
                self._stream.stop()
        finally:
            self._stream.close()
            self._stream = None
