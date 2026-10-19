"""播放状态栏两段式提示的服务层回归测试（2026-10-19）。

背景：切歌瞬间状态栏曾显示「播放：A - 播放歌曲：B」——playback_changed
早于 track_changed 发出，而 UI 读的是未更新的标题缓存。与用户确认的
新语义：

- play_track 受理时发 playback_requested（尝试播放）；
- playback_changed(True) 表示真正开始播放；
- 「暂停」提示只允许用户主动暂停：会话预览装载（force 但值未变）、
  切歌单停旧曲（_core.pause 直调）不得被表述成暂停。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.entities import Playlist, Track
from app.services.library_service import ALL_SONGS_ID, LibraryService
from app.services.player_service import PlayerService

TRACK_PATHS = {
    "A": "music/a.flac",
    "B": "music/b.flac",
    "C": "music/c.flac",
}


class FakeStore:
    """曲库存储替身：读写全部 no-op。"""

    def __init__(self, path):
        self.path = path

    def load(self):
        return {}, {}, None

    def save(self, *args, **kwargs):
        return None


class FakeCore:
    """播放内核替身：记录事件日志，支持按路径模拟加载失败。"""

    def __init__(self):
        self.playing = False
        self.pos = 0.0
        self.dur = 200.0
        self.fail_paths: set[str] = set()

    def _maybe_fail(self, source) -> None:
        if str(source) in self.fail_paths:
            raise RuntimeError(f"模拟加载失败: {source}")

    def set_error_callback(self, cb):
        self._err_cb = cb

    def load(self, source, start_sec=0.0, window_sec=None):
        self._maybe_fail(source)
        self.playing = False
        self.pos = float(start_sec)
        self.dur = float(window_sec) if window_sec else 200.0

    def load_streaming(self, source, start_sec=0.0, total_duration_sec=0.0):
        self._maybe_fail(source)
        self.playing = False
        self.pos = float(start_sec)
        self.dur = float(total_duration_sec) or 200.0

    def play(self, start_sec=0.0):
        self.pos = float(start_sec)
        self.playing = True

    def pause(self):
        self.playing = False

    def seek(self, sec):
        self.pos = float(sec)

    def unload(self):
        self.playing = False

    def current_time(self):
        return self.pos

    def duration(self):
        return self.dur

    def is_playing(self):
        return self.playing

    def set_volume(self, value):
        return None

    def set_playback_rate(self, rate):
        return None

    def set_output_device(self, name):
        return True

    def rebind_output_device(self):
        return True

    def decode_window(self, source, start_sec=0.0, window_sec=0.1):
        raise RuntimeError("本测试不涉及预读解码")

    def close(self):
        self.playing = False


def _build_library(root: Path, extra_playlist: bool) -> LibraryService:
    store = FakeStore(path=root / "library.json")
    library = LibraryService(store, metadata_service=None)  # type: ignore[arg-type]
    tracks = {
        tid: Track(id=tid, path=TRACK_PATHS[tid], title=f"曲目{tid}", artist="x", duration_sec=200.0)
        for tid in TRACK_PATHS
    }
    playlists = {
        ALL_SONGS_ID: Playlist(id=ALL_SONGS_ID, name="全部歌曲", track_ids=["A", "B"]),
    }
    if extra_playlist:
        playlists["p2"] = Playlist(id="p2", name="歌单二", track_ids=["C"])
    library.load_preloaded(tracks, playlists, ALL_SONGS_ID, quick=True)
    return library


@pytest.fixture(autouse=True)
def _qt_app():
    """QObject/QTimer 需要一个 QCoreApplication 实例（无需事件循环）。"""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


@pytest.fixture
def make_player(monkeypatch, tmp_path):
    """构造 PlayerService，并把播放内核替换为 FakeCore（不打开真实音频设备）。"""

    def _factory(extra_playlist: bool = False) -> tuple[PlayerService, FakeCore]:
        core = FakeCore()
        monkeypatch.setattr("app.services.player_service.PyAVPlayerCore", lambda: core)
        service = PlayerService(_build_library(tmp_path, extra_playlist), read_strategy_getter=lambda: "window")
        return service, core

    return _factory


def test_play_request_precedes_playback_started(make_player):
    """play_track 受理即发 playback_requested，且早于 playback_changed(True)。"""
    service, _core = make_player()

    sequence: list[tuple[str, object]] = []
    service.playback_requested.connect(lambda t: sequence.append(("requested", t.id)))
    service.playback_changed.connect(lambda playing: sequence.append(("started" if playing else "stopped", playing)))

    assert service.play_track("B", auto_play=True) is True
    assert ("requested", "B") in sequence
    assert ("started", True) in sequence
    # 尝试提示必须先于"开始播放"
    assert sequence.index(("requested", "B")) < sequence.index(("started", True))


def test_load_failure_keeps_request_without_start(make_player):
    """加载失败：有"尝试播放"，没有"开始播放"，也不广播 track_changed。"""
    service, core = make_player()
    core.fail_paths.add(str(Path(TRACK_PATHS["B"])))

    requested: list[str] = []
    started: list[bool] = []
    track_changed: list[str | None] = []
    service.playback_requested.connect(lambda t: requested.append(t.id))
    service.playback_changed.connect(lambda p: started.append(p))
    service.track_changed.connect(lambda t: track_changed.append(t.id if t is not None else None))

    assert service.play_track("B", auto_play=True) is False
    assert requested == ["B"]  # 尝试提示照发（UI 据此 + 错误消息构成失败现场）
    assert True not in started  # 从未"开始播放"
    assert track_changed == []


def test_system_stop_is_not_reported_as_user_pause(make_player):
    """切歌单停旧曲（系统侧停止）不得被标记为用户暂停。"""
    service, core = make_player(extra_playlist=True)

    assert service.play_track("A", auto_play=True) is True
    assert service.is_playing() is True

    # 正在播放时切到不含 A 的歌单：服务内部停旧曲、装载新歌单首曲（不播放）
    service.set_playlist("p2")

    assert service.is_playing() is False
    assert service.last_pause_was_user is False


def test_user_pause_sets_flag_and_resume_clears_it(make_player):
    """用户主动暂停 → last_pause_was_user=True；恢复播放后复位为 False。"""
    service, _core = make_player()

    assert service.play_track("A", auto_play=True) is True

    service.pause()
    assert service.last_pause_was_user is True

    service.play()
    assert service.is_playing() is True
    assert service.last_pause_was_user is False


def test_preview_load_keeps_silence(make_player):
    """会话预览装载（auto_play=False，值未变的 force 发射）不得标记为暂停。"""
    service, _core = make_player()

    assert service.play_track("A", auto_play=False) is True
    assert service.is_playing() is False
    assert service.last_pause_was_user is False
