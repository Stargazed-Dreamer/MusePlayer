"""运行时组件层。

包含应用程序运行时的核心组件，如控制服务器等。

导出的主要类:
- ControlServer: TCP控制服务器，提供外部程序接口
"""

from .control_server import ControlServer

__all__ = ["ControlServer"]
