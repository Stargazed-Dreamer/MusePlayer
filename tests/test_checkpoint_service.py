"""统计检查点服务测试（增删查改名 + 跨年自动保存）。"""

from __future__ import annotations

from app.services.playback_stats_service import PlaybackStatsService
from app.services.stats_checkpoint_service import StatsCheckpointService


def _make_snapshot(tmp_data_dir) -> tuple[PlaybackStatsService, dict]:
    """构造一个带两条曲目记录的统计服务及其快照。"""
    service = PlaybackStatsService(tmp_data_dir)
    service.record_play_start("t1", active_request=True)
    service.record_play_progress("t1", 100.0, 200.0)
    service.record_play_start("t2", active_request=False)
    return service, service.snapshot_payload()


def test_save_and_list_roundtrip(tmp_data_dir):
    """保存的检查点应可列出并加载，元信息一致。"""
    _, snapshot = _make_snapshot(tmp_data_dir)
    svc = StatsCheckpointService(tmp_data_dir)
    meta = svc.save(snapshot, name="测试快照")
    assert meta.track_count == 2
    assert meta.auto is False

    metas = svc.list_checkpoints()
    assert len(metas) == 1 and metas[0].name == "测试快照"
    payload = svc.load(meta.id)
    assert payload is not None
    assert payload["tracks"]["t1"]["play_count"] == 1
    assert "aggregate" in payload


def test_default_name_year_month(tmp_data_dir):
    """未命名时使用"年份+月份"默认名。"""
    _, snapshot = _make_snapshot(tmp_data_dir)
    svc = StatsCheckpointService(tmp_data_dir)
    meta = svc.save(snapshot)
    import time

    t = time.localtime()
    assert meta.name == f"{t.tm_year}年{t.tm_mon}月"


def test_rename_only_changes_name(tmp_data_dir):
    """改名只改显示名称，快照数据不变。"""
    _, snapshot = _make_snapshot(tmp_data_dir)
    svc = StatsCheckpointService(tmp_data_dir)
    meta = svc.save(snapshot, name="旧名")
    assert svc.rename(meta.id, "毕业那晚") is True
    payload = svc.load(meta.id)
    assert payload["name"] == "毕业那晚"
    assert payload["tracks"]["t1"]["play_count"] == 1


def test_rename_empty_or_missing(tmp_data_dir):
    """空名或不存在的检查点改名应失败。"""
    _, snapshot = _make_snapshot(tmp_data_dir)
    svc = StatsCheckpointService(tmp_data_dir)
    meta = svc.save(snapshot, name="x")
    assert svc.rename(meta.id, "   ") is False
    assert svc.rename("nonexistent", "y") is False


def test_delete_checkpoint(tmp_data_dir):
    """删除后不再列出，重复删除返回 False。"""
    _, snapshot = _make_snapshot(tmp_data_dir)
    svc = StatsCheckpointService(tmp_data_dir)
    meta = svc.save(snapshot, name="待删")
    assert svc.delete(meta.id) is True
    assert svc.list_checkpoints() == []
    assert svc.delete(meta.id) is False


def test_corrupt_file_skipped(tmp_data_dir):
    """损坏的检查点文件应被静默跳过。"""
    svc = StatsCheckpointService(tmp_data_dir)
    (tmp_data_dir / "checkpoints" / "broken.json").write_text("{not json", encoding="utf-8")
    assert svc.list_checkpoints() == []
    assert svc.load("broken") is None


def test_auto_save_on_year_rolled(tmp_data_dir):
    """跨年（上次 2025 → 本次 2026）应自动保存"2025 年度报告"并带 auto 标签。"""
    _, snapshot = _make_snapshot(tmp_data_dir)
    svc = StatsCheckpointService(tmp_data_dir)
    meta, latest = svc.auto_save_if_year_rolled(snapshot, last_seen_year=2025, current_year=2026)
    assert meta is not None and meta.name == "2025 年度报告" and meta.auto is True
    assert latest == 2026


def test_auto_save_same_year_and_first_run(tmp_data_dir):
    """同年或首次运行（last_seen_year=0）不保存。"""
    _, snapshot = _make_snapshot(tmp_data_dir)
    svc = StatsCheckpointService(tmp_data_dir)
    meta, latest = svc.auto_save_if_year_rolled(snapshot, last_seen_year=2026, current_year=2026)
    assert meta is None and latest == 2026
    meta, latest = svc.auto_save_if_year_rolled(snapshot, last_seen_year=0, current_year=2026)
    assert meta is None and latest == 2026
    assert svc.list_checkpoints() == []


def test_settings_roundtrip_new_fields(tmp_data_dir):
    """Settings 新字段应随 settings.json 持久化。"""
    from app.models.entities import Settings
    from app.models.settings_store import SettingsStore

    store = SettingsStore(tmp_data_dir)
    settings = Settings()
    settings.checkpoint_auto_year_save = False
    settings.stats_last_seen_year = 2025
    store.save(settings)

    loaded = store.load()
    assert loaded.checkpoint_auto_year_save is False
    assert loaded.stats_last_seen_year == 2025
