"""PlayerService的mixin组件。

为播放服务提供可复用的功能模块：
- PlayerServiceStatsMixin: 统计收集和处理功能
- PlayerServiceLazyDecodeMixin: 音频懒加载和预读取功能
"""

from .lazy_decode_mixin import PlayerServiceLazyDecodeMixin
from .stats_mixin import PlayerServiceStatsMixin

__all__ = ["PlayerServiceStatsMixin", "PlayerServiceLazyDecodeMixin"]
