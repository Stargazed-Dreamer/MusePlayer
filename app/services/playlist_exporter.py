"""歌单导出模块。

从 `LibraryService` 拆出，组合持有 `LibraryService` 引用，只调用其公开状态
（`tracks`/`get_playlist`/`save`/`_store.path`）与 `_normalize_relpath` 工具。
原方法逻辑逐字搬移，仅将 `self.tracks` 等改为 `self._library._tracks`。

拆分动机：`LibraryService` 单类承载清理/导入/导出/搜索多职责，本模块专注
"按统一格式导出歌单（含播放统计与歌词）"，便于独立测试与未来扩展。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from app.models.entities import Playlist, Track

if TYPE_CHECKING:
    from app.services.library_service import LibraryService

# 延迟导入避免循环依赖：MUSE_PLAYLIST_SCHEMA 在 LibraryService 模块顶部定义
logger = logging.getLogger("museplayer.library")


class PlaylistExporter:
    """歌单导出（按统一 JSON 格式输出，含播放统计与歌词）。

    组合持有 `LibraryService`，本身不持有独立状态。
    """

    def __init__(self, library: LibraryService) -> None:
        """初始化导出器。

        Args:
            library: 所属 `LibraryService`，提供 tracks/playlists 等状态访问。
        """
        self._library = library

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
        from app.services.library_service import MUSE_PLAYLIST_SCHEMA

        library = self._library
        # 获取指定ID的歌单对象
        playlist = library.get_playlist(playlist_id)
        # 过滤出当前库中存在的歌曲ID
        track_ids = [tid for tid in playlist.track_ids if tid in library._tracks]
        # 如果没有可导出的歌曲，抛出异常
        if not track_ids:
            raise ValueError("歌单没有可导出的歌曲")

        # 获取或生成歌单的导出哈希值（用于唯一标识导出文件）
        playlist_hash = self._get_or_create_export_hash(playlist)
        # 获取当前UTC时间作为导出时间戳
        exported_at = datetime.now(UTC).isoformat()
        # 确定源数据库位置：优先使用歌单自身的数据库位置，否则使用默认路径
        database_location = str(
            Path(str(playlist.source_database_location or "")).resolve()
            if str(playlist.source_database_location or "").strip()
            else library._store.path.parent.parent.resolve()
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
            track = library._tracks[tid]
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
        raw = hashlib.sha1(f"playlist:{playlist.id}".encode()).hexdigest()
        # 将新哈希存储到播放列表对象的属性中
        playlist.source_playlist_hash = raw
        # 更新播放列表的修改时间戳
        playlist.touch()
        # 保存当前实例的更改到持久化存储
        self._library.save()
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
            result.append({"relpath": self._library._normalize_relpath(rel), "lang": lang})
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
                    paths.append(p)  # 将其添加到结果列表中
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
            rel = self._library._normalize_relpath(str(track.source_lyrics_storage_relpath or "").strip())
            if rel:  # 如果预存路径非空，则直接返回
                return rel
            # 若无预存路径，则获取歌词的绝对路径
            lyrics_abs = str(track.source_lyrics_path or "").strip()
            if not lyrics_abs:  # 如果绝对路径也不存在，返回空字符串
                return ""
            try:
                # 将绝对路径转换为相对于 db_root 的路径，并统一使用正斜杠
                return self._library._normalize_relpath(
                    str(Path(lyrics_abs).resolve().relative_to(db_root)).replace("\\", "/")
                )
            except Exception as exc:
                # 路径不在 db_root 下时回退为文件名，导出仍可继续
                logger.debug("歌词绝对路径转相对路径失败，回退文件名 %s: %s", lyrics_abs, exc)
                return self._library._normalize_relpath(Path(lyrics_abs).name)

        # 处理非歌词资源（如音频文件）的路径逻辑
        # 优先使用 track 中预存的、已标准化的源文件相对路径
        rel = self._library._normalize_relpath(str(track.source_storage_relpath or "").strip())
        if rel:  # 如果预存路径非空，则直接返回
            return rel
        try:
            # 将 track.path 的绝对路径转换为相对于 db_root 的路径，并统一使用正斜杠
            return self._library._normalize_relpath(
                str(Path(track.path).resolve().relative_to(db_root)).replace("\\", "/")
            )
        except Exception as exc:
            # 路径不在 db_root 下时回退为文件名，导出仍可继续
            logger.debug("曲目路径转相对路径失败，回退文件名 %s: %s", track.path, exc)
            return self._library._normalize_relpath(Path(track.path).name)
