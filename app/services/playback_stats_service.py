from __future__ import annotations

"""播放统计服务。

统计口径说明：
1. `play_count`: 只要触发一次播放起点就 +1（不要求播放完成）。
2. `active_play_count`: 用户主动触发（如双击、拖入、主动搜索播放）才 +1。
3. `early_skip_count`: 在歌曲前 5% 被切走时 +1。
4. `played_percent_total`: 以秒级增量累计，可超过 100%。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
import time


def _now_ts() -> float:
    """获取当前时间戳。
    
    Returns:
        float: 当前时间的时间戳（秒）
    """
    return time.time()


@dataclass(slots=True)
class PlaybackStatsEntry:
    """播放统计条目。
    
    记录单个曲目的详细播放统计信息。
    使用slots=True优化内存使用。
    """
    track_id: str
    """曲目ID"""
    play_count: int = 0
    """播放次数（只要触发播放就+1）"""
    active_play_count: int = 0
    """主动播放次数（用户主动播放才+1）"""
    early_skip_count: int = 0
    """早期跳过次数（在曲目前5%被切走时+1）"""
    played_seconds_total: float = 0.0
    """累计播放秒数"""
    played_percent_total: float = 0.0
    """累计播放百分比（可超过100%）"""
    updated_at: float = field(default_factory=_now_ts)
    """最后更新时间戳"""

    def to_dict(self) -> dict:
        """转换为字典格式，用于序列化。
        
        Returns:
            dict: 包含所有统计数据的字典
        """
        return {
            "track_id": self.track_id,
            "play_count": int(self.play_count),
            "active_play_count": int(self.active_play_count),
            "early_skip_count": int(self.early_skip_count),
            "played_seconds_total": float(self.played_seconds_total),
            "played_percent_total": float(self.played_percent_total),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PlaybackStatsEntry":
        """从字典创建PlaybackStatsEntry实例，用于反序列化。
        
        对数值进行验证和限制，确保数据的合法性。
        
        Args:
            payload: 包含统计数据的字典
            
        Returns:
            PlaybackStatsEntry: 新创建的统计条目实例
        """
        return cls(
            track_id=str(payload.get("track_id", "")),
            play_count=max(0, int(payload.get("play_count", 0))),
            active_play_count=max(0, int(payload.get("active_play_count", 0))),
            early_skip_count=max(0, int(payload.get("early_skip_count", 0))),
            played_seconds_total=max(0.0, float(payload.get("played_seconds_total", 0.0))),
            played_percent_total=max(0.0, float(payload.get("played_percent_total", 0.0))),
            updated_at=float(payload.get("updated_at", _now_ts())),
        )


class PlaybackStatsService:
    """播放统计服务。
    
    负责收集、存储和管理所有曲目的播放统计数据。
    使用JSON文件持久化统计数据，支持增量保存以优化性能。
    """
    
    def __init__(self, data_dir: Path):
        """初始化播放统计服务。
        
        创建统计文件路径并自动加载现有统计数据。
        
        Args:
            data_dir: 数据存储目录路径
        """
        self._path = Path(data_dir).resolve() / "playback_stats.json"
        self._entries: dict[str, PlaybackStatsEntry] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        """从JSON文件加载播放统计数据。
        
        如果文件不存在或格式错误，将初始化空的统计数据。
        """
        if not self._path.exists():
            self._entries = {}
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._entries = {}
            return
        rows = payload.get("tracks", {}) if isinstance(payload, dict) else {}
        loaded: dict[str, PlaybackStatsEntry] = {}
        if isinstance(rows, dict):
            for track_id, row in rows.items():
                if not isinstance(row, dict):
                    continue
                item = PlaybackStatsEntry.from_dict({"track_id": track_id, **row})
                if item.track_id:
                    loaded[item.track_id] = item
        self._entries = loaded

    def save_if_dirty(self) -> None:
        """有条件地保存统计数据。
        
        仅在数据被修改（脏状态）时写入磁盘，
        以减少不必要的磁盘I/O操作。
        """
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tracks": {track_id: item.to_dict() for track_id, item in self._entries.items()}}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._dirty = False

    def record_play_start(self, track_id: str, *, active_request: bool) -> None:
        """记录播放开始事件。
        
        当曲目开始播放时调用此方法增加相应的计数器。
        
        Args:
            track_id: 曲目ID
            active_request: 是否为用户主动触发的播放
        """
        track_id = str(track_id or "").strip()
        if not track_id:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.play_count += 1
        if active_request:
            item.active_play_count += 1
        item.updated_at = _now_ts()
        self._dirty = True

    def record_play_progress(self, track_id: str, played_seconds: float, duration_sec: float) -> None:
        """记录播放进度。
        
        累加播放时间和播放百分比，使用增量方式避免
        进度拖动导致的重复统计。
        
        Args:
            track_id: 曲目ID
            played_seconds: 本次播放的秒数增量
            duration_sec: 曲目总时长
        """
        # 以"增量秒数"累加，避免拖动进度导致重复统计整曲播放。
        track_id = str(track_id or "").strip()
        delta = max(0.0, float(played_seconds))
        duration = max(0.0, float(duration_sec))
        if not track_id or delta <= 0.0:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.played_seconds_total += delta
        if duration > 0.0:
            item.played_percent_total += (delta / duration) * 100.0
        item.updated_at = _now_ts()
        self._dirty = True

    def remove_track(self, track_id: str) -> None:
        """移除曲目的统计数据。
        
        当曲目从库中删除时调用此方法清理统计信息。
        
        Args:
            track_id: 要移除的曲目ID
        """
        if track_id in self._entries:
            del self._entries[track_id]
            self._dirty = True

    def record_early_skip(self, track_id: str) -> None:
        """记录早期跳过事件。
        
        当曲目在前5%的时间内被跳过时调用此方法。
        
        Args:
            track_id: 曲目ID
        """
        track_id = str(track_id or "").strip()
        if not track_id:
            return
        item = self._entries.get(track_id)
        if item is None:
            item = PlaybackStatsEntry(track_id=track_id)
            self._entries[track_id] = item
        item.early_skip_count += 1
        item.updated_at = _now_ts()
        self._dirty = True

    def export_stats_for_track(self, track_id: str) -> dict[str, int] | None:
        """导出指定曲目的统计数据。
        
        返回适用于外部使用的统计信息字典。
        
        Args:
            track_id: 曲目ID
            
        Returns:
            dict[str, int] | None: 统计数据字典，如果曲目不存在则返回None
        """
        item = self._entries.get(str(track_id or "").strip())
        if item is None:
            return None
        return {
            "play_count": max(0, int(item.play_count)),
            "manual_play_count": max(0, int(item.active_play_count)),
            "play_seconds": max(0, int(round(item.played_seconds_total))),
            "early_skip_count": max(0, int(item.early_skip_count)),
        }
