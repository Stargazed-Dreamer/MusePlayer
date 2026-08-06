from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.models.entities import Playlist, SessionState, Settings, Track, new_id


# ---- new_id ----

def test_new_id_returns_32_char_hex():
    sid = new_id()
    assert isinstance(sid, str)
    assert len(sid) == 32
    assert all(c in "0123456789abcdef" for c in sid)


def test_new_id_unique():
    ids = {new_id() for _ in range(100)}
    assert len(ids) == 100


# ============ Track ============

def test_track_required_fields():
    track = Track(id="t1", path="music/a.flac", title="A")
    assert track.id == "t1"
    assert track.path == "music/a.flac"
    assert track.title == "A"


def test_track_default_values():
    track = Track(id="t1", path="p", title="A")
    assert track.artist == "未知歌手"
    assert track.album == "未知专辑"
    assert track.duration_sec == 0.0
    assert track.track_no == 0
    assert track.year == ""
    assert track.source_track_id == ""
    assert track.source_storage_relpath == ""
    assert track.source_lyrics_storage_relpath == ""
    assert track.source_lyrics_path == ""
    assert track.extra_lyrics_paths == ""
    assert track.source_sha256 == ""
    assert track.added_at > 0


def test_track_path_obj():
    track = Track(id="t1", path="music/a.flac", title="A")
    assert track.path_obj == Path("music/a.flac")


def test_track_to_dict_from_dict_roundtrip():
    track = Track(
        id="t1", path="music/a.flac", title="A", artist="AR", album="AL",
        duration_sec=123.4, track_no=5, year="2020", added_at=1700000000.0,
        source_track_id="src1", source_storage_relpath="rel/a",
        source_lyrics_storage_relpath="rel/a.lrc", source_lyrics_path="/abs/a.lrc",
        extra_lyrics_paths="/alt/a.lrc", source_sha256="hash1",
    )
    d = track.to_dict()
    restored = Track.from_dict(d)
    assert restored == track


def test_track_from_dict_missing_fields_uses_defaults():
    restored = Track.from_dict({"id": "t1", "path": "p", "title": "A"})
    assert restored.id == "t1"
    assert restored.artist == "未知歌手"
    assert restored.album == "未知专辑"
    assert restored.duration_sec == 0.0
    assert restored.track_no == 0
    assert restored.year == ""


def test_track_from_dict_empty_dict_generates_id():
    restored = Track.from_dict({})
    assert isinstance(restored.id, str)
    assert len(restored.id) == 32
    assert restored.title == "未知标题"
    assert restored.path == ""


def test_track_to_dict_types():
    track = Track(id="t1", path="p", title="A", duration_sec=10, track_no=2, added_at=1)
    d = track.to_dict()
    assert isinstance(d["duration_sec"], float)
    assert isinstance(d["track_no"], int)
    assert isinstance(d["added_at"], float)


def test_track_to_dict_keys_complete():
    track = Track(id="t1", path="p", title="A")
    d = track.to_dict()
    expected_keys = {
        "id", "path", "title", "artist", "album", "duration_sec", "track_no",
        "year", "added_at", "source_track_id", "source_storage_relpath",
        "source_lyrics_storage_relpath", "source_lyrics_path",
        "extra_lyrics_paths", "source_sha256",
    }
    assert set(d.keys()) == expected_keys


# ============ Playlist ============

def test_playlist_required_fields():
    pl = Playlist(id="p1", name="我的歌单")
    assert pl.id == "p1"
    assert pl.name == "我的歌单"


def test_playlist_default_values():
    pl = Playlist(id="p1", name="PL")
    assert pl.track_ids == []
    assert pl.ordered is True
    assert pl.source_schema == ""
    assert pl.source_file == ""
    assert pl.source_playlist_hash == ""
    assert pl.source_database_location == ""
    assert pl.source_exported_at == ""
    assert pl.created_at > 0
    assert pl.updated_at > 0


def test_playlist_to_dict_from_dict_roundtrip():
    pl = Playlist(
        id="p1", name="PL", track_ids=["t1", "t2"], created_at=1.0, updated_at=2.0,
        source_schema="v1", source_file="f.json", source_playlist_hash="h",
        source_database_location="db", source_exported_at="2024", ordered=False,
    )
    d = pl.to_dict()
    restored = Playlist.from_dict(d)
    assert restored == pl


def test_playlist_to_dict_track_ids_is_copy():
    pl = Playlist(id="p1", name="PL", track_ids=["t1", "t2"])
    d = pl.to_dict()
    d["track_ids"].append("t3")
    assert pl.track_ids == ["t1", "t2"]


def test_playlist_touch_updates_timestamp():
    pl = Playlist(id="p1", name="PL", updated_at=1000.0)
    old = pl.updated_at
    time.sleep(0.01)
    pl.touch()
    assert pl.updated_at > old


def test_playlist_add_remove_track_ids_via_list():
    """Playlist 通过 track_ids 列表管理曲目（模型未提供显式 add/remove 方法）。"""
    pl = Playlist(id="p1", name="PL")
    pl.track_ids.append("t1")
    pl.track_ids.append("t2")
    assert pl.track_ids == ["t1", "t2"]
    pl.track_ids.remove("t1")
    assert pl.track_ids == ["t2"]


def test_playlist_from_dict_missing_fields():
    restored = Playlist.from_dict({"id": "p1", "name": "PL"})
    assert restored.track_ids == []
    assert restored.ordered is True


def test_playlist_from_dict_empty_dict_generates_id():
    restored = Playlist.from_dict({})
    assert len(restored.id) == 32
    assert restored.name == "新建歌单"


def test_playlist_from_dict_track_ids_coerced_to_str():
    restored = Playlist.from_dict({"id": "p1", "name": "PL", "track_ids": [1, 2, 3]})
    assert restored.track_ids == ["1", "2", "3"]


# ============ SessionState ============

def test_session_state_defaults():
    s = SessionState()
    assert s.current_playlist_id is None
    assert s.current_track_id is None
    assert s.position_sec == 0.0
    assert s.volume == 1.0
    assert s.play_mode == "single_loop"
    assert s.random_seed == 1
    assert s.random_index == 0
    assert s.current_track_path == ""
    assert s.current_track_title == ""
    assert s.current_track_artist == ""


def test_session_state_to_dict_from_dict_roundtrip():
    s = SessionState(
        current_playlist_id="p1", current_track_id="t1", position_sec=42.5,
        volume=0.7, play_mode="random", random_seed=99, random_index=3,
        current_track_path="/music/a.flac", current_track_title="A", current_track_artist="AR",
    )
    d = s.to_dict()
    restored = SessionState.from_dict(d)
    assert restored == s


def test_session_state_from_dict_clamps_negative_position():
    s = SessionState.from_dict({"position_sec": -10})
    assert s.position_sec == 0.0


def test_session_state_from_dict_clamps_volume():
    assert SessionState.from_dict({"volume": 2.0}).volume == 1.0
    assert SessionState.from_dict({"volume": -1.0}).volume == 0.0


def test_session_state_from_dict_clamps_seed_and_index():
    s = SessionState.from_dict({"random_seed": -1, "random_index": -5})
    assert s.random_seed == 0
    assert s.random_index == 0


def test_session_state_from_dict_empty_uses_defaults():
    s = SessionState.from_dict({})
    assert s == SessionState()


# ============ Settings ============

def test_settings_defaults():
    s = Settings()
    assert s.control_host == "127.0.0.1"
    assert s.control_port == 43121
    assert s.control_interface_enabled is False
    assert s.auto_restore_session is True
    assert s.logging_enabled is False
    assert s.crash_logging_enabled is True
    assert s.enable_single_loop_mode is True
    assert s.enable_playlist_loop_mode is False
    assert s.global_gain_boost == 1.0
    assert s.read_strategy == "window"
    assert s.output_device == ""
    assert s.dark_theme is True
    assert s.timed_save_minutes == 5
    assert s.window_x == -1
    assert s.interface_shortcuts == {}
    assert s.global_shortcuts == {}


def test_settings_to_dict_from_dict_roundtrip():
    s = Settings(
        control_host="0.0.0.0", control_port=5000, control_interface_enabled=True,
        auto_restore_session=False, global_gain_boost=2.0, read_strategy="full",
        dark_theme=False, timed_save_minutes=30,
        interface_shortcuts={"play": "Ctrl+P"}, global_shortcuts={"next": "Ctrl+Right"},
    )
    d = s.to_dict()
    restored = Settings.from_dict(d)
    assert restored == s


def test_settings_from_dict_clamps_port():
    assert Settings.from_dict({"control_port": 99999}).control_port == 65535
    assert Settings.from_dict({"control_port": 0}).control_port == 1


def test_settings_from_dict_clamps_gain():
    assert Settings.from_dict({"global_gain_boost": 10.0}).global_gain_boost == 5.0
    assert Settings.from_dict({"global_gain_boost": 0.1}).global_gain_boost == 0.5


def test_settings_from_dict_clamps_timed_save_minutes():
    assert Settings.from_dict({"timed_save_minutes": 0}).timed_save_minutes == 1
    assert Settings.from_dict({"timed_save_minutes": 99999}).timed_save_minutes == 1440


def test_settings_from_dict_invalid_read_strategy_falls_back():
    s = Settings.from_dict({"read_strategy": "invalid"})
    assert s.read_strategy == "window"


def test_settings_from_dict_read_strategy_case_insensitive():
    assert Settings.from_dict({"read_strategy": "FULL"}).read_strategy == "full"


def test_settings_from_dict_empty_dict():
    assert Settings.from_dict({}) == Settings()


def test_settings_from_dict_non_dict_shortcuts_falls_back():
    s = Settings.from_dict({"interface_shortcuts": "not a dict", "global_shortcuts": 123})
    assert s.interface_shortcuts == {}
    assert s.global_shortcuts == {}


def test_settings_to_dict_types():
    s = Settings(control_port=5000, global_gain_boost=1.5, timed_save_minutes=10)
    d = s.to_dict()
    assert isinstance(d["control_port"], int)
    assert isinstance(d["global_gain_boost"], float)
    assert isinstance(d["timed_save_minutes"], int)
    assert isinstance(d["dark_theme"], bool)
