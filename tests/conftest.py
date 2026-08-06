from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 项目根目录（conftest.py 所在目录的上一级）
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


@pytest.fixture(scope="session")
def project_root() -> Path:
    """返回项目根目录，并确保其在 sys.path 中（session 级别）。"""
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    return Path(_PROJECT_ROOT)


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """创建一个隔离的临时 data 目录，避免污染真实运行时数据。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def sample_track_dict() -> dict:
    """返回示例 Track 字典数据，覆盖全部字段。"""
    return {
        "id": "track-001",
        "path": "music/song1.flac",
        "title": "测试歌曲",
        "artist": "测试歌手",
        "album": "测试专辑",
        "duration_sec": 256.5,
        "track_no": 3,
        "year": "2024",
        "added_at": 1700000000.0,
        "source_track_id": "src-001",
        "source_storage_relpath": "music/song1.flac",
        "source_lyrics_storage_relpath": "lyrics/song1.lrc",
        "source_lyrics_path": "/abs/lyrics/song1.lrc",
        "extra_lyrics_paths": "/alt/song1.lrc",
        "source_sha256": "abc123def456",
    }


@pytest.fixture
def sample_tracks():
    """返回多个 Track 实例，用于曲库相关测试。"""
    from app.models.entities import Track

    return [
        Track(id="t1", path="music/a.flac", title="A", artist="Artist A", album="Album A", duration_sec=180.0, track_no=1),
        Track(id="t2", path="music/b.flac", title="B", artist="Artist B", album="Album B", duration_sec=240.0, track_no=2),
        Track(id="t3", path="music/c.flac", title="C", artist="Artist C", album="Album C", duration_sec=300.0, track_no=3),
    ]
