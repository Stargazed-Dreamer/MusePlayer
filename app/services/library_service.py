from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Callable

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
MUSE_PLAYLIST_SCHEMA = "musearc_playlist_export_v1"
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

    def import_folder(
        self,
        folder: Path,
        playlist_id: str | None = None,
        recursive: bool = True,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[Track]:
        target = Path(folder).resolve()
        if not target.exists() or not target.is_dir():
            raise FileNotFoundError(str(target))

        plan: list[tuple[Playlist, list[Path]]] = []
        if playlist_id and playlist_id in self.playlists and playlist_id != ALL_SONGS_ID:
            root_playlist = self.playlists[playlist_id]
            root_files = self._scan_audio_files(target, recursive=False)
            if root_files:
                plan.append((root_playlist, root_files))
            children = [p for p in target.iterdir() if p.is_dir()]
            children.sort(key=lambda p: p.name.casefold())
            for child in children:
                child_files = self._scan_audio_files(child, recursive=False)
                if not child_files:
                    continue
                child_playlist = self._resolve_target_playlist_for_folder_import(child, None)
                plan.append((child_playlist, child_files))
        else:
            root_files = self._scan_audio_files(target, recursive=False)
            if root_files:
                root_playlist = self._resolve_target_playlist_for_folder_import(target, None)
                plan.append((root_playlist, root_files))

            children = [p for p in target.iterdir() if p.is_dir()]
            children.sort(key=lambda p: p.name.casefold())
            for child in children:
                child_files = self._scan_audio_files(child, recursive=False)
                if not child_files:
                    continue
                child_playlist = self._resolve_target_playlist_for_folder_import(child, None)
                plan.append((child_playlist, child_files))

        if not plan:
            return []

        flat_files: list[Path] = []
        for _, files in plan:
            flat_files.extend(files)
        total = len(flat_files)

        path_map = {Path(track.path).resolve(): track for track in self.tracks.values()}
        imported_by_id: dict[str, Track] = {}
        all_songs = self.playlists[ALL_SONGS_ID]
        all_ids = set(all_songs.track_ids)
        playlist_ids_map: dict[str, set[str]] = {pl.id: set(pl.track_ids) for pl in self.playlists.values()}
        touched_playlists: set[str] = set()

        last_tick = time.monotonic()
        processed = 0
        for playlist, files in plan:
            existing_ids = playlist_ids_map.setdefault(playlist.id, set(playlist.track_ids))
            for file_path in files:
                existing = path_map.get(file_path)
                if existing is None:
                    track = self._metadata.extract_track(file_path)
                    self.tracks[track.id] = track
                    path_map[file_path] = track
                else:
                    track = existing

                imported_by_id[track.id] = track
                if track.id not in existing_ids:
                    playlist.track_ids.append(track.id)
                    existing_ids.add(track.id)
                    touched_playlists.add(playlist.id)
                if track.id not in all_ids:
                    all_songs.track_ids.append(track.id)
                    all_ids.add(track.id)

                processed += 1
                if progress_callback is not None:
                    now = time.monotonic()
                    if now - last_tick >= 5.0:
                        progress_callback(processed, total, str(file_path))
                        last_tick = now

        for pid in touched_playlists:
            pl = self.playlists.get(pid)
            if pl is not None:
                pl.touch()
        all_songs.touch()

        active_playlist = plan[0][0]
        self.active_playlist_id = active_playlist.id
        self.save()

        if progress_callback is not None:
            progress_callback(total, total, "")

        imported = list(imported_by_id.values())
        logger.info("导入文件夹: %s, 新增/纳入歌曲=%s, 歌单数=%s", target, len(imported), len(plan))
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

    def import_muse_playlist(self, file_path: Path) -> Playlist:
        source_file = Path(file_path).resolve()
        if not source_file.exists() or not source_file.is_file():
            raise FileNotFoundError(str(source_file))
        if source_file.suffix.lower() != ".json" or not source_file.name.lower().endswith(".muse_playlist.json"):
            raise ValueError("不支持的歌单文件格式，请选择 *.muse_playlist.json")
        payload = json.loads(source_file.read_text(encoding="utf-8"))
        return self._import_muse_playlist_data(payload, source_file=source_file, fallback_name=source_file.stem)

    def import_muse_playlist_payload(self, payload: dict, source_hint: str = "runtime_payload") -> Playlist:
        if not isinstance(payload, dict):
            raise ValueError("歌单数据无效")
        playlist_hash = str(payload.get("playlist_hash", "")).strip()
        if playlist_hash:
            hash_part = playlist_hash[:12]
        else:
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            hash_part = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        hint_part = hashlib.sha1(str(source_hint or "runtime_payload").encode("utf-8")).hexdigest()[:8]
        virtual_source_file = (self._store.path.parent / f"_runtime_{hash_part}_{hint_part}.muse_playlist.json").resolve()
        return self._import_muse_playlist_data(payload, source_file=virtual_source_file, fallback_name="导入歌单")

    def _import_muse_playlist_data(self, payload: dict, *, source_file: Path, fallback_name: str) -> Playlist:
        if not isinstance(payload, dict):
            raise ValueError("歌单文件结构无效")
        if str(payload.get("schema", "")).strip() != MUSE_PLAYLIST_SCHEMA:
            raise ValueError("歌单 schema 不匹配")

        playlist_hash = str(payload.get("playlist_hash", "")).strip()
        playlist_name = self._normalize_playlist_name(str(payload.get("playlist_name", "")).strip() or fallback_name)
        exported_at = str(payload.get("exported_at", "")).strip()
        database_location = str(payload.get("database_location", "")).strip()
        tracks_payload = payload.get("tracks", [])
        if not isinstance(tracks_payload, list):
            raise ValueError("歌单 tracks 字段无效")

        if database_location:
            db_root_raw = Path(database_location)
            if db_root_raw.is_absolute():
                db_root = db_root_raw.resolve()
            else:
                db_root = (source_file.parent / db_root_raw).resolve()
        else:
            db_root = source_file.parent.resolve()

        playlist = self._find_existing_muse_playlist(playlist_hash=playlist_hash, source_file=source_file)
        if playlist is None:
            playlist_id = self._generate_muse_playlist_id(playlist_hash=playlist_hash, source_file=source_file)
            playlist = Playlist(id=playlist_id, name=playlist_name)
            self.playlists[playlist.id] = playlist
        else:
            playlist.name = playlist_name

        source_file_text = str(source_file) if source_file.exists() else ""
        playlist.source_schema = MUSE_PLAYLIST_SCHEMA
        playlist.source_file = source_file_text
        playlist.source_playlist_hash = playlist_hash
        playlist.source_database_location = str(db_root)
        playlist.source_exported_at = exported_at

        old_track_ids = set(playlist.track_ids)
        new_track_ids: list[str] = []
        new_track_ids_set: set[str] = set()

        for raw in tracks_payload:
            if not isinstance(raw, dict):
                continue

            source_track_id = str(raw.get("track_id", "")).strip()
            storage_relpath = self._normalize_relpath(str(raw.get("storage_relpath", "")).strip())
            lyrics_relpath = self._normalize_relpath(str(raw.get("lyrics_storage_relpath", "")).strip())
            source_sha256 = str(raw.get("source_sha256", "")).strip().lower()
            title = str(raw.get("title", "")).strip()
            artist = str(raw.get("artist", "")).strip()
            album = str(raw.get("album", "")).strip()

            track_path = self._resolve_muse_track_path(db_root=db_root, storage_relpath=storage_relpath)
            lyrics_path = self._resolve_muse_track_path(db_root=db_root, storage_relpath=lyrics_relpath) if lyrics_relpath else None
            track = self._find_track_by_source_fields(
                source_path=track_path,
                source_sha256=source_sha256,
                source_track_id=source_track_id,
                source_storage_relpath=storage_relpath,
            )
            if track is None:
                if track_path.exists() and track_path.is_file() and track_path.suffix.lower() in AUDIO_EXTENSIONS:
                    track = self._metadata.extract_track(track_path)
                else:
                    fallback_title = title or track_path.stem or "未知标题"
                    track = Track(
                        id=new_id(),
                        path=str(track_path),
                        title=fallback_title,
                        artist=artist or "未知歌手",
                        album=album or "未知专辑",
                    )
                self.tracks[track.id] = track

            if title:
                track.title = title
            if artist:
                track.artist = artist
            if album:
                track.album = album
            track.source_track_id = source_track_id
            track.source_storage_relpath = storage_relpath
            track.source_lyrics_storage_relpath = lyrics_relpath
            track.source_lyrics_path = str(lyrics_path) if lyrics_path is not None else ""
            track.source_sha256 = source_sha256
            if track_path.exists():
                track.path = str(track_path)

            if track.id not in new_track_ids_set:
                new_track_ids.append(track.id)
                new_track_ids_set.add(track.id)

        playlist.track_ids = new_track_ids
        playlist.touch()
        self.active_playlist_id = playlist.id

        all_songs = self.playlists[ALL_SONGS_ID]
        all_ids = set(all_songs.track_ids)
        for track_id in new_track_ids:
            if track_id not in all_ids:
                all_songs.track_ids.append(track_id)
                all_ids.add(track_id)
        all_songs.touch()

        removed_track_ids = old_track_ids - new_track_ids_set
        if removed_track_ids:
            orphan_track_ids = self._find_orphan_track_ids()
            for track_id in removed_track_ids:
                if track_id in orphan_track_ids:
                    self.tracks.pop(track_id, None)

        self._normalize_playlist_tracks()
        self.save()
        logger.info("导入歌单文件: %s, playlist=%s, songs=%s", source_file, playlist.name, len(playlist.track_ids))
        return playlist

    def sync_muse_playlist_stats(self, playback_stats_service) -> int:
        updated_files = 0
        updated_at = datetime.now(timezone.utc).isoformat()

        for playlist in self.playlists.values():
            if playlist.id == ALL_SONGS_ID:
                continue
            if playlist.source_schema != MUSE_PLAYLIST_SCHEMA:
                continue
            source_file_text = str(playlist.source_file or "").strip()
            if not source_file_text:
                continue
            source_file = Path(source_file_text).resolve()
            if not source_file.exists() or not source_file.is_file():
                continue

            try:
                payload = json.loads(source_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            tracks_payload = payload.get("tracks", [])
            if not isinstance(tracks_payload, list):
                continue

            by_sha: dict[str, int] = {}
            by_track_id: dict[str, int] = {}
            by_relpath: dict[str, int] = {}
            for idx, row in enumerate(tracks_payload):
                if not isinstance(row, dict):
                    continue
                sha = str(row.get("source_sha256", "")).strip().lower()
                tid = str(row.get("track_id", "")).strip()
                rel = self._normalize_relpath(str(row.get("storage_relpath", "")).strip())
                if sha and sha not in by_sha:
                    by_sha[sha] = idx
                if tid and tid not in by_track_id:
                    by_track_id[tid] = idx
                if rel and rel not in by_relpath:
                    by_relpath[rel] = idx

            changed = False
            db_root = Path(str(playlist.source_database_location or "")).resolve() if playlist.source_database_location else None

            for local_track_id in playlist.track_ids:
                track = self.tracks.get(local_track_id)
                if track is None:
                    continue

                target_idx: int | None = None
                track_sha = str(track.source_sha256 or "").strip().lower()
                if track_sha and track_sha in by_sha:
                    target_idx = by_sha[track_sha]
                elif track.source_track_id and track.source_track_id in by_track_id:
                    target_idx = by_track_id[track.source_track_id]
                else:
                    rel = self._normalize_relpath(track.source_storage_relpath)
                    if not rel and db_root is not None:
                        try:
                            rel = self._normalize_relpath(str(Path(track.path).resolve().relative_to(db_root)).replace("\\", "/"))
                        except Exception:
                            rel = ""
                    if rel and rel in by_relpath:
                        target_idx = by_relpath[rel]

                if target_idx is None:
                    continue
                row = tracks_payload[target_idx]
                if not isinstance(row, dict):
                    continue

                stats = playback_stats_service.export_stats_for_track(local_track_id)
                if stats is None:
                    continue
                prev_stats = row.get("stats")
                if not isinstance(prev_stats, dict) or prev_stats != stats:
                    row["stats"] = stats
                    changed = True

            total_play_count = 0
            total_manual_play_count = 0
            total_play_seconds = 0
            total_early_skip_count = 0
            for row in tracks_payload:
                if not isinstance(row, dict):
                    continue
                stats = row.get("stats")
                if not isinstance(stats, dict):
                    continue
                total_play_count += int(stats.get("play_count", 0) or 0)
                total_manual_play_count += int(stats.get("manual_play_count", 0) or 0)
                total_play_seconds += int(stats.get("play_seconds", 0) or 0)
                total_early_skip_count += int(stats.get("early_skip_count", 0) or 0)

            summary = payload.get("stats_summary")
            if not isinstance(summary, dict):
                summary = {}
            next_summary = {
                **summary,
                "total_play_count": int(total_play_count),
                "total_manual_play_count": int(total_manual_play_count),
                "total_play_seconds": int(total_play_seconds),
                "total_early_skip_count": int(total_early_skip_count),
                "updated_at": updated_at,
            }
            if payload.get("stats_summary") != next_summary:
                payload["stats_summary"] = next_summary
                changed = True
            if payload.get("playlist_name") != playlist.name:
                payload["playlist_name"] = playlist.name
                changed = True
            if int(payload.get("track_count", 0) or 0) != len(tracks_payload):
                payload["track_count"] = len(tracks_payload)
                changed = True

            if not changed:
                continue
            try:
                source_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                updated_files += 1
            except Exception:
                continue

        return updated_files

    @staticmethod
    def _normalize_relpath(value: str) -> str:
        text = (value or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        return text.strip("/")

    @staticmethod
    def _resolve_muse_track_path(*, db_root: Path, storage_relpath: str) -> Path:
        rel_text = (storage_relpath or "").strip()
        if not rel_text:
            return db_root.resolve()
        raw = Path(rel_text)
        if raw.is_absolute():
            return raw.resolve()
        normalized = Path(*[part for part in rel_text.replace("\\", "/").split("/") if part not in ("", ".")])
        return (db_root / normalized).resolve()

    def _generate_muse_playlist_id(self, *, playlist_hash: str, source_file: Path) -> str:
        if playlist_hash:
            base = f"muse_{playlist_hash[:16]}"
        else:
            digest = hashlib.sha1(str(source_file).lower().encode("utf-8")).hexdigest()
            base = f"muse_{digest[:16]}"
        candidate = base
        idx = 2
        while candidate in self.playlists:
            existing = self.playlists[candidate]
            same_hash = bool(playlist_hash) and existing.source_playlist_hash == playlist_hash
            same_file = bool(existing.source_file) and Path(existing.source_file).resolve() == source_file.resolve()
            if same_hash or same_file:
                break
            candidate = f"{base}_{idx}"
            idx += 1
        return candidate

    def _find_existing_muse_playlist(self, *, playlist_hash: str, source_file: Path) -> Playlist | None:
        source_text = str(source_file)
        for playlist in self.playlists.values():
            if playlist.id == ALL_SONGS_ID:
                continue
            if playlist.source_schema != MUSE_PLAYLIST_SCHEMA:
                continue
            if playlist_hash and playlist.source_playlist_hash == playlist_hash:
                return playlist
            if playlist.source_file and playlist.source_file == source_text:
                return playlist
        return None

    def _find_track_by_source_fields(
        self,
        *,
        source_path: Path,
        source_sha256: str,
        source_track_id: str,
        source_storage_relpath: str,
    ) -> Track | None:
        resolved_source = source_path.resolve()
        normalized_relpath = self._normalize_relpath(source_storage_relpath)

        if source_sha256:
            for track in self.tracks.values():
                if track.source_sha256 and track.source_sha256.lower() == source_sha256.lower():
                    return track
        if source_track_id:
            for track in self.tracks.values():
                if track.source_track_id == source_track_id:
                    return track
        if normalized_relpath:
            for track in self.tracks.values():
                if self._normalize_relpath(track.source_storage_relpath) == normalized_relpath:
                    return track

        for track in self.tracks.values():
            try:
                if Path(track.path).resolve() == resolved_source:
                    return track
            except Exception:
                continue
        return None

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
