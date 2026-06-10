from __future__ import annotations

"""应用启动入口。

除常规 Qt 启动外，本文件还负责安装"崩溃/异常保存兜底"：
- aboutToQuit
- atexit
- sys/threading excepthook
- SIGINT/SIGTERM
"""

import atexit
from datetime import datetime
import faulthandler
import json
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from app.services.app_controller import AppController
from app.ui.main_window import MainWindow
from app.ui.theme import APP_STYLE

_CRASHLOG_FILENAME = "crashlog.log"


def _archive_previous_crashlog(crash_dir: Path) -> None:
    prev = crash_dir / _CRASHLOG_FILENAME
    if not prev.exists():
        return
    try:
        size = prev.stat().st_size
    except OSError:
        return
    if size == 0:
        try:
            prev.unlink()
        except OSError:
            pass
        return
    stamp = datetime.fromtimestamp(prev.stat().st_mtime).strftime("%Y%m%d_%H%M%S")
    archived = crash_dir / f"crash_{stamp}.log"
    if archived.exists():
        i = 1
        while True:
            archived = crash_dir / f"crash_{stamp}_{i}.log"
            if not archived.exists():
                break
            i += 1
    try:
        prev.rename(archived)
    except OSError:
        pass


def _install_persist_fallbacks(app: QApplication, controller: AppController) -> None:
    """安装多通道保存兜底，尽量在异常退出时保留统计数据。"""
    saved = {"done": False}
    crash_stream_holder: dict[str, object] = {"file": None}
    fh_enabled = {"value": False}
    crash_dir = controller.data_dir / "crashlogs"
    crash_dir.mkdir(parents=True, exist_ok=True)

    _archive_previous_crashlog(crash_dir)

    def _is_crash_logging_enabled() -> bool:
        return bool(getattr(controller.settings, "crash_logging_enabled", True))

    def _open_crash_stream():
        stream = crash_stream_holder.get("file")
        if stream is not None:
            return stream
        crash_file = crash_dir / _CRASHLOG_FILENAME
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
                _close_crash_stream()
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
    t0 = time.perf_counter()
    app = QApplication(sys.argv)
    t1 = time.perf_counter()
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
    t2 = time.perf_counter()

    controller = AppController(project_root=project_root)
    t3 = time.perf_counter()
    _install_persist_fallbacks(app, controller)
    t4 = time.perf_counter()

    win = MainWindow(controller)
    if icon_path.exists():
        win.setWindowIcon(QIcon(str(icon_path)))
    win.show()
    t5 = time.perf_counter()

    from PySide6.QtCore import QTimer, QMetaObject, Qt as _Qt

    # 第一步：立即恢复会话（开始播放当前歌曲）
    controller.restore_session()
    win._refresh_current_track_ui(win.player.current_track())
    win._refresh_random_state_hint()

    # 第二步：延迟加载歌曲列表（不阻塞播放）
    def _deferred_ui_init():
        win._reload_playlist_combo()
        win._reload_track_list()

    QTimer.singleShot(0, _deferred_ui_init)

    # 后台延迟清理曲库（缺失文件检测等），不阻塞 UI
    startup_file_check = bool(getattr(controller.settings, "startup_file_check", True))

    def _run_deferred_cleanup():
        if not startup_file_check:
            return
        try:
            controller.library_service.deferred_cleanup()
            if controller.library_service.tracks:
                QMetaObject.invokeMethod(win, "_on_library_changed", _Qt.ConnectionType.QueuedConnection)
        except Exception:
            pass

    threading.Thread(target=_run_deferred_cleanup, daemon=True).start()

    total = t5 - t0
    print(f"[启动计时] QApplication: {t1-t0:.3f}s | 样式/图标: {t2-t1:.3f}s | "
          f"Controller: {t3-t2:.3f}s | 兜底安装: {t4-t3:.3f}s | "
          f"MainWindow+show: {t5-t4:.3f}s | 总计: {total:.3f}s")

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
