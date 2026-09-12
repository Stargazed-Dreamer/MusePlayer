"""曲库服务层。

核心职责：
1. 管理曲目与歌单的内存态及持久化读写。
2. 提供文件夹导入、歌单导入、去重、搜索、歌单管理能力。
3. 维护“全部歌曲”与其它歌单双向同步关系。
4. 支持按统一格式导出歌单（含播放统计），供后续数据库分析使用。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.models.entities import Playlist, Track, new_id
from app.models.library_store import LibraryStore
from app.services.library_cleaner import LibraryCleaner
from app.services.metadata_service import MetadataService
from app.services.muse_playlist_importer import MusePlaylistImporter
from app.services.playlist_exporter import PlaylistExporter

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
        self._tracks: dict[str, Track] = {}  # 存储所有曲目，键为曲目ID，值为Track对象
        self._playlists: dict[str, Playlist] = {}  # 存储所有播放列表，键为播放列表ID，值为Playlist对象
        self.active_playlist_id: str | None = None  # 当前活跃/正在播放的播放列表ID，None表示无
        self._cleanup_log_path = self._store.path.parent / "logs" / "data_cleanup.log"  # 数据清理日志文件路径
        self._data_maintenance_logging_enabled = True  # 是否启用数据维护日志记录
        self._path_index: dict[Path, Track] = {}  # 按文件路径索引曲目，用于快速查找
        self._sha256_index: dict[str, Track] = {}  # 按SHA256哈希索引曲目，用于去重和校验
        # 数据清理器（缺失文件/失效歌词/去重/归一化），组合持有本服务
        self._cleaner = LibraryCleaner(self)
        # 歌单导出器（含播放统计与歌词），组合持有本服务
        self._exporter = PlaylistExporter(self)
        # Muse 歌单导入器（解析 *.muse_playlist.json 文件 / 运行时 payload），组合持有本服务
        self._muse_importer = MusePlaylistImporter(self)

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

    def load_preloaded(
        self,
        tracks: dict[str, Track],
        playlists: dict[str, Playlist],
        active: str | None,
        indexes: tuple[dict[Path, Track], dict[str, Track]] | None = None,
        *,
        quick: bool = False,
    ) -> None:
        self._tracks = tracks
        self._playlists = playlists
        self._ensure_system_playlists()

        if active in self._playlists:
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
        logger.info("曲库加载完成: tracks=%s playlists=%s quick=%s", len(self._tracks), len(self._playlists), quick)

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

        with contextlib.suppress(Exception):  # 日志记录失败时静默忽略异常，不影响主流程
            # 通过日志记录器输出警告级别日志
            logger.warning(text)

        try:
            # 确保日志文件父目录存在，不存在则自动创建
            self._cleanup_log_path.parent.mkdir(parents=True, exist_ok=True)

            # 生成当前时间戳，格式为年-月-日 时:分:秒
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 以追加模式打开日志文件，使用UTF-8编码
            with self._cleanup_log_path.open("a", encoding="utf-8") as f:
                # 写入带时间戳的清理记录并换行
                f.write(f"{stamp} {text}\n")
        except Exception as exc:
            # 清理日志写入失败不应阻断曲库清理主流程，仅记录原因
            logger.warning("写入曲库清理日志失败: %s", exc)

    @staticmethod
    def build_indexes_for_tracks(tracks: dict[str, Track]) -> tuple[dict[Path, Track], dict[str, Track]]:
        path_index: dict[Path, Track] = {}
        sha256_index: dict[str, Track] = {}
        for track in tracks.values():
            with contextlib.suppress(Exception):
                path_index[Path(track.path).resolve()] = track
            sha = str(getattr(track, "source_sha256", "") or "").strip().lower()
            if sha:
                sha256_index[sha] = track
        return path_index, sha256_index

    def _rebuild_indexes(self) -> None:
        """重建路径索引和SHA256索引，加速查找。"""
        self._path_index, self._sha256_index = self.build_indexes_for_tracks(self._tracks)

    def _drop_missing_tracks(self) -> bool:
        """清理曲库中指向不存在的文件的跟踪记录。

        委托给 `LibraryCleaner._drop_missing_tracks`；逻辑详见清理器模块。
        """
        return self._cleaner._drop_missing_tracks()

    def _cleanup_missing_lyrics_paths(self) -> bool:
        """清理不存在的歌词文件路径。

        委托给 `LibraryCleaner._cleanup_missing_lyrics_paths`；逻辑详见清理器模块。

        返回值：
            bool: 如果有任何路径被清理则返回True，否则返回False。
        """
        return self._cleaner._cleanup_missing_lyrics_paths()

    def _deduplicate_tracks(self) -> bool:
        """基于文件特征检测并合并重复曲目。

        委托给 `LibraryCleaner._deduplicate_tracks`；逻辑详见清理器模块。
        """
        return self._cleaner._deduplicate_tracks()

    def save(self) -> None:
        """持久化保存曲库数据。

        保存所有曲目、歌单和当前活动歌单状态到存储层。
        """
        self._store.save(self._tracks, self._playlists, self.active_playlist_id)

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
        if ALL_SONGS_ID not in self._playlists:
            # 创建一个新的播放列表对象，包含ID、名称和空的曲目列表，并添加到播放列表集合
            self._playlists[ALL_SONGS_ID] = Playlist(id=ALL_SONGS_ID, name="全部歌曲", track_ids=[])
            return
        # 如果“全部歌曲”播放列表已存在，则确保其名称为“全部歌曲”
        self._playlists[ALL_SONGS_ID].name = "全部歌曲"

    def _ensure_favorites_playlist(self) -> None:
        """确保收藏播放列表存在。如果不存在，则创建一个新的收藏播放列表；如果存在，则将其名称设置为“我喜欢”。参数：无。返回：None。"""
        if FAVORITES_ID not in self._playlists:  # 检查收藏播放列表是否不存在于self.playlists字典中
            self._playlists[FAVORITES_ID] = Playlist(
                id=FAVORITES_ID, name="我喜欢", track_ids=[]
            )  # 创建新的收藏播放列表并添加到字典
            return  # 返回，因为新播放列表已创建完成
        self._playlists[FAVORITES_ID].name = "我喜欢"  # 如果已存在，则将播放列表名称重命名为“我喜欢”

    def _ensure_system_playlists(self) -> None:
        """确保系统播放列表存在。参数：无。返回值：无。"""
        self._ensure_all_songs_playlist()  # 确保所有歌曲播放列表存在
        self._ensure_favorites_playlist()  # 确保收藏夹播放列表存在

    def _normalize_playlist_tracks(self) -> bool:
        """归一化播放列表的轨道，清理无效或重复的轨道ID，并更新播放列表的来源字段。

        委托给 `LibraryCleaner._normalize_playlist_tracks`；逻辑详见清理器模块。

        返回值：布尔值，表示是否有更改发生。
        """
        return self._cleaner._normalize_playlist_tracks()

    def get_playlist(self, playlist_id: str | None) -> Playlist:
        """获取指定ID的歌单，如果不存在则返回"全部歌曲"歌单。

        Args:
            playlist_id: 歌单ID，None返回默认歌单

        Returns:
            Playlist对象
        """
        if playlist_id and playlist_id in self._playlists:
            return self._playlists[playlist_id]
        if ALL_SONGS_ID in self._playlists:
            return self._playlists[ALL_SONGS_ID]
        return None

    def find_playlist(self, playlist_id: str | None) -> Playlist | None:
        """严格查找指定 ID 的歌单，不存在返回 None（不做"全部歌曲"兜底）。

        与 get_playlist 的区别：get_playlist 在找不到时回退到"全部歌曲"，
        适用于播放场景；本方法适用于协议响应、存在性判断等需要区分
        "歌单不存在"与"回退默认"的场景。

        Args:
            playlist_id: 歌单ID

        Returns:
            Playlist 对象；ID 为空或不存在时返回 None
        """
        if not playlist_id:
            return None
        return self._playlists.get(str(playlist_id))

    def has_track(self, track_id: str) -> bool:
        """判断指定 ID 的曲目是否存在于曲库。

        Args:
            track_id: 曲目ID

        Returns:
            存在返回 True
        """
        return str(track_id) in self._tracks

    def track_ids(self) -> set[str]:
        """返回当前曲库全部曲目 ID 的快照集合。

        Returns:
            ID 集合的副本，调用方修改不影响曲库本身
        """
        return set(self._tracks.keys())

    def list_playlists(self) -> list[Playlist]:
        """获取所有歌单列表。

        返回的列表以"全部歌曲"为首，其余歌单按名称字母序排序。

        Returns:
            排序后的歌单列表
        """
        all_pl = self._playlists.get(ALL_SONGS_ID)
        fav_pl = self._playlists.get(FAVORITES_ID)
        others = [p for p in self._playlists.values() if p.id not in {ALL_SONGS_ID, FAVORITES_ID}]
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
        return [self._tracks[tid] for tid in playlist.track_ids if tid in self._tracks]

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
        self._playlists[playlist.id] = playlist
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
        playlist = self._playlists.get(playlist_id)
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
        source = self._playlists.get(source_playlist_id)
        if source is None:
            return None

        base_name = (new_name or "").strip() or f"{source.name} - 副本"
        target_name = self._make_unique_playlist_name(base_name)
        copied_ids = [track_id for track_id in source.track_ids if track_id in self._tracks]

        playlist = Playlist(id=new_id(), name=target_name, track_ids=copied_ids)
        self._playlists[playlist.id] = playlist
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
        source = self._playlists.get(source_playlist_id)
        target = self._playlists.get(target_playlist_id)
        if source is None or target is None:
            return 0

        before = len(target.track_ids)
        existing = set(target.track_ids)
        for track_id in source.track_ids:
            if track_id not in self._tracks:
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
        if playlist_id not in self._playlists:
            return

        removed = self._playlists[playlist_id]
        del self._playlists[playlist_id]

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
        if playlist_id not in self._playlists:
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
        playlist = self._playlists.get(playlist_id)
        if playlist is None:
            return
        existing = set(playlist.track_ids)
        for track_id in track_ids:
            if track_id not in self._tracks:
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

        if track_id not in self._tracks:  # 检查track_id是否存在于当前轨道集合中
            return removed_globally

        if playlist_id == ALL_SONGS_ID:  # 如果播放列表ID是ALL_SONGS_ID，全局移除该轨道
            self._remove_track_globally(track_id)
            removed_globally.add(track_id)
            self._normalize_playlist_tracks()  # 标准化播放列表轨道
            self.save()  # 保存更改
            return removed_globally

        if playlist_id == FAVORITES_ID:  # 如果播放列表ID是FAVORITES_ID，从收藏列表中移除该轨道
            playlist = self._playlists.get(FAVORITES_ID)
            if playlist is None:  # 如果收藏列表不存在，返回空集合
                return removed_globally
            before = len(playlist.track_ids)  # 记录移除前的轨道数量
            playlist.track_ids = [x for x in playlist.track_ids if x != track_id]  # 创建一个新列表，排除指定的track_id
            if len(playlist.track_ids) != before:  # 如果轨道数量发生变化，表示移除成功
                playlist.touch()  # 更新播放列表的时间戳
                self.save()  # 保存更改
            return removed_globally

        playlist = self._playlists.get(playlist_id)  # 获取指定播放列表
        if playlist is None:  # 如果播放列表不存在，返回空集合
            return removed_globally

        before = len(playlist.track_ids)  # 记录移除前的轨道数量
        playlist.track_ids = [x for x in playlist.track_ids if x != track_id]  # 创建一个新列表，排除指定的track_id
        if len(playlist.track_ids) != before:  # 如果轨道数量发生变化，表示移除成功
            playlist.touch()  # 更新播放列表的时间戳

        still_referenced = False  # 初始化标志，检查track_id是否还在其他播放列表中被引用
        for pl in self._playlists.values():  # 遍历所有播放列表
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
        playlist = self._playlists.get(FAVORITES_ID)
        if playlist is None:
            return False
        return tid in playlist.track_ids

    def toggle_favorite(self, track_id: str) -> bool:
        """切换歌曲的“我喜欢”状态，返回切换后的状态。"""
        tid = str(track_id or "").strip()
        if not tid or tid not in self._tracks:
            return False
        self._ensure_favorites_playlist()
        favorites = self._playlists[FAVORITES_ID]
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
        return self._tracks.get(track_id)

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
        # 根歌单解析：指定了有效歌单 ID 则复用该歌单；否则按目录名新建/复用，
        # 且仅在根目录确实扫描到音频文件时才解析（避免空根目录产生无意义的新歌单）
        use_existing_root = bool(playlist_id and playlist_id in self._playlists and playlist_id != ALL_SONGS_ID)
        root_files = self._scan_audio_files(target, recursive=False)
        if root_files:
            if use_existing_root:
                root_playlist = self._playlists[str(playlist_id)]
            else:
                root_playlist = self._resolve_target_playlist_for_folder_import(target, None)
            plan.append((root_playlist, root_files))

        # 第一级子目录分别建立/复用独立歌单（与根歌单的处理方式无关）
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
        all_songs = self._playlists[ALL_SONGS_ID]
        all_ids = set(all_songs.track_ids)
        playlist_ids_map: dict[str, set[str]] = {pl.id: set(pl.track_ids) for pl in self._playlists.values()}
        touched_playlists: set[str] = set()

        last_tick = time.monotonic()
        processed = 0
        for playlist, files in plan:
            existing_ids = playlist_ids_map.setdefault(playlist.id, set(playlist.track_ids))
            for file_path in files:
                existing = self._path_index.get(file_path)
                if existing is None:
                    track = self._metadata.extract_track(file_path)
                    self._tracks[track.id] = track
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
            pl = self._playlists.get(pid)
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
            self._tracks[track.id] = track
            self._path_index[source] = track

        all_songs = self._playlists[ALL_SONGS_ID]
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
        """从指定的 JSON 文件导入一个 muse 播放列表（委托 MusePlaylistImporter）。

        参数、返回值与原实现一致；业务逻辑（schema 校验、曲目解析、孤立清理）
        搬至 `app/services/muse_playlist_importer.py`。
        """
        return self._muse_importer.import_muse_playlist(file_path)

    def import_muse_playlist_payload(self, payload: dict, source_hint: str = "runtime_payload") -> Playlist:
        """导入 Muse 歌单的运行时 payload（委托 MusePlaylistImporter）。

        参数、返回值与原实现一致；虚拟源文件路径构造与导入逻辑搬至
        `app/services/muse_playlist_importer.py`。
        """
        return self._muse_importer.import_muse_playlist_payload(payload, source_hint)

    def export_playlist_file(self, playlist_id: str, out_dir: Path, playback_stats_service) -> Path:
        """导出歌单文件为 JSON 格式（委托 PlaylistExporter）。

        参数、返回值与原实现一致；业务逻辑（JSON 序列化、SHA1、相对路径计算）
        搬至 `app/services/playlist_exporter.py`。
        """
        return self._exporter.export_playlist_file(playlist_id, out_dir, playback_stats_service)

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
        raw = hashlib.sha1(f"playlist:{playlist.id}".encode()).hexdigest()
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
        safe = "".join(
            ch if ch not in '\\/:*?"<>|' else "_" for ch in str(name or "").strip()
        ).strip()  # 去除首尾空格，替换特殊字符为下划线，然后连接
        return safe or "playlist"  # 如果safe为空字符串或None，则返回默认名称"playlist"

    def _export_track_lyrics(self, track: Track, db_root: Path) -> list[dict]:
        result: list[dict] = []
        lyrics_paths = self._get_track_lyrics_paths(track)
        for lp in lyrics_paths:
            rel = ""
            try:
                rel = str(Path(lp).resolve().relative_to(db_root)).replace("\\", "/")
            except Exception as exc:
                # 歌词路径不在曲库根下时回退为文件名，导出仍可继续
                logger.debug("导出歌词相对路径失败，回退文件名 %s: %s", lp, exc)
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
        extra = str(
            getattr(track, "extra_lyrics_paths", "") or ""
        ).strip()  # 尝试获取额外歌词路径，转换为字符串并去除空格
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
            except Exception as exc:
                # 路径不在 db_root 下时回退为文件名，导出仍可继续
                logger.debug("歌词绝对路径转相对路径失败，回退文件名 %s: %s", lyrics_abs, exc)
                return self._normalize_relpath(Path(lyrics_abs).name)

        # 处理非歌词资源（如音频文件）的路径逻辑
        # 优先使用 track 中预存的、已标准化的源文件相对路径
        rel = self._normalize_relpath(str(track.source_storage_relpath or "").strip())
        if rel:  # 如果预存路径非空，则直接返回
            return rel
        try:
            # 将 track.path 的绝对路径转换为相对于 db_root 的路径，并统一使用正斜杠
            return self._normalize_relpath(str(Path(track.path).resolve().relative_to(db_root)).replace("\\", "/"))
        except Exception as exc:
            # 路径不在 db_root 下时回退为文件名，导出仍可继续
            logger.debug("曲目路径转相对路径失败，回退文件名 %s: %s", track.path, exc)
            return self._normalize_relpath(Path(track.path).name)

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
        if playlist_id and playlist_id in self._playlists and playlist_id != ALL_SONGS_ID:
            return self._playlists[playlist_id]

        folder_name = self._normalize_playlist_name(folder.name)
        existing = self._find_playlist_by_name(folder_name)
        if existing is not None:
            return existing

        playlist = Playlist(id=new_id(), name=folder_name)
        self._playlists[playlist.id] = playlist
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
        for playlist in self._playlists.values():
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
        used = {playlist.name.strip().casefold() for playlist in self._playlists.values() if playlist.id != ALL_SONGS_ID}
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
        for playlist in self._playlists.values():
            if playlist.id == ALL_SONGS_ID:
                continue
            if exclude_playlist_id and playlist.id == exclude_playlist_id:
                continue
            referenced.update(track_id for track_id in playlist.track_ids if track_id in self._tracks)

        # 返回曲库中存在但未被引用（除全部歌曲外）的曲目
        all_track_ids = set(self._tracks.keys())
        return all_track_ids - referenced

    def _remove_track_globally(self, track_id: str) -> None:
        """
        从全局索引中移除指定ID的音轨。

        参数:
            track_id (str): 要移除的音轨ID。

        返回:
            None: 无返回值。
        """
        track = self._tracks.get(track_id)  # 获取指定ID的音轨对象
        if track is not None:  # 如果音轨存在
            del self._tracks[track_id]  # 从全局音轨字典中删除该音轨
            try:
                path_key = Path(track.path).resolve()  # 解析音轨路径为绝对路径键
                if self._path_index.get(path_key) is track:  # 检查路径索引中是否映射到该音轨
                    del self._path_index[path_key]  # 如果是，则删除路径索引条目
            except Exception as exc:
                # 路径索引清理失败不阻断曲目移除；记录原因便于排查索引漂移
                logger.debug("移除曲目路径索引失败 %s: %s", track_id, exc)
            sha = str(getattr(track, "source_sha256", "") or "").strip().lower()  # 获取音轨的SHA256哈希值
            if sha and self._sha256_index.get(sha) is track:  # 如果SHA256存在且索引映射到该音轨
                del self._sha256_index[sha]  # 删除SHA256索引条目
        for playlist in self._playlists.values():  # 遍历所有播放列表
            if track_id in playlist.track_ids:  # 如果播放列表包含该track_id
                playlist.track_ids = [x for x in playlist.track_ids if x != track_id]  # 移除该track_id
                playlist.touch()  # 更新播放列表的修改时间
