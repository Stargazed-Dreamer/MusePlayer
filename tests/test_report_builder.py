"""报告聚合器与导出器测试（纯逻辑，无 Qt）。"""

from __future__ import annotations

import time

from app.services.playback_stats_service import PlaybackStatsService
from app.services.stats_checkpoint_service import StatsCheckpointService
from app.services.stats_report_builder import ReportSource, StatsReportBuilder, apply_comparison
from app.ui.stats_report_exporter import export_report_html, export_report_text


def _make_builder(tmp_data_dir, monkeypatch, years=(2025, 2026)):
    """构造带两年模拟数据的 builder。返回 (builder, stats, checkpoints)。"""
    lookup = lambda tid: (f"标题-{tid}", f"歌手-{tid[0]}", f"专辑-{tid[0]}")  # noqa: E731
    stats = PlaybackStatsService(tmp_data_dir)
    checkpoints = StatsCheckpointService(tmp_data_dir)
    for year in years:
        fixed = time.struct_time((year, 4, 18, 23, 0, 0, 0, 0, -1))
        monkeypatch.setattr("app.services.playback_stats_service.time.localtime", lambda ts=None, f=fixed: f)
        for tid in ("a1", "a2", "b1"):
            stats.record_play_start(tid, active_request=True)
            stats.record_play_progress(tid, 120.0, 240.0)
            stats.record_complete_play(tid)
    return StatsReportBuilder(stats, checkpoints, lookup), stats, checkpoints


def test_year_report_structure(tmp_data_dir, monkeypatch):
    """年度报告应含 KPI、榜单与趣味发现。"""
    builder, stats, _ = _make_builder(tmp_data_dir, monkeypatch)
    report = builder.build(ReportSource(kind="year", year=2026, label="2026 年"))
    assert report.title.startswith("2026")
    assert len(report.kpis) == 4
    assert report.kpis[0]["value"] == 3  # 三首各一次
    assert len(report.top_tracks) == 3
    assert report.top_tracks[0]["artist"].startswith("歌手-")
    assert len(report.top_artists) >= 1
    assert {fact["title"] for fact in report.fun_facts} >= {"时段偏好", "单曲循环之王", "探索 vs 怀旧"}


def test_year_report_exploration_ratio(tmp_data_dir, monkeypatch):
    """2026 首次出现的曲目应计为新听（探索比 > 0）。"""
    builder, _, _ = _make_builder(tmp_data_dir, monkeypatch)
    report = builder.build(ReportSource(kind="year", year=2026, label="2026 年"))
    fact = next(f for f in report.fun_facts if f["title"] == "探索 vs 怀旧")
    assert "3 首" in fact["text"]  # 三首都是 2026 首听


def test_checkpoint_report_and_comparison(tmp_data_dir, monkeypatch):
    """检查点报告为累计口径，对比增量正确标注。"""
    builder, stats, checkpoints = _make_builder(tmp_data_dir, monkeypatch)
    meta = checkpoints.save(stats.snapshot_payload(), name="毕业那晚")
    primary = builder.build(ReportSource(kind="year", year=2026, label="2026 年"))
    secondary = builder.build(ReportSource(kind="checkpoint", checkpoint_id=meta.id, label="毕业那晚"))
    assert secondary.is_cumulative is True
    assert secondary.kpis[0]["value"] == 6  # 两年 × 三首

    apply_comparison(primary, secondary)
    assert primary.kpis[0]["delta_pct"] is not None
    assert primary.kpis[0]["delta_pct"] < 0  # 2026 年 3 次 < 累计 6 次
    # 同一首歌两年都听：检查点里也有 → 非 NEW
    assert all(row.get("delta") in (None, "up", "down") for row in primary.top_tracks)


def test_comparison_rank_changes(tmp_data_dir, monkeypatch):
    """主副均为年份时，播放次数差异应产生 NEW 或排名变化标记。"""
    builder, stats, _ = _make_builder(tmp_data_dir, monkeypatch, years=(2026,))
    # 2025 无数据 → 主副均为 2026 时 delta 全为 None/up/down；构造副为空年
    primary = builder.build(ReportSource(kind="year", year=2026, label="2026 年"))
    secondary = builder.build(ReportSource(kind="year", year=2025, label="2025 年"))
    apply_comparison(primary, secondary)
    assert all(row.get("delta") == "new" for row in primary.top_tracks)
    # KPI 副数据为 0 → 不可比
    assert primary.kpis[0]["delta_pct"] is None


def test_self_comparison_rejected_by_ui_layer(tmp_data_dir, monkeypatch):
    """同源对比（UI 层已拦截）此处仅验证 builder 对同源输入不崩溃。"""
    builder, _, _ = _make_builder(tmp_data_dir, monkeypatch)
    primary = builder.build(ReportSource(kind="year", year=2026, label="2026 年"))
    apply_comparison(primary, primary)
    assert primary.kpis[0]["delta_pct"] == 0.0


def test_empty_checkpoint_source(tmp_data_dir, monkeypatch):
    """不存在的检查点 ID 返回空报告而非异常。"""
    builder, _, checkpoints = _make_builder(tmp_data_dir, monkeypatch)
    report = builder.build(ReportSource(kind="checkpoint", checkpoint_id="ghost", label="不存在"))
    assert report.kpis == []


def test_export_html_and_text(tmp_data_dir, monkeypatch):
    """导出 HTML 应为自包含文档，文本应含核心数据。"""
    builder, _, checkpoints = _make_builder(tmp_data_dir, monkeypatch)
    report = builder.build(ReportSource(kind="year", year=2026, label="2026 年"))
    html = export_report_html(report)
    assert html.startswith("<!DOCTYPE html>")
    assert "月度听歌节奏" in html and "Top 10" in html
    text = export_report_text(report)
    assert "2026" in text and "播放次数" in text and "趣味发现" in text
