from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

LOGGER_ROOT = "museplayer"
LOG_PREFIX = "museplayer_"
MAX_LOG_FILES = 10

_session_log_path: Path | None = None


def configure_logging(data_dir: Path, enabled: bool, current_log_path: Path | None = None) -> Path | None:
    global _session_log_path

    logger = logging.getLogger(LOGGER_ROOT)
    logger.propagate = False

    if enabled:
        existing_file_handler = None
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None):
                existing_file_handler = h
                break
        if existing_file_handler is not None:
            return _session_log_path or current_log_path

        reuse_path = _session_log_path or current_log_path
        if reuse_path is not None and reuse_path.exists():
            handler = logging.FileHandler(reuse_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            return reuse_path

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    if not enabled:
        logger.setLevel(logging.CRITICAL + 1)
        logger.addHandler(logging.NullHandler())
        return None

    logger.setLevel(logging.INFO)
    log_dir = Path(data_dir).resolve() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    _remove_legacy_logs(log_dir)
    _prune_old_logs(log_dir, keep=max(0, MAX_LOG_FILES - 1))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = log_dir / f"{LOG_PREFIX}{stamp}.log"
    _session_log_path = log_file

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)

    _prune_old_logs(log_dir, keep=MAX_LOG_FILES)
    return log_file


def get_logger(name: str | None = None) -> logging.Logger:
    if not name:
        return logging.getLogger(LOGGER_ROOT)
    return logging.getLogger(f"{LOGGER_ROOT}.{name}")


def _prune_old_logs(log_dir: Path, keep: int) -> None:
    files = [p for p in log_dir.glob(f"{LOG_PREFIX}*.log") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for file_path in files[max(0, int(keep)) :]:
        try:
            file_path.unlink()
        except Exception:
            pass


def _remove_legacy_logs(log_dir: Path) -> None:
    legacy_candidates = [log_dir / "museplayer.log", *log_dir.glob("museplayer.log.*")]
    for file_path in legacy_candidates:
        if not file_path.is_file():
            continue
        try:
            file_path.unlink()
        except Exception:
            pass
