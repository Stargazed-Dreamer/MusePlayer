from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import time
import uuid


def _now_ts() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass(slots=True)
class Track:
    id: str
    path: str
    title: str
    artist: str = "未知歌手"
    album: str = "未知专辑"
    duration_sec: float = 0.0
    track_no: int = 0
    year: str = ""
    added_at: float = field(default_factory=_now_ts)

    @property
    def path_obj(self) -> Path:
        return Path(self.path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration_sec": float(self.duration_sec),
            "track_no": int(self.track_no),
            "year": self.year,
            "added_at": float(self.added_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Track":
        return cls(
            id=str(data.get("id", new_id())),
            path=str(data.get("path", "")),
            title=str(data.get("title", "未知标题")),
            artist=str(data.get("artist", "未知歌手")),
            album=str(data.get("album", "未知专辑")),
            duration_sec=float(data.get("duration_sec", 0.0)),
            track_no=int(data.get("track_no", 0)),
            year=str(data.get("year", "")),
            added_at=float(data.get("added_at", _now_ts())),
        )


@dataclass(slots=True)
class Playlist:
    id: str
    name: str
    track_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=_now_ts)
    updated_at: float = field(default_factory=_now_ts)

    def touch(self) -> None:
        self.updated_at = _now_ts()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "track_ids": list(self.track_ids),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Playlist":
        return cls(
            id=str(data.get("id", new_id())),
            name=str(data.get("name", "新建歌单")),
            track_ids=[str(x) for x in data.get("track_ids", [])],
            created_at=float(data.get("created_at", _now_ts())),
            updated_at=float(data.get("updated_at", _now_ts())),
        )


@dataclass(slots=True)
class Settings:
    control_host: str = "127.0.0.1"
    control_port: int = 43121
    control_interface_enabled: bool = True
    auto_restore_session: bool = True
    logging_enabled: bool = False
    enable_playlist_loop_mode: bool = False
    collect_playback_data: bool = True
    global_gain_boost: float = 1.35
    read_strategy: str = "window"
    timed_save_enabled: bool = False
    timed_save_minutes: int = 5
    dark_theme: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_host": self.control_host,
            "control_port": int(self.control_port),
            "control_interface_enabled": bool(self.control_interface_enabled),
            "auto_restore_session": bool(self.auto_restore_session),
            "logging_enabled": bool(self.logging_enabled),
            "enable_playlist_loop_mode": bool(self.enable_playlist_loop_mode),
            "collect_playback_data": bool(self.collect_playback_data),
            "global_gain_boost": float(self.global_gain_boost),
            "read_strategy": self.read_strategy,
            "timed_save_enabled": bool(self.timed_save_enabled),
            "timed_save_minutes": int(self.timed_save_minutes),
            "dark_theme": bool(self.dark_theme),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        read_strategy = str(data.get("read_strategy", "window")).strip().lower()
        if read_strategy not in {"window", "full"}:
            read_strategy = "window"
        return cls(
            control_host=str(data.get("control_host", "127.0.0.1")),
            control_port=max(1, min(65535, int(data.get("control_port", 43121)))),
            control_interface_enabled=bool(data.get("control_interface_enabled", True)),
            auto_restore_session=bool(data.get("auto_restore_session", True)),
            logging_enabled=bool(data.get("logging_enabled", False)),
            enable_playlist_loop_mode=bool(data.get("enable_playlist_loop_mode", False)),
            collect_playback_data=bool(data.get("collect_playback_data", True)),
            global_gain_boost=max(0.5, min(5.0, float(data.get("global_gain_boost", 1.35)))),
            read_strategy=read_strategy,
            timed_save_enabled=bool(data.get("timed_save_enabled", False)),
            timed_save_minutes=max(1, min(1440, int(data.get("timed_save_minutes", 5)))),
            dark_theme=bool(data.get("dark_theme", True)),
        )


@dataclass(slots=True)
class SessionState:
    current_playlist_id: str | None = None
    current_track_id: str | None = None
    position_sec: float = 0.0
    volume: float = 0.8
    play_mode: str = "single_loop"
    random_seed: int = 1
    random_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_playlist_id": self.current_playlist_id,
            "current_track_id": self.current_track_id,
            "position_sec": float(self.position_sec),
            "volume": float(self.volume),
            "play_mode": self.play_mode,
            "random_seed": int(self.random_seed),
            "random_index": int(self.random_index),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        return cls(
            current_playlist_id=data.get("current_playlist_id"),
            current_track_id=data.get("current_track_id"),
            position_sec=max(0.0, float(data.get("position_sec", 0.0))),
            volume=max(0.0, min(1.0, float(data.get("volume", 0.8)))),
            play_mode=str(data.get("play_mode", "single_loop")),
            random_seed=max(0, int(data.get("random_seed", 1))),
            random_index=max(0, int(data.get("random_index", 0))),
        )
