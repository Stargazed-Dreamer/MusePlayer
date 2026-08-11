from __future__ import annotations

import json
from pathlib import Path

from .entities import SessionState


class SessionStore:
    """会话状态存储器。

    负责播放器会话状态的持久化存储和加载。
    包含当前播放列表、曲目、播放位置等信息。
    """

    def __init__(self, data_dir: Path):
        """初始化会话存储器。

        Args:
            data_dir: 数据存储目录路径
        """
        self._path = Path(data_dir).resolve() / "session.json"

    def load(self) -> SessionState:
        """加载会话状态。

        从JSON文件读取会话状态，如果文件不存在或格式错误，返回新的SessionState。

        Returns:
            SessionState: 会话状态对象
        """
        if not self._path.exists():
            return SessionState()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return SessionState()
        if not isinstance(payload, dict):
            return SessionState()
        return SessionState.from_dict(payload)

    def save(self, state: SessionState) -> None:
        """保存会话状态。

        将会话状态转换为JSON格式并保存到文件。
        自动创建必要的目录结构。

        Args:
            state: 要保存的会话状态对象
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
