"""主窗口公开入口。

该文件保持轻量，仅对外导出 `MainWindow`，
以便将复杂实现拆分到 `main_window_impl.py`，降低单文件复杂度。
"""

from app.ui.main_window_impl import MainWindow

__all__ = ["MainWindow"]
