from __future__ import annotations

import logging
from pathlib import Path

from app.models.entities import Playlist, Track, new_id
from app.models.library_store import LibraryStore
from app.services.metadata_service import MetadataService

AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".wav",
    ".ogg",
    ".opus",
    ".wma",
}

ALL_SONGS_ID = "all_songs"
logger = logging.getLogger("museplayer.library")


class LibraryService:
    def __init__(self, store: LibraryStore, metadata_service: MetadataService):
        self._store = store
        self._metadata = metadata_service
        self.tracks: dict[str, Track] = {}
        self.playlists: dict[str, Playlist] = {}
        self.active_playlist_id: str | None = None

    def load(self) -> None:
        tracks, playlists, active = self._store.load()
        self.tracks = tracks
        self.playlists = playlists
        self._ensure_all_songs_playlist()
        self._drop_missing_tracks()
        self._deduplicate_tracks()

        if active in self.playlists:
            self.active_playlist_id = active
        else:
            self.active_playlist_id = ALL_SONGS_ID

        self._normalize_playlist_tracks()
        logger.info("曲库加载完成: tracks=%s playlists=%s", len(self.tracks), len(self.playlists))

    def _drop_missing_tracks(self) -> None:
        missing_ids = []
        for track_id, track in self.tracks.items():
            try:
                if not Path(track.path).exists():
                    missing_ids.append(track_id)
            except Exception:
                missing_ids.append(track_id)

        if not missing_ids:
            return

        for track_id in missing_ids:
            self.tracks.pop(track_id, None)
        logger.info("清理失效歌曲记录: %s", len(missing_ids))

    def _deduplicate_tracks(self) -> None:
        if len(self.tracks) <= 1:
            return

        ordered = sorted(
            self.tracks.values(),
            key=lambda t: (float(t.added_at), t.id),
            reverse=True,
        )

        key_owner: dict[tuple[str, int, int], str] = {}
        remap: dict[str, str] = {}

        for track in ordered:
            key = self._dedupe_key(track)
            owner = key_owner.get(key)
            if owner is None:
                key_owner[key] = track.id
            else:
                remap[track.id] = owner

        if not remap:
            return

        for playlist in self.playlists.values():
            new_track_ids: list[str] = []
            seen: set[str] = set()
            for track_id in playlist.track_ids:
                mapped = remap.get(track_id, track_id)
                if mapped in seen:
                    continue
                seen.add(mapped)
                new_track_ids.append(mapped)
            playlist.track_ids = new_track_ids

        for old_id in remap:
            self.tracks.pop(old_id, None)

        logger.info("清理重复歌曲记录: %s", len(remap))

    @staticmethod
    def _dedupe_key(track: Track) -> tuple[str, int, int]:
        source = Path(track.path)
        try:
            resolved = source.resolve()
        except Exception:
            resolved = source

        filename = resolved.name.lower()
        duration_ms = max(0, int(round(float(track.duration_sec) * 1000.0)))
        try:
            size = int(resolved.stat().st_size)
        except Exception:
            size = -1
        return filename, size, duration_ms

    def save(self) -> None:
        self._store.save(self.tracks, self.playlists, self.active_playlist_id)

    def _ensure_all_songs_playlist(self) -> None:
        if ALL_SONGS_ID not in self.playlists:
            self.playlists[ALL_SONGS_ID] = Playlist(id=ALL_SONGS_ID, name="全部歌曲", track_ids=[])
            return
        self.playlists[ALL_SONGS_ID].name = "全部歌曲"

    def _normalize_playlist_tracks(self) -> None:
        existing_track_ids = set(self.tracks.keys())
        for playlist in self.playlists.values():
            original = list(playlist.track_ids)
            playlist.track_ids = [track_id for track_id in original if track_id in existing_track_ids]
        self.playlists[ALL_SONGS_ID].track_ids = list(self.tracks.keys())

    def get_playlist(self, playlist_id: str | None) -> Playlist:
        if playlist_id and playlist_id in self.playlists:
            return self.playlists[playlist_id]
        return self.playlists[ALL_SONGS_ID]

    def list_playlists(self) -> list[Playlist]:
        all_pl = self.playlists.get(ALL_SONGS_ID)
        others = [p for p in self.playlists.values() if p.id != ALL_SONGS_ID]
        others.sort(key=lambda p: p.name.lower())
        if all_pl is None:
            return others
        return [all_pl, *others]

    def get_playlist_tracks(self, playlist_id: str | None) -> list[Track]:
        playlist = self.get_playlist(playlist_id)
        return [self.tracks[tid] for tid in playlist.track_ids if tid in self.tracks]

    def search_playlist_tracks(self, playlist_id: str | None, keyword: str) -> list[Track]:
        key = (keyword or "").strip().lower()
        tracks = self.get_playlist_tracks(playlist_id)
        if not key:
            return tracks
        result: list[Track] = []
        for track in tracks:
            hay = f"{track.title} {track.artist} {track.album}".lower()
            if key in hay:
                result.append(track)
        return result

    def create_playlist(self, name: str) -> Playlist:
        clean_name = (name or "").strip() or "新建歌单"
        playlist = Playlist(id=new_id(), name=clean_name)
        self.playlists[playlist.id] = playlist
        self.active_playlist_id = playlist.id
        self.save()
        logger.info("创建歌单: %s (%s)", playlist.name, playlist.id)
        return playlist

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        if playlist_id == ALL_SONGS_ID:
            return
        playlist = self.playlists.get(playlist_id)
        if playlist is None:
            return
        old_name = playlist.name
        playlist.name = (name or "").strip() or playlist.name
        playlist.touch()
        self.save()
        logger.info("重命名歌单: %s -> %s", old_name, playlist.name)

    def copy_playlist(self, source_playlist_id: str, new_name: str | None = None) -> Playlist | None:
        source = self.playlists.get(source_playlist_id)
        if source is None:
            return None

        base_name = (new_name or "").strip() or f"{source.name} - 副本"
        target_name = self._make_unique_playlist_name(base_name)
        copied_ids = [track_id for track_id in source.track_ids if track_id in self.tracks]

        playlist = Playlist(id=new_id(), name=target_name, track_ids=copied_ids)
        self.playlists[playlist.id] = playlist
        self.active_playlist_id = playlist.id
        self.save()
        logger.info("复制歌单: %s -> %s", source.name, playlist.name)
        return playlist

    def merge_playlist(self, source_playlist_id: str, target_playlist_id: str) -> int:
        if source_playlist_id == target_playlist_id:
            return 0
        source = self.playlists.get(source_playlist_id)
        target = self.playlists.get(target_playlist_id)
        if source is None or target is None:
            return 0

        before = len(target.track_ids)
        existing = set(target.track_ids)
        for track_id in source.track_ids:
            if track_id not in self.tracks:
                continue
            if track_id in existing:
                continue
            target.track_ids.append(track_id)
            existing.add(track_id)
        target.touch()
        self.save()

        merged_count = max(0, len(target.track_ids) - before)
        logger.info("合并歌单: %s -> %s, 新增=%s", source.name, target.name, merged_count)
        return merged_count

    def delete_playlist(self, playlist_id: str) -> None:
        if playlist_id == ALL_SONGS_ID:
            return
        if playlist_id not in self.playlists:
            return
        removed = self.playlists[playlist_id]
        del self.playlists[playlist_id]

        orphan_track_ids = self._find_orphan_track_ids(exclude_playlist_id=playlist_id)
        for track_id in orphan_track_ids:
            self.tracks.pop(track_id, None)

        self._normalize_playlist_tracks()
        if self.active_playlist_id == playlist_id:
            self.active_playlist_id = ALL_SONGS_ID
        self.save()
        logger.info("删除歌单: %s (%s), 清理歌曲=%s", removed.name, playlist_id, len(orphan_track_ids))

    def set_active_playlist(self, playlist_id: str) -> None:
        if playlist_id not in self.playlists:
            return
        self.active_playlist_id = playlist_id
        self.save()

    def add_track_ids_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        playlist = self.playlists.get(playlist_id)
        if playlist is None:
            return
        existing = set(playlist.track_ids)
        for track_id in track_ids:
            if track_id not in self.tracks:
                continue
            if track_id in existing:
                continue
            playlist.track_ids.append(track_id)
            existing.add(track_id)
        playlist.touch()
        self.save()

    def remove_track_from_playlist(self, playlist_id: str, track_id: str) -> set[str]:
        removed_globally: set[str] = set()
        track_id = str(track_id or "").strip()
        if not track_id:
            return removed_globally

        if track_id not in self.tracks:
            return removed_globally

        if playlist_id == ALL_SONGS_ID:
            self._remove_track_globally(track_id)
            removed_globally.add(track_id)
            self._normalize_playlist_tracks()
            self.save()
            return removed_globally

        playlist = self.playlists.get(playlist_id)
        if playlist is None:
            return removed_globally

        before = len(playlist.track_ids)
        playlist.track_ids = [x for x in playlist.track_ids if x != track_id]
        if len(playlist.track_ids) != before:
            playlist.touch()

        still_referenced = False
        for pl in self.playlists.values():
            if pl.id == ALL_SONGS_ID:
                continue
            if track_id in pl.track_ids:
                still_referenced = True
                break

        if not still_referenced:
            self._remove_track_globally(track_id)
            removed_globally.add(track_id)

        self._normalize_playlist_tracks()
        self.save()
        return removed_globally

    def get_track(self, track_id: str | None) -> Track | None:
        if not track_id:
            return None
        return self.tracks.get(track_id)

    def import_folder(self, folder: Path, playlist_id: str | None = None, recursive: bool = True) -> list[Track]:
        target = Path(folder).resolve()
        if not target.exists() or not target.is_dir():
            raise FileNotFoundError(str(target))

        files = self._scan_audio_files(target, recursive=recursive)
        if not files:
            return []

        target_playlist = self._resolve_target_playlist_for_folder_import(target, playlist_id)
        path_map = {Path(track.path).resolve(): track for track in self.tracks.values()}

        imported: list[Track] = []
        for file_path in files:
            existing = path_map.get(file_path)
            if existing is None:
                track = self._metadata.extract_track(file_path)
                self.tracks[track.id] = track
                path_map[file_path] = track
                imported.append(track)
            else:
                imported.append(existing)

        all_songs = self.playlists[ALL_SONGS_ID]
        all_existing = set(all_songs.track_ids)
        for track in imported:
            if track.id not in all_existing:
                all_songs.track_ids.append(track.id)
                all_existing.add(track.id)
        all_songs.touch()

        target_existing = set(target_playlist.track_ids)
        for track in imported:
            if track.id not in target_existing:
                target_playlist.track_ids.append(track.id)
                target_existing.add(track.id)
        target_playlist.touch()

        self.active_playlist_id = target_playlist.id

        self.save()
        logger.info("导入文件夹: %s -> 歌单=%s, 新增/纳入歌曲=%s", target, target_playlist.name, len(imported))
        return imported

    def import_file(self, file_path: Path, playlist_id: str | None = None) -> Track:
        source = Path(file_path).resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(str(source))
        if source.suffix.lower() not in AUDIO_EXTENSIONS:
            raise ValueError(f"不支持的音频格式: {source.suffix}")

        path_map = {Path(track.path).resolve(): track for track in self.tracks.values()}
        existing = path_map.get(source)
        track = existing if existing is not None else self._metadata.extract_track(source)

        if existing is None:
            self.tracks[track.id] = track

        all_songs = self.playlists[ALL_SONGS_ID]
        if track.id not in all_songs.track_ids:
            all_songs.track_ids.append(track.id)
            all_songs.touch()

        target_playlist = self.get_playlist(playlist_id or self.active_playlist_id)
        if track.id not in target_playlist.track_ids:
            target_playlist.track_ids.append(track.id)
            target_playlist.touch()

        self.save()
        logger.info("导入文件: %s", source)
        return track

    @staticmethod
    def _scan_audio_files(folder: Path, recursive: bool = True) -> list[Path]:
        globber = folder.rglob("*") if recursive else folder.glob("*")
        files: list[Path] = []
        for p in globber:
            if not p.is_file():
                continue
            if p.suffix.lower() in AUDIO_EXTENSIONS:
                files.append(p.resolve())
        files.sort(key=lambda x: str(x).lower())
        return files

    def _resolve_target_playlist_for_folder_import(self, folder: Path, playlist_id: str | None) -> Playlist:
        if playlist_id and playlist_id in self.playlists and playlist_id != ALL_SONGS_ID:
            return self.playlists[playlist_id]

        folder_name = self._normalize_playlist_name(folder.name)
        existing = self._find_playlist_by_name(folder_name)
        if existing is not None:
            return existing

        playlist = Playlist(id=new_id(), name=folder_name)
        self.playlists[playlist.id] = playlist
        return playlist

    def _find_playlist_by_name(self, name: str) -> Playlist | None:
        expected = self._normalize_playlist_name(name).casefold()
        for playlist in self.playlists.values():
            if playlist.id == ALL_SONGS_ID:
                continue
            if playlist.name.strip().casefold() == expected:
                return playlist
        return None

    def _make_unique_playlist_name(self, base_name: str) -> str:
        base = self._normalize_playlist_name(base_name)
        used = {playlist.name.strip().casefold() for playlist in self.playlists.values() if playlist.id != ALL_SONGS_ID}
        if base.casefold() not in used:
            return base
        idx = 2
        while True:
            candidate = f"{base} ({idx})"
            if candidate.casefold() not in used:
                return candidate
            idx += 1

    @staticmethod
    def _normalize_playlist_name(name: str) -> str:
        return (name or "").strip() or "新建歌单"

    def _find_orphan_track_ids(self, exclude_playlist_id: str | None = None) -> set[str]:
        referenced: set[str] = set()
        for playlist in self.playlists.values():
            if playlist.id == ALL_SONGS_ID:
                continue
            if exclude_playlist_id and playlist.id == exclude_playlist_id:
                continue
            referenced.update(track_id for track_id in playlist.track_ids if track_id in self.tracks)

        all_track_ids = set(self.tracks.keys())
        return all_track_ids - referenced

    def _remove_track_globally(self, track_id: str) -> None:
        if track_id in self.tracks:
            del self.tracks[track_id]
        for playlist in self.playlists.values():
            if track_id in playlist.track_ids:
                playlist.track_ids = [x for x in playlist.track_ids if x != track_id]
                playlist.touch()
