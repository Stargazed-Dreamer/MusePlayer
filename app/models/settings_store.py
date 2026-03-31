from __future__ import annotations

import json
from pathlib import Path

from .entities import Settings


class SettingsStore:
    """应用设置存储器。
    
    负责应用程序设置的持久化存储和加载。
    包含界面、播放、控制接口等各种配置。
    """
    
    def __init__(self, data_dir: Path):
        """初始化设置存储器。
        
        Args:
            data_dir: 数据存储目录路径
        """
        self._path = Path(data_dir).resolve() / "settings.json"

    def load(self) -> Settings:
        """加载应用设置。
        
        从JSON文件读取设置数据，如果文件不存在或格式错误，返回默认设置。
        
        Returns:
            Settings: 应用设置对象
        """
        if not self._path.exists():
            return Settings()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return Settings()
        if not isinstance(payload, dict):
            return Settings()
        return Settings.from_dict(payload)

    def save(self, settings: Settings) -> None:
        """保存应用设置。
        
        将设置对象转换为JSON格式并保存到文件。
        自动创建必要的目录结构。
        
        Args:
            settings: 要保存的应用设置对象
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )