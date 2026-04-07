from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .entities import Playlist, Track


class LibraryStore:
    """曲库数据存储器。
    
    负责曲目和歌单数据的持久化存储和加载。
    数据以JSON格式保存在library.json文件中。
    """
    
    def __init__(self, data_dir: Path):
        """初始化曲库存储器。
        
        Args:
            data_dir: 数据存储目录路径
        """
        self._path = Path(data_dir).resolve() / "library.json"

    @property
    def path(self) -> Path:
        """获取曲库文件路径。
        
        Returns:
            Path: 曲库JSON文件的完整路径
        """
        return self._path

    def load(self) -> tuple[dict[str, Track], dict[str, Playlist], str | None]:
        """加载曲库数据。
        
        从JSON文件读取曲目和歌单数据，并转换为实体对象。
        如果文件不存在或格式错误，返回空数据。
        
        Returns:
            tuple: (曲目字典, 歌单字典, 活跃歌单ID)
        """
        if not self._path.exists():
            return {}, {}, None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}, {}, None

        tracks_payload = payload.get("tracks", {})
        playlists_payload = payload.get("playlists", {})
        tracks: dict[str, Track] = {}
        playlists: dict[str, Playlist] = {}

        if isinstance(tracks_payload, dict):
            for track_id, item in tracks_payload.items():
                if not isinstance(item, dict):
                    continue
                try:
                    parsed = Track.from_dict(item)
                except Exception:
                    continue
                key = str(track_id or "").strip() or parsed.id
                tracks[key] = parsed

        if isinstance(playlists_payload, dict):
            for playlist_id, item in playlists_payload.items():
                if not isinstance(item, dict):
                    continue
                try:
                    parsed = Playlist.from_dict(item)
                except Exception:
                    continue
                key = str(playlist_id or "").strip() or parsed.id
                playlists[key] = parsed

        active_raw = payload.get("active_playlist_id")
        active_playlist_id = str(active_raw).strip() if isinstance(active_raw, str) else None
        return tracks, playlists, active_playlist_id

    def save(
        self,
        tracks: dict[str, Track],
        playlists: dict[str, Playlist],
        active_playlist_id: str | None,
    ) -> None:
        """保存曲库数据。
        
        将曲目和歌单数据转换为字典格式并保存到JSON文件。
        自动创建必要的目录结构。
        
        Args:
            tracks: 曲目字典，ID到Track对象的映射
            playlists: 歌单字典，ID到Playlist对象的映射
            active_playlist_id: 当前活跃的歌单ID
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "tracks": {track_id: t.to_dict() for track_id, t in tracks.items()},
            "playlists": {playlist_id: p.to_dict() for playlist_id, p in playlists.items()},
            "active_playlist_id": active_playlist_id,
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
