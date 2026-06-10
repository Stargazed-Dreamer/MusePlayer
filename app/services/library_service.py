from __future__ import annotations

"""曲库服务层。

核心职责：
1. 管理曲目与歌单的内存态及持久化读写。
2. 提供文件夹导入、歌单导入、去重、搜索、歌单管理能力。
3. 维护“全部歌曲”与其它歌单双向同步关系。
4. 支持按统一格式导出歌单（含播放统计），供后续数据库分析使用。
"""

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
FAVORITES_ID = "favorites"
MUSE_PLAYLIST_SCHEMA = "musearc_playlist_export_v2"
logger = logging.getLogger("museplayer.library")


class LibraryService:
    def __init__(self, store: LibraryStore, metadata_service: MetadataService):
        self._store = store
        self._metadata = metadata_service
        self.tracks: dict[str, Track] = {}
        self.playlists: dict[str, Playlist] = {}
        self.active_playlist_id: str | None = None
        self._cleanup_log_path = self._store.path.parent / "logs" / "data_cleanup.log"
        self._data_maintenance_logging_enabled = True
        self._path_index: dict[Path, Track] = {}
        self._sha256_index: dict[str, Track] = {}

    def set_data_maintenance_logging_enabled(self, enabled: bool) -> None:
        self._data_maintenance_logging_enabled = bool(enabled)

    def load(self) -> None:
        """加载曲库数据。
        
        执行完整的曲库初始化流程：
        1. 从持久化存储加载基础数据
        2. 确保"全部歌曲"歌单存在
        3. 清理指向不存在文件的歌曲记录
        4. 检测并移除重复的曲目
        5. 设置活动歌单（如果不存在则使用默认）
        6. 同步歌单与实际曲库数据
        
        这是应用启动时的关键初始化步骤，确保内存数据与磁盘数据的一致性。
        """
        tracks, playlists, active = self._store.load()
        self.tracks = tracks
        self.playlists = playlists
        self._ensure_system_playlists()
        changed = False
        changed = self._drop_missing_tracks() or changed
        changed = self._cleanup_missing_lyrics_paths() or changed
        changed = self._deduplicate_tracks() or changed

        if active in self.playlists:
            self.active_playlist_id = active
        else:
            self.active_playlist_id = ALL_SONGS_ID
            self._record_cleanup(
                item=f"active_playlist_id={active}",
                reason="活动歌单不存在，已回退到系统歌单",
            )

        changed = self._normalize_playlist_tracks() or changed
        if changed:
            self.save()
        self._rebuild_indexes()
        logger.info("曲库加载完成: tracks=%s playlists=%s", len(self.tracks), len(self.playlists))

    def _record_cleanup(self, *, item: str, reason: str) -> None:
        if not self._data_maintenance_logging_enabled:
            return
        text = f"数据清理: item={item} reason={reason}"
        try:
            logger.warning(text)
        except Exception:
            pass
        try:
            self._cleanup_log_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._cleanup_log_path.open("a", encoding="utf-8") as f:
                f.write(f"{stamp} {text}\n")
        except Exception:
            pass

    def _rebuild_indexes(self) -> None:
        """重建路径索引和SHA256索引，加速查找。"""
        self._path_index.clear()
        self._sha256_index.clear()
        for track in self.tracks.values():
            try:
                self._path_index[Path(track.path).resolve()] = track
            except Exception:
                pass
            sha = str(getattr(track, "source_sha256", "") or "").strip().lower()
            if sha:
                self._sha256_index[sha] = track

    def _drop_missing_tracks(self) -> bool:
        """清理曲库中指向不存在的文件的跟踪记录。
        
        遍历所有曲目，检查文件路径是否存在，将不存在的文件记录移除。
        这是数据清理的重要步骤，防止播放时出现文件找不到的错误。
        """
        missing_ids: list[str] = []
        for track_id, track in self.tracks.items():
            try:
                source = Path(str(track.path or "")).resolve()
                if not source.exists() or not source.is_file():
                    missing_ids.append(track_id)
            except Exception:
                missing_ids.append(track_id)

        if not missing_ids:
            return False

        for track_id in missing_ids:
            track = self.tracks.get(track_id)
            path_text = str(track.path) if track is not None else ""
            self._record_cleanup(
                item=f"track:{track_id}",
                reason=f"歌曲文件不存在，已移除（path={path_text}）",
            )
            self.tracks.pop(track_id, None)
        logger.info("清理失效歌曲记录: %s", len(missing_ids))
        return True

    def _cleanup_missing_lyrics_paths(self) -> bool:
        changed = False
        for track in self.tracks.values():
            source_lyrics = str(getattr(track, "source_lyrics_path", "") or "").strip()
            if not source_lyrics:
                continue
            try:
                lyric_path = Path(source_lyrics).resolve()
                exists = lyric_path.exists() and lyric_path.is_file()
            except Exception:
                exists = False
            if exists:
                continue
            self._record_cleanup(
                item=f"track:{track.id}",
                reason=f"歌词文件不存在，已清理歌词路径字段（lyrics={source_lyrics}）",
            )
            track.source_lyrics_path = ""
            track.source_lyrics_storage_relpath = ""
            changed = True
        return changed

    def _deduplicate_tracks(self) -> bool:
        """基于文件特征检测并合并重复曲目。
        
        使用文件名、文件大小和时长构建唯一键值进行去重，
        保留最新添加的记录，更新歌单引用关系。
        
        算法流程：
        1. 按时间戳排序，新添加的曲目优先保留
        2. 使用 (文件名, 文件大小, 时长) 作为去重键
        3. 建立ID映射关系，更新歌单引用
        4. 移除重复的曲目记录
        """
        # 去重键采用"文件名 + 文件大小 + 时长毫秒"，兼顾速度与可用性。
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
            return False

        for playlist in self.playlists.values():
            new_track_ids: list[str] = []
            seen: set[str] = set()
            for track_id in playlist.track_ids:
                mapped = remap.get(track_id, track_id)
                if mapped in seen:
                    self._record_cleanup(
                        item=f"playlist:{playlist.id}",
                        reason=f"歌单内重复歌曲引用已去重（track_id={mapped}）",
                    )
                    continue
                seen.add(mapped)
                new_track_ids.append(mapped)
            playlist.track_ids = new_track_ids

        for old_id in remap:
            self._record_cleanup(
                item=f"track:{old_id}",
                reason=f"重复歌曲记录已合并到保留项（target={remap[old_id]}）",
            )
            self.tracks.pop(old_id, None)

        logger.info("清理重复歌曲记录: %s", len(remap))
        return True

    @staticmethod
    def _dedupe_key(track: Track) -> tuple[str, int, int]:
        """生成曲目的去重识别键。
        
        基于文件名、文件大小和时长创建唯一标识，用于检测重复曲目。
        采用小写文件名以确保大小写不敏感的比较。
        
        Args:
            track: Track对象
            
        Returns:
            去重键 (小写文件名, 文件大小, 时长毫秒数)
            如果无法获取文件大小，返回-1
        """
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
        """持久化保存曲库数据。
        
        保存所有曲目、歌单和当前活动歌单状态到存储层。
        """
        self._store.save(self.tracks, self.playlists, self.active_playlist_id)

    def _ensure_all_songs_playlist(self) -> None:
        if ALL_SONGS_ID not in self.playlists:
            self.playlists[ALL_SONGS_ID] = Playlist(id=ALL_SONGS_ID, name="全部歌曲", track_ids=[])
            return
        self.playlists[ALL_SONGS_ID].name = "全部歌曲"

    def _ensure_favorites_playlist(self) -> None:
        if FAVORITES_ID not in self.playlists:
            self.playlists[FAVORITES_ID] = Playlist(id=FAVORITES_ID, name="我喜欢", track_ids=[])
            return
        self.playlists[FAVORITES_ID].name = "我喜欢"

    def _ensure_system_playlists(self) -> None:
        self._ensure_all_songs_playlist()
        self._ensure_favorites_playlist()

    def _normalize_playlist_tracks(self) -> bool:
        changed = False
        existing_track_ids = set(self.tracks.keys())
        for playlist in self.playlists.values():
            original = list(playlist.track_ids)
            filtered: list[str] = []
            seen: set[str] = set()
            removed_invalid = False
            for track_id in original:
                if track_id not in existing_track_ids:
                    removed_invalid = True
                    changed = True
                    self._record_cleanup(
                        item=f"playlist:{playlist.id}",
                        reason=f"歌单引用了不存在歌曲，已清理（track_id={track_id}）",
                    )
                    continue
                if track_id in seen:
                    changed = True
                    self._record_cleanup(
                        item=f"playlist:{playlist.id}",
                        reason=f"歌单内重复歌曲已去重（track_id={track_id}）",
                    )
                    continue
                seen.add(track_id)
                filtered.append(track_id)
            if filtered != original:
                playlist.track_ids = filtered
            if removed_invalid and playlist.source_playlist_hash:
                self._record_cleanup(
                    item=f"playlist:{playlist.id}",
                    reason="歌单出现失效歌曲引用，已清理旧歌单哈希与来源绑定字段",
                )
                playlist.source_playlist_hash = ""
                playlist.source_schema = ""
                playlist.source_file = ""
                playlist.source_database_location = ""
                playlist.source_exported_at = ""
                changed = True
            if not playlist.track_ids and playlist.source_playlist_hash:
                self._record_cleanup(
                    item=f"playlist:{playlist.id}",
                    reason="歌单无有效歌曲，已清理歌单哈希字段",
                )
                playlist.source_playlist_hash = ""
                changed = True
        all_ids = list(self.tracks.keys())
        if self.playlists[ALL_SONGS_ID].track_ids != all_ids:
            self.playlists[ALL_SONGS_ID].track_ids = all_ids
            changed = True
        return changed

    def get_playlist(self, playlist_id: str | None) -> Playlist:
        """获取指定ID的歌单，如果不存在则返回"全部歌曲"歌单。
        
        Args:
            playlist_id: 歌单ID，None返回默认歌单
            
        Returns:
            Playlist对象
        """
        if playlist_id and playlist_id in self.playlists:
            return self.playlists[playlist_id]
        return self.playlists[ALL_SONGS_ID]

    def list_playlists(self) -> list[Playlist]:
        """获取所有歌单列表。
        
        返回的列表以"全部歌曲"为首，其余歌单按名称字母序排序。
        
        Returns:
            排序后的歌单列表
        """
        all_pl = self.playlists.get(ALL_SONGS_ID)
        fav_pl = self.playlists.get(FAVORITES_ID)
        others = [p for p in self.playlists.values() if p.id not in {ALL_SONGS_ID, FAVORITES_ID}]
        others.sort(key=lambda p: p.name.lower())
        ordered: list[Playlist] = []
        if all_pl is not None:
            ordered.append(all_pl)
        if fav_pl is not None:
            ordered.append(fav_pl)
        ordered.extend(others)
        return ordered

    def get_playlist_tracks(self, playlist_id: str | None) -> list[Track]:
        """获取指定歌单的所有曲目。
        
        Args:
            playlist_id: 歌单ID
            
        Returns:
            该歌单下的Track对象列表
        """
        playlist = self.get_playlist(playlist_id)
        return [self.tracks[tid] for tid in playlist.track_ids if tid in self.tracks]

    def search_playlist_tracks(self, playlist_id: str | None, keyword: str) -> list[Track]:
        """在指定歌单中搜索曲目。
        
        搜索范围包括曲目标题、艺术家、专辑名，不区分大小写。
        
        Args:
            playlist_id: 要搜索的歌单ID
            keyword: 搜索关键词
            
        Returns:
            匹配的Track对象列表
        """
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
        """创建新歌单。
        
        创建指定名称的歌单并设置为当前活动歌单。
        歌单名称会自动清理空白并补全默认值。
        
        Args:
            name: 歌单名称
            
        Returns:
            新创建的Playlist对象
        """
        clean_name = (name or "").strip() or "新建歌单"
        playlist = Playlist(id=new_id(), name=clean_name)
        self.playlists[playlist.id] = playlist
        self.active_playlist_id = playlist.id
        self.save()
        logger.info("创建歌单: %s (%s)", playlist.name, playlist.id)
        return playlist

    def rename_playlist(self, playlist_id: str, name: str) -> None:
        """重命名歌单。
        
        "全部歌曲"歌单为系统保留不允许重命名。
        
        Args:
            playlist_id: 要重命名的歌单ID
            name: 新的歌单名称
        """
        if playlist_id in {ALL_SONGS_ID, FAVORITES_ID}:
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
        """复制歌单。
        
        创建源歌单的副本，包含所有曲目的引用。
        自动设置唯一的歌单名称。
        
        Args:
            source_playlist_id: 源歌单ID
            new_name: 新歌单名称，None则使用默认命名规则
            
        Returns:
            新创建的Playlist对象，如果源歌单不存在返回None
        """
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
        """合并两个歌单。
        
        将源歌单的所有曲目添加到目标歌单，自动去重，
        保持目标歌单原有的排序。
        
        Args:
            source_playlist_id: 源歌单ID
            target_playlist_id: 目标歌单ID
            
        Returns:
            实际新增的曲目数量
        """
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
        """删除歌单及其相关的孤立曲目。
        
        删除指定歌单，如果曲目不再被其他歌单引用则一并删除。
        "全部歌曲"歌单为系统保留，不允许删除。
        
        Args:
            playlist_id: 要删除的歌单ID
        """
        if playlist_id in {ALL_SONGS_ID, FAVORITES_ID}:
            return  # 系统保护，不允许删除系统歌单
        if playlist_id not in self.playlists:
            return
        
        removed = self.playlists[playlist_id]
        del self.playlists[playlist_id]

        # 查找并删除不再被其他歌单引用的孤立曲目
        orphan_track_ids = self._find_orphan_track_ids(exclude_playlist_id=playlist_id)
        for track_id in orphan_track_ids:
            self.tracks.pop(track_id, None)

        # 重新同步全部歌曲歌单
        self._normalize_playlist_tracks()
        
        # 如果删除的是当前活动歌单，切换到全部歌曲
        if self.active_playlist_id == playlist_id:
            self.active_playlist_id = ALL_SONGS_ID
            
        self.save()
        logger.info("删除歌单: %s (%s), 清理歌曲=%s", removed.name, playlist_id, len(orphan_track_ids))

    def set_active_playlist(self, playlist_id: str) -> None:
        """设置当前活动歌单。
        
        Args:
            playlist_id: 要设为活动的歌单ID
        """
        if playlist_id not in self.playlists:
            return
        self.active_playlist_id = playlist_id
        self.save()

    def add_track_ids_to_playlist(self, playlist_id: str, track_ids: list[str]) -> None:
        """批量添加曲目到歌单。
        
        自动去重，只添加有效且未存在的曲目。
        
        Args:
            playlist_id: 目标歌单ID
            track_ids: 要添加的曲目ID列表
        """
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

        if playlist_id == FAVORITES_ID:
            playlist = self.playlists.get(FAVORITES_ID)
            if playlist is None:
                return removed_globally
            before = len(playlist.track_ids)
            playlist.track_ids = [x for x in playlist.track_ids if x != track_id]
            if len(playlist.track_ids) != before:
                playlist.touch()
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

    def is_favorite(self, track_id: str | None) -> bool:
        tid = str(track_id or "").strip()
        if not tid:
            return False
        playlist = self.playlists.get(FAVORITES_ID)
        if playlist is None:
            return False
        return tid in playlist.track_ids

    def toggle_favorite(self, track_id: str) -> bool:
        """切换歌曲的“我喜欢”状态，返回切换后的状态。"""
        tid = str(track_id or "").strip()
        if not tid or tid not in self.tracks:
            return False
        self._ensure_favorites_playlist()
        favorites = self.playlists[FAVORITES_ID]
        if tid in favorites.track_ids:
            favorites.track_ids = [x for x in favorites.track_ids if x != tid]
            favorites.touch()
            self.save()
            return False
        favorites.track_ids.append(tid)
        favorites.touch()
        self.save()
        return True

    def get_track(self, track_id: str | None) -> Track | None:
        """根据ID获取单个曲目信息。
        
        Args:
            track_id: 曲目ID
            
        Returns:
            Track对象，如果ID无效或不存在返回None
        """
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
        """导入整个文件夹的音频文件到曲库。
        
        智能文件夹导入策略：
        1. 如果指定了现有歌单ID且不是"全部歌曲"：
           - 将所选目录下的音频文件导入到该歌单
           - 该目录的第一级子目录分别创建/复用独立歌单
        2. 如果没有指定歌单或指定"全部歌曲"：
           - 所选目录下的音频文件导入到以目录名为基础的新歌单
           - 第一级子目录分别创建/复用独立歌单
        
        深层子目录（第二级及以下）不再展开，控制导入范围避免歌单过多。
        
        Args:
            folder: 要导入的文件夹路径
            playlist_id: 指定目标歌单ID，None则自动创建新歌单
            recursive: Scan模式下是否递归搜索，默认为True但只影响当前目录层级
            progress_callback: 进度回调函数 (processed_count, total_count, current_file_path)
            
        Returns:
            新导入的Track对象列表
            
        Raises:
            FileNotFoundError: 文件夹不存在或不是目录
        """
        # 导入策略：
        # - 所选目录下的歌曲导入根歌单
        # - 第一级子目录分别建立/复用独立歌单
        # - 深层子目录不再展开，控制导入范围
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
                existing = self._path_index.get(file_path)
                if existing is None:
                    track = self._metadata.extract_track(file_path)
                    self.tracks[track.id] = track
                    self._path_index[file_path] = track
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

    def import_file(self, file_path: Path, playlist_id: str | None = None, *, skip_save: bool = False) -> Track:
        """导入单个音频文件到曲库。
        
        将指定的音频文件元数据提取到曲库，并添加到指定的歌单中。
        如果文件已存在，则直接引用现有曲目记录。
        
        Args:
            file_path: 音频文件路径
            playlist_id: 目标歌单ID，None则使用当前活动歌单
            skip_save: 为True时跳过持久化保存（批量导入时由调用方统一保存）
            
        Returns:
            Track对象
            
        Raises:
            FileNotFoundError: 文件不存在或不是常规文件
            ValueError: 文件格式不受支持
        """
        source = Path(file_path).resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(str(source))
        if source.suffix.lower() not in AUDIO_EXTENSIONS:
            raise ValueError(f"不支持的音频格式: {source.suffix}")

        existing = self._path_index.get(source)
        track = existing if existing is not None else self._metadata.extract_track(source)

        if existing is None:
            self.tracks[track.id] = track
            self._path_index[source] = track

        all_songs = self.playlists[ALL_SONGS_ID]
        if track.id not in all_songs.track_ids:
            all_songs.track_ids.append(track.id)
            all_songs.touch()

        target_playlist = self.get_playlist(playlist_id or self.active_playlist_id)
        if track.id not in target_playlist.track_ids:
            target_playlist.track_ids.append(track.id)
            target_playlist.touch()

        if not skip_save:
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
        # DB 导出歌单导入时保留 source_* 元数据，供后续统计回写和歌词路径解析使用。
        if not isinstance(payload, dict):
            raise ValueError("歌单文件结构无效")
        schema_raw = str(payload.get("schema", "")).strip()
        if schema_raw not in {"musearc_playlist_export_v1", "musearc_playlist_export_v2"}:
            raise ValueError("歌单 schema 不匹配")

        # 解析歌单基本信息
        playlist_hash = str(payload.get("playlist_hash", "")).strip()
        playlist_name = self._normalize_playlist_name(str(payload.get("playlist_name", "")).strip() or fallback_name)
        playlist_ordered = bool(payload.get("ordered", True))
        exported_at = str(payload.get("exported_at", "")).strip()
        database_location = str(payload.get("database_location", "")).strip()
        tracks_payload = payload.get("tracks", [])
        if not isinstance(tracks_payload, list):
            raise ValueError("歌单 tracks 字段无效")

        # 解析数据库根目录路径，支持绝对路径和相对路径
        if database_location:
            db_root_raw = Path(database_location)
            if db_root_raw.is_absolute():
                db_root = db_root_raw.resolve()
            else:
                db_root = (source_file.parent / db_root_raw).resolve()
        else:
            db_root = source_file.parent.resolve()

        # 查找或创建歌单
        playlist = self._find_existing_muse_playlist(playlist_hash=playlist_hash, source_file=source_file)
        if playlist is None:
            # 创建新歌单
            playlist_id = self._generate_muse_playlist_id(playlist_hash=playlist_hash, source_file=source_file)
            playlist = Playlist(id=playlist_id, name=playlist_name, ordered=playlist_ordered)
            self.playlists[playlist.id] = playlist
        else:
            # 更新现有歌单名称
            playlist.name = playlist_name
            playlist.ordered = playlist_ordered

        # 导入后即落地为内部歌单，不保留“后续必须写回源文件”的绑定关系。
        playlist.source_schema = ""
        playlist.source_file = ""
        playlist.source_playlist_hash = playlist_hash
        playlist.source_database_location = str(db_root)
        playlist.source_exported_at = exported_at

        # 处理歌单中的曲目数据
        old_track_ids = set(playlist.track_ids)
        new_track_ids: list[str] = []
        new_track_ids_set: set[str] = set()

        for raw in tracks_payload:
            if not isinstance(raw, dict):
                continue

            # 解析曲目元数据
            source_track_id = str(raw.get("track_id", "")).strip()
            storage_relpath = self._normalize_relpath(str(raw.get("storage_relpath", "")).strip())
            lyrics_relpath = self._normalize_relpath(str(raw.get("lyrics_storage_relpath", "")).strip())
            lyrics_array = raw.get("lyrics", [])
            source_sha256 = str(raw.get("source_sha256", "")).strip().lower()
            title = str(raw.get("title", "")).strip()
            artist = str(raw.get("artist", "")).strip()
            album = str(raw.get("album", "")).strip()

            track_path = self._resolve_muse_track_path(db_root=db_root, storage_relpath=storage_relpath)
            lyrics_path = self._resolve_muse_track_path(db_root=db_root, storage_relpath=lyrics_relpath) if lyrics_relpath else None
            
            # 尝试通过多种方式查找现有曲目记录
            track = self._find_track_by_source_fields(
                source_path=track_path,
                source_sha256=source_sha256,
                source_track_id=source_track_id,
                source_storage_relpath=storage_relpath,
            )
            
            # 如果找不到现有记录，则创建新曲目
            if track is None:
                if track_path.exists() and track_path.is_file() and track_path.suffix.lower() in AUDIO_EXTENSIONS:
                    # 文件存在且为支持的音频格式，提取元数据
                    track = self._metadata.extract_track(track_path)
                else:
                    # 文件不存在，使用数据库中的元数据创建占位记录
                    fallback_title = title or track_path.stem or "未知标题"
                    track = Track(
                        id=new_id(),
                        path=str(track_path),
                        title=fallback_title,
                        artist=artist or "未知歌手",
                        album=album or "未知专辑",
                    )
                self.tracks[track.id] = track

            # 更新曲目元数据
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
            if isinstance(lyrics_array, list) and lyrics_array:
                extra_paths: list[str] = []
                for lentry in lyrics_array:
                    if not isinstance(lentry, dict):
                        continue
                    lrel = self._normalize_relpath(str(lentry.get("relpath", "")).strip())
                    if not lrel:
                        continue
                    lpath = self._resolve_muse_track_path(db_root=db_root, storage_relpath=lrel)
                    lstr = str(lpath) if lpath else ""
                    if lstr and lstr != track.source_lyrics_path:
                        extra_paths.append(lstr)
                track.extra_lyrics_paths = "|".join(extra_paths)
            if track_path.exists():
                track.path = str(track_path)

            # 添加到新歌单中（去重）
            if track.id not in new_track_ids_set:
                new_track_ids.append(track.id)
                new_track_ids_set.add(track.id)

        # 更新歌单曲目列表
        playlist.track_ids = new_track_ids
        playlist.touch()
        self.active_playlist_id = playlist.id

        # 同步到"全部歌曲"歌单
        all_songs = self.playlists[ALL_SONGS_ID]
        all_ids = set(all_songs.track_ids)
        for track_id in new_track_ids:
            if track_id not in all_ids:
                all_songs.track_ids.append(track_id)
                all_ids.add(track_id)
        all_songs.touch()

        # 清理被移除的孤立曲目
        removed_track_ids = old_track_ids - new_track_ids_set
        if removed_track_ids:
            orphan_track_ids = self._find_orphan_track_ids()
            for track_id in removed_track_ids:
                if track_id in orphan_track_ids:
                    self.tracks.pop(track_id, None)

        # 重新同步歌单数据并保存
        self._normalize_playlist_tracks()
        self.save()
        logger.info("导入歌单文件: %s, playlist=%s, songs=%s", source_file, playlist.name, len(playlist.track_ids))
        return playlist

    def export_playlist_file(self, playlist_id: str, out_dir: Path, playback_stats_service) -> Path:
        playlist = self.get_playlist(playlist_id)
        track_ids = [tid for tid in playlist.track_ids if tid in self.tracks]
        if not track_ids:
            raise ValueError("歌单没有可导出的歌曲")

        playlist_hash = self._get_or_create_export_hash(playlist)
        exported_at = datetime.now(timezone.utc).isoformat()
        database_location = str(
            Path(str(playlist.source_database_location or "")).resolve()
            if str(playlist.source_database_location or "").strip()
            else self._store.path.parent.parent.resolve()
        )

        tracks_out: list[dict] = []
        total_play_count = 0
        total_manual_play_count = 0
        total_play_seconds = 0
        total_early_skip_count = 0
        db_root = Path(database_location)

        for tid in track_ids:
            track = self.tracks[tid]
            stats = playback_stats_service.export_stats_for_track(tid) or {
                "play_count": 0,
                "manual_play_count": 0,
                "play_seconds": 0,
                "early_skip_count": 0,
            }
            total_play_count += int(stats.get("play_count", 0) or 0)
            total_manual_play_count += int(stats.get("manual_play_count", 0) or 0)
            total_play_seconds += int(stats.get("play_seconds", 0) or 0)
            total_early_skip_count += int(stats.get("early_skip_count", 0) or 0)

            track_id_export = str(track.source_track_id or tid).strip() or tid
            lyrics_list = self._export_track_lyrics(track, db_root)
            tracks_out.append(
                {
                    "track_id": track_id_export,
                    "storage_relpath": self._export_relpath(track=track, db_root=db_root, kind="audio"),
                    "title": str(track.title or "").strip(),
                    "artist": str(track.artist or "").strip(),
                    "album": str(track.album or "").strip(),
                    "lyrics": lyrics_list,
                    "lyrics_storage_relpath": lyrics_list[0]["relpath"] if lyrics_list else "",
                    "source_sha256": str(track.source_sha256 or "").strip(),
                    "stats": {
                        "play_count": max(0, int(stats.get("play_count", 0) or 0)),
                        "manual_play_count": max(0, int(stats.get("manual_play_count", 0) or 0)),
                        "play_seconds": max(0, int(stats.get("play_seconds", 0) or 0)),
                        "early_skip_count": max(0, int(stats.get("early_skip_count", 0) or 0)),
                    },
                }
            )

        out_root = Path(out_dir).expanduser().resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        safe_name = self._sanitize_export_name(playlist.name or "playlist")
        file_path = out_root / f"{safe_name}_{playlist_hash[:10]}.muse_playlist.json"

        payload = {
            "schema": MUSE_PLAYLIST_SCHEMA,
            "playlist_hash": playlist_hash,
            "playlist_name": str(playlist.name or "").strip(),
            "ordered": bool(getattr(playlist, "ordered", True)),
            "exported_at": exported_at,
            "database_location": database_location,
            "track_count": len(tracks_out),
            "stats_summary": {
                "total_play_count": int(total_play_count),
                "total_manual_play_count": int(total_manual_play_count),
                "total_play_seconds": int(total_play_seconds),
                "total_early_skip_count": int(total_early_skip_count),
                "updated_at": exported_at,
            },
            "tracks": tracks_out,
        }
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("导出歌单: playlist=%s tracks=%s file=%s", playlist.id, len(tracks_out), file_path)
        return file_path

    def _get_or_create_export_hash(self, playlist: Playlist) -> str:
        raw = str(playlist.source_playlist_hash or "").strip().lower()
        if raw:
            return raw
        raw = hashlib.sha1(f"playlist:{playlist.id}".encode("utf-8")).hexdigest()
        playlist.source_playlist_hash = raw
        playlist.touch()
        self.save()
        return raw

    @staticmethod
    def _sanitize_export_name(name: str) -> str:
        safe = "".join(ch if ch not in "\\/:*?\"<>|" else "_" for ch in str(name or "").strip()).strip()
        return safe or "playlist"

    def _export_track_lyrics(self, track: Track, db_root: Path) -> list[dict]:
        result: list[dict] = []
        lyrics_paths = self._get_track_lyrics_paths(track)
        for lp in lyrics_paths:
            rel = ""
            try:
                rel = str(Path(lp).resolve().relative_to(db_root)).replace("\\", "/")
            except Exception:
                rel = Path(lp).name
            suffix = Path(lp).suffix.lower()
            lang = "original"
            if "_qmRoma" in Path(lp).stem or suffix == ".qmroma":
                lang = "romaji"
            elif "_qmts" in Path(lp).stem or suffix == ".qmts":
                lang = "translation"
            elif "_qm" in Path(lp).stem or suffix == ".qrc":
                lang = "japanese"
            result.append({"relpath": self._normalize_relpath(rel), "lang": lang})
        return result

    def _get_track_lyrics_paths(self, track: Track) -> list[str]:
        paths: list[str] = []
        main = str(track.source_lyrics_path or "").strip()
        if main:
            paths.append(main)
        extra = str(getattr(track, "extra_lyrics_paths", "") or "").strip()
        if extra:
            for p in extra.split("|"):
                p = p.strip()
                if p and p not in paths:
                    paths.append(p)
        return paths

    def _export_relpath(self, *, track: Track, db_root: Path, kind: str) -> str:
        if kind == "lyrics":
            rel = self._normalize_relpath(str(track.source_lyrics_storage_relpath or "").strip())
            if rel:
                return rel
            lyrics_abs = str(track.source_lyrics_path or "").strip()
            if not lyrics_abs:
                return ""
            try:
                return self._normalize_relpath(str(Path(lyrics_abs).resolve().relative_to(db_root)).replace("\\", "/"))
            except Exception:
                return self._normalize_relpath(Path(lyrics_abs).name)

        rel = self._normalize_relpath(str(track.source_storage_relpath or "").strip())
        if rel:
            return rel
        try:
            return self._normalize_relpath(str(Path(track.path).resolve().relative_to(db_root)).replace("\\", "/"))
        except Exception:
            return self._normalize_relpath(Path(track.path).name)

    def sync_muse_playlist_stats(self, playback_stats_service) -> int:
        """同步播放统计数据到外部歌单文件。
        
        将运行时收集的播放统计数据回写到对应的 .muse_playlist.json 文件中，
        只在本地有实际统计数据时才更新，避免覆盖文件中已有的历史数据。
        
        Args:
            playback_stats_service: 播放统计服务实例
            
        Returns:
            更新的文件数量
        """
        _ = playback_stats_service
        # 按需求停用“导入歌单文件后持续回写源文件统计”。
        # 导入后的歌单按内部数据管理，导出时再生成新文件。
        return 0

        # 将运行时统计回写至外部歌单文件。
        # 安全策略：本地无统计时，不覆盖文件中已有统计，避免误清空历史数据。
        updated_files = 0
        updated_at = datetime.now(timezone.utc).isoformat()

        # 遍历所有歌单，只处理 Muse 格式导出的歌单文件
        for playlist in self.playlists.values():
            # 跳过系统歌单和非 Muse 格式歌单
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

            # 读取现有的歌单文件内容
            try:
                payload = json.loads(source_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            tracks_payload = payload.get("tracks", [])
            if not isinstance(tracks_payload, list):
                continue

            # 建立索引映射：SHA256 -> 位置, track_id -> 位置, 相对路径 -> 位置
            # 多维度索引确保与外部数据的准确匹配
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

            # 遍历当前歌单中的所有本地曲目
            for local_track_id in playlist.track_ids:
                track = self.tracks.get(local_track_id)
                if track is None:
                    continue

                # 使用多维度匹配策略查找对应的外部记录
                target_idx: int | None = None
                track_sha = str(track.source_sha256 or "").strip().lower()
                if track_sha and track_sha in by_sha:
                    target_idx = by_sha[track_sha]
                elif track.source_track_id and track.source_track_id in by_track_id:
                    target_idx = by_track_id[track.source_track_id]
                else:
                    # 尝试使用相对路径匹配
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

                # 只在本地有统计数据时才更新外部文件
                stats = playback_stats_service.export_stats_for_track(local_track_id)
                if stats is None:
                    continue
                prev_stats = row.get("stats") 
                # 只有当统计数据实际发生变化时标记为需要更新
                if not isinstance(prev_stats, dict) or prev_stats != stats:
                    row["stats"] = stats
                    changed = True

            # 计算整张歌单的总统计信息
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

            # 更新歌单级别的统计汇总信息
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
            # 仅当统计汇总发生变化时才更新
            if payload.get("stats_summary") != next_summary:
                payload["stats_summary"] = next_summary
                changed = True
            if payload.get("playlist_name") != playlist.name:
                payload["playlist_name"] = playlist.name
                changed = True
            if int(payload.get("track_count", 0) or 0) != len(tracks_payload):
                payload["track_count"] = len(tracks_payload)
                changed = True

            # 只有在数据实际发生变化时才写回文件
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
        """标准化相对路径格式。
        
        将路径转换为统一的相对路径格式，用于歌单数据中的路径存储。
        - 转换为正斜杠
        - 移除开头的 ./ 前缀
        - 移除首尾斜杠
        
        Args:
            value: 原始路径字符串
            
        Returns:
            标准化后的相对路径
        """
        text = (value or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        return text.strip("/")

    @staticmethod
    def _resolve_muse_track_path(*, db_root: Path, storage_relpath: str) -> Path:
        """解析Muse数据库中的存储相对路径为绝对路径。
        
        处理歌单导入时从数据库记录中读取的存储路径，将其转换为完整的文件路径。
        支持相对路径和绝对路径的混合处理。
        
        Args:
            db_root: 数据库根目录路径
            storage_relpath: 存储相对路径或绝对路径
            
        Returns:
            解析后的完整Path对象
        """
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
            if playlist.id in {ALL_SONGS_ID, FAVORITES_ID}:
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
            hit = self._sha256_index.get(source_sha256.lower())
            if hit is not None:
                return hit
        if source_track_id:
            for track in self.tracks.values():
                if track.source_track_id == source_track_id:
                    return track
        if normalized_relpath:
            for track in self.tracks.values():
                if self._normalize_relpath(track.source_storage_relpath) == normalized_relpath:
                    return track

        hit = self._path_index.get(resolved_source)
        if hit is not None:
            return hit
        return None

    @staticmethod
    def _scan_audio_files(folder: Path, recursive: bool = True) -> list[Path]:
        """扫描文件夹中的音频文件。
        
        使用指定的扩展名过滤标准来扫描音频文件，并按字母顺序排序返回。
        
        Args:
            folder: 要扫描的文件夹路径
            recursive: 是否递归扫描子文件夹，默认为True
            
        Returns:
            按字母序排序的音频文件路径列表
        """
        # 根据递归参数选择扫描模式
        globber = folder.rglob("*") if recursive else folder.glob("*")
        files: list[Path] = []
        for p in globber:
            if not p.is_file():
                continue
            if p.suffix.lower() in AUDIO_EXTENSIONS:
                files.append(p.resolve())
        # 按文件路径的字母序排序返回
        files.sort(key=lambda x: str(x).lower())
        return files

    def _resolve_target_playlist_for_folder_import(self, folder: Path, playlist_id: str | None) -> Playlist:
        """为文件夹导入解析目标歌单。
        
        智能歌单匹配策略：
        1. 如果指定了现有歌单ID且有效，直接使用该歌单
        2. 根据文件夹名查找现有歌单
        3. 如果不存在，创建以文件夹名命名的新歌单
        
        Args:
            folder: 要导入的文件夹路径
            playlist_id: 指定的目标歌单ID，None则自动选择
            
        Returns:
            目标Playlist对象
        """
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
        """根据歌单名称查找现有歌单。
        
        不区分大小写的名称匹配，忽略首尾空格。
        系统保留歌单"全部歌曲"不参与查找。
        
        Args:
            name: 要查找的歌单名称
            
        Returns:
            找到的Playlist对象，如果未找到则返回None
        """
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
        """查找不再被任何歌单引用的孤立曲目ID。
        
        通过扫描所有歌单的track_ids，找出曲库中存在但不再被任何歌单引用的曲目。
        系统保留歌单"全部歌曲"不参与引用统计。
        
        Args:
            exclude_playlist_id: 要排除的歌单ID，用于在删除歌单时查找孤立曲目
            
        Returns:
            孤立曲目ID的集合
        """
        # 收集所有非系统歌单引用的曲目ID
        referenced: set[str] = set()
        for playlist in self.playlists.values():
            if playlist.id == ALL_SONGS_ID:
                continue
            if exclude_playlist_id and playlist.id == exclude_playlist_id:
                continue
            referenced.update(track_id for track_id in playlist.track_ids if track_id in self.tracks)

        # 返回曲库中存在但未被引用（除全部歌曲外）的曲目
        all_track_ids = set(self.tracks.keys())
        return all_track_ids - referenced

    def _remove_track_globally(self, track_id: str) -> None:
        track = self.tracks.get(track_id)
        if track is not None:
            del self.tracks[track_id]
            try:
                path_key = Path(track.path).resolve()
                if self._path_index.get(path_key) is track:
                    del self._path_index[path_key]
            except Exception:
                pass
            sha = str(getattr(track, "source_sha256", "") or "").strip().lower()
            if sha and self._sha256_index.get(sha) is track:
                del self._sha256_index[sha]
        for playlist in self.playlists.values():
            if track_id in playlist.track_ids:
                playlist.track_ids = [x for x in playlist.track_ids if x != track_id]
                playlist.touch()
