from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class PlayerCoreError(RuntimeError):
    """Base error raised by player core."""


@dataclass(slots=True)
class AudioMeta:
    source_path: Path
    duration_sec: float
    sample_rate: int
    channels: int
    frame_count: int


@dataclass(slots=True)
class PlaybackWindow:
    start_sec: float
    end_sec: float
