"""统计检查点服务。

检查点是播放统计的只读快照：
- 增：`save()` 保存当前统计快照（默认名"年份+月份"，可自定义纪念名）。
- 删：`delete()` 删除检查点（二次确认由 UI 层负责）。
- 查：`list_checkpoints()` / `load()`。
- 无"改"操作：快照数据保存后不可修改；`rename()` 只改显示名称。

存储位置：`data/checkpoints/<id>.json`，单文件 schema `museplayer_stats_checkpoint_v1`。
快照包含曲目分桶与全局聚合块，因此老检查点同样能生成完整年度报告。

跨年自动保存由 AppController 在启动时调用 `auto_save_if_year_rolled()` 完成。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

CHECKPOINT_SCHEMA = "museplayer_stats_checkpoint_v1"


@dataclass(slots=True)
class CheckpointMeta:
    """检查点元信息（不含快照正文）。"""

    id: str
    """检查点唯一标识"""
    name: str
    """显示名称（默认"年份+月份"，可自定义）"""
    created_at: float
    """保存时间戳"""
    auto: bool
    """是否为跨年自动保存"""
    track_count: int
    """快照包含的曲目数"""


def default_checkpoint_name(now: time.struct_time | None = None) -> str:
    """生成默认检查点名称："年份+月份"。

    Returns:
        str: 如 "2026年9月"
    """
    t = now or time.localtime()
    return f"{t.tm_year}年{t.tm_mon}月"


class StatsCheckpointService:
    """统计检查点服务（只写只读快照的增删查）。"""

    def __init__(self, data_dir: Path):
        """初始化检查点服务。

        Args:
            data_dir: 数据目录（检查点存于其下 checkpoints/ 子目录）
        """
        self._dir = Path(data_dir).resolve() / "checkpoints"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, checkpoint_id: str) -> Path:
        """获取指定检查点的文件路径。"""
        return self._dir / f"{checkpoint_id}.json"

    def save(self, stats_snapshot: dict, name: str | None = None, *, auto: bool = False) -> CheckpointMeta:
        """保存一份统计快照为新检查点。

        Args:
            stats_snapshot: PlaybackStatsService.snapshot_payload() 的返回值
            name: 显示名称；为空时使用默认"年份+月份"
            auto: 是否为跨年自动保存（用于 UI 标签）

        Returns:
            CheckpointMeta: 新检查点的元信息
        """
        tracks = stats_snapshot.get("tracks", {}) if isinstance(stats_snapshot, dict) else {}
        meta = CheckpointMeta(
            id=uuid.uuid4().hex,
            name=str(name or "").strip() or default_checkpoint_name(),
            created_at=time.time(),
            auto=bool(auto),
            track_count=len(tracks) if isinstance(tracks, dict) else 0,
        )
        payload = {
            "schema": CHECKPOINT_SCHEMA,
            "id": meta.id,
            "name": meta.name,
            "created_at": meta.created_at,
            "auto": meta.auto,
            "track_count": meta.track_count,
            **stats_snapshot,
        }
        self._path_for(meta.id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def list_checkpoints(self) -> list[CheckpointMeta]:
        """列出所有检查点（按创建时间降序）。

        Returns:
            list[CheckpointMeta]: 元信息列表；损坏文件静默跳过
        """
        metas: list[CheckpointMeta] = []
        if not self._dir.exists():
            return metas
        for path in self._dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            metas.append(
                CheckpointMeta(
                    id=str(payload.get("id", path.stem)),
                    name=str(payload.get("name", path.stem)),
                    created_at=float(payload.get("created_at", 0.0)),
                    auto=bool(payload.get("auto", False)),
                    track_count=max(0, int(payload.get("track_count", 0))),
                )
            )
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas

    def get(self, checkpoint_id: str) -> CheckpointMeta | None:
        """按 ID 查找检查点元信息。

        Returns:
            CheckpointMeta | None: 不存在或损坏时返回 None
        """
        for meta in self.list_checkpoints():
            if meta.id == checkpoint_id:
                return meta
        return None

    def load(self, checkpoint_id: str) -> dict | None:
        """加载检查点快照正文。

        Args:
            checkpoint_id: 检查点 ID

        Returns:
            dict | None: {"tracks": {...}, "aggregate": {...}, ...}，不存在/损坏时返回 None
        """
        path = self._path_for(str(checkpoint_id or "").strip())
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) and payload.get("schema") == CHECKPOINT_SCHEMA else None

    def rename(self, checkpoint_id: str, name: str) -> bool:
        """修改检查点的显示名称（仅名称，快照数据不可改）。

        Args:
            checkpoint_id: 检查点 ID
            name: 新名称（去空白后非空才生效）

        Returns:
            bool: 是否改名成功
        """
        new_name = str(name or "").strip()
        if not new_name:
            return False
        payload = self.load(checkpoint_id)
        if payload is None:
            return False
        payload["name"] = new_name
        self._path_for(checkpoint_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def delete(self, checkpoint_id: str) -> bool:
        """删除检查点文件。

        Args:
            checkpoint_id: 检查点 ID

        Returns:
            bool: 是否删除成功
        """
        path = self._path_for(str(checkpoint_id or "").strip())
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False

    def auto_save_if_year_rolled(
        self, stats_snapshot: dict, last_seen_year: int, current_year: int | None = None
    ) -> tuple[CheckpointMeta | None, int]:
        """跨年自动保存：上次启动与本次启动不在同一年时保存上一年度检查点。

        首次运行（last_seen_year == 0）只记录不保存；同一年不保存。

        Args:
            stats_snapshot: 当前统计快照（应为跨年时刻/新年首次启动时的状态，
                其中累计值即上一年的最终数据）
            last_seen_year: 设置中记录的上次启动年份
            current_year: 当前年份（缺省取本地时间，便于测试注入）

        Returns:
            tuple[CheckpointMeta | None, int]: (新检查点元信息或 None, 应写回设置的最新年份)
        """
        year = int(current_year if current_year is not None else time.localtime().tm_year)
        last = int(last_seen_year or 0)
        if last <= 0 or last == year:
            return None, year
        meta = self.save(stats_snapshot, name=f"{last} 年度报告", auto=True)
        return meta, year
