from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.entities import Playlist, SessionState, Settings, Track
from app.models.library_store import LibraryStore
from app.models.session_store import SessionStore
from app.models.settings_store import SettingsStore


# ============ LibraryStore ============

def test_library_store_load_missing_file(tmp_path):
    store = LibraryStore(tmp_path)
    tracks, playlists, active = store.load()
    assert tracks == {}
    assert playlists == {}
    assert active is None


def test_library_store_load_corrupt_json(tmp_path):
    store = LibraryStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not valid json", encoding="utf-8")
    tracks, playlists, active = store.load()
    assert tracks == {}
    assert playlists == {}
    assert active is None


def test_library_store_save_load_roundtrip(tmp_path, sample_tracks):
    store = LibraryStore(tmp_path)
    tracks = {t.id: t for t in sample_tracks}
    playlists = {
        "p1": Playlist(id="p1", name="PL", track_ids=["t1", "t2"]),
    }
    store.save(tracks, playlists, "p1")
    loaded_tracks, loaded_playlists, active = store.load()
    assert active == "p1"
    assert set(loaded_tracks.keys()) == {"t1", "t2", "t3"}
    assert loaded_tracks["t1"].title == "A"
    assert loaded_tracks["t2"].artist == "Artist B"
    assert loaded_playlists["p1"].track_ids == ["t1", "t2"]


def test_library_store_save_load_empty(tmp_path):
    store = LibraryStore(tmp_path)
    store.save({}, {}, None)
    tracks, playlists, active = store.load()
    assert tracks == {}
    assert playlists == {}
    assert active is None


def test_library_store_save_creates_parent_dir(tmp_path):
    nested = tmp_path / "nested" / "data"
    store = LibraryStore(nested)
    store.save({}, {}, None)
    assert store.path.exists()


def test_library_store_path_property(tmp_path):
    store = LibraryStore(tmp_path)
    assert store.path == tmp_path.resolve() / "library.json"


def test_library_store_load_skips_invalid_entries(tmp_path):
    store = LibraryStore(tmp_path)
    payload = {
        "tracks": {
            "t1": {"id": "t1", "path": "a.flac", "title": "A"},
            "bad": "not a dict",
        },
        "playlists": {
            "p1": {"id": "p1", "name": "PL"},
            "bad": 123,
        },
        "active_playlist_id": "p1",
    }
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tracks, playlists, active = store.load()
    assert "t1" in tracks
    assert "bad" not in tracks
    assert "p1" in playlists
    assert "bad" not in playlists
    assert active == "p1"


def test_library_store_load_track_and_playlist(tmp_path, sample_tracks):
    store = LibraryStore(tmp_path)
    tracks = {sample_tracks[0].id: sample_tracks[0]}
    playlists = {"p1": Playlist(id="p1", name="PL", track_ids=["t1"])}
    store.save(tracks, playlists, "p1")
    track, playlist = store.load_track_and_playlist("t1", "p1")
    assert track is not None
    assert track.id == "t1"
    assert playlist is not None
    assert playlist.id == "p1"


def test_library_store_load_track_and_playlist_uses_active(tmp_path):
    store = LibraryStore(tmp_path)
    tracks = {"t1": Track(id="t1", path="a.flac", title="A")}
    playlists = {"p1": Playlist(id="p1", name="PL", track_ids=["t1"])}
    store.save(tracks, playlists, "p1")
    # playlist_id 为 None 时应回退到 active_playlist_id
    track, playlist = store.load_track_and_playlist("t1", None)
    assert track is not None
    assert playlist is not None
    assert playlist.id == "p1"


def test_library_store_load_track_and_playlist_missing(tmp_path):
    store = LibraryStore(tmp_path)
    store.save({}, {}, None)
    track, playlist = store.load_track_and_playlist("nope", None)
    assert track is None
    assert playlist is None


def test_library_store_load_track_and_playlist_missing_file(tmp_path):
    store = LibraryStore(tmp_path)
    track, playlist = store.load_track_and_playlist("t1", "p1")
    assert track is None
    assert playlist is None


# ============ SessionStore ============

def test_session_store_load_missing_file(tmp_path):
    store = SessionStore(tmp_path)
    state = store.load()
    assert isinstance(state, SessionState)
    assert state == SessionState()


def test_session_store_load_corrupt_json(tmp_path):
    store = SessionStore(tmp_path)
    store._path.parent.mkdir(parents=True, exist_ok=True)
    store._path.write_text("not json at all", encoding="utf-8")
    state = store.load()
    assert state == SessionState()


def test_session_store_load_non_dict_payload(tmp_path):
    store = SessionStore(tmp_path)
    store._path.parent.mkdir(parents=True, exist_ok=True)
    store._path.write_text("[1, 2, 3]", encoding="utf-8")
    state = store.load()
    assert state == SessionState()


def test_session_store_save_load_roundtrip(tmp_path):
    store = SessionStore(tmp_path)
    state = SessionState(
        current_playlist_id="p1", current_track_id="t1", position_sec=42.5,
        volume=0.7, play_mode="random", random_seed=99, random_index=3,
        current_track_path="/a.flac", current_track_title="A", current_track_artist="AR",
    )
    store.save(state)
    loaded = store.load()
    assert loaded == state


def test_session_store_save_creates_parent_dir(tmp_path):
    nested = tmp_path / "s" / "d"
    store = SessionStore(nested)
    store.save(SessionState())
    assert store._path.exists()


# ============ SettingsStore ============

def test_settings_store_load_missing_file(tmp_path):
    store = SettingsStore(tmp_path)
    s = store.load()
    assert isinstance(s, Settings)
    assert s == Settings()


def test_settings_store_load_corrupt_json(tmp_path):
    store = SettingsStore(tmp_path)
    store._path.parent.mkdir(parents=True, exist_ok=True)
    store._path.write_text("not json", encoding="utf-8")
    s = store.load()
    assert s == Settings()


def test_settings_store_load_non_dict_payload(tmp_path):
    store = SettingsStore(tmp_path)
    store._path.parent.mkdir(parents=True, exist_ok=True)
    store._path.write_text("\"just a string\"", encoding="utf-8")
    s = store.load()
    assert s == Settings()


def test_settings_store_save_load_roundtrip(tmp_path):
    store = SettingsStore(tmp_path)
    s = Settings(
        control_port=5000, dark_theme=False, global_gain_boost=2.0,
        read_strategy="full", interface_shortcuts={"play": "Ctrl+P"},
    )
    store.save(s)
    loaded = store.load()
    assert loaded == s


def test_settings_store_save_creates_parent_dir(tmp_path):
    nested = tmp_path / "x" / "y"
    store = SettingsStore(nested)
    store.save(Settings())
    assert store._path.exists()


def test_settings_store_load_applies_validation(tmp_path):
    """加载时应对越界值进行规整（如端口、增益）。"""
    store = SettingsStore(tmp_path)
    store._path.parent.mkdir(parents=True, exist_ok=True)
    store._path.write_text(
        json.dumps({"control_port": 99999, "global_gain_boost": 99.0, "read_strategy": "bad"}),
        encoding="utf-8",
    )
    s = store.load()
    assert s.control_port == 65535
    assert s.global_gain_boost == 5.0
    assert s.read_strategy == "window"
