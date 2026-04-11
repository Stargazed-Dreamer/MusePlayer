from __future__ import annotations

"""应用启动入口。

除常规 Qt 启动外，本文件还负责安装“崩溃/异常保存兜底”：
- aboutToQuit
- atexit
- sys/threading excepthook
- SIGINT/SIGTERM
"""

import atexit
from datetime import datetime
import faulthandler
import json
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
    crash_stream_holder: dict[str, object] = {"file": None}
    fh_enabled = {"value": False}

    def _is_crash_logging_enabled() -> bool:
        return bool(getattr(controller.settings, "crash_logging_enabled", True))

    def _open_crash_stream():
        stream = crash_stream_holder.get("file")
        if stream is not None:
            return stream
        crash_file = controller.data_dir / "crashlog.log"
        crash_file.parent.mkdir(parents=True, exist_ok=True)
        stream = crash_file.open("a", encoding="utf-8")
        crash_stream_holder["file"] = stream
        return stream

    def _close_crash_stream() -> None:
        stream = crash_stream_holder.get("file")
        crash_stream_holder["file"] = None
        if stream is None:
            return
        try:
            stream.close()
        except Exception:
            pass

    def _write_crash(reason: str, exc_type=None, exc_value=None, exc_tb=None) -> None:
        if not _is_crash_logging_enabled():
            return
        try:
            stream = _open_crash_stream()
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            stream.write(f"[{stamp}] reason={reason}\n")
            if exc_type is not None:
                traceback.print_exception(exc_type, exc_value, exc_tb, file=stream)
            try:
                snapshot = controller.player_service.state_snapshot()
                stream.write(f"Playback state: {json.dumps(snapshot, ensure_ascii=False)}\n")
                track_id = snapshot.get("track_id", "")
                if track_id:
                    stats = controller.playback_stats_service.export_stats_for_track(track_id)
                    if stats:
                        stream.write(f"Track stats: {json.dumps(stats)}\n")
            except Exception:
                pass
            stream.write("\n")
            stream.flush()
        except Exception:
            pass

    def _save_once(_reason: str) -> None:
        if saved["done"]:
            return
        saved["done"] = True
        try:
            controller.playback_stats_service.save_if_dirty()
        except Exception:
            pass
        try:
            controller.save_session()
        except Exception:
            pass

    app.aboutToQuit.connect(lambda: _save_once("aboutToQuit"))
    atexit.register(lambda: _save_once("atexit"))
    atexit.register(_close_crash_stream)

    def _sync_faulthandler() -> None:
        try:
            if _is_crash_logging_enabled():
                if not fh_enabled["value"]:
                    fh_stream = _open_crash_stream()
                    faulthandler.enable(file=fh_stream, all_threads=True)
                    fh_enabled["value"] = True
                return
            if fh_enabled["value"]:
                faulthandler.disable()
                fh_enabled["value"] = False
        except Exception:
            pass

    _sync_faulthandler()
    controller.settings_changed.connect(lambda _s: _sync_faulthandler())

    old_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        _write_crash("sys.excepthook", exc_type, exc_value, exc_tb)
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
            _write_crash("threading.excepthook", args.exc_type, args.exc_value, args.exc_traceback)
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
        _write_crash(f"signal:{signum}")
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
