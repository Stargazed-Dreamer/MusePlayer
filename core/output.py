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

    def __init__(self, latency: str | float = "low", device: str | int | None = None):
        self._latency = latency
        self._device = device
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
        kwargs = dict(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            callback=callback,
            blocksize=blocksize,
            latency=self._latency,
        )
        if self._device is not None:
            if isinstance(self._device, str):
                resolved = resolve_output_device_index(self._device)
                kwargs["device"] = resolved if resolved is not None else self._device
            else:
                kwargs["device"] = self._device
        self._stream = sd.OutputStream(**kwargs)

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
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass
        finally:
            self._stream = None


def list_output_devices() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd
    except Exception:
        return []
    devices = sd.query_devices()
    if isinstance(devices, dict):
        return []
    seen_names: set[str] = set()
    result: list[dict[str, Any]] = []
    wasapi_entries: dict[str, int] = {}
    for idx, dev in enumerate(devices):
        if not isinstance(dev, dict):
            continue
        if dev.get("max_output_channels", 0) <= 0:
            continue
        name = str(dev.get("name", "")).strip()
        if not name:
            continue
        hostapi = int(dev.get("hostapi", -1))
        if hostapi == 2 and name not in wasapi_entries:
            wasapi_entries[name] = idx
    for idx, dev in enumerate(devices):
        if not isinstance(dev, dict):
            continue
        if dev.get("max_output_channels", 0) <= 0:
            continue
        name = str(dev.get("name", "")).strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        chosen_idx = wasapi_entries.get(name, idx)
        result.append({"index": chosen_idx, "name": name})
    return result


def resolve_output_device_index(device_name: str) -> int | None:
    if not device_name:
        return None
    try:
        import sounddevice as sd
    except Exception:
        return None
    devices = sd.query_devices()
    if isinstance(devices, dict):
        return None
    wasapi_idx: int | None = None
    first_idx: int | None = None
    for idx, dev in enumerate(devices):
        if not isinstance(dev, dict):
            continue
        if dev.get("max_output_channels", 0) <= 0:
            continue
        name = str(dev.get("name", "")).strip()
        if name != device_name:
            continue
        if first_idx is None:
            first_idx = idx
        hostapi = int(dev.get("hostapi", -1))
        if hostapi == 2:
            wasapi_idx = idx
            break
    return wasapi_idx if wasapi_idx is not None else first_idx
