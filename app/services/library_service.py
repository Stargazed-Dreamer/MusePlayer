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
        """
        初始化音频库管理器实例。

        功能：
            设置音乐库的核心数据结构，包括存储引用、元数据服务、
            曲目索引、播放列表管理以及数据维护相关的配置。

        参数：
            store (LibraryStore): 图书馆/媒体库的底层存储服务，负责数据持久化
            metadata_service (MetadataService): 元数据服务，用于获取和管理曲目元信息

        返回值：
            None（构造方法无返回值）
        """
        self._store = store  # 存储服务引用
        self._metadata = metadata_service  # 元数据服务引用
        self.tracks: dict[str, Track] = {}  # 存储所有曲目，键为曲目ID，值为Track对象
        self.playlists: dict[str, Playlist] = {}  # 存储所有播放列表，键为播放列表ID，值为Playlist对象
        self.active_playlist_id: str | None = None  # 当前活跃/正在播放的播放列表ID，None表示无
        self._cleanup_log_path = self._store.path.parent / "logs" / "data_cleanup.log"  # 数据清理日志文件路径
        self._data_maintenance_logging_enabled = True  # 是否启用数据维护日志记录
        self._path_index: dict[Path, Track] = {}  # 按文件路径索引曲目，用于快速查找
        self._sha256_index: dict[str, Track] = {}  # 按SHA256哈希索引曲目，用于去重和校验

    def set_data_maintenance_logging_enabled(self, enabled: bool) -> None:
        self._data_maintenance_logging_enabled = bool(enabled)

    def load(self, *, quick: bool = False) -> None:
        """加载曲库数据。
        
        执行曲库初始化流程：
        1. 从持久化存储加载基础数据
        2. 确保"全部歌曲"歌单存在
        3. 设置活动歌单（如果不存在则使用默认）
        4. 同步歌单与实际曲库数据
        
        当 quick=True 时跳过耗时的磁盘检查（缺失文件检测、歌词路径清理、
        重复曲目检测），这些操作可通过 deferred_cleanup() 在后台执行。
        
        这是应用启动时的关键初始化步骤，确保内存数据与磁盘数据的一致性。
        """
        tracks, playlists, active = self._store.load()
        self.load_preloaded(tracks, playlists, active, quick=quick)

    def load_preloaded(self, tracks: dict[str, Track], playlists: dict[str, Playlist], active: str | None, indexes: tuple[dict[Path, Track], dict[str, Track]] | None = None, *, quick: bool = False) -> None:
        self.tracks = tracks
        self.playlists = playlists
        self._ensure_system_playlists()

        if active in self.playlists:
            self.active_playlist_id = active
        else:
            self.active_playlist_id = ALL_SONGS_ID
            self._record_cleanup(
                item=f"active_playlist_id={active}",
                reason="活动歌单不存在，已回退到系统歌单",
            )

        changed = False
        if not quick:
            changed = self._normalize_playlist_tracks() or changed
            changed = self._drop_missing_tracks() or changed
            changed = self._cleanup_missing_lyrics_paths() or changed
            changed = self._deduplicate_tracks() or changed
        if changed:
            self.save()
        if indexes is None:
            self._rebuild_indexes()
        else:
            self._path_index, self._sha256_index = indexes
        logger.info("曲库加载完成: tracks=%s playlists=%s quick=%s", len(self.tracks), len(self.playlists), quick)


    def deferred_cleanup(self) -> None:
        """执行延迟的曲库清理操作（缺失文件检测、歌词路径清理、重复曲目检测）。
        
        适用于在后台线程中运行，避免阻塞 UI 启动。
        如果检测到变更会自动保存并重建索引。
        """
        changed = False
        changed = self._drop_missing_tracks() or changed
        changed = self._cleanup_missing_lyrics_paths() or changed
        changed = self._deduplicate_tracks() or changed
        changed = self._normalize_playlist_tracks() or changed
        if changed:
            self.save()
            self._rebuild_indexes()
        logger.info("延迟清理完成: changed=%s", changed)

    def _record_cleanup(self, *, item: str, reason: str) -> None:
        """
        记录数据清理操作，将清理事件写入日志和文件。
    
        功能:
            - 检查是否启用数据维护日志记录
            - 构造清理事件描述文本
            - 通过标准日志记录器输出警告级别日志
            - 将带时间戳的清理记录追加到指定日志文件
        
        参数:
            item (str): 被清理的数据项目标识
            reason (str): 清理原因说明
        
        返回值:
            None: 此方法不返回任何值，仅执行记录操作
        """
        # 检查是否启用数据维护日志记录，未启用则直接返回
        if not self._data_maintenance_logging_enabled:
            return
    
        # 构造清理事件描述文本
        text = f"数据清理: item={item} reason={reason}"
    
        try:
            # 通过日志记录器输出警告级别日志
            logger.warning(text)
        except Exception:
            # 日志记录失败时静默忽略异常，不影响主流程
            pass
    
        try:
            # 确保日志文件父目录存在，不存在则自动创建
            self._cleanup_log_path.parent.mkdir(parents=True, exist_ok=True)
        
            # 生成当前时间戳，格式为年-月-日 时:分:秒
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
            # 以追加模式打开日志文件，使用UTF-8编码
            with self._cleanup_log_path.open("a", encoding="utf-8") as f:
                # 写入带时间戳的清理记录并换行
                f.write(f"{stamp} {text}\n")
        except Exception:
            # 文件操作失败时静默忽略异常，确保程序健壮性
            pass

    @staticmethod
    def build_indexes_for_tracks(tracks: dict[str, Track]) -> tuple[dict[Path, Track], dict[str, Track]]:
        path_index: dict[Path, Track] = {}
        sha256_index: dict[str, Track] = {}
        for track in tracks.values():
            try:
                path_index[Path(track.path).resolve()] = track
            except Exception:
                pass
            sha = str(getattr(track, "source_sha256", "") or "").strip().lower()
            if sha:
                sha256_index[sha] = track
        return path_index, sha256_index

    def _rebuild_indexes(self) -> None:
        """重建路径索引和SHA256索引，加速查找。"""
        self._path_index, self._sha256_index = self.build_indexes_for_tracks(self.tracks)

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
        """清理不存在的歌词文件路径。

        遍历所有歌曲，如果歌词文件路径指向的文件不存在，则重置该路径字段并记录清理操作。

        返回值：
            bool: 如果有任何路径被清理则返回True，否则返回False。
        """
        changed = False  # 初始化改变标志为False
        for track in self.tracks.values():  # 遍历所有歌曲
            # 获取歌词路径字符串，并清理空白字符
            source_lyrics = str(getattr(track, "source_lyrics_path", "") or "").strip()
            if not source_lyrics:  # 如果路径为空，跳过
                continue
            try:
                # 尝试解析路径为绝对路径
                lyric_path = Path(source_lyrics).resolve()
                # 检查路径是否存在且是文件
                exists = lyric_path.exists() and lyric_path.is_file()
            except Exception:  # 如果发生任何异常，将exists设为False
                exists = False
            if exists:  # 如果文件存在，跳过清理
                continue
            # 记录清理操作
            self._record_cleanup(
                item=f"track:{track.id}",
                reason=f"歌词文件不存在，已清理歌词路径字段（lyrics={source_lyrics}）",
            )
            # 重置歌词路径字段
            track.source_lyrics_path = ""
            track.source_lyrics_storage_relpath = ""
            changed = True  # 设置改变标志为True
        return changed  # 返回是否有改变

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
        """
        确保存储库中存在“全部歌曲”播放列表。
    
        功能：
            检查播放列表集合中是否存在ID为ALL_SONGS_ID的播放列表。
            如果不存在，则创建一个名为"全部歌曲"的空播放列表并添加。
            如果已存在，则将其名称更新为"全部歌曲"。
    
        参数：
            self (类实例): 调用方法的实例本身。
    
        返回值：
            None: 该方法无返回值。
        """
        # 检查“全部歌曲”播放列表是否不存在
        if ALL_SONGS_ID not in self.playlists:
            # 创建一个新的播放列表对象，包含ID、名称和空的曲目列表，并添加到播放列表集合
            self.playlists[ALL_SONGS_ID] = Playlist(id=ALL_SONGS_ID, name="全部歌曲", track_ids=[])
            return
        # 如果“全部歌曲”播放列表已存在，则确保其名称为“全部歌曲”
        self.playlists[ALL_SONGS_ID].name = "全部歌曲"

    def _ensure_favorites_playlist(self) -> None:
        """确保收藏播放列表存在。如果不存在，则创建一个新的收藏播放列表；如果存在，则将其名称设置为“我喜欢”。参数：无。返回：None。"""
        if FAVORITES_ID not in self.playlists:  # 检查收藏播放列表是否不存在于self.playlists字典中
            self.playlists[FAVORITES_ID] = Playlist(id=FAVORITES_ID, name="我喜欢", track_ids=[])  # 创建新的收藏播放列表并添加到字典
            return  # 返回，因为新播放列表已创建完成
        self.playlists[FAVORITES_ID].name = "我喜欢"  # 如果已存在，则将播放列表名称重命名为“我喜欢”

    def _ensure_system_playlists(self) -> None:
        """确保系统播放列表存在。参数：无。返回值：无。"""
        self._ensure_all_songs_playlist()  # 确保所有歌曲播放列表存在
        self._ensure_favorites_playlist()  # 确保收藏夹播放列表存在

    def _normalize_playlist_tracks(self) -> bool:
        """功能：归一化播放列表的轨道，清理无效或重复的轨道ID，并更新播放列表的来源字段。

        参数：无显式参数，但操作对象为self.playlists和self.tracks。

        返回值：布尔值，表示是否有更改发生。
        """
        # 初始化标志，记录是否有任何更改
        changed = False
        # 获取所有存在的轨道ID集合，用于检查轨道是否有效
        existing_track_ids = set(self.tracks.keys())
        # 遍历所有播放列表进行处理
        for playlist in self.playlists.values():
            # 保存原始轨道ID列表，用于后续比较
            original = list(playlist.track_ids)
            # 过滤后的轨道ID列表
            filtered: list[str] = []
            # 记录已见的轨道ID，用于去重
            seen: set[str] = set()
            # 标记是否有无效轨道被移除
            removed_invalid = False
            # 遍历原始轨道ID列表
            for track_id in original:
                # 检查轨道ID是否存在于所有轨道中，如果不存在则为无效引用
                if track_id not in existing_track_ids:
                    removed_invalid = True
                    changed = True
                    # 记录清理操作，注明原因
                    self._record_cleanup(
                        item=f"playlist:{playlist.id}",
                        reason=f"歌单引用了不存在歌曲，已清理（track_id={track_id}）",
                    )
                    continue
                # 检查轨道ID是否重复，如果重复则去重
                if track_id in seen:
                    changed = True
                    # 记录清理操作，注明去重原因
                    self._record_cleanup(
                        item=f"playlist:{playlist.id}",
                        reason=f"歌单内重复歌曲已去重（track_id={track_id}）",
                    )
                    continue
                # 记录轨道ID并添加到过滤列表
                seen.add(track_id)
                filtered.append(track_id)
            # 如果过滤后的列表与原始列表不同，则更新播放列表的轨道ID
            if filtered != original:
                playlist.track_ids = filtered
            # 如果有无效轨道被移除且来源哈希存在，则清理相关来源字段
            if removed_invalid and playlist.source_playlist_hash:
                self._record_cleanup(
                    item=f"playlist:{playlist.id}",
                    reason="歌单出现失效歌曲引用，已清理旧歌单哈希与来源绑定字段",
                )
                # 清空来源相关字段
                playlist.source_playlist_hash = ""
                playlist.source_schema = ""
                playlist.source_file = ""
                playlist.source_database_location = ""
                playlist.source_exported_at = ""
                changed = True
            # 如果播放列表无有效歌曲但来源哈希存在，则清理哈希字段
            if not playlist.track_ids and playlist.source_playlist_hash:
                self._record_cleanup(
                    item=f"playlist:{playlist.id}",
                    reason="歌单无有效歌曲，已清理歌单哈希字段",
                )
                playlist.source_playlist_hash = ""
                changed = True
        # 获取所有轨道ID列表
        all_ids = list(self.tracks.keys())
        # 检查“所有歌曲”播放列表是否需要更新，确保包含所有轨道
        if self.playlists[ALL_SONGS_ID].track_ids != all_ids:
            self.playlists[ALL_SONGS_ID].track_ids = all_ids
            changed = True
        # 返回是否有更改发生
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
        if ALL_SONGS_ID in self.playlists:
            return self.playlists[ALL_SONGS_ID]
        return None

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

        仅创建指定名称的歌单，不切换当前活动歌单。
        歌单名称会自动清理空白并补全默认值。

        Args:
            name: 歌单名称

        Returns:
            新创建的Playlist对象
        """
        clean_name = (name or "").strip() or "新建歌单"
        playlist = Playlist(id=new_id(), name=clean_name)
        self.playlists[playlist.id] = playlist
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
            self._remove_track_globally(track_id)

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
        """
        从播放列表中移除指定的轨道，并可能从全局移除该轨道。

        参数:
            playlist_id (str): 播放列表的ID。
            track_id (str): 要移除的轨道的ID。

        返回值:
            set[str]: 一个集合，包含从全局移除的轨道ID；如果没有全局移除，则为空集合。
        """
        removed_globally: set[str] = set()  # 初始化一个集合，用于存储从全局移除的轨道ID
        track_id = str(track_id or "").strip()  # 确保track_id是字符串，并去除首尾空白
        if not track_id:  # 如果track_id为空，直接返回空集合
            return removed_globally

        if track_id not in self.tracks:  # 检查track_id是否存在于当前轨道集合中
            return removed_globally

        if playlist_id == ALL_SONGS_ID:  # 如果播放列表ID是ALL_SONGS_ID，全局移除该轨道
            self._remove_track_globally(track_id)
            removed_globally.add(track_id)
            self._normalize_playlist_tracks()  # 标准化播放列表轨道
            self.save()  # 保存更改
            return removed_globally

        if playlist_id == FAVORITES_ID:  # 如果播放列表ID是FAVORITES_ID，从收藏列表中移除该轨道
            playlist = self.playlists.get(FAVORITES_ID)
            if playlist is None:  # 如果收藏列表不存在，返回空集合
                return removed_globally
            before = len(playlist.track_ids)  # 记录移除前的轨道数量
            playlist.track_ids = [x for x in playlist.track_ids if x != track_id]  # 创建一个新列表，排除指定的track_id
            if len(playlist.track_ids) != before:  # 如果轨道数量发生变化，表示移除成功
                playlist.touch()  # 更新播放列表的时间戳
                self.save()  # 保存更改
            return removed_globally

        playlist = self.playlists.get(playlist_id)  # 获取指定播放列表
        if playlist is None:  # 如果播放列表不存在，返回空集合
            return removed_globally

        before = len(playlist.track_ids)  # 记录移除前的轨道数量
        playlist.track_ids = [x for x in playlist.track_ids if x != track_id]  # 创建一个新列表，排除指定的track_id
        if len(playlist.track_ids) != before:  # 如果轨道数量发生变化，表示移除成功
            playlist.touch()  # 更新播放列表的时间戳

        still_referenced = False  # 初始化标志，检查track_id是否还在其他播放列表中被引用
        for pl in self.playlists.values():  # 遍历所有播放列表
            if pl.id == ALL_SONGS_ID:  # 跳过ALL_SONGS_ID播放列表
                continue
            if track_id in pl.track_ids:  # 如果track_id在其他播放列表中，设置标志为True
                still_referenced = True
                break

        if not still_referenced:  # 如果track_id没有在其他播放列表中被引用
            self._remove_track_globally(track_id)  # 全局移除该轨道
            removed_globally.add(track_id)  # 将track_id添加到返回集合中

        self._normalize_playlist_tracks()  # 标准化播放列表轨道
        self.save()  # 保存更改
        return removed_globally  # 返回包含从全局移除的轨道ID的集合

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
        """从指定的 JSON 文件导入一个 muse 播放列表。

        该方法会读取一个格式为 `*.muse_playlist.json` 的文件，
        解析其中的内容并返回一个对应的 Playlist 对象。

        Args:
            file_path (Path): 要导入的播放列表 JSON 文件的路径。

        Returns:
            Playlist: 导入成功后返回的播放列表对象。

        Raises:
            FileNotFoundError: 如果指定的文件路径不存在或不是一个文件。
            ValueError: 如果文件后缀名不符合要求的格式。
        """
        # 将传入的路径转换为完整的绝对路径
        source_file = Path(file_path).resolve()
        # 检查文件是否存在且为一个文件（非目录）
        if not source_file.exists() or not source_file.is_file():
            raise FileNotFoundError(str(source_file))
        # 验证文件后缀是否为 '.json' 且完整文件名以 '.muse_playlist.json' 结尾
        if source_file.suffix.lower() != ".json" or not source_file.name.lower().endswith(".muse_playlist.json"):
            raise ValueError("不支持的歌单文件格式，请选择 *.muse_playlist.json")
        # 读取文件内容并使用 UTF-8 编码解析为 JSON 对象
        payload = json.loads(source_file.read_text(encoding="utf-8"))
        # 将解析后的数据传递给内部处理方法，以生成播放列表对象
        return self._import_muse_playlist_data(payload, source_file=source_file, fallback_name=source_file.stem)

    def import_muse_playlist_payload(self, payload: dict, source_hint: str = "runtime_payload") -> Playlist:
        """
        导入Muse歌单的有效载荷（payload）数据，并生成一个虚拟源文件路径。

        参数：
            payload (dict): 包含歌单数据的字典。
            source_hint (str): 来源提示，默认为"runtime_payload"。

        返回值：
            Playlist: 导入的歌单对象。
        """
        if not isinstance(payload, dict):
            raise ValueError("歌单数据无效")  # 检查payload是否为字典，如果不是则抛出异常
        playlist_hash = str(payload.get("playlist_hash", "")).strip()  # 从payload中获取playlist_hash，如果没有则默认为空字符串，并去除首尾空格
        if playlist_hash:
            hash_part = playlist_hash[:12]  # 如果playlist_hash存在，取前12个字符作为哈希部分
        else:
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)  # 将payload转换为JSON字符串，确保非ASCII字符保留，按键排序
            hash_part = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]  # 计算JSON字符串的SHA1哈希，并取前12个十六进制字符
        hint_part = hashlib.sha1(str(source_hint or "runtime_payload").encode("utf-8")).hexdigest()[:8]  # 基于source_hint计算SHA1哈希，取前8个字符作为提示部分
        virtual_source_file = (self._store.path.parent / f"_runtime_{hash_part}_{hint_part}.muse_playlist.json").resolve()  # 构建虚拟源文件路径，使用哈希部分和提示部分生成文件名，并解析为绝对路径
        return self._import_muse_playlist_data(payload, source_file=virtual_source_file, fallback_name="导入歌单")  # 调用导入方法，传入有效载荷、虚拟源文件路径和备用名称

    def _import_muse_playlist_data(self, payload: dict, *, source_file: Path, fallback_name: str) -> Playlist:
        """从 payload 字典解析并导入 Muse 格式的歌单数据。

        该方法负责将导入的歌单数据结构（如 JSON 文件内容）转换为应用内部的 Playlist 对象。
        过程包括验证数据格式、解析歌单及曲目信息、处理文件路径、创建或更新内部记录，以及同步数据。

        Args:
            payload (dict): 包含歌单数据的字典，通常由解析歌单文件（如 JSON）得到。
            source_file (Path): 导入歌单数据的源文件路径，用于解析相对路径和生成唯一标识。
            fallback_name (str): 当歌单数据中未提供名称时使用的默认/备用名称。

        Returns:
            Playlist: 成功导入后对应的内部 Playlist 对象。
        """
        # DB 导出歌单导入时保留 source_* 元数据，供后续统计回写和歌词路径解析使用。
        if not isinstance(payload, dict):
            raise ValueError("歌单文件结构无效")
        # 验证歌单的 schema 版本是否支持
        schema_raw = str(payload.get("schema", "")).strip()
        if schema_raw not in {"musearc_playlist_export_v1", "musearc_playlist_export_v2"}:
            raise ValueError("歌单 schema 不匹配")

        # 解析歌单基本信息
        playlist_hash = str(payload.get("playlist_hash", "")).strip()
        # 对歌单名称进行标准化处理，若为空则使用传入的备选名称
        playlist_name = self._normalize_playlist_name(str(payload.get("playlist_name", "")).strip() or fallback_name)
        playlist_ordered = bool(payload.get("ordered", True))
        exported_at = str(payload.get("exported_at", "")).strip()
        database_location = str(payload.get("database_location", "")).strip()
        tracks_payload = payload.get("tracks", [])
        if not isinstance(tracks_payload, list):
            raise ValueError("歌单 tracks 字段无效")

        # 解析数据库根目录路径，支持绝对路径和相对路径（相对于 source_file）
        if database_location:
            db_root_raw = Path(database_location)
            if db_root_raw.is_absolute():
                # 如果是绝对路径，则解析并使用
                db_root = db_root_raw.resolve()
            else:
                # 如果是相对路径，则基于源文件所在目录进行解析
                db_root = (source_file.parent / db_root_raw).resolve()
        else:
            # 若未指定数据库位置，则默认使用源文件所在目录
            db_root = source_file.parent.resolve()

        # 尝试根据唯一标识（playlist_hash 和 source_file）查找现有的内部歌单记录
        playlist = self._find_existing_muse_playlist(playlist_hash=playlist_hash, source_file=source_file)
        if playlist is None:
            # 如果找不到匹配的现有歌单，则创建一个新的内部歌单对象
            playlist_id = self._generate_muse_playlist_id(playlist_hash=playlist_hash, source_file=source_file)
            playlist = Playlist(id=playlist_id, name=playlist_name, ordered=playlist_ordered)
            self.playlists[playlist.id] = playlist
        else:
            # 如果找到了现有歌单，则更新其名称和排序属性
            playlist.name = playlist_name
            playlist.ordered = playlist_ordered

        # 导入后即落地为内部歌单，不保留“后续必须写回源文件”的绑定关系。
        # 设置歌单的源相关元数据
        playlist.source_schema = ""
        playlist.source_file = ""
        playlist.source_playlist_hash = playlist_hash
        playlist.source_database_location = str(db_root)
        playlist.source_exported_at = exported_at

        # 处理歌单中的曲目数据
        # 记录原有曲目 ID 集合，用于后续清理
        old_track_ids = set(playlist.track_ids)
        new_track_ids: list[str] = []
        new_track_ids_set: set[str] = set()

        # 遍历歌单中的每一条原始曲目数据
        for raw in tracks_payload:
            if not isinstance(raw, dict):
                continue

            # 解析单个曲目的元数据
            source_track_id = str(raw.get("track_id", "")).strip()
            # 标准化曲目文件的相对路径
            storage_relpath = self._normalize_relpath(str(raw.get("storage_relpath", "")).strip())
            # 标准化歌词文件的相对路径
            lyrics_relpath = self._normalize_relpath(str(raw.get("lyrics_storage_relpath", "")).strip())
            # 获取原始歌词数组（可能包含多份歌词）
            lyrics_array = raw.get("lyrics", [])
            source_sha256 = str(raw.get("source_sha256", "")).strip().lower()
            title = str(raw.get("title", "")).strip()
            artist = str(raw.get("artist", "")).strip()
            album = str(raw.get("album", "")).strip()

            # 解析完整的音频文件和歌词文件绝对路径
            track_path = self._resolve_muse_track_path(db_root=db_root, storage_relpath=storage_relpath)
            lyrics_path = self._resolve_muse_track_path(db_root=db_root, storage_relpath=lyrics_relpath) if lyrics_relpath else None

            # 尝试通过多种来源字段查找内部已存在的对应曲目记录
            track = self._find_track_by_source_fields(
                source_path=track_path,
                source_sha256=source_sha256,
                source_track_id=source_track_id,
                source_storage_relpath=storage_relpath,
            )

            # 如果在内部库中找不到对应的曲目记录，则需要新建
            if track is None:
                if track_path.exists() and track_path.is_file() and track_path.suffix.lower() in AUDIO_EXTENSIONS:
                    # 如果音频文件物理存在且格式支持，则从文件本身提取元数据创建曲目记录
                    track = self._metadata.extract_track(track_path)
                else:
                    # 如果音频文件不存在，则基于导入的元数据创建一个占位性质的曲目记录
                    fallback_title = title or track_path.stem or "未知标题"
                    track = Track(
                        id=new_id(),
                        path=str(track_path),
                        title=fallback_title,
                        artist=artist or "未知歌手",
                        album=album or "未知专辑",
                    )
                # 将新创建的曲目注册到全局曲目库
                self.tracks[track.id] = track

            # 更新曲目元数据（优先使用导入数据中的字段）
            if title:
                track.title = title
            if artist:
                track.artist = artist
            if album:
                track.album = album
            # 设置该曲目在导入源中的各种标识和路径信息
            track.source_track_id = source_track_id
            track.source_storage_relpath = storage_relpath
            track.source_lyrics_storage_relpath = lyrics_relpath
            track.source_lyrics_path = str(lyrics_path) if lyrics_path is not None else ""
            track.source_sha256 = source_sha256
            # 处理额外的歌词文件路径列表
            if isinstance(lyrics_array, list) and lyrics_array:
                extra_paths: list[str] = []
                for lentry in lyrics_array:
                    if not isinstance(lentry, dict):
                        continue
                    # 获取并解析单个歌词条目的相对路径
                    lrel = self._normalize_relpath(str(lentry.get("relpath", "")).strip())
                    if not lrel:
                        continue
                    lpath = self._resolve_muse_track_path(db_root=db_root, storage_relpath=lrel)
                    lstr = str(lpath) if lpath else ""
                    # 避免与主歌词路径重复
                    if lstr and lstr != track.source_lyrics_path:
                        extra_paths.append(lstr)
                # 将额外歌词路径用管道符连接存储
                track.extra_lyrics_paths = "|".join(extra_paths)
            # 如果原始音频文件确实存在，则更新其内部路径为解析后的绝对路径
            if track_path.exists():
                track.path = str(track_path)

            # 将处理好的曲目 ID 添加到新歌单列表中（自动去重）
            if track.id not in new_track_ids_set:
                new_track_ids.append(track.id)
                new_track_ids_set.add(track.id)

        # 用新的曲目 ID 列表完全替换歌单的原有曲目列表
        playlist.track_ids = new_track_ids
        playlist.touch()
        self.active_playlist_id = playlist.id

        # 将新歌单中的所有曲目同步到“全部歌曲”歌单中
        all_songs = self.playlists[ALL_SONGS_ID]
        all_ids = set(all_songs.track_ids)
        for track_id in new_track_ids:
            if track_id not in all_ids:
                all_songs.track_ids.append(track_id)
                all_ids.add(track_id)
        all_songs.touch()

        # 计算并清理从本次导入中被移除的、且在其他地方也没有引用的孤立曲目
        removed_track_ids = old_track_ids - new_track_ids_set
        if removed_track_ids:
            orphan_track_ids = self._find_orphan_track_ids()
            for track_id in removed_track_ids:
                if track_id in orphan_track_ids:
                    self._remove_track_globally(track_id)

        # 对所有歌单的曲目列表进行一次标准化处理（如去重、排序），然后保存整个状态
        self._normalize_playlist_tracks()
        self.save()
        logger.info("导入歌单文件: %s, playlist=%s, songs=%s", source_file, playlist.name, len(playlist.track_ids))
        return playlist

    def export_playlist_file(self, playlist_id: str, out_dir: Path, playback_stats_service) -> Path:
        """
        导出歌单文件为JSON格式。
    
        功能：
            从当前库中导出指定歌单的详细信息，包括歌曲元数据、播放统计和歌词，
            生成一个完整的JSON文件。
    
        参数：
            playlist_id (str): 要导出的歌单ID。
            out_dir (Path): 导出文件的输出目录。
            playback_stats_service: 播放统计服务，用于获取每首歌的播放数据。
    
        返回值：
            Path: 生成的JSON文件的完整路径。
        """
        # 获取指定ID的歌单对象
        playlist = self.get_playlist(playlist_id)
        # 过滤出当前库中存在的歌曲ID
        track_ids = [tid for tid in playlist.track_ids if tid in self.tracks]
        # 如果没有可导出的歌曲，抛出异常
        if not track_ids:
            raise ValueError("歌单没有可导出的歌曲")

        # 获取或生成歌单的导出哈希值（用于唯一标识导出文件）
        playlist_hash = self._get_or_create_export_hash(playlist)
        # 获取当前UTC时间作为导出时间戳
        exported_at = datetime.now(timezone.utc).isoformat()
        # 确定源数据库位置：优先使用歌单自身的数据库位置，否则使用默认路径
        database_location = str(
            Path(str(playlist.source_database_location or "")).resolve()
            if str(playlist.source_database_location or "").strip()
            else self._store.path.parent.parent.resolve()
        )

        # 初始化导出数据列表和统计变量
        tracks_out: list[dict] = []
        total_play_count = 0
        total_manual_play_count = 0
        total_complete_play_count = 0
        total_play_seconds = 0
        total_early_skip_count = 0
        # 将数据库位置转换为Path对象，用于计算相对路径
        db_root = Path(database_location)

        # 遍历每首歌曲ID，收集歌曲信息和播放统计
        for tid in track_ids:
            track = self.tracks[tid]
            # 获取当前歌曲的播放统计数据，若无则使用默认零值
            stats = playback_stats_service.export_stats_for_track(tid) or {
                "play_count": 0,
                "manual_play_count": 0,
                "complete_play_count": 0,
                "play_seconds": 0,
                "early_skip_count": 0,
                "peak_session_play_count": 0,
                "peak_session_play_at": 0.0,
            }
            # 累加全局统计计数器
            total_play_count += int(stats.get("play_count", 0) or 0)
            total_manual_play_count += int(stats.get("manual_play_count", 0) or 0)
            total_complete_play_count += int(stats.get("complete_play_count", 0) or 0)
            total_play_seconds += int(stats.get("play_seconds", 0) or 0)
            total_early_skip_count += int(stats.get("early_skip_count", 0) or 0)

            # 确定导出的歌曲ID：优先使用源ID，否则使用内部ID
            track_id_export = str(track.source_track_id or tid).strip() or tid
            # 导出当前歌曲的歌词列表
            lyrics_list = self._export_track_lyrics(track, db_root)
            # 构建单首歌曲的导出数据字典
            tracks_out.append(
                {
                    "track_id": track_id_export,
                    # 计算音频文件的相对存储路径
                    "storage_relpath": self._export_relpath(track=track, db_root=db_root, kind="audio"),
                    "title": str(track.title or "").strip(),
                    "artist": str(track.artist or "").strip(),
                    "album": str(track.album or "").strip(),
                    "lyrics": lyrics_list,
                    # 设置歌词文件的相对路径（如果存在歌词）
                    "lyrics_storage_relpath": lyrics_list[0]["relpath"] if lyrics_list else "",
                    "source_sha256": str(track.source_sha256 or "").strip(),
                    # 歌曲级别的播放统计（确保所有值为非负数）
                    "stats": {
                        "play_count": max(0, int(stats.get("play_count", 0) or 0)),
                        "manual_play_count": max(0, int(stats.get("manual_play_count", 0) or 0)),
                        "complete_play_count": max(0, int(stats.get("complete_play_count", 0) or 0)),
                        "play_seconds": max(0, int(stats.get("play_seconds", 0) or 0)),
                        "early_skip_count": max(0, int(stats.get("early_skip_count", 0) or 0)),
                        "peak_session_play_count": max(0, int(stats.get("peak_session_play_count", 0) or 0)),
                        "peak_session_play_at": max(0.0, float(stats.get("peak_session_play_at", 0.0) or 0.0)),
                    },
                }
            )

        # 确定并创建输出目录（如果不存在则创建）
        out_root = Path(out_dir).expanduser().resolve()
        out_root.mkdir(parents=True, exist_ok=True)
        # 清理歌单名称用于文件名，并生成安全的文件名
        safe_name = self._sanitize_export_name(playlist.name or "playlist")
        # 构建最终输出文件路径：包含歌单名称和哈希前10位
        file_path = out_root / f"{safe_name}_{playlist_hash[:10]}.muse_playlist.json"

        # 构建完整的JSON载荷数据
        payload = {
            "schema": MUSE_PLAYLIST_SCHEMA,
            "playlist_hash": playlist_hash,
            "playlist_name": str(playlist.name or "").strip(),
            "ordered": bool(getattr(playlist, "ordered", True)),  # 默认为有序
            "exported_at": exported_at,
            "database_location": database_location,
            "track_count": len(tracks_out),
            # 汇总所有歌曲的播放统计数据
            "stats_summary": {
                "total_play_count": int(total_play_count),
                "total_manual_play_count": int(total_manual_play_count),
                "total_complete_play_count": int(total_complete_play_count),
                "total_play_seconds": int(total_play_seconds),
                "total_early_skip_count": int(total_early_skip_count),
                "updated_at": exported_at,  # 统计更新时间与导出时间相同
            },
            "tracks": tracks_out,
        }
        # 将载荷数据写入JSON文件（使用UTF-8编码，确保中文字符正确保存）
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # 记录导出成功的日志
        logger.info("导出歌单: playlist=%s tracks=%s file=%s", playlist.id, len(tracks_out), file_path)
        # 返回生成的文件路径
        return file_path

    def _get_or_create_export_hash(self, playlist: Playlist) -> str:
        """
        功能：获取或创建播放列表的导出哈希。如果播放列表已有哈希则直接返回，否则基于播放列表ID生成新的SHA1哈希并保存。
        参数：
            self: 实例自身
            playlist: 播放列表对象，类型为Playlist
        返回值：字符串，表示导出哈希
        """
        # 获取现有的哈希，若为空则处理为空字符串，并清理和小写化以统一格式
        raw = str(playlist.source_playlist_hash or "").strip().lower()
        if raw:  # 如果哈希已存在，直接返回
            return raw
        # 使用SHA1算法基于播放列表ID生成新的哈希值，并编码为十六进制字符串
        raw = hashlib.sha1(f"playlist:{playlist.id}".encode("utf-8")).hexdigest()
        # 将新哈希存储到播放列表对象的属性中
        playlist.source_playlist_hash = raw
        # 更新播放列表的修改时间戳
        playlist.touch()
        # 保存当前实例的更改到持久化存储
        self.save()
        return raw

    @staticmethod
    def _sanitize_export_name(name: str) -> str:
        """将输入的名称清理为安全的导出名称。

        功能：替换或删除不允许在文件名中使用的特殊字符。
        参数：
            name (str): 要清理的名称字符串。如果为None或空，则使用空字符串。
        返回值：
            str: 清理后的安全字符串。如果清理后为空，则返回默认值"playlist"。
        """
        safe = "".join(ch if ch not in "\\/:*?\"<>|" else "_" for ch in str(name or "").strip()).strip()  # 去除首尾空格，替换特殊字符为下划线，然后连接
        return safe or "playlist"  # 如果safe为空字符串或None，则返回默认名称"playlist"

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
        """获取指定音轨的歌词文件路径列表。

        Args:
            track (Track): 目标音轨对象。

        Returns:
            list[str]: 不重复的歌词路径字符串列表。
        """
        paths: list[str] = []  # 初始化一个空列表，用于存储找到的歌词路径
        main = str(track.source_lyrics_path or "").strip()  # 尝试获取主歌词路径，并转换为字符串、去除首尾空格
        if main:  # 如果主路径非空
            paths.append(main)  # 将其添加到结果列表中
        extra = str(getattr(track, "extra_lyrics_paths", "") or "").strip()  # 尝试获取额外歌词路径，转换为字符串并去除空格
        if extra:  # 如果额外路径非空
            for p in extra.split("|"):  # 遍历以 "|" 分隔的每个路径
                p = p.strip()  # 对单个路径进行空格处理
                if p and p not in paths:  # 如果路径非空且尚未存在于结果列表中（避免重复）
                    paths.append(p)  # 将其添加到结果列表
        return paths  # 返回收集到的所有歌词路径列表

    def _export_relpath(self, *, track: Track, db_root: Path, kind: str) -> str:
        """生成相对于数据库根目录的文件路径。

        根据给定的资源类型（歌词或其他），从 track 对象中提取原始存储路径，
        并将其转换为相对于数据库根目录的规范化相对路径。

        参数:
            track (Track): 包含源文件路径信息的 Track 对象。
            db_root (Path): 数据库的根目录路径，用作计算相对路径的基准。
            kind (str): 资源类型，如 "lyrics" 表示歌词文件，其他值则默认处理音频文件。

        返回:
            str: 规范化后的相对路径字符串。如果原始路径不存在或无法计算，则返回文件名或空字符串。
        """
        # 处理歌词文件的路径逻辑
        if kind == "lyrics":
            # 优先使用 track 中预存的、已标准化的歌词相对路径
            rel = self._normalize_relpath(str(track.source_lyrics_storage_relpath or "").strip())
            if rel:  # 如果预存路径非空，则直接返回
                return rel
            # 若无预存路径，则获取歌词的绝对路径
            lyrics_abs = str(track.source_lyrics_path or "").strip()
            if not lyrics_abs:  # 如果绝对路径也不存在，返回空字符串
                return ""
            try:
                # 将绝对路径转换为相对于 db_root 的路径，并统一使用正斜杠
                return self._normalize_relpath(str(Path(lyrics_abs).resolve().relative_to(db_root)).replace("\\", "/"))
            except Exception:
                # 如果路径不在 db_root 下（无法计算相对路径），则仅返回文件名作为备用
                return self._normalize_relpath(Path(lyrics_abs).name)

        # 处理非歌词资源（如音频文件）的路径逻辑
        # 优先使用 track 中预存的、已标准化的源文件相对路径
        rel = self._normalize_relpath(str(track.source_storage_relpath or "").strip())
        if rel:  # 如果预存路径非空，则直接返回
            return rel
        try:
            # 将 track.path 的绝对路径转换为相对于 db_root 的路径，并统一使用正斜杠
            return self._normalize_relpath(str(Path(track.path).resolve().relative_to(db_root)).replace("\\", "/"))
        except Exception:
            # 如果路径不在 db_root 下，则仅返回文件名作为备用
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
        """
        生成一个唯一的播放列表ID。

        功能：基于播放列表哈希值或源文件路径创建播放列表ID。如果ID已存在，
              则通过添加数字后缀确保唯一性，避免重复添加相同内容（根据哈希或文件路径判断）。

        参数：
            playlist_hash (str): 播放列表的哈希标识（可选）。如果存在，则使用其前16位。
            source_file (Path): 播放列表的源文件路径。当playlist_hash为空时，用其SHA1哈希值的前16位。

        返回值：
            str: 生成的唯一播放列表ID字符串。
        """
        if playlist_hash:
            # 使用播放列表哈希值的前16位作为基础ID
            base = f"muse_{playlist_hash[:16]}"
        else:
            # 当无哈希值时，对源文件路径进行SHA1哈希处理，取前16位作为基础ID
            digest = hashlib.sha1(str(source_file).lower().encode("utf-8")).hexdigest()
            base = f"muse_{digest[:16]}"
    
        candidate = base  # 初始候选ID为基础ID
        idx = 2  # 从2开始编号，避免与无后缀的基础ID重复
    
        # 循环检查候选ID是否已存在于播放列表中
        while candidate in self.playlists:
            existing = self.playlists[candidate]
        
            # 判断已存在的播放列表是否与当前内容相同（通过哈希值或文件路径比较）
            same_hash = bool(playlist_hash) and existing.source_playlist_hash == playlist_hash
            same_file = bool(existing.source_file) and Path(existing.source_file).resolve() == source_file.resolve()
        
            # 如果内容相同（哈希或文件路径匹配），则跳出循环复用该ID
            if same_hash or same_file:
                break
        
            # 内容不同但ID冲突时，生成带数字后缀的新候选ID
            candidate = f"{base}_{idx}"
            idx += 1
    
        return candidate

    def _find_existing_muse_playlist(self, *, playlist_hash: str, source_file: Path) -> Playlist | None:
        """
        在现有播放列表中查找是否存在与给定哈希值或源文件路径匹配的播放列表。

        参数:
            playlist_hash (str): 用于标识播放列表的哈希值。
            source_file (Path): 播放列表的源文件路径。

        返回值:
            Playlist | None: 如果找到匹配的播放列表，则返回该播放列表对象；否则返回None。
        """
        # 将路径对象转换为字符串，便于后续比较
        source_text = str(source_file)
        # 遍历所有现有的播放列表
        for playlist in self.playlists.values():
            # 跳过全局的“所有歌曲”和“收藏”播放列表，它们是特殊的默认列表
            if playlist.id in {ALL_SONGS_ID, FAVORITES_ID}:
                continue
            # 优先使用哈希值进行匹配查找
            if playlist_hash and playlist.source_playlist_hash == playlist_hash:
                return playlist
            # 如果哈希值未提供或不匹配，则尝试使用源文件路径进行匹配
            if playlist.source_file and playlist.source_file == source_text:
                return playlist
        # 遍历完所有播放列表后仍未找到匹配项，返回None
        return None

    def _find_track_by_source_fields(
        self,
        *,
        source_path: Path,
        source_sha256: str,
        source_track_id: str,
        source_storage_relpath: str,
    ) -> Track | None:
        """通过多个来源字段查找对应的track对象。

        此方法按照优先级依次使用不同的来源信息进行查找：
        1. 首先尝试通过SHA256哈希值精确匹配
        2. 然后尝试通过source_track_id匹配
        3. 接着通过标准化后的相对路径匹配
        4. 最后通过完整的路径匹配

        参数:
            source_path (Path): 来源文件的完整路径
            source_sha256 (str): 来源文件的SHA256哈希值
            source_track_id (str): 来源系统分配的track标识符
            source_storage_relpath (str): 来源文件在存储系统中的相对路径

        返回:
            Track | None: 如果找到匹配的track则返回该对象，否则返回None
        """
        resolved_source = source_path.resolve()
        normalized_relpath = self._normalize_relpath(source_storage_relpath)

        # 第一优先级：通过SHA256哈希值查找
        if source_sha256:
            # 使用小写形式进行匹配，确保大小写不敏感
            hit = self._sha256_index.get(source_sha256.lower())
            if hit is not None:
                return hit
    
        # 第二优先级：通过source_track_id查找
        if source_track_id:
            # 遍历所有tracks，查找匹配的source_track_id
            for track in self.tracks.values():
                if track.source_track_id == source_track_id:
                    return track
    
        # 第三优先级：通过标准化后的相对路径查找
        if normalized_relpath:
            # 遍历所有tracks，比较标准化后的相对路径
            for track in self.tracks.values():
                if self._normalize_relpath(track.source_storage_relpath) == normalized_relpath:
                    return track

        # 第四优先级：通过完整解析路径查找
        hit = self._path_index.get(resolved_source)
        if hit is not None:
            return hit
    
        # 所有查找方式都未找到匹配项
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
        """
        生成一个不重复的播放列表名称。

        功能：基于给定的基础名称，检查是否与现有播放列表名称冲突，如果冲突则添加递增的数字后缀，直到找到唯一名称。
        参数：base_name (str) - 欲使用的播放列表基础名称。
        返回值：一个唯一的播放列表名称字符串（str）。
        """
        # 规范化输入的基础名称（例如去除首尾空格等）
        base = self._normalize_playlist_name(base_name)
        # 创建一个集合，包含所有现有播放列表的规范化名称（已去除首尾空格并转为小写），排除“所有歌曲”播放列表
        used = {playlist.name.strip().casefold() for playlist in self.playlists.values() if playlist.id != ALL_SONGS_ID}
        # 检查规范化后的基础名称（转为小写）是否已存在于已使用名称集合中
        if base.casefold() not in used:
            # 如果不存在，直接返回该名称
            return base
        # 如果存在冲突，从2开始尝试添加数字后缀
        idx = 2
        while True:
            # 构造候选名称，格式为 "基础名称 (数字)"
            candidate = f"{base} ({idx})"
            # 检查该候选名称（转为小写）是否已存在于已使用名称集合中
            if candidate.casefold() not in used:
                # 如果不存在，返回该候选名称作为唯一名称
                return candidate
            # 如果存在，递增数字，继续尝试下一个候选名称
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
        """
        从全局索引中移除指定ID的音轨。
    
        参数:
            track_id (str): 要移除的音轨ID。
    
        返回:
            None: 无返回值。
        """
        track = self.tracks.get(track_id)  # 获取指定ID的音轨对象
        if track is not None:  # 如果音轨存在
            del self.tracks[track_id]  # 从全局音轨字典中删除该音轨
            try:
                path_key = Path(track.path).resolve()  # 解析音轨路径为绝对路径键
                if self._path_index.get(path_key) is track:  # 检查路径索引中是否映射到该音轨
                    del self._path_index[path_key]  # 如果是，则删除路径索引条目
            except Exception:
                pass  # 忽略路径解析或索引操作中的任何异常
            sha = str(getattr(track, "source_sha256", "") or "").strip().lower()  # 获取音轨的SHA256哈希值
            if sha and self._sha256_index.get(sha) is track:  # 如果SHA256存在且索引映射到该音轨
                del self._sha256_index[sha]  # 删除SHA256索引条目
        for playlist in self.playlists.values():  # 遍历所有播放列表
            if track_id in playlist.track_ids:  # 如果播放列表包含该track_id
                playlist.track_ids = [x for x in playlist.track_ids if x != track_id]  # 移除该track_id
                playlist.touch()  # 更新播放列表的修改时间
