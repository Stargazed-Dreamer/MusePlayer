"""播放统计服务。

统计口径说明：
1. `play_count`: 只要触发一次播放起点就 +1（不要求播放完成）。
2. `active_play_count`: 用户主动触发（如双击、拖入、主动搜索播放）才 +1。
3. `early_skip_count`: 在歌曲前 5% 被切走时 +1。
4. `played_percent_total`: 以秒级增量累计，可超过 100%。
5. `complete_play_count`: 播放进度达到 95% 以上时 +1（完播次数）。
6. `peak_session_play_count` / `peak_session_play_at`: 历史最高密集播放次数及时间。

时间维度（2026-09 引入，用于年度报告）：
- 每条曲目持有 `yearly` 年分桶：`{"2026": [播放, 主动, 完播, 秒数, 早期跳过]}`，只写非零年份。
- 服务层持有全局聚合块 `aggregate`：`monthly`（每年 12 个月播放次数/秒数）、
  `hours`（每年 24 小时分布）、`daily_peak`（每年最狂热一天）。聚合开销为每年约数百字节。
- 历史数据无分桶字段时留空，累计值不受影响；历史年份无法回填。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


def _now_ts() -> float:
    """获取当前时间戳。

    Returns:
        float: 当前时间的时间戳（秒）
    """
    return time.time()


def _current_year() -> int:
    """获取当前本地年份，用于年分桶归档。

    Returns:
        int: 本地时区的当前年份
    """
    return time.localtime().tm_year


# 年分桶的紧凑存储字段顺序：[播放次数, 主动播放, 完播, 累计秒数, 早期跳过]
_YEAR_BUCKET_LEN = 5


@dataclass(slots=True)
class PlaybackStatsEntry:
    """播放统计条目。

    记录单个曲目的详细播放统计信息。
    使用slots=True优化内存使用。
    """

    track_id: str
    """曲目ID"""
    play_count: int = 0
    """播放次数（只要触发播放就+1）"""
    active_play_count: int = 0
    """主动播放次数（用户主动播放才+1）"""
    early_skip_count: int = 0
    """早期跳过次数（在曲目前5%被切走时+1）"""
    complete_play_count: int = 0
    """完播次数（播放进度达到95%以上时+1）"""
    played_seconds_total: float = 0.0
    """累计播放秒数"""
    played_percent_total: float = 0.0
    """累计播放百分比（可超过100%）"""
    peak_session_play_count: int = 0
    """历史最高密集播放次数（单次启动期间最高播放次数）"""
    peak_session_play_at: float = 0.0
    """历史最高密集播放次数的记录时间戳"""
    yearly: dict[str, list] = field(default_factory=dict)
    """年分桶：{"2026": [play, active, complete, seconds, early_skip]}，只保留非零年份"""
    updated_at: float = field(default_factory=_now_ts)
    """最后更新时间戳"""

    def bucket_for_year(self, year: int) -> list:
        """取指定年份的分桶（缺失时创建空桶）。

        注意：本方法会修改自身但不知道脏标记，调用方负责标记服务层脏状态。

        Args:
            year: 年份（如 2026）

        Returns:
            list: 5 元素分桶 [play, active, complete, seconds, early_skip]
        """
        key = str(year)
        bucket = self.yearly.get(key)
        if bucket is None or len(bucket) != _YEAR_BUCKET_LEN:
            bucket = [0, 0, 0, 0.0, 0]
            self.yearly[key] = bucket
        return bucket

    def first_active_year(self) -> int | None:
        """首次产生播放记录的年份（用于"今年新听的歌"推导）。

        Returns:
            int | None: 最早的非零年份桶，无分桶数据时返回 None
        """
        years = [
            int(key)
            for key, bucket in self.yearly.items()
            if key.lstrip("-").isdigit() and (bucket[0] > 0 or bucket[3] > 0)
        ]
        return min(years) if years else None

    def to_dict(self) -> dict:
        """转换为字典格式，用于序列化。

        Returns:
            dict: 包含所有统计数据的字典
        """
        return {
            "track_id": self.track_id,
            "play_count": int(self.play_count),
            "active_play_count": int(self.active_play_count),
            "early_skip_count": int(self.early_skip_count),
            "complete_play_count": int(self.complete_play_count),
            "played_seconds_total": float(self.played_seconds_total),
            "played_percent_total": float(self.played_percent_total),
            "peak_session_play_count": int(self.peak_session_play_count),
            "peak_session_play_at": float(self.peak_session_play_at),
            "yearly": {key: list(bucket) for key, bucket in self.yearly.items()},
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> PlaybackStatsEntry:
        """从字典创建PlaybackStatsEntry实例，用于反序列化。

        对数值进行验证和限制，确保数据的合法性。

        Args:
            payload: 包含统计数据的字典

        Returns:
            dict: 新创建的统计条目实例
        """
        yearly_raw = payload.get("yearly")
        yearly: dict[str, list] = {}
        if isinstance(yearly_raw, dict):
            for key, bucket in yearly_raw.items():
                if isinstance(bucket, (list, tuple)) and len(bucket) == _YEAR_BUCKET_LEN:
                    yearly[str(key)] = [
                        max(0, int(bucket[0])),
                        max(0, int(bucket[1])),
                        max(0, int(bucket[2])),
                        max(0.0, float(bucket[3])),
                        max(0, int(bucket[4])),
                    ]
        return cls(
            track_id=str(payload.get("track_id", "")),
            play_count=max(0, int(payload.get("play_count", 0))),
            active_play_count=max(0, int(payload.get("active_play_count", 0))),
            early_skip_count=max(0, int(payload.get("early_skip_count", 0))),
            complete_play_count=max(0, int(payload.get("complete_play_count", 0))),
            played_seconds_total=max(0.0, float(payload.get("played_seconds_total", 0.0))),
            played_percent_total=max(0.0, float(payload.get("played_percent_total", 0.0))),
            peak_session_play_count=max(0, int(payload.get("peak_session_play_count", 0))),
            peak_session_play_at=max(0.0, float(payload.get("peak_session_play_at", 0.0))),
            yearly=yearly,
            updated_at=float(payload.get("updated_at", _now_ts())),
        )


class PlaybackStatsService:
    """播放统计服务。

    负责收集、存储和管理所有曲目的播放统计数据。
    使用JSON文件持久化统计数据，支持增量保存以优化性能。
    """

    def __init__(self, data_dir: Path):
        """初始化播放统计服务。

        创建统计文件路径并自动加载现有统计数据。

        Args:
            data_dir: 数据存储目录路径
        """
        self._path = Path(data_dir).resolve() / "playback_stats.json"
        self._entries: dict[str, PlaybackStatsEntry] = {}
        self._dirty = False
        # 本次启动期间每首歌的播放次数（用于计算密集播放）
        self._session_play_counts: dict[str, int] = {}
        # 全局聚合块（年度报告数据源，见模块 docstring）
        self._aggregate_monthly: dict[str, dict[str, list]] = {}
        self._aggregate_hours: dict[str, list] = {}
        self._aggregate_daily_peak: dict[str, dict] = {}
        # 当日播放计数游标（日期翻转时清零并与峰值比较）
        self._day_cursor: tuple[int, int, int] | None = None
        self._day_play_count = 0
        self._load()

    def _load(self) -> None:
        """从JSON文件加载播放统计数据。

        如果文件不存在或格式错误，将初始化空的统计数据。
        """
        if not self._path.exists():
            self._entries = {}
            self._load_aggregate({})
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._entries = {}
            self._load_aggregate({})
            return
        rows = payload.get("tracks", {}) if isinstance(payload, dict) else {}
        loaded: dict[str, PlaybackStatsEntry] = {}
        if isinstance(rows, dict):
            for track_id, row in rows.items():
                if not isinstance(row, dict):
                    continue
                item = PlaybackStatsEntry.from_dict({"track_id": track_id, **row})
                if item.track_id:
                    loaded[item.track_id] = item
        self._entries = loaded
        aggregate = payload.get("aggregate", {}) if isinstance(payload, dict) else {}
        self._load_aggregate(aggregate if isinstance(aggregate, dict) else {})

    def _load_aggregate(self, aggregate: dict) -> None:
        """从反序列化数据恢复全局聚合块，非法字段静默丢弃。"""
        monthly = aggregate.get("monthly", {})
        self._aggregate_monthly = {}
        if isinstance(monthly, dict):
            for year, series in monthly.items():
                if not isinstance(series, dict):
                    continue
                play = series.get("play", [])
                seconds = series.get("seconds", [])
                if isinstance(play, list) and isinstance(seconds, list):
                    self._aggregate_monthly[str(year)] = {
                        "play": [max(0, int(v)) for v in play[:12]],
                        "seconds": [max(0.0, float(v)) for v in seconds[:12]],
                    }
        hours = aggregate.get("hours", {})
        self._aggregate_hours = {}
        if isinstance(hours, dict):
            for year, series in hours.items():
                if isinstance(series, list):
                    padded = [max(0, int(v)) for v in series[:24]]
                    padded += [0] * (24 - len(padded))
                    self._aggregate_hours[str(year)] = padded
        daily_peak = aggregate.get("daily_peak", {})
        self._aggregate_daily_peak = {}
        if isinstance(daily_peak, dict):
            for year, peak in daily_peak.items():
                if isinstance(peak, dict) and "date" in peak and "count" in peak:
                    self._aggregate_daily_peak[str(year)] = {
                        "date": str(peak["date"]),
                        "count": max(0, int(peak["count"])),
                    }

    def save_if_dirty(self) -> None:
        """有条件地保存统计数据。

        仅在数据被修改（脏状态）时写入磁盘，
        以减少不必要的磁盘I/O操作。
        """
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tracks": {track_id: item.to_dict() for track_id, item in self._entries.items()},
            "aggregate": {
                "monthly": self._aggregate_monthly,
                "hours": self._aggregate_hours,
                "daily_peak": self._aggregate_daily_peak,
            },
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._dirty = False

    def record_play_start(self, track_id: str, *, active_request: bool) -> None:
        """记录播放开始事件。

        当曲目开始播放时调用此方法增加相应的计数器。
        同时更新本次启动期间的会话播放计数，并判断是否更新历史最高密集播放次数。

        Args:
            track_id: 曲目ID
            active_request: 是否为用户主动触发的播放
        """
        track_id = str(track_id or "").strip()
        if not track_id:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.play_count += 1
        if active_request:
            item.active_play_count += 1
        item.updated_at = _now_ts()
        self._dirty = True

        # 年分桶 + 全局聚合（播放起点、主动播放）
        now = time.localtime()
        year_key = str(now.tm_year)
        bucket = item.bucket_for_year(now.tm_year)
        bucket[0] += 1
        if active_request:
            bucket[1] += 1
        month_series = self._aggregate_monthly.setdefault(year_key, {"play": [0] * 12, "seconds": [0.0] * 12})
        month_series["play"][now.tm_mon - 1] += 1
        hour_series = self._aggregate_hours.setdefault(year_key, [0] * 24)
        hour_series[now.tm_hour] += 1
        self._update_daily_peak(now, year_key)

        # 更新本次启动期间的会话播放计数
        session_count = self._session_play_counts.get(track_id, 0) + 1
        self._session_play_counts[track_id] = session_count

        # 判断是否更新历史最高密集播放次数
        # 规则：本次 > 历史 → 覆盖；本次 < 历史 但差值 ≤ 10% → 也覆盖
        previous_peak = item.peak_session_play_count
        if previous_peak <= 0 or session_count > previous_peak:
            item.peak_session_play_count = session_count
            item.peak_session_play_at = _now_ts()
        elif previous_peak > 0 and session_count <= previous_peak:
            # 差值不超过10%时覆盖
            if previous_peak - session_count <= max(1, previous_peak * 0.10):
                item.peak_session_play_count = session_count
                item.peak_session_play_at = _now_ts()

    def record_play_progress(self, track_id: str, played_seconds: float, duration_sec: float) -> None:
        """记录播放进度。

        累加播放时间和播放百分比，使用增量方式避免
        进度拖动导致的重复统计。

        Args:
            track_id: 曲目ID
            played_seconds: 本次播放的秒数增量
            duration_sec: 曲目总时长
        """
        # 以"增量秒数"累加，避免拖动进度导致重复统计整曲播放。
        track_id = str(track_id or "").strip()
        delta = max(0.0, float(played_seconds))
        duration = max(0.0, float(duration_sec))
        if not track_id or delta <= 0.0:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.played_seconds_total += delta
        if duration > 0.0:
            item.played_percent_total += (delta / duration) * 100.0
        item.updated_at = _now_ts()
        self._dirty = True

        # 年分桶 + 全局聚合（秒数）
        now = time.localtime()
        bucket = item.bucket_for_year(now.tm_year)
        bucket[3] += delta
        month_series = self._aggregate_monthly.setdefault(str(now.tm_year), {"play": [0] * 12, "seconds": [0.0] * 12})
        month_series["seconds"][now.tm_mon - 1] += delta

    def remove_track(self, track_id: str) -> None:
        """移除曲目的统计数据。

        当曲目从库中删除时调用此方法清理统计信息。

        Args:
            track_id: 要移除的曲目ID
        """
        if track_id in self._entries:
            del self._entries[track_id]
            self._dirty = True

    def record_early_skip(self, track_id: str) -> None:
        """记录早期跳过事件。

        当曲目在前5%的时间内被跳过时调用此方法。

        Args:
            track_id: 曲目ID
        """
        track_id = str(track_id or "").strip()
        if not track_id:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.early_skip_count += 1
        item.updated_at = _now_ts()
        self._dirty = True
        item.bucket_for_year(time.localtime().tm_year)[4] += 1

    def record_complete_play(self, track_id: str) -> None:
        """记录完播事件。

        当曲目播放进度达到95%以上时调用此方法。

        Args:
            track_id: 曲目ID
        """
        track_id = str(track_id or "").strip()
        if not track_id:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.complete_play_count += 1
        item.updated_at = _now_ts()
        self._dirty = True
        item.bucket_for_year(time.localtime().tm_year)[2] += 1

    def reset_early_skip_count(self, track_id: str) -> None:
        """重置指定轨道ID的早期跳过计数。

        功能：将对应条目的early_skip_count重置为0，并更新时间戳。
        参数：
            track_id (str): 轨道ID。
        返回值：None。
        """
        track_id = str(track_id or "").strip()  # 将track_id转换为字符串，去除空白，处理None或空值
        if not track_id:  # 如果track_id为空字符串
            return  # 直接返回
        item = self._entries.get(track_id)  # 从条目字典中获取指定track_id的条目
        if item is None:  # 如果条目不存在
            return  # 直接返回
        if item.early_skip_count != 0:  # 检查早期跳过计数是否非零
            item.early_skip_count = 0  # 重置早期跳过计数为0
            item.updated_at = _now_ts()  # 更新条目的更新时间戳
            self._dirty = True  # 标记数据为已修改

    def _update_daily_peak(self, now: time.struct_time, year_key: str) -> None:
        """更新"最狂热的一天"峰值（仅破纪录时写盘，开销极小）。"""
        today = (now.tm_year, now.tm_mon, now.tm_mday)
        if self._day_cursor != today:
            self._day_cursor = today
            self._day_play_count = 0
        self._day_play_count += 1
        peak = self._aggregate_daily_peak.get(year_key)
        if peak is None or self._day_play_count > peak.get("count", 0):
            self._aggregate_daily_peak[year_key] = {
                "date": f"{today[0]:04d}-{today[1]:02d}-{today[2]:02d}",
                "count": self._day_play_count,
            }

    @property
    def has_entries(self) -> bool:
        """是否存有统计数据。

        UI 层用于在弹出导出对话框前预判是否值得操作。
        """
        return bool(self._entries)

    def export_to_file(self, dest: Path, *, library: object | None = None) -> dict:
        """导出统计数据为 MuseArc 兼容格式（业务逻辑）。

        生成 schema=musearc_playlist_export_v1 的 JSON 文件，包含每首曲目的
        播放次数/主动播放/秒数/早期跳过，以及可选的 source_sha256/storage_relpath
        元数据（需传入 library 进行曲目查询）。

        Args:
            dest: 目标 JSON 文件路径
            library: 可选的 LibraryService（需提供 get_track(track_id) 访问器），
                用于查询曲目的 source_sha256/path 字段；
                为 None 时跳过元数据补全（仅保留 stats 字段）。

        Returns:
            dict: 导出摘要 ``{"track_count": int, "path": str}``。
            无条目时返回 ``{"track_count": 0, "path": ""}`` 且不写盘。
        """
        import hashlib

        entries = self._entries
        if not entries:
            return {"track_count": 0, "path": ""}

        tracks_list: list[dict] = []
        for track_id, item in entries.items():
            # 经由公开访问器查询曲目元数据（原先 getattr(library, "tracks") 会在
            # 曲库私有化后静默返回空字典，导致元数据无声丢失）
            track = library.get_track(track_id) if library is not None else None
            entry = {
                "track_id": f"trk_{track_id}" if not track_id.startswith("trk_") else track_id,
                "stats": {
                    "play_count": max(0, int(item.play_count)),
                    "manual_play_count": max(0, int(item.active_play_count)),
                    "play_seconds": max(0, int(item.played_seconds_total)),
                    "early_skip_count": max(0, int(item.early_skip_count)),
                },
            }
            if track:
                if track.source_sha256:
                    entry["source_sha256"] = track.source_sha256
                if track.path:
                    entry["storage_relpath"] = str(track.path)
            tracks_list.append(entry)

        content_hash = hashlib.sha256(
            json.dumps(tracks_list, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        payload = {
            "schema": "musearc_playlist_export_v1",
            "playlist_hash": f"museplayer_stats_{content_hash}",
            "playlist_name": "MusePlayer 播放统计",
            "tracks": tracks_list,
        }

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return {"track_count": len(tracks_list), "path": str(dest)}

    def export_stats_for_track(self, track_id: str) -> dict[str, int] | None:
        """导出指定曲目的统计数据。

        返回适用于外部使用的统计信息字典。

        Args:
            track_id: 曲目ID

        Returns:
            dict[str, int] | None: 统计数据字典，如果曲目不存在则返回None
        """
        item = self._entries.get(str(track_id or "").strip())
        if item is None:
            return None
        result = {
            "play_count": max(0, int(item.play_count)),
            "manual_play_count": max(0, int(item.active_play_count)),
            "complete_play_count": max(0, int(item.complete_play_count)),
            "play_seconds": max(0, int(round(item.played_seconds_total))),
            "early_skip_count": max(0, int(item.early_skip_count)),
            "peak_session_play_count": max(0, int(item.peak_session_play_count)),
            "peak_session_play_at": max(0.0, float(item.peak_session_play_at)),
        }
        return result

    # ===== 年度报告数据接口（纯逻辑，无 Qt 依赖）=====

    def available_years(self) -> list[int]:
        """列出有统计数据的年份（分桶或聚合），升序。

        Returns:
            list[int]: 年份列表，可能为空
        """
        years: set[int] = set()
        for item in self._entries.values():
            first = item.first_active_year()
            if first is not None:
                years.add(first)
        for key in self._aggregate_monthly:
            if key.lstrip("-").isdigit():
                years.add(int(key))
        return sorted(years)

    def year_summary(self, year: int) -> dict:
        """汇总指定年份的宏观指标。

        Args:
            year: 年份

        Returns:
            dict: {play_count, active_play_count, complete_play_count,
                   seconds_total, early_skip_count, distinct_tracks, new_tracks}
        """
        year_key = str(year)
        play = active = complete = skip = 0
        seconds = 0.0
        distinct = 0
        new_tracks = 0
        for item in self._entries.values():
            bucket = item.yearly.get(year_key)
            if bucket is None or len(bucket) != _YEAR_BUCKET_LEN:
                continue
            if bucket[0] > 0 or bucket[3] > 0:
                distinct += 1
                if item.first_active_year() == year:
                    new_tracks += 1
            play += int(bucket[0])
            active += int(bucket[1])
            complete += int(bucket[2])
            seconds += float(bucket[3])
            skip += int(bucket[4])
        return {
            "play_count": play,
            "active_play_count": active,
            "complete_play_count": complete,
            "seconds_total": seconds,
            "early_skip_count": skip,
            "distinct_tracks": distinct,
            "new_tracks": new_tracks,
        }

    def monthly_series(self, year: int) -> dict[str, list]:
        """指定年份的月度序列（全局聚合）。

        Returns:
            dict: {"play": [12], "seconds": [12]}，缺月为 0
        """
        series = self._aggregate_monthly.get(str(year))
        if series is None:
            return {"play": [0] * 12, "seconds": [0.0] * 12}
        play = list(series.get("play", [0] * 12))
        seconds = list(series.get("seconds", [0.0] * 12))
        play += [0] * (12 - len(play))
        seconds += [0.0] * (12 - len(seconds))
        return {"play": play, "seconds": seconds}

    def hours_series(self, year: int) -> list[int]:
        """指定年份的 24 小时播放分布。

        Returns:
            list[int]: 24 个整数，缺年份为全 0
        """
        series = self._aggregate_hours.get(str(year))
        if series is None:
            return [0] * 24
        padded = [max(0, int(v)) for v in series[:24]]
        return padded + [0] * (24 - len(padded))

    def daily_peak(self, year: int) -> dict | None:
        """指定年份最狂热的一天。

        Returns:
            dict | None: {"date": "YYYY-MM-DD", "count": int} 或 None
        """
        peak = self._aggregate_daily_peak.get(str(year))
        return dict(peak) if peak else None

    def year_top_entries(self, year: int, limit: int = 10) -> list[tuple[str, list]]:
        """指定年份播放次数最多的曲目分桶。

        Returns:
            list[tuple[str, list]]: [(track_id, bucket), ...] 按 bucket[0]（播放次数）降序
        """
        year_key = str(year)
        rows = []
        for track_id, item in self._entries.items():
            bucket = item.yearly.get(year_key)
            if bucket is not None and len(bucket) == _YEAR_BUCKET_LEN and bucket[0] > 0:
                rows.append((track_id, list(bucket)))
        rows.sort(key=lambda pair: pair[1][0], reverse=True)
        return rows[: max(0, int(limit))]

    def year_bucket_entries(self, year: int) -> dict[str, list]:
        """返回指定年份的全部分桶（track_id -> [play, active, complete, seconds, skip]）。

        供报告聚合器遍历；仅含该年份有数据的曲目。

        Returns:
            dict[str, list]: 曲目分桶映射
        """
        year_key = str(year)
        return {
            track_id: list(bucket)
            for track_id, item in self._entries.items()
            if (bucket := item.yearly.get(year_key)) is not None and len(bucket) == _YEAR_BUCKET_LEN
        }

    def snapshot_payload(self) -> dict:
        """生成当前统计的全量快照（供检查点保存，深拷贝）。"""
        return {
            "tracks": {track_id: item.to_dict() for track_id, item in self._entries.items()},
            "aggregate": {
                "monthly": json.loads(json.dumps(self._aggregate_monthly)),
                "hours": json.loads(json.dumps(self._aggregate_hours)),
                "daily_peak": json.loads(json.dumps(self._aggregate_daily_peak)),
            },
        }
