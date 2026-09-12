"""曲库数据清理模块（缺失文件/失效歌词/去重/归一化）。

从 `LibraryService` 拆出，组合持有 `LibraryService` 引用，只调用其公开状态与
`_record_cleanup` 日志钩子。原方法逻辑逐字搬移，仅将 `self.tracks` 等改为
`self._library._tracks`。

拆分动机：`LibraryService` 单类承载清理/导入/导出/搜索多职责，本模块专注
"对存量数据的健康度维护"，便于独立测试与未来扩展（如外部清理任务）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.models.entities import Track

if TYPE_CHECKING:
    from app.services.library_service import LibraryService

logger = logging.getLogger("museplayer.library")


class LibraryCleaner:
    """曲库数据清理（缺失文件/失效歌词/去重/归一化）。

    组合持有 `LibraryService`，只调用其公开状态；本身不持有独立状态。
    """

    def __init__(self, library: LibraryService) -> None:
        """初始化清理器。

        Args:
            library: 所属 `LibraryService`，提供 tracks/playlists 等状态访问。
        """
        self._library = library

    def _drop_missing_tracks(self) -> bool:
        """清理曲库中指向不存在的文件的跟踪记录。

        遍历所有曲目，检查文件路径是否存在，将不存在的文件记录移除。
        这是数据清理的重要步骤，防止播放时出现文件找不到的错误。
        """
        missing_ids: list[str] = []
        for track_id, track in self._library._tracks.items():
            try:
                source = Path(str(track.path or "")).resolve()
                if not source.exists() or not source.is_file():
                    missing_ids.append(track_id)
            except Exception as exc:
                # 逐曲目路径解析失败按缺失处理；高频路径用 debug 避免日志噪音
                logger.debug("解析曲目路径失败，视为缺失 %s: %s", track_id, exc)
                missing_ids.append(track_id)

        if not missing_ids:
            return False

        for track_id in missing_ids:
            track = self._library._tracks.get(track_id)
            path_text = str(track.path) if track is not None else ""
            self._library._record_cleanup(
                item=f"track:{track_id}",
                reason=f"歌曲文件不存在，已移除（path={path_text}）",
            )
            self._library._tracks.pop(track_id, None)
        logger.info("清理失效歌曲记录: %s", len(missing_ids))
        return True

    def _cleanup_missing_lyrics_paths(self) -> bool:
        """清理不存在的歌词文件路径。

        遍历所有歌曲，如果歌词文件路径指向的文件不存在，则重置该路径字段并记录清理操作。

        返回值：
            bool: 如果有任何路径被清理则返回True，否则返回False。
        """
        changed = False  # 初始化改变标志为False
        for track in self._library._tracks.values():  # 遍历所有歌曲
            # 获取歌词路径字符串，并清理空白字符
            source_lyrics = str(getattr(track, "source_lyrics_path", "") or "").strip()
            if not source_lyrics:  # 如果路径为空，跳过
                continue
            try:
                # 尝试解析路径为绝对路径
                lyric_path = Path(source_lyrics).resolve()
                # 检查路径是否存在且是文件
                exists = lyric_path.exists() and lyric_path.is_file()
            except Exception as exc:  # 如果发生任何异常，将exists设为False
                # 逐曲目清理路径，高频路径用 debug 避免日志噪音
                logger.debug("解析歌词路径失败，视为不存在 %s: %s", source_lyrics, exc)
                exists = False
            if exists:  # 如果文件存在，跳过清理
                continue
            # 记录清理操作
            self._library._record_cleanup(
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
            self._library._tracks.values(),
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

        for playlist in self._library._playlists.values():
            new_track_ids: list[str] = []
            seen: set[str] = set()
            for track_id in playlist.track_ids:
                mapped = remap.get(track_id, track_id)
                if mapped in seen:
                    self._library._record_cleanup(
                        item=f"playlist:{playlist.id}",
                        reason=f"歌单内重复歌曲引用已去重（track_id={mapped}）",
                    )
                    continue
                seen.add(mapped)
                new_track_ids.append(mapped)
            playlist.track_ids = new_track_ids

        for old_id in remap:
            self._library._record_cleanup(
                item=f"track:{old_id}",
                reason=f"重复歌曲记录已合并到保留项（target={remap[old_id]}）",
            )
            self._library._tracks.pop(old_id, None)

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
        except Exception as exc:
            # _dedupe_key 在逐曲目去重循环中调用，高频路径用 debug
            logger.debug("去重键路径解析失败，回退原值 %s: %s", source, exc)
            resolved = source

        filename = resolved.name.lower()
        duration_ms = max(0, int(round(float(track.duration_sec) * 1000.0)))
        try:
            size = int(resolved.stat().st_size)
        except Exception as exc:
            # 文件大小不可用时返回 -1，去重键仍可由文件名+时长构成
            logger.debug("去重键获取文件大小失败 %s: %s", resolved, exc)
            size = -1
        return filename, size, duration_ms

    def _normalize_playlist_tracks(self) -> bool:
        """功能：归一化播放列表的轨道，清理无效或重复的轨道ID，并更新播放列表的来源字段。

        参数：无显式参数，但操作对象为self.playlists和self.tracks。

        返回值：布尔值，表示是否有更改发生。
        """
        # 初始化标志，记录是否有任何更改
        changed = False
        # 获取所有存在的轨道ID集合，用于检查轨道是否有效
        existing_track_ids = set(self._library._tracks.keys())
        # 遍历所有播放列表进行处理
        for playlist in self._library._playlists.values():
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
                    self._library._record_cleanup(
                        item=f"playlist:{playlist.id}",
                        reason=f"歌单引用了不存在歌曲，已清理（track_id={track_id}）",
                    )
                    continue
                # 检查轨道ID是否重复，如果重复则去重
                if track_id in seen:
                    changed = True
                    # 记录清理操作，注明去重原因
                    self._library._record_cleanup(
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
                self._library._record_cleanup(
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
                self._library._record_cleanup(
                    item=f"playlist:{playlist.id}",
                    reason="歌单无有效歌曲，已清理歌单哈希字段",
                )
                playlist.source_playlist_hash = ""
                changed = True
        # 获取所有轨道ID列表
        all_ids = list(self._library._tracks.keys())
        # 检查“所有歌曲”播放列表是否需要更新，确保包含所有轨道
        from app.services.library_service import ALL_SONGS_ID  # 延迟导入避免循环依赖

        if self._library._playlists[ALL_SONGS_ID].track_ids != all_ids:
            self._library._playlists[ALL_SONGS_ID].track_ids = all_ids
            changed = True
        # 返回是否有更改发生
        return changed
