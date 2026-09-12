"""Muse 歌单导入路径测试（`MusePlaylistImporter`，*.muse_playlist.json / 运行时 payload）。

覆盖此前无测试的导入核心逻辑：
- 文件校验（不存在 / 后缀 / schema）；
- 占位曲目创建（音频文件缺失时用导入元数据兜底）与 source_* 字段落位；
- 歌词主路径 + 额外歌词数组解析；
- db_root 解析（默认 / 相对 / 绝对 database_location）；
- 重复导入去重（同文件复用歌单，不产生重复曲目）；
- 已有曲目按 source_track_id / source_sha256 复用（不新建）；
- 再导入时孤立曲目清理；
- 运行时 payload 的虚拟源文件与内容哈希 ID 确定性。

全部数据落在 tmp_path，不触碰真实 data/，也不依赖真实音频文件解码。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.entities import Track
from app.models.library_store import LibraryStore
from app.services.library_service import ALL_SONGS_ID, LibraryService
from app.services.metadata_service import MetadataService

PLAYLIST_HASH = "abc123def4567890abcdef"


# ============ 公共构造与辅助 ============


def _make_service(tmp_path: Path) -> LibraryService:
    """构造一个隔离的 LibraryService（数据落到 tmp_path）。"""
    store = LibraryStore(tmp_path / "library.json")
    svc = LibraryService(store, MetadataService())
    svc.load_preloaded({}, {}, None, quick=True)
    return svc


def _muse_track(
    relpath: str,
    *,
    track_id: str = "src-1",
    title: str = "测试歌曲",
    artist: str = "测试歌手",
    album: str = "测试专辑",
    sha: str = "",
    lyrics_relpath: str = "",
    lyrics: list[dict] | None = None,
) -> dict:
    """构造单条 Muse 导出格式的曲目记录。"""
    d: dict = {
        "track_id": track_id,
        "storage_relpath": relpath,
        "title": title,
        "artist": artist,
        "album": album,
    }
    if sha:
        d["source_sha256"] = sha
    if lyrics_relpath:
        d["lyrics_storage_relpath"] = lyrics_relpath
    if lyrics is not None:
        d["lyrics"] = lyrics
    return d


def _muse_payload(
    tracks: list[dict],
    *,
    playlist_hash: str = PLAYLIST_HASH,
    name: str = "我的Muse歌单",
    schema: str = "musearc_playlist_export_v2",
    database_location: str = "",
) -> dict:
    """构造一份 Muse 导出格式的歌单 payload。"""
    return {
        "schema": schema,
        "playlist_hash": playlist_hash,
        "playlist_name": name,
        "ordered": True,
        "exported_at": "2026-09-13T00:00:00+00:00",
        "database_location": database_location,
        "tracks": tracks,
    }


def _write_playlist_file(tmp_path: Path, payload: dict, name: str = "demo.muse_playlist.json") -> Path:
    """把 payload 写成合法的 *.muse_playlist.json 文件。"""
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ============ 文件与 schema 校验 ============


class TestImportValidation:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        with pytest.raises(FileNotFoundError):
            svc.import_muse_playlist(tmp_path / "nope.muse_playlist.json")

    def test_wrong_extension_raises(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        path = tmp_path / "demo.txt"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError):
            svc.import_muse_playlist(path)

    def test_bad_schema_raises(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        payload = _muse_payload([], schema="some_other_schema_v9")
        path = _write_playlist_file(tmp_path, payload)
        with pytest.raises(ValueError):
            svc.import_muse_playlist(path)

    def test_schema_v1_accepted(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        payload = _muse_payload([_muse_track("music/a.flac")], schema="musearc_playlist_export_v1")
        playlist = svc.import_muse_playlist(_write_playlist_file(tmp_path, payload))
        assert playlist.id == f"muse_{PLAYLIST_HASH[:16]}"


# ============ 核心导入行为 ============


class TestImportBehavior:
    def test_placeholder_tracks_created_with_source_metadata(self, tmp_path: Path) -> None:
        """音频文件物理缺失时，用导入元数据创建占位曲目。"""
        svc = _make_service(tmp_path)
        payload = _muse_payload(
            [
                _muse_track("music/a.flac", track_id="src-a", title="歌A", artist="甲", album="专辑一", sha="c0ffee"),
                _muse_track("music/b.flac", track_id="src-b", title="歌B", artist="乙", album="专辑二"),
            ]
        )
        playlist = svc.import_muse_playlist(_write_playlist_file(tmp_path, payload))

        assert playlist.id == f"muse_{PLAYLIST_HASH[:16]}"
        assert playlist.name == "我的Muse歌单"
        assert len(playlist.track_ids) == 2

        track_a = svc.get_track(playlist.track_ids[0])
        assert track_a is not None
        assert track_a.title == "歌A"
        assert track_a.artist == "甲"
        assert track_a.source_track_id == "src-a"
        assert track_a.source_sha256 == "c0ffee"
        assert track_a.source_storage_relpath == "music/a.flac"
        assert track_a.path.endswith("music\\a.flac") or track_a.path.endswith("music/a.flac")

        # 同步进入"全部歌曲"，且导入后活动歌单指向新歌单
        all_songs = svc.find_playlist(ALL_SONGS_ID)
        assert all_songs is not None
        assert set(playlist.track_ids) <= set(all_songs.track_ids)
        assert svc.active_playlist_id == playlist.id

    def test_lyrics_paths_attached(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        payload = _muse_payload(
            [
                _muse_track(
                    "music/a.flac",
                    lyrics_relpath="lyrics/a.lrc",
                    lyrics=[{"relpath": "lyrics/a.romaji.lrc"}, {"relpath": "lyrics/a.lrc"}],  # 含主歌词重复项
                )
            ]
        )
        playlist = svc.import_muse_playlist(_write_playlist_file(tmp_path, payload))
        track = svc.get_track(playlist.track_ids[0])
        assert track is not None
        assert track.source_lyrics_storage_relpath == "lyrics/a.lrc"
        assert track.source_lyrics_path.endswith("lyrics/a.lrc") or track.source_lyrics_path.endswith("lyrics\\a.lrc")
        extra = [p for p in (track.extra_lyrics_paths or "").split("|") if p]
        assert len(extra) == 1  # 与主歌词重复的条目被剔除
        assert "a.romaji.lrc" in extra[0]

    def test_db_root_resolution_default_and_absolute(self, tmp_path: Path) -> None:
        """未指定 database_location 时默认源文件目录；绝对路径则直接使用。"""
        svc = _make_service(tmp_path)
        source = _write_playlist_file(tmp_path, _muse_payload([_muse_track("music/a.flac", track_id="s1")]))
        playlist = svc.import_muse_playlist(source)
        track = svc.get_track(playlist.track_ids[0])
        assert track is not None
        assert Path(track.path) == (tmp_path / "music" / "a.flac").resolve()

        payload = _muse_payload(
            [_muse_track("music/b.flac", track_id="s2")],
            playlist_hash="f" * 40,
            database_location=str(tmp_path / "db"),
        )
        playlist2 = svc.import_muse_playlist(_write_playlist_file(tmp_path, payload, name="db2.muse_playlist.json"))
        track2 = svc.get_track(playlist2.track_ids[0])
        assert track2 is not None
        assert Path(track2.path) == (tmp_path / "db" / "music" / "b.flac").resolve()

    def test_relative_database_location_resolved_against_source_dir(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        payload = _muse_payload(
            [_muse_track("music/c.flac", track_id="s3")],
            playlist_hash="e" * 40,
            database_location="db",  # 相对路径，相对 source_file 所在目录解析
        )
        playlist = svc.import_muse_playlist(_write_playlist_file(tmp_path, payload, name="rel.muse_playlist.json"))
        track = svc.get_track(playlist.track_ids[0])
        assert track is not None
        assert Path(track.path) == (db_dir / "music" / "c.flac").resolve()


class TestReuseAndDedupe:
    def test_reimport_same_file_reuses_playlist_without_duplicates(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        source = _write_playlist_file(
            tmp_path,
            _muse_payload(
                [
                    _muse_track("music/a.flac", track_id="src-a"),
                    _muse_track("music/b.flac", track_id="src-b"),
                ]
            ),
        )
        first = svc.import_muse_playlist(source)
        second = svc.import_muse_playlist(source)

        assert second.id == first.id
        assert len(second.track_ids) == 2  # 不因重复导入而翻倍
        assert len(svc.track_ids()) == 2  # 曲库层同样不重复

    def test_existing_track_reused_by_source_track_id(self, tmp_path: Path) -> None:
        """不同歌单、不同相对路径，但 source_track_id 相同 → 复用已有曲目。"""
        svc = _make_service(tmp_path)
        first = svc.import_muse_playlist(
            _write_playlist_file(
                tmp_path,
                _muse_payload([_muse_track("music/a.flac", track_id="src-a")], playlist_hash="1" * 40),
                name="p1.muse_playlist.json",
            )
        )
        existing_id = first.track_ids[0]

        second = svc.import_muse_playlist(
            _write_playlist_file(
                tmp_path,
                _muse_payload([_muse_track("other/path.flac", track_id="src-a")], playlist_hash="2" * 40),
                name="p2.muse_playlist.json",
            )
        )
        assert second.id != first.id
        assert second.track_ids == [existing_id]  # 复用而非新建

    def test_existing_track_reused_by_sha256(self, tmp_path: Path) -> None:
        """预置曲目带 source_sha256 时，导入按哈希最高优先级复用。"""
        svc = _make_service(tmp_path)
        seeded = Track(
            id="t-seed",
            path=str(tmp_path / "seed.flac"),
            title="种子",
            artist="歌手",
            album="专辑",
            source_sha256="cafe1234",
        )
        svc.load_preloaded({"t-seed": seeded}, {}, None, quick=True)

        playlist = svc.import_muse_playlist(
            _write_playlist_file(
                tmp_path,
                _muse_payload([_muse_track("music/x.flac", track_id="src-x", sha="CAFE1234")]),  # 大小写不敏感
            )
        )
        assert playlist.track_ids == ["t-seed"]

    def test_reimport_prunes_orphan_tracks(self, tmp_path: Path) -> None:
        """同一歌单再导入且曲目列表变化后，被移除的孤立曲目应从曲库清理。"""
        svc = _make_service(tmp_path)
        source = _write_playlist_file(
            tmp_path,
            _muse_payload([_muse_track("music/a.flac", track_id="src-a")]),
        )
        first = svc.import_muse_playlist(source)
        removed_id = first.track_ids[0]

        second = svc.import_muse_playlist(
            _write_playlist_file(
                tmp_path,
                _muse_payload([_muse_track("music/b.flac", track_id="src-b")]),
                name="demo.muse_playlist.json",
            )
        )
        assert second.id == first.id
        assert len(second.track_ids) == 1
        assert not svc.has_track(removed_id)  # 孤立曲目被清理
        assert svc.has_track(second.track_ids[0])


class TestRuntimePayload:
    def test_payload_import_uses_deterministic_content_hash_id(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        payload = _muse_payload([_muse_track("music/a.flac", track_id="src-a")], playlist_hash="", name="")

        first = svc.import_muse_playlist_payload(payload, source_hint="ctrl")
        second = svc.import_muse_playlist_payload(payload, source_hint="ctrl")

        assert first.id.startswith("muse_")
        assert first.id == second.id  # 同内容 + 同来源提示 → 确定性 ID
        assert len(second.track_ids) == 1  # 复用歌单，不重复
        assert first.name == "导入歌单"  # fallback 名称

    def test_payload_with_hash_uses_hash_prefix_id(self, tmp_path: Path) -> None:
        svc = _make_service(tmp_path)
        playlist = svc.import_muse_playlist_payload(
            _muse_payload([_muse_track("music/a.flac", track_id="src-a")], playlist_hash="deadbeef01"),
            source_hint="runtime_payload",
        )
        assert playlist.id == "muse_deadbeef01"
