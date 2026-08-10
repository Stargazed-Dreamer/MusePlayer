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

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from app.services.app_controller import AppController
from app.ui.main_window import MainWindow
from app.ui.theme import APP_STYLE

_CRASHLOG_FILENAME = "crashlog.log"


def _archive_previous_crashlog(crash_dir: Path) -> None:
    """
    将前一次的崩溃日志文件进行归档，以防止被覆盖。
    
    本函数会检查指定目录中是否存在名为 _CRASHLOG_FILENAME 的崩溃日志文件。
    如果存在，则尝试将其重命名为带有时间戳的新文件名（例如 'crash_20231027_143025.log'）。
    如果目标归档文件名已存在，则会通过添加递增数字后缀来避免冲突。
    如果原日志文件大小为0，则直接删除。
    在整个过程中，任何因文件操作导致的异常都会被捕获并忽略。

    参数:
        crash_dir (Path): 包含崩溃日志文件的目录路径。

    返回:
        None: 此函数不返回任何值。
    """
    # 构造之前崩溃日志文件的完整路径
    prev = crash_dir / _CRASHLOG_FILENAME
    # 如果之前的崩溃日志文件不存在，则直接返回
    if not prev.exists():
        return
    # 尝试获取文件大小，如果发生OSError（如文件权限问题），则直接返回
    try:
        size = prev.stat().st_size
    except OSError:
        return
    # 如果文件大小为0，说明是空日志文件，直接删除它
    if size == 0:
        try:
            prev.unlink()
        except OSError:
            pass
        return
    # 使用文件的最后修改时间生成一个时间戳字符串，用于新文件名
    stamp = datetime.fromtimestamp(prev.stat().st_mtime).strftime("%Y%m%d_%H%M%S")
    # 构造归档日志文件的初始路径
    archived = crash_dir / f"crash_{stamp}.log"
    # 如果该初始归档文件名已存在，则通过循环添加数字后缀来找到一个不冲突的文件名
    if archived.exists():
        i = 1
        while True:
            # 构造带数字后缀的新文件名
            archived = crash_dir / f"crash_{stamp}_{i}.log"
            # 检查新文件名是否已被占用，如果没有则跳出循环
            if not archived.exists():
                break
            i += 1
    # 将之前的崩溃日志文件重命名为归档文件
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


class _StartupEventProbe(QObject):
    def __init__(self, target, started_at: float):
        super().__init__(target)
        self._target = target
        self._started_at = started_at
        self._show_logged = False
        self._paint_logged = False

    def eventFilter(self, watched, event):
        if watched is self._target:
            elapsed = time.perf_counter() - self._started_at
            if event.type() == QEvent.Type.Show and not self._show_logged:
                self._show_logged = True
                print(f"[StartupProbe] show_event={elapsed:.3f}s", flush=True)
            elif event.type() == QEvent.Type.Paint and not self._paint_logged:
                self._paint_logged = True
                print(f"[StartupProbe] first_paint={elapsed:.3f}s", flush=True)
        return super().eventFilter(watched, event)


def _timed_startup_call(name: str, callback):
    def _wrapped(*args, **kwargs):
        started_at = time.perf_counter()
        try:
            return callback(*args, **kwargs)
        finally:
            print(f"[StartupProbe] {name}={time.perf_counter() - started_at:.3f}s", flush=True)

    return _wrapped


def main() -> int:
    """应用程序主入口函数。
    
    负责启动MusePlayer音乐播放器，包括：
    1. 初始化QApplication并配置基本属性（样式、字体、图标等）
    2. 按优先级分步初始化服务（播放器→曲库→会话恢复）
    3. 延迟加载UI组件和后台清理任务，优化启动速度
    4. 启动Qt事件循环
    
    返回值:
        int: 应用程序退出码，0表示正常退出
    """
    t0 = time.perf_counter()  # 记录启动开始时间
    startup_probe_enabled = os.getenv("MUSE_STARTUP_PROBE", "").strip().lower() in {
        "1", "true", "yes", "on"
    }

    def _probe_log(message: str) -> None:
        if startup_probe_enabled:
            print(f"[StartupProbe] {message}", flush=True)
    app = QApplication(sys.argv)  # 创建Qt应用实例
    t1 = time.perf_counter()  # 记录QApplication创建耗时
    app.setApplicationName("MusePlayer")  # 设置应用程序名称
    app.setStyle("Fusion")  # 设置UI风格为跨平台一致的Fusion样式
    if sys.platform.startswith("win"):  # Windows系统下设置中文字体
        app.setFont(QFont("Microsoft YaHei", 9))
    elif sys.platform == "darwin":  # macOS 使用苹方
        app.setFont(QFont("PingFang SC", 13))
    else:  # Linux 使用思源黑体
        app.setFont(QFont("Noto Sans CJK SC", 10))
    project_root = Path(__file__).resolve().parent  # 获取项目根目录路径
    icon_path = project_root / "icon.ico"  # 应用图标文件路径
    if icon_path.exists():  # 如果图标文件存在则设置应用图标
        icon = QIcon(str(icon_path))
        app.setWindowIcon(icon)
    app.setStyleSheet(APP_STYLE)  # 应用全局样式表
    t2 = time.perf_counter()  # 记录样式配置耗时

    # 先读会话数据（几KB，瞬间完成），用于窗口预览
    from app.models.session_store import SessionStore
    _session_preview = SessionStore(project_root / "data").load()

    # 轻量 Controller（仅加载设置，不初始化库和播放器）
    controller = AppController(project_root=project_root)
    t3 = time.perf_counter()  # 记录Controller创建耗时

    # 第一步：初始化播放器（~0.3s，之后即可播放/暂停）
    controller.initialize_services()
    t3b = time.perf_counter()  # 记录播放器初始化耗时

    # 创建窗口（player 已存在，UI 可正常初始化）
    win = MainWindow(controller)
    startup_probe = None
    startup_original_methods = {}
    if startup_probe_enabled:
        startup_probe = _StartupEventProbe(win, t0)
        win.installEventFilter(startup_probe)
        for method_name in (
            "_refresh_current_track_ui",
            "_reload_playlist_combo",
            "_reload_track_list",
        ):
            original = getattr(win, method_name)
            startup_original_methods[method_name] = original
            setattr(win, method_name, _timed_startup_call(method_name.lstrip("_"), original))
    if icon_path.exists():  # 为窗口单独设置图标（确保窗口标题栏显示）
        win.setWindowIcon(QIcon(str(icon_path)))

    # 用会话预览数据立即显示当前歌曲信息（避免窗口出现时显示空白）
    if _session_preview.current_track_title:
        win.title_label.setText(_session_preview.current_track_title)
        win.artist_label.setText(_session_preview.current_track_artist)
        win._current_track_title = _session_preview.current_track_title
        win._current_track_artist = _session_preview.current_track_artist
        win._update_window_title()  # 更新窗口标题栏显示

    win.show()  # 显示主窗口
    t4 = time.perf_counter()  # 记录窗口显示耗时

    from PySide6.QtCore import QTimer, QMetaObject, Qt as _Qt

    _install_persist_fallbacks(app, controller)
    startup_file_check = bool(getattr(controller.settings, "startup_file_check", True))

    def _disable_startup_probe():
        nonlocal startup_probe
        for method_name, original in startup_original_methods.items():
            setattr(win, method_name, original)
        startup_original_methods.clear()
        if startup_probe is not None:
            win.removeEventFilter(startup_probe)
            startup_probe.deleteLater()
            startup_probe = None

    def _run_deferred_cleanup():
        if not startup_file_check:
            return
        try:
            controller.library_service.deferred_cleanup()
            if controller.library_service.tracks:
                QMetaObject.invokeMethod(win, "_on_library_changed", _Qt.ConnectionType.QueuedConnection)
        except Exception:
            pass

    def _deferred_ui_init():
        win._reload_playlist_combo()
        win._reload_track_list()
        win.statusBar().clear_hint("startup")
        win.statusBar().showMessage("Ready", 1500)
        _disable_startup_probe()
        threading.Thread(target=_run_deferred_cleanup, daemon=True).start()

    def _restore_startup_session():
        win.statusBar().set_hint("startup", "Restoring session...")
        phase_started = time.perf_counter()
        controller.restore_session()
        _probe_log(f"restore_session_total={time.perf_counter() - phase_started:.3f}s")
        win._refresh_current_track_ui(win.player.current_track())
        win._refresh_random_state_hint()
        win._on_mode_changed(win.player.mode.value)
        win._on_playback_changed(win.player.is_playing())
        win._refresh_volume_ui()
        QTimer.singleShot(0, _deferred_ui_init)

    def _load_startup_library():
        win.statusBar().set_hint("startup", "Preparing current track...")
        preview_started = time.perf_counter()
        preview_ready = controller.restore_session_preview(_session_preview)
        preview_state = win.player.state_snapshot()
        _probe_log(
            f"current_track_preview={time.perf_counter() - preview_started:.3f}s | "
            f"ready={preview_ready} | duration={preview_state.get('duration_sec', 0.0):.3f}s | "
            f"playlist={preview_state.get('playlist_id')}"
        )
        win.statusBar().set_hint("startup", "Loading library...")
        state = {"done": False, "payload": None, "error": None}
        started_at = time.perf_counter()
        poll_timer = QTimer(win)
        poll_timer.setInterval(15)

        def _read_library_data():
            try:
                state["payload"] = controller.prepare_library_load()
            except Exception as exc:
                state["error"] = exc
            finally:
                state["done"] = True

        def _finish_library_load():
            if not state["done"]:
                return
            poll_timer.stop()
            if state["error"] is not None:
                message = f"Library load failed: {state['error']}"
                _probe_log(message)
                _disable_startup_probe()
                controller.error_occurred.emit(message)
                win.statusBar().set_hint("startup", message)
                return

            read_done_at = time.perf_counter()
            controller.finish_library_load(state["payload"])
            finished_at = time.perf_counter()
            _probe_log(
                f"library_read_worker={read_done_at - started_at:.3f}s | "
                f"library_install_main={finished_at - read_done_at:.3f}s | "
                f"load_library_total={finished_at - started_at:.3f}s | "
                f"tracks={len(controller.library_service.tracks)} | "
                f"playlists={len(controller.library_service.playlists)}"
            )
            QTimer.singleShot(0, _restore_startup_session)

        poll_timer.timeout.connect(_finish_library_load)
        poll_timer.start()
        threading.Thread(target=_read_library_data, daemon=True).start()

    QTimer.singleShot(50, _load_startup_library)

    # 输出各阶段启动耗时，便于性能分析和优化
    total = t4 - t0
    print(f"[启动计时] QApplication: {t1-t0:.3f}s | 样式: {t2-t1:.3f}s | "
          f"Controller(轻量): {t3-t2:.3f}s | 播放器: {t3b-t3:.3f}s | "
          f"MainWindow+show: {t4-t3b:.3f}s | 窗口出现: {total:.3f}s")

    return app.exec()  # 进入Qt事件循环，返回应用程序退出码


if __name__ == "__main__":
    raise SystemExit(main())
