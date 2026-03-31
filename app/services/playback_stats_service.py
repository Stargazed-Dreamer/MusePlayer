from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
import time


def _now_ts() -> float:
    return time.time()


@dataclass(slots=True)
class PlaybackStatsEntry:
    track_id: str
    play_count: int = 0
    active_play_count: int = 0
    early_skip_count: int = 0
    played_seconds_total: float = 0.0
    played_percent_total: float = 0.0
    updated_at: float = field(default_factory=_now_ts)

    def to_dict(self) -> dict:
        return {
            "track_id": self.track_id,
            "play_count": int(self.play_count),
            "active_play_count": int(self.active_play_count),
            "early_skip_count": int(self.early_skip_count),
            "played_seconds_total": float(self.played_seconds_total),
            "played_percent_total": float(self.played_percent_total),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlaybackStatsEntry":
        return cls(
            track_id=str(payload.get("track_id", "")),
            play_count=max(0, int(payload.get("play_count", 0))),
            active_play_count=max(0, int(payload.get("active_play_count", 0))),
            early_skip_count=max(0, int(payload.get("early_skip_count", 0))),
            played_seconds_total=max(0.0, float(payload.get("played_seconds_total", 0.0))),
            played_percent_total=max(0.0, float(payload.get("played_percent_total", 0.0))),
            updated_at=float(payload.get("updated_at", _now_ts())),
        )


class PlaybackStatsService:
    def __init__(self, data_dir: Path):
        self._path = Path(data_dir).resolve() / "playback_stats.json"
        self._entries: dict[str, PlaybackStatsEntry] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._entries = {}
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._entries = {}
            return
        rows = payload.get("tracks", {}) if isinstance(payload, dict) else {}
        loaded: dict[str, PlaybackStatsEntry] = {}
        if isinstance(rows, dict):
            for track_id, row in rows.items():
                if not isinstance(row, dict):
                    continue
                item = PlaybackStatsEntry.from_dict({"track_id": track_id, **row})
                if item.track_id:
                    loaded[item.track_id] = item
        self._entries = loaded

    def save_if_dirty(self) -> None:
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tracks": {track_id: item.to_dict() for track_id, item in self._entries.items()}}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._dirty = False

    def record_play_start(self, track_id: str, *, active_request: bool) -> None:
        track_id = str(track_id or "").strip()
        if not track_id:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.play_count += 1
        if active_request:
            item.active_play_count += 1
        item.updated_at = _now_ts()
        self._dirty = True

    def record_play_progress(self, track_id: str, played_seconds: float, duration_sec: float) -> None:
        track_id = str(track_id or "").strip()
        delta = max(0.0, float(played_seconds))
        duration = max(0.0, float(duration_sec))
        if not track_id or delta <= 0.0:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.played_seconds_total += delta
        if duration > 0.0:
            item.played_percent_total += (delta / duration) * 100.0
        item.updated_at = _now_ts()
        self._dirty = True

    def remove_track(self, track_id: str) -> None:
        if track_id in self._entries:
            del self._entries[track_id]
            self._dirty = True

    def record_early_skip(self, track_id: str) -> None:
        track_id = str(track_id or "").strip()
        if not track_id:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.early_skip_count += 1
        item.updated_at = _now_ts()
        self._dirty = True

    def export_stats_for_track(self, track_id: str) -> dict[str, int] | None:
        item = self._entries.get(str(track_id or "").strip())
        if item is None:
            return None
        return {
            "play_count": max(0, int(item.play_count)),
            "manual_play_count": max(0, int(item.active_play_count)),
            "play_seconds": max(0, int(round(item.played_seconds_total))),
            "early_skip_count": max(0, int(item.early_skip_count)),
        }
