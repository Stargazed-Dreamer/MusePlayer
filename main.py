from __future__ import annotations

"""应用启动入口。

除常规 Qt 启动外，本文件还负责安装“崩溃/异常保存兜底”：
- aboutToQuit
- atexit
- sys/threading excepthook
- SIGINT/SIGTERM
"""

import atexit
import signal
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from app.services.app_controller import AppController
from app.ui.main_window import MainWindow
from app.ui.theme import APP_STYLE


def _install_persist_fallbacks(app: QApplication, controller: AppController) -> None:
    """安装多通道保存兜底，尽量在异常退出时保留统计数据。"""
    saved = {"done": False}

    def _save_once(_reason: str) -> None:
        if saved["done"]:
            return
        saved["done"] = True
        try:
            controller.save_stats_now()
            controller.playback_stats_service.save_if_dirty()
        except Exception:
            pass

    app.aboutToQuit.connect(lambda: _save_once("aboutToQuit"))
    atexit.register(lambda: _save_once("atexit"))

    old_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        _save_once("sys.excepthook")
        try:
            if old_excepthook is not None:
                old_excepthook(exc_type, exc_value, exc_tb)
            else:
                traceback.print_exception(exc_type, exc_value, exc_tb)
        except Exception:
            traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    if hasattr(threading, "excepthook"):
        old_threading_hook = threading.excepthook

        def _threading_excepthook(args):
            _save_once("threading.excepthook")
            try:
                if old_threading_hook is not None:
                    old_threading_hook(args)
                    return
            except Exception:
                pass
            traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)

        threading.excepthook = _threading_excepthook

    def _signal_handler(signum, _frame):
        _save_once(f"signal:{signum}")
        app.quit()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _signal_handler)
        except Exception:
            pass


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MusePlayer")
    app.setStyle("Fusion")
    if sys.platform.startswith("win"):
        app.setFont(QFont("Microsoft YaHei", 9))
    project_root = Path(__file__).resolve().parent
    icon_path = project_root / "icon.ico"
    if icon_path.exists():
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)
    app.setStyleSheet(APP_STYLE)

    controller = AppController(project_root=project_root)
    _install_persist_fallbacks(app, controller)

    win = MainWindow(controller)
    if icon_path.exists():
        win.setWindowIcon(QIcon(str(icon_path)))
    win.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
