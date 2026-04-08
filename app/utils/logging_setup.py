from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

LOGGER_ROOT = "museplayer"
LOG_PREFIX = "museplayer_"
MAX_LOG_FILES = 10


def configure_logging(data_dir: Path, enabled: bool, current_log_path: Path | None = None) -> Path | None:
    logger = logging.getLogger(LOGGER_ROOT)
    logger.propagate = False

    if enabled and current_log_path is not None and current_log_path.exists():
        existing_file_handler = None
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None):
                existing_file_handler = h
                break
        if existing_file_handler is not None:
            return current_log_path

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

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)

    _prune_old_logs(log_dir, keep=MAX_LOG_FILES)
    return log_file


def get_logger(name: str | None = None) -> logging.Logger:
    """获取已配置的日志记录器。
    
    返回与应用程序根日志器关联的日志记录器。
    
    Args:
        name: 日志记录器名称，如果为None则返回根日志记录器
        
    Returns:
        logging.Logger: 配置好的日志记录器实例
    """
    if not name:
        return logging.getLogger(LOGGER_ROOT)
    return logging.getLogger(f"{LOGGER_ROOT}.{name}")


def _prune_old_logs(log_dir: Path, keep: int) -> None:
    """清理旧的日志文件。
    
    保留最新的指定数量的日志文件，删除其他旧文件。
    
    Args:
        log_dir: 日志目录路径
        keep: 要保留的最新日志文件数量
    """
    files = [p for p in log_dir.glob(f"{LOG_PREFIX}*.log") if p.is_file()]
    # 按修改时间排序，最新的在前面
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    # 删除超出保留数量的文件
    for file_path in files[max(0, int(keep)) :]:
        try:
            file_path.unlink()
        except Exception:
            pass


def _remove_legacy_logs(log_dir: Path) -> None:
    """清理旧版本的日志文件。
    
    删除旧版本的日志文件格式，保持目录整洁。
    
    Args:
        log_dir: 日志目录路径
    """
    # 旧版本的日志文件格式
    legacy_candidates = [log_dir / "museplayer.log", *log_dir.glob("museplayer.log.*")]
    for file_path in legacy_candidates:
        if not file_path.is_file():
            continue
        try:
            file_path.unlink()
        except Exception:
            pass
