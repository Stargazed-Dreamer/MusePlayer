"""PlayerService 会话恢复的播放状态保持测试。

覆盖启动竞态：窗口快速出现后，用户在曲库加载/磁盘会话恢复完成之前就按下
播放；此时会话恢复不得把正在播放的曲目重新加载成暂停态。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.entities import Playlist, SessionState, Track
from app.services.library_service import ALL_SONGS_ID, LibraryService
from app.services.player_service import PlayerService

TRACK_PATH = "music/a.flac"
OTHER_PATH = "music/b.flac"


class FakeStore:
    """曲库存储替身：只保留路径语义，读写全部 no-op。"""

    def __init__(self, path):
        self.path = path

    def load(self):
        return {}, {}, None

    def save(self, *args, **kwargs):
        return None


class FakeCore:
    """播放内核替身：用事件日志记录所有调用，便于断言"有没有被重新加载"。"""

    def __init__(self):
        self.events: list[tuple] = []
        self.playing = False
        self.pos = 0.0
        self.dur = 6.2

    def set_error_callback(self, cb):
        self._err_cb = cb

    def load(self, source, start_sec=0.0, window_sec=None):
        self.events.append(("load", str(source), round(float(start_sec), 3), window_sec))
        self.playing = False
        self.pos = 0.0
        self.dur = float(window_sec) if window_sec else 6.2

    def load_streaming(self, source, start_sec=0.0, total_duration_sec=0.0):
        self.events.append(("load_streaming", str(source), round(float(start_sec), 3)))
        self.playing = False
        self.pos = 0.0
        self.dur = float(total_duration_sec) or 6.2

    def load_decoded_pcm(
        self, source, pcm, sample_rate, channels, reopen_stream=False, resume_sec=0.0, keep_playing=True
    ):
        self.events.append(("load_decoded_pcm", round(float(resume_sec), 3), bool(keep_playing)))
        self.playing = bool(keep_playing)
        self.pos = float(resume_sec)
        self.dur = 6.2

    def play(self, start_sec=0.0):
        self.events.append(("play", round(float(start_sec), 3)))
        self.pos = float(start_sec)
        self.playing = True

    def pause(self):
        self.events.append(("pause",))
        self.playing = False

    def seek(self, sec):
        self.events.append(("seek", round(float(sec), 3)))
        self.pos = float(sec)

    def unload(self):
        self.events.append(("unload",))
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


def _build_library(root) -> LibraryService:
    store = FakeStore(path=root / "library.json")
    library = LibraryService(store, metadata_service=None)  # type: ignore[arg-type]
    track_a = Track(id="A", path=TRACK_PATH, title="A", artist="x", duration_sec=200.0)
    track_b = Track(id="B", path=OTHER_PATH, title="B", artist="y", duration_sec=200.0)
    playlists = {ALL_SONGS_ID: Playlist(id=ALL_SONGS_ID, name="全部歌曲", track_ids=["A", "B"])}
    library.load_preloaded({"A": track_a, "B": track_b}, playlists, ALL_SONGS_ID, quick=True)
    return library


@pytest.fixture(autouse=True)
def _qt_app():
    """QObject/QTimer 需要一个 QCoreApplication 实例（无需事件循环）。"""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


@pytest.fixture
def player(monkeypatch, tmp_path):
    """构造 PlayerService，并把播放内核替换为 FakeCore（不打开真实音频设备）。"""
    core = FakeCore()
    monkeypatch.setattr("app.services.player_service.PyAVPlayerCore", lambda: core)
    service = PlayerService(_build_library(tmp_path), read_strategy_getter=lambda: "window")
    return service, core


def _session_state() -> SessionState:
    return SessionState(
        current_playlist_id=ALL_SONGS_ID,
        current_track_id="A",
        position_sec=30.0,
        volume=0.8,
        play_mode="single_loop",
        random_seed=1,
        random_index=0,
        current_track_path=TRACK_PATH,
        current_track_title="A",
        current_track_artist="x",
    )


def _prepare_current_track(player: PlayerService) -> None:
    """模拟启动预备：预加载会话曲目但不播放，随后武装接管监视。"""
    player.set_current_playlist_id(ALL_SONGS_ID)
    player.play_track("A", auto_play=False, start_sec=30.0, manual_select=False)
    player.arm_playback_takeover_watch()


def test_restore_session_keeps_playing_when_user_started_playback(player):
    """预备后用户已播放：会话恢复必须保持播放状态，且不重新加载音频。"""
    service, core = player
    _prepare_current_track(service)

    service.play()  # 用户在启动尚未完成时按下播放
    assert service.is_playing() is True
    loads_before = sum(1 for event in core.events if event[0] == "load")

    service.restore_session(_session_state())

    assert service.is_playing() is True
    assert sum(1 for event in core.events if event[0] == "load") == loads_before
    assert core.events[-1][0] != "pause"


def test_restore_session_keeps_paused_position_after_takeover(player):
    """预备后用户播放过又暂停：恢复同样不应把曲目拉回磁盘会话位置。"""
    service, core = player
    _prepare_current_track(service)

    service.play()
    service.seek(60.0)  # 用户拖动进度条
    service.pause()

    loads_before = sum(1 for event in core.events if event[0] == "load")
    service.restore_session(_session_state())

    assert service.is_playing() is False
    assert sum(1 for event in core.events if event[0] == "load") == loads_before


def test_restore_session_loads_track_when_user_has_not_acted(player):
    """用户未接管播放时，会话恢复保持原有行为：按会话位置重建当前曲目。"""
    service, core = player
    _prepare_current_track(service)
    core.events.clear()

    service.restore_session(_session_state())

    assert service.is_playing() is False
    assert service.current_playlist_id == ALL_SONGS_ID
    assert core.events == [
        ("load", str(Path(TRACK_PATH)), 30.0, PlayerService._LAZY_WINDOW_SEC),
        ("seek", 0.0),
    ]
    assert core.pos == 0.0


def test_playback_taken_over_falls_back_to_core_state(player):
    """未走 PlayerService 播放入口时，仍以"内核正在播放"兜底判定已接管。"""
    service, core = player
    _prepare_current_track(service)
    assert service.playback_taken_over() is False

    core.play(0.0)  # 模拟其他路径直接开始播放

    assert service.playback_taken_over() is True


def test_arm_watch_resets_previous_takeover(player):
    """重新武装后，先前的播放行为不应继续影响后续会话恢复。"""
    service, _core = player
    service.play_track("A", auto_play=True, manual_select=True)
    assert service.playback_taken_over() is True

    service.pause()
    service.arm_playback_takeover_watch()

    assert service.playback_taken_over() is False
