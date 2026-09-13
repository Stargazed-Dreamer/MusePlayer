"""年度报告聚合器。

将播放统计（实时分桶或检查点快照）聚合为可直接渲染的报告数据。
纯逻辑、零 Qt 依赖：UI 层与导出器都只消费这里的 ReportData。

数据源（ReportSource）：
- ("year", 2026)：按年分桶的实时统计。
- ("checkpoint", id)：检查点快照（累计口径，KPI 为保存时刻的累计值）。

对比：主副两份 ReportData 由 UI 传入 `apply_comparison()` 计算增量
（KPI 百分比变化、Top 榜排名 ▲▼/NEW 标记）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

# 年分桶字段下标：[play, active, complete, seconds, early_skip]
_B_PLAY, _B_ACTIVE, _B_COMPLETE, _B_SECONDS, _B_SKIP = 0, 1, 2, 3, 4

# 时段划分（24 小时 → 4 段，用于"深夜电台"趣味卡）
_HOUR_SEGMENTS = [
    ("凌晨", range(0, 6)),
    ("上午", range(6, 12)),
    ("下午", range(12, 18)),
    ("晚上", range(18, 24)),
]


@dataclass(slots=True)
class ReportSource:
    """报告数据源。

    kind: "year"（年份分桶）或 "checkpoint"（检查点快照）
    year: kind == "year" 时的年份
    checkpoint_id: kind == "checkpoint" 时的检查点 ID
    label: 显示名称（由 UI 层填充，如 "2026 年（至今）" / "毕业那晚"）
    """

    kind: str
    year: int = 0
    checkpoint_id: str = ""
    label: str = ""


@dataclass(slots=True)
class ReportData:
    """一份可直接渲染的报告数据。"""

    title: str = ""
    """报告标题（如 "2026 · 年度听歌报告"）"""
    subtitle: str = ""
    """副标题（统计口径说明）"""
    source_label: str = ""
    """主数据源显示名"""
    kpis: list[dict] = field(default_factory=list)
    """[{key, label, value, display}]，value 为数值用于对比"""
    monthly_play: list[int] = field(default_factory=list)
    """月度播放次数序列（12 个）"""
    monthly_seconds: list[float] = field(default_factory=list)
    """月度播放秒数序列（12 个）"""
    hours: list[int] = field(default_factory=list)
    """24 小时播放分布"""
    daily_peak: dict | None = None
    """{"date": "...", "count": n} 或 None"""
    top_tracks: list[dict] = field(default_factory=list)
    """[{track_id, title, artist, count, seconds}]"""
    top_artists: list[dict] = field(default_factory=list)
    """[{name, count}]"""
    top_albums: list[dict] = field(default_factory=list)
    """[{name, count}]"""
    fun_facts: list[dict] = field(default_factory=list)
    """[{emoji, title, text}]"""
    is_cumulative: bool = False
    """是否为累计口径（检查点快照）"""


def _fmt_hours(seconds: float) -> str:
    """秒数格式化为小时（保留 1 位小数）。"""
    return f"{seconds / 3600.0:.1f}"


def _fmt_count(value: int) -> str:
    """整数千分位格式化。"""
    return f"{value:,}"


def _segment_of_hour(hour: int) -> str:
    """将 0-23 小时映射到时段名称。"""
    for name, hours_range in _HOUR_SEGMENTS:
        if hour in hours_range:
            return name
    return "晚上"


def _summarize_entries(
    entries: dict[str, list],
    year: int | None,
    lookup: Callable[[str], tuple[str, str, str] | None],
) -> dict:
    """按年份分桶（或累计，year=None）汇总 entries。

    entries: {track_id: bucket 或累计五元组}
    返回 KPI 值 + Top 榜原始数据。
    """
    play = active = complete = skip = 0
    seconds = 0.0
    distinct = 0
    top_rows: list[dict] = []
    artist_counter: dict[str, int] = {}
    album_counter: dict[str, int] = {}
    for track_id, values in entries.items():
        if year is not None and len(values) < 5:
            continue
        if year is not None and values[_B_PLAY] <= 0 and values[_B_SECONDS] <= 0:
            continue
        row_play = int(values[_B_PLAY])
        row_seconds = float(values[_B_SECONDS])
        play += row_play
        active += int(values[_B_ACTIVE])
        complete += int(values[_B_COMPLETE])
        skip += int(values[_B_SKIP])
        seconds += row_seconds
        if row_play > 0 or row_seconds > 0:
            distinct += 1
        meta = lookup(track_id) or ("未知标题", "未知歌手", "未知专辑")
        top_rows.append(
            {
                "track_id": track_id,
                "title": meta[0],
                "artist": meta[1],
                "count": row_play,
                "seconds": row_seconds,
            }
        )
        if meta[1]:
            artist_counter[meta[1]] = artist_counter.get(meta[1], 0) + row_play
        if meta[2]:
            album_counter[meta[2]] = album_counter.get(meta[2], 0) + row_play
    top_rows.sort(key=lambda row: (row["count"], row["seconds"]), reverse=True)
    top_tracks = top_rows[:10]
    top_artists = sorted(artist_counter.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_albums = sorted(album_counter.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return {
        "play": play,
        "active": active,
        "complete": complete,
        "skip": skip,
        "seconds": seconds,
        "distinct": distinct,
        "top_tracks": top_tracks,
        "top_artists": top_artists,
        "top_albums": top_albums,
    }


def _cumulative_entries_from_snapshot(payload: dict) -> dict[str, list]:
    """从检查点快照提取累计五元组 [play, active, complete, seconds, skip]。"""
    result: dict[str, list] = {}
    for track_id, row in (payload.get("tracks") or {}).items():
        if not isinstance(row, dict):
            continue
        result[track_id] = [
            max(0, int(row.get("play_count", 0))),
            max(0, int(row.get("active_play_count", 0))),
            max(0, int(row.get("complete_play_count", 0))),
            max(0.0, float(row.get("played_seconds_total", 0.0))),
            max(0, int(row.get("early_skip_count", 0))),
        ]
    return result


class StatsReportBuilder:
    """报告聚合器：从实时统计或检查点快照生成 ReportData。"""

    def __init__(
        self,
        stats_service,
        checkpoint_service,
        track_lookup: Callable[[str], tuple[str, str, str] | None],
    ):
        """初始化。

        Args:
            stats_service: PlaybackStatsService
            checkpoint_service: StatsCheckpointService
            track_lookup: track_id -> (title, artist, album) | None
        """
        self._stats = stats_service
        self._checkpoints = checkpoint_service
        self._lookup = track_lookup

    def build(self, source: ReportSource) -> ReportData:
        """构建报告数据。

        Args:
            source: 主数据源

        Returns:
            ReportData: 无数据时各字段为空列表
        """
        if source.kind == "checkpoint":
            return self._build_from_checkpoint(source)
        return self._build_from_year(source)

    def _build_from_year(self, source: ReportSource) -> ReportData:
        """按年份分桶构建。"""
        year = int(source.year)
        raw = self._stats.year_summary(year)
        summary = {
            "play": raw["play_count"],
            "active": raw["active_play_count"],
            "complete": raw["complete_play_count"],
            "skip": raw["early_skip_count"],
            "seconds": raw["seconds_total"],
            "distinct": raw["distinct_tracks"],
            "new_tracks": raw["new_tracks"],
            "top_tracks": [],
            "top_artists": [],
            "top_albums": [],
        }
        monthly = self._stats.monthly_series(year)
        hours = self._stats.hours_series(year)
        daily_peak = self._stats.daily_peak(year)
        top_raw = self._stats.year_top_entries(year, limit=10)
        top_tracks = []
        for track_id, bucket in top_raw:
            meta = self._lookup(track_id) or ("未知标题", "未知歌手", "未知专辑")
            top_tracks.append(
                {
                    "track_id": track_id,
                    "title": meta[0],
                    "artist": meta[1],
                    "count": int(bucket[0]),
                    "seconds": float(bucket[3]),
                }
            )
        artist_counter: dict[str, int] = {}
        album_counter: dict[str, int] = {}
        for track_id, bucket in self._stats.year_top_entries(year, limit=10000):
            meta = self._lookup(track_id) or ("", "未知歌手", "未知专辑")
            if meta[1]:
                artist_counter[meta[1]] = artist_counter.get(meta[1], 0) + int(bucket[0])
            if meta[2]:
                album_counter[meta[2]] = album_counter.get(meta[2], 0) + int(bucket[0])
        top_artists = sorted(artist_counter.items(), key=lambda kv: kv[1], reverse=True)[:5]
        top_albums = sorted(album_counter.items(), key=lambda kv: kv[1], reverse=True)[:3]
        report = ReportData(
            title=f"{year} · 年度听歌报告",
            subtitle="按年分桶统计" + ("（至今）" if year == time.localtime().tm_year else ""),
            source_label=source.label or str(year),
            kpis=self._year_kpis(summary),
            monthly_play=[int(v) for v in monthly["play"]],
            monthly_seconds=[float(v) for v in monthly["seconds"]],
            hours=list(hours),
            daily_peak=daily_peak,
            top_tracks=top_tracks,
            top_artists=[{"name": n, "count": c} for n, c in top_artists],
            top_albums=[{"name": n, "count": c} for n, c in top_albums],
            is_cumulative=False,
        )
        report.fun_facts = self._year_fun_facts(report, year, summary)
        return report

    def _build_from_checkpoint(self, source: ReportSource) -> ReportData:
        """按检查点快照构建（累计口径）。"""
        payload = self._checkpoints.load(source.checkpoint_id)
        if payload is None:
            return ReportData(title="检查点不存在或已损坏", source_label=source.label)
        summary = _summarize_entries(_cumulative_entries_from_snapshot(payload), None, self._lookup)
        name = payload.get("name", source.label)
        created = payload.get("created_at", 0.0)
        created_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(created)) if created else ""
        monthly_play = [0] * 12
        monthly_seconds = [0.0] * 12
        hours = [0] * 24
        daily_peak: dict | None = None
        aggregate = payload.get("aggregate") or {}
        for series in (aggregate.get("monthly") or {}).values():
            if not isinstance(series, dict):
                continue
            for idx, v in enumerate((series.get("play") or [])[:12]):
                monthly_play[idx] += int(v)
            for idx, v in enumerate((series.get("seconds") or [])[:12]):
                monthly_seconds[idx] += float(v)
        for series in (aggregate.get("hours") or {}).values():
            for idx, v in enumerate((series or [])[:24]):
                hours[idx] += int(v)
        for peak in (aggregate.get("daily_peak") or {}).values():
            if isinstance(peak, dict) and (daily_peak is None or int(peak.get("count", 0)) > daily_peak["count"]):
                daily_peak = {"date": str(peak.get("date", "")), "count": int(peak.get("count", 0))}
        loop_king, listen_champion = self._peak_facts_from_snapshot(payload)
        report = ReportData(
            title=f"{name} · 统计快照报告",
            subtitle=f"检查点快照（累计口径） · 保存于 {created_text}" if created_text else "检查点快照（累计口径）",
            source_label=name,
            kpis=self._year_kpis(summary),
            monthly_play=monthly_play,
            monthly_seconds=monthly_seconds,
            hours=hours,
            daily_peak=daily_peak,
            top_tracks=summary["top_tracks"],
            top_artists=[{"name": n, "count": c} for n, c in summary["top_artists"]],
            top_albums=[{"name": n, "count": c} for n, c in summary["top_albums"]],
            is_cumulative=True,
        )
        report.fun_facts = self._checkpoint_fun_facts(report, summary, loop_king, listen_champion)
        return report

    def _peak_facts_from_snapshot(self, payload: dict) -> tuple[dict | None, dict | None]:
        """从快照提取单曲循环之王与耐听冠军（累计口径）。"""
        loop_king: dict | None = None
        listen_champion: dict | None = None
        for track_id, row in (payload.get("tracks") or {}).items():
            if not isinstance(row, dict):
                continue
            peak = int(row.get("peak_session_play_count", 0))
            if peak > 0 and (loop_king is None or peak > loop_king["count"]):
                meta = self._lookup(track_id) or ("未知标题", "未知歌手", "未知专辑")
                loop_king = {"title": meta[0], "count": peak}
            seconds = float(row.get("played_seconds_total", 0.0))
            if seconds > 0 and (listen_champion is None or seconds > listen_champion["seconds"]):
                meta = self._lookup(track_id) or ("未知标题", "未知歌手", "未知专辑")
                listen_champion = {"title": meta[0], "seconds": seconds}
        return loop_king, listen_champion

    @staticmethod
    def _year_kpis(summary: dict) -> list[dict]:
        """KPI 四卡数据。"""
        return [
            {"key": "play", "label": "播放次数", "value": summary["play"], "display": _fmt_count(summary["play"])},
            {
                "key": "seconds",
                "label": "听歌总时长",
                "value": summary["seconds"],
                "display": f"{_fmt_hours(summary['seconds'])} 小时",
            },
            {
                "key": "complete",
                "label": "完播次数",
                "value": summary["complete"],
                "display": _fmt_count(summary["complete"]),
            },
            {
                "key": "tracks",
                "label": "听过的歌",
                "value": summary["distinct"],
                "display": _fmt_count(summary["distinct"]),
            },
        ]

    def _year_fun_facts(self, report: ReportData, year: int, summary: dict) -> list[dict]:
        """年度视图的趣味发现。"""
        facts: list[dict] = []
        # 时段偏好
        total_hour_plays = sum(report.hours)
        if total_hour_plays > 0:
            seg_sums: dict[str, int] = {}
            for hour, count in enumerate(report.hours):
                seg = _segment_of_hour(hour)
                seg_sums[seg] = seg_sums.get(seg, 0) + count
            seg, count = max(seg_sums.items(), key=lambda kv: kv[1])
            facts.append(
                {
                    "emoji": "🌙",
                    "title": "时段偏好",
                    "text": f"你最常在「{seg}」听歌，这个时段占了该年度播放的 {count * 100 // max(1, total_hour_plays)}%",
                }
            )
        # 单曲循环之王（该年分桶播放最多的曲目的单年播放次数）
        if report.top_tracks:
            king = report.top_tracks[0]
            facts.append(
                {
                    "emoji": "🔁",
                    "title": "单曲循环之王",
                    "text": f"「{king['title']}」该年度被播放 {king['count']} 次",
                }
            )
        # 探索 vs 怀旧（服务层用"最早非零年份桶 == 目标年份"推导新听曲目数）
        if summary["distinct"] > 0:
            full_summary = self._stats.year_summary(year)
            new_count = full_summary.get("new_tracks", 0)
            ratio = new_count * 100 // max(1, summary["distinct"])
            facts.append(
                {
                    "emoji": "🧭",
                    "title": "探索 vs 怀旧",
                    "text": f"该年度听过的 {summary['distinct']} 首歌中，有 {new_count} 首（{ratio}%）是当年新听的",
                }
            )
        # 切歌总数
        if summary["skip"] > 0:
            facts.append(
                {
                    "emoji": "⚡",
                    "title": "手速最快",
                    "text": f"该年度共 {_fmt_count(summary['skip'])} 次在歌的前 5% 就切走",
                }
            )
        # 耐听冠军
        if report.top_tracks:
            champion = max(report.top_tracks, key=lambda row: row["seconds"])
            facts.append(
                {
                    "emoji": "🏆",
                    "title": "年度耐听冠军",
                    "text": f"「{champion['title']}」该年度累计听了 {_fmt_hours(champion['seconds'])} 小时",
                }
            )
        # 最狂热的一天
        if report.daily_peak:
            peak = report.daily_peak
            facts.append(
                {
                    "emoji": "📅",
                    "title": "最狂热的一天",
                    "text": f"{peak['date']}：一天播放了 {_fmt_count(peak['count'])} 次",
                }
            )
        return facts

    def _checkpoint_fun_facts(
        self, report: ReportData, summary: dict, loop_king: dict | None, listen_champion: dict | None
    ) -> list[dict]:
        """检查点视图的趣味发现（累计口径，无探索比）。"""
        facts: list[dict] = []
        total_hour_plays = sum(report.hours)
        if total_hour_plays > 0:
            seg_sums: dict[str, int] = {}
            for hour, count in enumerate(report.hours):
                seg = _segment_of_hour(hour)
                seg_sums[seg] = seg_sums.get(seg, 0) + count
            seg, count = max(seg_sums.items(), key=lambda kv: kv[1])
            facts.append(
                {
                    "emoji": "🌙",
                    "title": "时段偏好",
                    "text": f"累计来看，「{seg}」占了播放的 {count * 100 // max(1, total_hour_plays)}%",
                }
            )
        if loop_king:
            facts.append(
                {
                    "emoji": "🔁",
                    "title": "单曲循环之王",
                    "text": f"「{loop_king['title']}」单次会话最高连播 {loop_king['count']} 遍（历史纪录）",
                }
            )
        if summary["skip"] > 0:
            facts.append(
                {
                    "emoji": "⚡",
                    "title": "手速最快",
                    "text": f"累计 {_fmt_count(summary['skip'])} 次在歌的前 5% 就切走",
                }
            )
        if listen_champion:
            facts.append(
                {
                    "emoji": "🏆",
                    "title": "耐听冠军",
                    "text": f"「{listen_champion['title']}」累计听了 {_fmt_hours(listen_champion['seconds'])} 小时",
                }
            )
        if report.daily_peak:
            peak = report.daily_peak
            facts.append(
                {
                    "emoji": "📅",
                    "title": "最狂热的一天",
                    "text": f"{peak['date']}：一天播放了 {_fmt_count(peak['count'])} 次",
                }
            )
        return facts


def apply_comparison(primary: ReportData, secondary: ReportData | None) -> None:
    """将副数据源的对比增量写入主 ReportData（原地修改）。

    - KPI：追加 delta_pct（百分比变化，None 表示不可比）与 delta_text。
    - Top10：追加 delta 字段（"up"/"down"/"new"/None）与 delta_rank（排名变化量）。
    """
    if secondary is None:
        return
    second_by_key = {kpi["key"]: kpi for kpi in secondary.kpis}
    for kpi in primary.kpis:
        base = second_by_key.get(kpi["key"])
        if base is None or base["value"] <= 0:
            kpi["delta_pct"] = None
            kpi["delta_text"] = ""
            continue
        delta_pct = (kpi["value"] - base["value"]) * 100.0 / base["value"]
        kpi["delta_pct"] = delta_pct
        kpi["delta_text"] = f"{delta_pct:+.1f}%"
    second_rank = {row["track_id"]: idx for idx, row in enumerate(secondary.top_tracks)}
    for idx, row in enumerate(primary.top_tracks):
        base_idx = second_rank.get(row["track_id"])
        if base_idx is None:
            row["delta"] = "new"
            row["delta_rank"] = 0
        else:
            change = base_idx - idx
            row["delta"] = "up" if change > 0 else ("down" if change < 0 else None)
            row["delta_rank"] = change
