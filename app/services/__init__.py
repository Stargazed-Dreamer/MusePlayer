"""服务层

包含应用程序的核心业务服务，负责协调数据模型层和UI层。

导出的主要类:
- LibraryService: 曲库管理服务
- PlayerService: 播放服务核心
- PlayMode: 播放模式枚举
- AppController: 应用控制器，协调各服务组件
"""

from .app_controller import AppController
from .library_service import LibraryService
from .player_service import PlayerService, PlayMode

__all__ = ["LibraryService", "PlayerService", "PlayMode", "AppController"]
