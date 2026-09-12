"""Muse 歌单导入模块（解析 `*.muse_playlist.json` 文件 / 运行时 payload）。

从 `LibraryService` 拆出，组合持有 `LibraryService` 引用，只调用其公开状态与
既有辅助方法（`_normalize_relpath` / `_normalize_playlist_name` /
`_find_orphan_track_ids` / `_remove_track_globally` / `_normalize_playlist_tracks` /
`save` 等）。原方法逻辑逐字搬移，仅将 `self.tracks` 等改为 `self._library.tracks`。

拆分动机：`LibraryService` 单类承载清理/导入/导出/搜索多职责，本模块专注
"Muse 格式歌单的解析与导入"，便于独立测试与未来扩展（如新 schema 版本）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.models.entities import Playlist, Track, new_id

if TYPE_CHECKING:
    from app.services.library_service import LibraryService

logger = logging.getLogger("museplayer.library")


class MusePlaylistImporter:
    """Muse 歌单导入（解析 `*.muse_playlist.json` 文件 / 运行时 payload）。

    组合持有 `LibraryService`，只调用其公开状态与既有辅助方法；本身不持有独立状态。
    """

    def __init__(self, library: LibraryService) -> None:
        """初始化导入器。

        Args:
            library: 所属 `LibraryService`，提供 tracks/playlists/_metadata/_store
                等状态与服务访问。
        """
        self._library = library

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
        playlist_hash = str(
            payload.get("playlist_hash", "")
        ).strip()  # 从payload中获取playlist_hash，如果没有则默认为空字符串，并去除首尾空格
        if playlist_hash:
            hash_part = playlist_hash[:12]  # 如果playlist_hash存在，取前12个字符作为哈希部分
        else:
            raw = json.dumps(
                payload, ensure_ascii=False, sort_keys=True
            )  # 将payload转换为JSON字符串，确保非ASCII字符保留，按键排序
            hash_part = hashlib.sha1(raw.encode("utf-8")).hexdigest()[
                :12
            ]  # 计算JSON字符串的SHA1哈希，并取前12个十六进制字符
        hint_part = hashlib.sha1(str(source_hint or "runtime_payload").encode("utf-8")).hexdigest()[
            :8
        ]  # 基于source_hint计算SHA1哈希，取前8个字符作为提示部分
        virtual_source_file = (
            self._library._store.path.parent / f"_runtime_{hash_part}_{hint_part}.muse_playlist.json"
        ).resolve()  # 构建虚拟源文件路径，使用哈希部分和提示部分生成文件名，并解析为绝对路径
        return self._import_muse_playlist_data(
            payload, source_file=virtual_source_file, fallback_name="导入歌单"
        )  # 调用导入方法，传入有效载荷、虚拟源文件路径和备用名称

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
        from app.services.library_service import ALL_SONGS_ID, AUDIO_EXTENSIONS  # 延迟导入避免循环依赖

        library = self._library
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
        playlist_name = library._normalize_playlist_name(str(payload.get("playlist_name", "")).strip() or fallback_name)
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
            library.playlists[playlist.id] = playlist
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
            storage_relpath = library._normalize_relpath(str(raw.get("storage_relpath", "")).strip())
            # 标准化歌词文件的相对路径
            lyrics_relpath = library._normalize_relpath(str(raw.get("lyrics_storage_relpath", "")).strip())
            # 获取原始歌词数组（可能包含多份歌词）
            lyrics_array = raw.get("lyrics", [])
            source_sha256 = str(raw.get("source_sha256", "")).strip().lower()
            title = str(raw.get("title", "")).strip()
            artist = str(raw.get("artist", "")).strip()
            album = str(raw.get("album", "")).strip()

            # 解析完整的音频文件和歌词文件绝对路径
            track_path = self._resolve_muse_track_path(db_root=db_root, storage_relpath=storage_relpath)
            lyrics_path = (
                self._resolve_muse_track_path(db_root=db_root, storage_relpath=lyrics_relpath)
                if lyrics_relpath
                else None
            )

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
                    track = library._metadata.extract_track(track_path)
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
                library.tracks[track.id] = track

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
                    lrel = library._normalize_relpath(str(lentry.get("relpath", "")).strip())
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
        library.active_playlist_id = playlist.id

        # 将新歌单中的所有曲目同步到“全部歌曲”歌单中
        all_songs = library.playlists[ALL_SONGS_ID]
        all_ids = set(all_songs.track_ids)
        for track_id in new_track_ids:
            if track_id not in all_ids:
                all_songs.track_ids.append(track_id)
                all_ids.add(track_id)
        all_songs.touch()

        # 计算并清理从本次导入中被移除的、且在其他地方也没有引用的孤立曲目
        removed_track_ids = old_track_ids - new_track_ids_set
        if removed_track_ids:
            orphan_track_ids = library._find_orphan_track_ids()
            for track_id in removed_track_ids:
                if track_id in orphan_track_ids:
                    library._remove_track_globally(track_id)

        # 对所有歌单的曲目列表进行一次标准化处理（如去重、排序），然后保存整个状态
        library._normalize_playlist_tracks()
        library.save()
        logger.info("导入歌单文件: %s, playlist=%s, songs=%s", source_file, playlist.name, len(playlist.track_ids))
        return playlist

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
        while candidate in self._library.playlists:
            existing = self._library.playlists[candidate]

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
        from app.services.library_service import ALL_SONGS_ID, FAVORITES_ID  # 延迟导入避免循环依赖

        # 将路径对象转换为字符串，便于后续比较
        source_text = str(source_file)
        # 遍历所有现有的播放列表
        for playlist in self._library.playlists.values():
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
        library = self._library
        resolved_source = source_path.resolve()
        normalized_relpath = library._normalize_relpath(source_storage_relpath)

        # 第一优先级：通过SHA256哈希值查找
        if source_sha256:
            # 使用小写形式进行匹配，确保大小写不敏感
            hit = library._sha256_index.get(source_sha256.lower())
            if hit is not None:
                return hit

        # 第二优先级：通过source_track_id查找
        if source_track_id:
            # 遍历所有tracks，查找匹配的source_track_id
            for track in library.tracks.values():
                if track.source_track_id == source_track_id:
                    return track

        # 第三优先级：通过标准化后的相对路径查找
        if normalized_relpath:
            # 遍历所有tracks，比较标准化后的相对路径
            for track in library.tracks.values():
                if library._normalize_relpath(track.source_storage_relpath) == normalized_relpath:
                    return track

        # 第四优先级：通过完整解析路径查找
        hit = library._path_index.get(resolved_source)
        if hit is not None:
            return hit

        # 所有查找方式都未找到匹配项
        return None
