from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.entities import Playlist, Track
from app.models.library_store import LibraryStore
from app.services.library_service import (
    ALL_SONGS_ID,
    FAVORITES_ID,
    LibraryService,
)
from app.services.metadata_service import MetadataService
from app.services.playback_stats_service import PlaybackStatsService

# ============ 公共构造与辅助 ============


def _make_service(tmp_path: Path) -> LibraryService:
    """构造一个隔离的 LibraryService（数据落到 tmp_path，不触碰真实 data/）。"""
    store = LibraryStore(tmp_path / "library.json")
    return LibraryService(store, MetadataService())


def _make_track(
    track_id: str,
    path: str,
    *,
    title: str = "测试歌曲",
    artist: str = "测试歌手",
    album: str = "测试专辑",
    duration_sec: float = 180.0,
    added_at: float = 1.0,
    source_sha256: str = "",
    source_storage_relpath: str = "",
) -> Track:
    """构造单个 Track 实例（仅赋字段，不读盘）。"""
    return Track(
        id=track_id,
        path=path,
        title=title,
        artist=artist,
        album=album,
        duration_sec=duration_sec,
        added_at=added_at,
        source_sha256=source_sha256,
        source_storage_relpath=source_storage_relpath,
    )


def _preload(
    svc: LibraryService,
    tracks: list[Track],
    playlists: list[Playlist] | None = None,
    *,
    active: str | None = None,
    quick: bool = True,
) -> None:
    """以 quick 模式预载曲库，跳过自动清理，便于在受控状态下调用被测方法。"""
    track_map = {t.id: t for t in tracks}
    pl_map: dict[str, Playlist] = {}
    if playlists:
        for pl in playlists:
            pl_map[pl.id] = pl
    svc.load_preloaded(track_map, pl_map, active, quick=quick)


# ============ _normalize_relpath ============


def test_normalize_relpath_converts_backslashes():
    assert LibraryService._normalize_relpath("a\\b\\c.flac") == "a/b/c.flac"


def test_normalize_relpath_strips_dot_slash_and_edges():
    assert LibraryService._normalize_relpath("./a/b/") == "a/b"
    assert LibraryService._normalize_relpath("/a/b/") == "a/b"


def test_normalize_relpath_empty_and_whitespace():
    assert LibraryService._normalize_relpath("") == ""
    assert LibraryService._normalize_relpath("   ") == ""
    assert LibraryService._normalize_relpath(None) == ""  # type: ignore[arg-type]


def test_normalize_relpath_keeps_internal_slashes():
    assert LibraryService._normalize_relpath("dir/sub/file.flac") == "dir/sub/file.flac"


# ============ _deduplicate_tracks ============


def test_deduplicate_keeps_newest_same_dedupe_key(tmp_path):
    """相同 dedupe_key（文件名+大小+时长）的两条曲目保留 added_at 较大者。"""
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    # 同名 + 同内容（同 size） + 同 duration → 同 dedupe_key
    (d1 / "song.flac").write_bytes(b"x" * 64)
    (d2 / "song.flac").write_bytes(b"x" * 64)

    older = _make_track("t-old", str(d1 / "song.flac"), added_at=100.0)
    newer = _make_track("t-new", str(d2 / "song.flac"), added_at=200.0)

    svc = _make_service(tmp_path)
    _preload(svc, [older, newer])

    changed = svc._deduplicate_tracks()

    assert changed is True
    assert not svc.has_track("t-old")  # 旧者被移除
    assert svc.has_track("t-new")  # 新者保留


def test_deduplicate_no_duplicates_returns_false(tmp_path):
    f = tmp_path / "a.flac"
    f.write_bytes(b"a")
    t1 = _make_track("t1", str(f), added_at=1.0)
    t2 = _make_track("t2", str(tmp_path / "b.flac"), added_at=2.0)
    (tmp_path / "b.flac").write_bytes(b"b")

    svc = _make_service(tmp_path)
    _preload(svc, [t1, t2])

    assert svc._deduplicate_tracks() is False
    assert len(svc.track_ids()) == 2


def test_deduplicate_remaps_playlist_references(tmp_path):
    """歌单引用被移除曲目时，引用应改指向保留曲目。"""
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    (d1 / "song.flac").write_bytes(b"z" * 32)
    (d2 / "song.flac").write_bytes(b"z" * 32)

    older = _make_track("t-old", str(d1 / "song.flac"), added_at=1.0)
    newer = _make_track("t-new", str(d2 / "song.flac"), added_at=2.0)
    pl = Playlist(id="pl1", name="歌单1", track_ids=["t-old"])

    svc = _make_service(tmp_path)
    _preload(svc, [older, newer], [pl])

    svc._deduplicate_tracks()

    assert svc.find_playlist("pl1").track_ids == ["t-new"]


def test_deduplicate_same_sha256_different_dedupe_key_not_merged(tmp_path):
    """相同 source_sha256 但 dedupe_key 不同时不会被 _deduplicate_tracks 合并（锁定当前行为）。"""
    f1 = tmp_path / "alpha.flac"
    f2 = tmp_path / "beta.flac"
    f1.write_bytes(b"a" * 10)
    f2.write_bytes(b"b" * 20)  # 不同名 + 不同 size → 不同 dedupe_key

    t1 = _make_track("t1", str(f1), added_at=1.0, source_sha256="samehash")
    t2 = _make_track("t2", str(f2), added_at=2.0, source_sha256="samehash")

    svc = _make_service(tmp_path)
    _preload(svc, [t1, t2])

    assert svc._deduplicate_tracks() is False
    assert {t1.id, t2.id} == svc.track_ids()


# ============ _drop_missing_tracks ============


def test_drop_missing_tracks_removes_only_missing(tmp_path):
    exists = tmp_path / "exists.flac"
    exists.write_bytes(b"data")
    t_ok = _make_track("t-ok", str(exists))
    t_missing = _make_track("t-missing", str(tmp_path / "nope.flac"))

    svc = _make_service(tmp_path)
    _preload(svc, [t_ok, t_missing])

    changed = svc._drop_missing_tracks()

    assert changed is True
    assert svc.has_track("t-ok")
    assert not svc.has_track("t-missing")


def test_drop_missing_tracks_all_present_returns_false(tmp_path):
    f1 = tmp_path / "a.flac"
    f2 = tmp_path / "b.flac"
    f1.write_bytes(b"a")
    f2.write_bytes(b"b")
    svc = _make_service(tmp_path)
    _preload(svc, [_make_track("t1", str(f1)), _make_track("t2", str(f2))])

    assert svc._drop_missing_tracks() is False
    assert len(svc.track_ids()) == 2


# ============ _normalize_playlist_tracks ============


def test_normalize_playlist_tracks_drops_invalid_refs(tmp_path):
    f = tmp_path / "real.flac"
    f.write_bytes(b"x")
    t = _make_track("t-real", str(f))
    # 引用一条存在 + 一条不存在的曲目
    pl = Playlist(id="pl1", name="歌单1", track_ids=["t-real", "t-ghost"])

    svc = _make_service(tmp_path)
    _preload(svc, [t], [pl])

    changed = svc._normalize_playlist_tracks()

    assert changed is True
    assert svc.find_playlist("pl1").track_ids == ["t-real"]


def test_normalize_playlist_tracks_rebuilds_all_songs(tmp_path):
    """ALL_SONGS 歌单应被重建为当前全部曲目 id。"""
    f1 = tmp_path / "a.flac"
    f2 = tmp_path / "b.flac"
    f1.write_bytes(b"a")
    f2.write_bytes(b"b")
    t1 = _make_track("t1", str(f1))
    t2 = _make_track("t2", str(f2))

    svc = _make_service(tmp_path)
    _preload(svc, [t1, t2])
    # 手动清空 ALL_SONGS 以触发重建
    svc.find_playlist(ALL_SONGS_ID).track_ids = []

    svc._normalize_playlist_tracks()

    assert set(svc.find_playlist(ALL_SONGS_ID).track_ids) == {"t1", "t2"}


def test_normalize_playlist_tracks_dedupes_duplicates(tmp_path):
    f = tmp_path / "real.flac"
    f.write_bytes(b"x")
    t = _make_track("t-real", str(f))
    pl = Playlist(id="pl1", name="歌单1", track_ids=["t-real", "t-real", "t-real"])

    svc = _make_service(tmp_path)
    _preload(svc, [t], [pl])

    svc._normalize_playlist_tracks()

    assert svc.find_playlist("pl1").track_ids == ["t-real"]


# ============ 歌单 CRUD ============


def test_create_playlist_persists_with_clean_name(tmp_path):
    svc = _make_service(tmp_path)
    _preload(svc, [])

    pl = svc.create_playlist("  我的歌单  ")

    assert pl.name == "我的歌单"
    assert svc.find_playlist(pl.id) is not None
    # 库存文件应已写入（save 被调用）
    assert (tmp_path / "library.json").exists()


def test_rename_playlist_protects_system_playlists(tmp_path):
    svc = _make_service(tmp_path)
    _preload(svc, [])

    svc.rename_playlist(ALL_SONGS_ID, "不应被改")
    svc.rename_playlist(FAVORITES_ID, "不应被改")

    assert svc.find_playlist(ALL_SONGS_ID).name == "全部歌曲"
    assert svc.find_playlist(FAVORITES_ID).name == "我喜欢"


def test_rename_playlist_user_playlist(tmp_path):
    svc = _make_service(tmp_path)
    _preload(svc, [])
    pl = svc.create_playlist("原名")

    svc.rename_playlist(pl.id, "新名")

    assert svc.find_playlist(pl.id).name == "新名"


def test_delete_playlist_protects_system_and_removes_orphans(tmp_path):
    """删除用户歌单应移除仅被其引用的孤立曲目；系统歌单不可删。"""
    f = tmp_path / "only_in_pl.flac"
    f.write_bytes(b"x")
    t = _make_track("t-orphan", str(f))
    pl = Playlist(id="pl1", name="歌单1", track_ids=["t-orphan"])

    svc = _make_service(tmp_path)
    _preload(svc, [t], [pl])

    svc.delete_playlist("pl1")

    assert svc.find_playlist("pl1") is None
    assert not svc.has_track("t-orphan")  # 孤立曲目被清理

    # 系统歌单删除为 no-op
    svc.delete_playlist(ALL_SONGS_ID)
    svc.delete_playlist(FAVORITES_ID)
    assert svc.find_playlist(ALL_SONGS_ID) is not None
    assert svc.find_playlist(FAVORITES_ID) is not None


def test_toggle_favorite_roundtrip(tmp_path):
    f = tmp_path / "a.flac"
    f.write_bytes(b"a")
    t = _make_track("t1", str(f))
    svc = _make_service(tmp_path)
    _preload(svc, [t])

    assert svc.is_favorite("t1") is False
    assert svc.toggle_favorite("t1") is True
    assert svc.is_favorite("t1") is True
    assert "t1" in svc.find_playlist(FAVORITES_ID).track_ids
    assert svc.toggle_favorite("t1") is False  # 再次切换取消
    assert "t1" not in svc.find_playlist(FAVORITES_ID).track_ids


# ============ export_playlist_file ============


def test_export_playlist_file_schema_and_track_count(tmp_path):
    f = tmp_path / "music"
    f.mkdir()
    audio = f / "song.flac"
    audio.write_bytes(b"audio-bytes")
    t = _make_track(
        "t1",
        str(audio),
        source_sha256="abc123",
        source_storage_relpath="music/song.flac",
    )
    pl = Playlist(id="pl1", name="导出歌单", track_ids=["t1"])
    pl.source_database_location = str(tmp_path)  # 以 tmp_path 作为 db_root

    svc = _make_service(tmp_path)
    _preload(svc, [t], [pl])
    stats = PlaybackStatsService(tmp_path)

    out = svc.export_playlist_file("pl1", tmp_path / "out", stats)

    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    # 锁定顶层 schema 字段
    assert payload["schema"] == "musearc_playlist_export_v2"
    assert payload["playlist_name"] == "导出歌单"
    assert payload["track_count"] == 1
    assert "playlist_hash" in payload
    assert "exported_at" in payload
    assert "database_location" in payload
    # 统计汇总结构
    summary = payload["stats_summary"]
    for key in (
        "total_play_count",
        "total_manual_play_count",
        "total_complete_play_count",
        "total_play_seconds",
        "total_early_skip_count",
        "updated_at",
    ):
        assert key in summary
    # 单曲目结构
    track_obj = payload["tracks"][0]
    for key in (
        "track_id",
        "storage_relpath",
        "title",
        "artist",
        "album",
        "lyrics",
        "lyrics_storage_relpath",
        "source_sha256",
        "stats",
    ):
        assert key in track_obj
    # 曲目级统计字段
    for key in (
        "play_count",
        "manual_play_count",
        "complete_play_count",
        "play_seconds",
        "early_skip_count",
        "peak_session_play_count",
        "peak_session_play_at",
    ):
        assert key in track_obj["stats"]


def test_export_playlist_file_empty_raises(tmp_path):
    svc = _make_service(tmp_path)
    _preload(svc, [])
    stats = PlaybackStatsService(tmp_path)

    with pytest.raises(ValueError):
        svc.export_playlist_file(ALL_SONGS_ID, tmp_path / "out", stats)
