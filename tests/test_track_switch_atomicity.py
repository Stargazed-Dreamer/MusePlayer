"""切歌原子性的回归测试（对应 2026-10-10 定位的竞态 bug）。

现场症状：自动下一首与用户双击（几乎同时）相撞后，
音频与标题显示新曲，而列表高亮却停在旧曲——即"服务层/音频已切、
列表却按旧状态落定"的分裂。

本文件覆盖服务层的三处修复：
1. play_track 加载失败时整体回滚先写入的状态（不再留下半切换态）；
2. next_track / previous_track 在发信号前锁定目标曲目（不被信号回调重入改写）；
3. 窗口预读调度失败不得让整次切歌判负。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.entities import Playlist, Track
from app.services.library_service import ALL_SONGS_ID, LibraryService
from app.services.player_service import PlayerService, PlayMode

TRACK_PATHS = {
    "A": "music/a.flac",
    "B": "music/b.flac",
    "C": "music/c.flac",
    "D": "music/d.flac",
}


class FakeStore:
    """曲库存储替身：只保留路径语义，读写全部 no-op。"""

    def __init__(self, path):
        self.path = path

    def load(self):
        return {}, {}, None

    def save(self, *args, **kwargs):
        return None


class FakeCore:
    """播放内核替身：记录事件日志，并支持按路径模拟加载失败。"""

    def __init__(self):
        self.events: list[tuple] = []
        self.playing = False
        self.pos = 0.0
        self.dur = 200.0
        self.fail_paths: set[str] = set()

    # --- 失败注入 ---------------------------------------------------------
    def _maybe_fail(self, source) -> None:
        if str(source) in self.fail_paths:
            raise RuntimeError(f"模拟加载失败: {source}")

    def set_error_callback(self, cb):
        self._err_cb = cb

    def load(self, source, start_sec=0.0, window_sec=None):
        self._maybe_fail(source)
        self.events.append(("load", str(source)))
        self.playing = False
        self.pos = float(start_sec)
        self.dur = float(window_sec) if window_sec else 200.0

    def load_streaming(self, source, start_sec=0.0, total_duration_sec=0.0):
        self._maybe_fail(source)
        self.events.append(("load_streaming", str(source)))
        self.playing = False
        self.pos = float(start_sec)
        self.dur = float(total_duration_sec) or 200.0

    def load_decoded_pcm(self, source, pcm, sample_rate, channels, reopen_stream=False,
                         resume_sec=0.0, keep_playing=True):
        self.playing = bool(keep_playing)
        self.pos = float(resume_sec)

    def play(self, start_sec=0.0):
        self.events.append(("play", round(float(start_sec), 3)))
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


def _build_library(root, track_ids: list[str]) -> LibraryService:
    store = FakeStore(path=root / "library.json")
    library = LibraryService(store, metadata_service=None)  # type: ignore[arg-type]
    tracks = {
        tid: Track(id=tid, path=TRACK_PATHS[tid], title=f"曲目{tid}", artist="x", duration_sec=200.0)
        for tid in track_ids
    }
    playlists = {ALL_SONGS_ID: Playlist(id=ALL_SONGS_ID, name="全部歌曲", track_ids=list(track_ids))}
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
    def _factory(track_ids: list[str]) -> tuple[PlayerService, FakeCore]:
        core = FakeCore()
        monkeypatch.setattr("app.services.player_service.PyAVPlayerCore", lambda: core)
        service = PlayerService(_build_library(tmp_path, track_ids), read_strategy_getter=lambda: "window")
        return service, core

    return _factory


def test_play_track_rolls_back_state_when_load_fails(make_player):
    """加载失败时，play_track 必须整体回滚：当前曲目/索引与音频都停在旧曲。"""
    service, core = make_player(["A", "B"])

    assert service.play_track("A", auto_play=True) is True
    assert service.current_track_id == "A"

    emitted: list[str | None] = []
    service.track_changed.connect(lambda t: emitted.append(t.id if t is not None else None))
    errors: list[str] = []
    service.error_occurred.connect(errors.append)

    # 让切换到 B 的加载必然失败
    core.fail_paths.add(str(Path(TRACK_PATHS["B"])))

    assert service.play_track("B", auto_play=True) is False

    # 关键断言：没有留下"状态已切、音视频未切"的半切换态
    assert service.current_track_id == "A"
    assert service.is_playing() is True
    # 失败切歌不应广播 track_changed（否则界面会显示一首并未播放的曲目）
    assert emitted == []
    assert any("加载失败" in e for e in errors)


def test_play_track_rolls_back_sequential_index(make_player):
    """回滚须覆盖顺序索引：失败后 _sequential_index 仍指向旧曲。"""
    service, core = make_player(["A", "B", "C"])
    service.set_mode(PlayMode.PLAYLIST_LOOP)

    assert service.play_track("B", auto_play=True) is True
    index_before = service._sequential_index

    core.fail_paths.add(str(Path(TRACK_PATHS["C"])))
    assert service.play_track("C", auto_play=True) is False

    assert service.current_track_id == "B"
    assert service._sequential_index == index_before


def test_next_track_locks_target_before_signal(make_player):
    """随机模式下 next_track 先锁定目标再发信号：信号回调重入改写索引也不影响切歌目标。"""
    service, core = make_player(["A", "B", "C", "D"])
    service.set_mode(PlayMode.RANDOM)
    assert service.play_track("A", auto_play=True) is True

    captured: dict[str, object] = {}

    def on_random_state(_seed: int, index: int) -> None:
        # 记录"发出信号这一刻"本应切到的曲目
        captured["target"] = service._random_order[index]
        # 模拟重入：另一个切歌在信号回调里改写了随机索引。
        # 旧实现会在 emit 之后再取 target，于是切到这首被改写的曲目。
        service._random_index = (index + 2) % len(service._random_order)

    service.random_state_changed.connect(on_random_state)

    assert service.next_track() is True
    assert service.current_track_id == captured["target"]


def test_previous_track_locks_target_before_signal(make_player):
    """previous_track 同 next_track：先锁定目标再发信号。"""
    service, core = make_player(["A", "B", "C", "D"])
    service.set_mode(PlayMode.RANDOM)
    assert service.play_track("D", auto_play=True) is True
    # 把当前曲目挪到随机序中部，确保 previous 仍有可退的一步
    service._random_index = 2
    service._current_track_id = service._random_order[2]

    captured: dict[str, object] = {}

    def on_random_state(_seed: int, index: int) -> None:
        captured["target"] = service._random_order[index]
        service._random_index = (index + 1) % len(service._random_order)

    service.random_state_changed.connect(on_random_state)

    assert service.previous_track() is True
    assert service.current_track_id == captured["target"]


def test_lazy_prefetch_failure_does_not_fail_switch(make_player):
    """窗口预读调度失败只是性能退化，不得让本次切歌判负。"""
    service, core = make_player(["A", "B"])
    assert service.play_track("A", auto_play=True) is True

    def _boom(*_args, **_kwargs):
        raise RuntimeError("模拟预读调度失败")

    service._schedule_lazy_prefetch = _boom  # type: ignore[assignment]

    assert service.play_track("B", auto_play=True) is True
    assert service.current_track_id == "B"
    assert service.is_playing() is True


def test_is_switching_track_flag_is_reset_after_switch(make_player):
    """切歌临界区标志在 play_track 返回后必须复位（不能被异常路径卡在 True）。"""
    service, core = make_player(["A", "B"])
    assert service.is_switching_track() is False

    assert service.play_track("A", auto_play=True) is True
    assert service.is_switching_track() is False

    core.fail_paths.add(str(Path(TRACK_PATHS["B"])))
    assert service.play_track("B", auto_play=True) is False
    assert service.is_switching_track() is False
