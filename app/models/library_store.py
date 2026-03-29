from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .entities import Playlist, Track


class LibraryStore:
    def __init__(self, data_dir: Path):
        self._path = Path(data_dir).resolve() / "library.json"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> tuple[dict[str, Track], dict[str, Playlist], str | None]:
        if not self._path.exists():
            return {}, {}, None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}, {}, None

        tracks_payload = payload.get("tracks", {})
        playlists_payload = payload.get("playlists", {})
        tracks = {
            track_id: Track.from_dict(item)
            for track_id, item in tracks_payload.items()
            if isinstance(item, dict)
        }
        playlists = {
            playlist_id: Playlist.from_dict(item)
            for playlist_id, item in playlists_payload.items()
            if isinstance(item, dict)
        }
        active_playlist_id = payload.get("active_playlist_id")
        return tracks, playlists, active_playlist_id

    def save(
        self,
        tracks: dict[str, Track],
        playlists: dict[str, Playlist],
        active_playlist_id: str | None,
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "tracks": {track_id: t.to_dict() for track_id, t in tracks.items()},
            "playlists": {playlist_id: p.to_dict() for playlist_id, p in playlists.items()},
            "active_playlist_id": active_playlist_id,
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")