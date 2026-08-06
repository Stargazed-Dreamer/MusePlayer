from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.playback_stats_service import PlaybackStatsEntry, PlaybackStatsService


# ============ 初始化 ============

def test_stats_service_init_empty(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    assert svc.export_stats_for_track("any") is None


def test_stats_service_load_missing_file(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    assert svc.export_stats_for_track("t1") is None


def test_stats_service_load_corrupt_json(tmp_path):
    p = tmp_path / "playback_stats.json"
    p.write_text("not json", encoding="utf-8")
    svc = PlaybackStatsService(tmp_path)
    assert svc.export_stats_for_track("t1") is None


def test_stats_service_load_invalid_payload_structure(tmp_path):
    p = tmp_path / "playback_stats.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    svc = PlaybackStatsService(tmp_path)
    assert svc.export_stats_for_track("t1") is None


# ============ record_play_start ============

def test_record_play_start_active(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_start("t1", active_request=True)
    stats = svc.export_stats_for_track("t1")
    assert stats is not None
    assert stats["play_count"] == 1
    assert stats["manual_play_count"] == 1


def test_record_play_start_passive(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_start("t1", active_request=False)
    stats = svc.export_stats_for_track("t1")
    assert stats["play_count"] == 1
    assert stats["manual_play_count"] == 0


def test_record_play_start_empty_track_id_ignored(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_start("", active_request=True)
    svc.record_play_start("   ", active_request=True)
    assert svc.export_stats_for_track("") is None


# ============ record_complete_play / record_early_skip ============

def test_record_complete_play(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_complete_play("t1")
    stats = svc.export_stats_for_track("t1")
    assert stats["complete_play_count"] == 1


def test_record_early_skip(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_early_skip("t1")
    stats = svc.export_stats_for_track("t1")
    assert stats["early_skip_count"] == 1


def test_record_complete_play_empty_track_id_ignored(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_complete_play("")
    assert svc.export_stats_for_track("") is None


# ============ record_play_progress ============

def test_record_play_progress_accumulates_seconds(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_progress("t1", 30.0, 300.0)
    svc.record_play_progress("t1", 15.0, 300.0)
    stats = svc.export_stats_for_track("t1")
    assert stats["play_seconds"] == 45


def test_record_play_progress_does_not_affect_peak(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_progress("t1", 30.0, 300.0)
    stats = svc.export_stats_for_track("t1")
    assert stats["peak_session_play_count"] == 0


def test_record_play_progress_negative_delta_ignored(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_progress("t1", -10.0, 300.0)
    assert svc.export_stats_for_track("t1") is None


def test_record_play_progress_zero_duration(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_progress("t1", 30.0, 0.0)
    stats = svc.export_stats_for_track("t1")
    assert stats["play_seconds"] == 30


# ============ 累积统计 ============

def test_stats_accumulation_multiple_plays(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    for _ in range(3):
        svc.record_play_start("t1", active_request=True)
    svc.record_complete_play("t1")
    svc.record_complete_play("t1")
    svc.record_early_skip("t1")
    stats = svc.export_stats_for_track("t1")
    assert stats["play_count"] == 3
    assert stats["manual_play_count"] == 3
    assert stats["complete_play_count"] == 2
    assert stats["early_skip_count"] == 1


def test_stats_multiple_tracks_independent(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_start("t1", active_request=True)
    svc.record_play_start("t1", active_request=True)
    svc.record_play_start("t2", active_request=True)
    assert svc.export_stats_for_track("t1")["play_count"] == 2
    assert svc.export_stats_for_track("t2")["play_count"] == 1


def test_peak_session_play_count_updates(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    for _ in range(5):
        svc.record_play_start("t1", active_request=True)
    stats = svc.export_stats_for_track("t1")
    assert stats["peak_session_play_count"] == 5
    assert stats["peak_session_play_at"] > 0


# ============ save_if_dirty / load 往返 ============

def test_save_if_dirty_roundtrip(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_start("t1", active_request=True)
    svc.record_complete_play("t1")
    svc.save_if_dirty()
    # 重新加载，验证持久化
    svc2 = PlaybackStatsService(tmp_path)
    stats = svc2.export_stats_for_track("t1")
    assert stats is not None
    assert stats["play_count"] == 1
    assert stats["complete_play_count"] == 1


def test_save_if_dirty_no_change_does_not_write(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.save_if_dirty()  # 无数据，不应写文件
    assert not (tmp_path / "playback_stats.json").exists()


def test_save_if_dirty_resets_dirty_flag(tmp_path):
    """save_if_dirty 后脏标志应被清除：无新改动时再次调用不应改变文件内容。"""
    stats_file = tmp_path / "playback_stats.json"
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_start("t1", active_request=True)
    svc.save_if_dirty()
    content_after_first = stats_file.read_text(encoding="utf-8")
    # 无新改动，再次 save_if_dirty 不应触发写入
    svc.save_if_dirty()
    content_after_second = stats_file.read_text(encoding="utf-8")
    assert content_after_first == content_after_second


def test_save_if_dirty_after_new_change_writes_again(tmp_path):
    """新增改动后脏标志应重新置位，save_if_dirty 应再次写入。"""
    stats_file = tmp_path / "playback_stats.json"
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_start("t1", active_request=True)
    svc.save_if_dirty()
    content_before = stats_file.read_text(encoding="utf-8")
    # 新改动 → 脏 → 写入
    svc.record_complete_play("t1")
    svc.save_if_dirty()
    content_after = stats_file.read_text(encoding="utf-8")
    assert content_before != content_after


# ============ remove_track / reset_early_skip_count ============

def test_remove_track(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_play_start("t1", active_request=True)
    assert svc.export_stats_for_track("t1") is not None
    svc.remove_track("t1")
    assert svc.export_stats_for_track("t1") is None


def test_remove_track_nonexistent_no_error(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.remove_track("nonexistent")  # 不应抛异常


def test_reset_early_skip_count(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.record_early_skip("t1")
    assert svc.export_stats_for_track("t1")["early_skip_count"] == 1
    svc.reset_early_skip_count("t1")
    assert svc.export_stats_for_track("t1")["early_skip_count"] == 0


def test_reset_early_skip_count_nonexistent_no_error(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.reset_early_skip_count("nonexistent")  # 不应抛异常


def test_reset_early_skip_count_empty_id_ignored(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    svc.reset_early_skip_count("")  # 不应抛异常


# ============ 不存在的 track_id ============

def test_export_stats_for_nonexistent_returns_none(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    assert svc.export_stats_for_track("nonexistent") is None


def test_export_stats_empty_track_id_returns_none(tmp_path):
    svc = PlaybackStatsService(tmp_path)
    assert svc.export_stats_for_track("") is None


# ============ PlaybackStatsEntry 序列化 ============

def test_entry_to_dict_from_dict_roundtrip():
    entry = PlaybackStatsEntry(
        track_id="t1", play_count=5, active_play_count=3, early_skip_count=1,
        complete_play_count=2, played_seconds_total=120.0, played_percent_total=40.0,
        peak_session_play_count=5, peak_session_play_at=1700000000.0, updated_at=1700000001.0,
    )
    d = entry.to_dict()
    restored = PlaybackStatsEntry.from_dict(d)
    assert restored == entry


def test_entry_from_dict_clamps_negative_values():
    entry = PlaybackStatsEntry.from_dict({
        "track_id": "t1", "play_count": -5, "played_seconds_total": -10.0,
        "early_skip_count": -1,
    })
    assert entry.play_count == 0
    assert entry.played_seconds_total == 0.0
    assert entry.early_skip_count == 0
