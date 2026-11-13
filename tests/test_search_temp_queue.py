"""搜索结果"临时歌单"播放模式的回归测试。

覆盖设置项 `search_result_play_mode` 的两种语义：
- playlist（默认）：搜索不影响播放上下文，切歌仍按原歌单顺序（本次不改动）；
- temp_queue：搜索结果集临时取代原歌单成为播放队列，播放模式作用在结果集上；
  搜索词一变（含清空）临时队列立即作废，回到原歌单顺序且音频不重载。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication

from app.models.entities import Playlist, Settings, Track
from app.services.library_service import ALL_SONGS_ID, LibraryService
from app.services.player_service import PlayerService, PlayMode


class FakeStore:
    """曲库存储替身：只保留路径语义，读写全部 no-op。"""

    def __init__(self, path):
        self.path = path

    def load(self):
        return {}, {}, None

    def save(self, *args, **kwargs):
        return None


class FakeCore:
    """播放内核替身：用事件日志记录所有调用，便于断言"音频有没有被重新加载"。"""

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
    tracks = {
        tid: Track(id=tid, path=f"music/{tid}.flac", title=f"歌名{tid}", artist="歌手", duration_sec=200.0)
        for tid in ("A", "B", "C", "D")
    }
    playlists = {ALL_SONGS_ID: Playlist(id=ALL_SONGS_ID, name="全部歌曲", track_ids=["A", "B", "C", "D"])}
    library.load_preloaded(tracks, playlists, ALL_SONGS_ID, quick=True)
    return library


@pytest.fixture(autouse=True)
def _qt_app():
    """QObject/QTimer 需要一个 QCoreApplication 实例（无需事件循环）。"""
    app = QCoreApplication.instance() or QCoreApplication(["test"])
    yield app


@pytest.fixture
def player(monkeypatch, tmp_path):
    """构造 PlayerService，并把播放内核替换为 FakeCore（不打开真实音频设备）。"""
    core = FakeCore()
    monkeypatch.setattr("app.services.player_service.PyAVPlayerCore", lambda: core)
    service = PlayerService(_build_library(tmp_path), read_strategy_getter=lambda: "window")
    return service, core


# ============ 临时队列本身 ============


def test_temporary_queue_replaces_playlist_order(player):
    """登记临时队列后，导航与"下一首"预览都只走结果集。"""
    service, _core = player
    service.set_playlist_loop_mode_enabled(True)
    service.set_mode(PlayMode.PLAYLIST_LOOP)  # 单曲循环下"下一首预览"等于当前曲，不适合本用例
    assert service.set_temporary_queue(["C", "A"]) is True

    assert service._playlist_track_ids() == ["C", "A"]
    assert service.play_track("A", auto_play=False, manual_select=True) is True
    # 结果集顺序 [C, A]：A 的下一首环绕回 C
    assert service.preview_next_track().id == "C"
    assert service.next_track() is True
    assert service.current_track_id == "C"


def test_temporary_queue_dedupes_and_drops_missing_tracks(player):
    """重复 ID 只保留一次；不在库中的 ID 被丢弃；结果集为空时拒绝登记。"""
    service, _core = player
    assert service.set_temporary_queue(["A", "A", "GHOST"]) is True
    assert service._playlist_track_ids() == ["A"]
    assert service.set_temporary_queue([]) is False
    assert service.has_temporary_queue is True  # 失败不覆盖原有队列


def test_clear_temporary_queue_restores_original_order_without_audio_reload(player):
    """清空临时队列必须只换队列口径：音频不重载，下一首按原歌单顺序。"""
    service, core = player
    service.set_playlist_loop_mode_enabled(True)
    service.set_mode(PlayMode.PLAYLIST_LOOP)  # 让"下一首"预览反映顺序队列
    service.set_temporary_queue(["C"])
    assert service.play_track("C", auto_play=True, manual_select=True) is True
    assert service.is_playing() is True

    events_before_clear = list(core.events)
    assert service.clear_temporary_queue() is True

    assert core.events == events_before_clear  # 无 load/pause/seek
    assert service.is_playing() is True
    assert service.current_track_id == "C"
    assert service._playlist_track_ids() == ["A", "B", "C", "D"]
    assert service.preview_next_track().id == "D"
    assert service.next_track() is True
    assert service.current_track_id == "D"


def test_clear_temporary_queue_is_noop_when_absent(player):
    service, _core = player
    assert service.clear_temporary_queue() is False


def test_set_playlist_invalidates_temporary_queue(player):
    """切换歌单后临时队列失效，切歌重新落在原歌单里。"""
    service, _core = player
    service.set_temporary_queue(["C"])
    service.set_playlist(ALL_SONGS_ID)

    assert service.has_temporary_queue is False
    assert service._playlist_track_ids() == ["A", "B", "C", "D"]


def test_random_mode_navigates_only_within_temporary_queue(player):
    """随机模式下"下一首"不会跑到结果集之外的曲目。"""
    service, _core = player
    service.set_mode(PlayMode.RANDOM)
    assert service.set_temporary_queue(["C", "A"]) is True
    assert service.play_track("A", auto_play=False, manual_select=True) is True

    assert service.next_track() is True
    assert service.current_track_id in {"A", "C"}
    assert set(service._random_order) == {"A", "C"}


# ============ 设置项 ============


def test_search_result_play_mode_defaults_to_playlist():
    assert Settings().search_result_play_mode == "playlist"


def test_search_result_play_mode_roundtrip():
    settings = Settings.from_dict({"search_result_play_mode": "temp_queue"})
    assert settings.search_result_play_mode == "temp_queue"
    assert settings.to_dict()["search_result_play_mode"] == "temp_queue"


def test_search_result_play_mode_rejects_unknown_value():
    assert Settings.from_dict({"search_result_play_mode": " TMP "}).search_result_play_mode == "playlist"
    assert Settings.from_dict({}).search_result_play_mode == "playlist"


# ============ UI 接线（纯桩，不建窗口） ============


class _QueuePlayerStub:
    """只保留临时队列相关接口的播放器桩（复用真实 mixin 的判定逻辑）。"""

    def __init__(self, result_ids: list[str]) -> None:
        self.result_ids = list(result_ids)
        self.queue: list[str] | None = None
        self.clear_calls = 0

    def search_playlist_tracks(self, _keyword: str):
        from types import SimpleNamespace

        return [SimpleNamespace(id=tid) for tid in self.result_ids]

    @property
    def has_temporary_queue(self) -> bool:
        return self.queue is not None

    def set_temporary_queue(self, track_ids) -> bool:
        self.queue = list(track_ids)
        return True

    def clear_temporary_queue(self) -> bool:
        self.clear_calls += 1
        self.queue = None
        return True


def _mixin_stub(*, mode: str, keyword: str, player):
    from types import SimpleNamespace

    return SimpleNamespace(
        controller=SimpleNamespace(settings=SimpleNamespace(search_result_play_mode=mode)),
        player=player,
        search_edit=SimpleNamespace(text=lambda: keyword),
    )


def test_double_click_arms_temp_queue_only_in_temp_mode():
    """默认模式不得登记临时队列（B 的现有行为必须原样保留）。"""
    from app.ui.main_window_mixins.playback_mixin import MainWindowPlaybackMixin

    player = _QueuePlayerStub(["A", "C"])
    stub = _mixin_stub(mode="playlist", keyword="s", player=player)

    assert MainWindowPlaybackMixin._arm_search_temporary_queue(stub, "A") is False
    assert player.queue is None


def test_double_click_arms_temp_queue_with_result_set():
    from app.ui.main_window_mixins.playback_mixin import MainWindowPlaybackMixin

    player = _QueuePlayerStub(["A", "C"])
    stub = _mixin_stub(mode="temp_queue", keyword="s", player=player)

    assert MainWindowPlaybackMixin._arm_search_temporary_queue(stub, "A") is True
    assert player.queue == ["A", "C"]


def test_double_click_without_keyword_or_outside_results_does_not_arm():
    from app.ui.main_window_mixins.playback_mixin import MainWindowPlaybackMixin

    player = _QueuePlayerStub(["A", "C"])
    no_keyword = _mixin_stub(mode="temp_queue", keyword="", player=player)
    assert MainWindowPlaybackMixin._arm_search_temporary_queue(no_keyword, "A") is False

    outside = _mixin_stub(mode="temp_queue", keyword="s", player=player)
    assert MainWindowPlaybackMixin._arm_search_temporary_queue(outside, "B") is False
    assert player.queue is None


def test_search_text_change_clears_temporary_queue():
    """搜索词变动必须立即作废临时队列（回到原歌单顺序）。"""
    from types import SimpleNamespace

    from app.ui.main_window_impl import MainWindow

    player = _QueuePlayerStub(["A", "C"])
    player.set_temporary_queue(["A", "C"])
    stub = SimpleNamespace(
        player=player,
        search_edit=SimpleNamespace(text=lambda: "abc"),
        search_clear_btn=SimpleNamespace(setVisible=lambda _visible: None),
        _position_search_clear_button=lambda: None,
    )

    MainWindow._on_search_text_changed(stub, "abc")

    assert player.clear_calls == 1
    assert player.has_temporary_queue is False


def test_search_text_change_without_queue_does_not_touch_player():
    from types import SimpleNamespace

    from app.ui.main_window_impl import MainWindow

    player = _QueuePlayerStub(["A"])
    stub = SimpleNamespace(
        player=player,
        search_edit=SimpleNamespace(text=lambda: "abc"),
        search_clear_btn=SimpleNamespace(setVisible=lambda _visible: None),
        _position_search_clear_button=lambda: None,
    )

    MainWindow._on_search_text_changed(stub, "abc")

    assert player.clear_calls == 0
