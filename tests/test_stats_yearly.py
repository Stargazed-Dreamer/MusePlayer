"""年分桶 + 全局聚合块测试（年度报告数据层）。"""

from __future__ import annotations

import json
import time

from app.services.playback_stats_service import PlaybackStatsService


def _patch_year(monkeypatch, year: int, month: int = 6, day: int = 15, hour: int = 10) -> None:
    """固定 record_* 写入的本地时间到指定年月日时（只影响分桶逻辑）。"""
    fixed = time.struct_time((year, month, day, hour, 0, 0, 0, 0, -1))

    monkeypatch.setattr("app.services.playback_stats_service.time.localtime", lambda ts=None: fixed)


def test_yearly_bucket_recorded_on_play(tmp_data_dir, monkeypatch):
    """播放记录应同时累计到当年分桶。"""
    _patch_year(monkeypatch, 2026)
    service = PlaybackStatsService(tmp_data_dir)
    service.record_play_start("t1", active_request=True)
    service.record_play_progress("t1", 120.0, 240.0)
    service.record_complete_play("t1")
    service.record_early_skip("t2")

    bucket = service.year_bucket_entries(2026)["t1"]
    assert bucket[0] == 1  # play
    assert bucket[1] == 1  # active
    assert bucket[2] == 1  # complete
    assert bucket[3] == 120.0  # seconds
    assert service.year_bucket_entries(2026)["t2"][4] == 1  # early skip


def test_yearly_bucket_persists_and_roundtrips(tmp_data_dir, monkeypatch):
    """分桶应随 save_if_dirty 落盘并在重载后保留。"""
    _patch_year(monkeypatch, 2025)
    service = PlaybackStatsService(tmp_data_dir)
    service.record_play_start("t1", active_request=False)
    service.record_play_progress("t1", 60.0, 100.0)
    service.save_if_dirty()

    reloaded = PlaybackStatsService(tmp_data_dir)
    bucket = reloaded.year_bucket_entries(2025)["t1"]
    assert bucket[0] == 1
    assert bucket[3] == 60.0


def test_legacy_file_without_yearly_migrates(tmp_data_dir):
    """旧格式文件（无 yearly/aggregate）加载不报错且累计值保留。"""
    legacy = {"tracks": {"t1": {"play_count": 7, "played_seconds_total": 300.0}}}
    (tmp_data_dir / "playback_stats.json").write_text(json.dumps(legacy), encoding="utf-8")
    service = PlaybackStatsService(tmp_data_dir)
    assert service._entries["t1"].play_count == 7
    assert service._entries["t1"].yearly == {}
    assert service.available_years() == []


def test_aggregate_monthly_hours_daily_peak(tmp_data_dir, monkeypatch):
    """聚合块应记录月度、小时与最狂热一天。"""
    _patch_year(monkeypatch, 2026, month=4, day=18, hour=23)
    service = PlaybackStatsService(tmp_data_dir)
    for _ in range(3):
        service.record_play_start("t1", active_request=True)
    summary = service.year_summary(2026)
    assert summary["play_count"] == 3
    assert summary["active_play_count"] == 3
    monthly = service.monthly_series(2026)
    assert monthly["play"][3] == 3  # 4 月
    assert sum(service.hours_series(2026)) == 3
    assert service.hours_series(2026)[23] == 3
    peak = service.daily_peak(2026)
    assert peak is not None and peak["date"] == "2026-04-18" and peak["count"] == 3


def test_new_tracks_derivation(tmp_data_dir, monkeypatch):
    """最早非零年份 == 目标年份的曲目应计为当年新听。"""
    _patch_year(monkeypatch, 2025)
    service = PlaybackStatsService(tmp_data_dir)
    service.record_play_start("old", active_request=True)
    service.save_if_dirty()
    _patch_year(monkeypatch, 2026)
    service.record_play_start("old", active_request=True)  # 老歌 2026 再听
    service.record_play_start("fresh", active_request=True)  # 2026 首听
    summary = service.year_summary(2026)
    assert summary["new_tracks"] == 1  # 只有 fresh
    assert summary["distinct_tracks"] == 2


def test_year_top_entries_sorted(tmp_data_dir, monkeypatch):
    """年 Top 应按播放次数降序。"""
    _patch_year(monkeypatch, 2026)
    service = PlaybackStatsService(tmp_data_dir)
    for _ in range(3):
        service.record_play_start("a", active_request=True)
    for _ in range(9):
        service.record_play_start("b", active_request=True)
    rows = service.year_top_entries(2026, limit=10)
    assert rows[0][0] == "b" and rows[0][1][0] == 9


def test_snapshot_payload_deep_copies(tmp_data_dir, monkeypatch):
    """快照应为深拷贝：修改原服务不影响快照。"""
    _patch_year(monkeypatch, 2026)
    service = PlaybackStatsService(tmp_data_dir)
    service.record_play_start("t1", active_request=True)
    snapshot = service.snapshot_payload()
    service.record_play_start("t1", active_request=True)
    service.record_play_start("t2", active_request=True)
    assert snapshot["tracks"]["t1"]["play_count"] == 1
    assert "t2" not in snapshot["tracks"]
    assert "aggregate" in snapshot
