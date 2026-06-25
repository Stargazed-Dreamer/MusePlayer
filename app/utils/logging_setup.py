from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

LOGGER_ROOT = "museplayer"
LOG_PREFIX = "museplayer_"
MAX_LOG_FILES = 10

_session_log_path: Path | None = None


def configure_logging(data_dir: Path, enabled: bool, current_log_path: Path | None = None) -> Path | None:
    """配置日志记录器，根据启用状态决定创建新日志文件或复用已有文件。

    Args:
        data_dir (Path): 数据目录路径，用于存放日志文件。
        enabled (bool): 是否启用日志记录。
        current_log_path (Path | None, optional): 当前已使用的日志文件路径，用于尝试复用。默认为 None。

    Returns:
        Path | None: 启用时返回有效的日志文件路径，禁用时返回 None。
    """
    global _session_log_path

    logger = logging.getLogger(LOGGER_ROOT)
    logger.propagate = False  # 阻止日志向父级记录器传递，避免重复输出

    if enabled:
        # 检查是否已有文件处理器，避免重复添加
        existing_file_handler = None
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", None):
                existing_file_handler = h
                break
        if existing_file_handler is not None:
            # 如果已有处理器，返回当前会话日志路径或传入的路径
            return _session_log_path or current_log_path

        # 尝试复用已存在的日志文件
        reuse_path = _session_log_path or current_log_path
        if reuse_path is not None and reuse_path.exists():
            handler = logging.FileHandler(reuse_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            return reuse_path

    # 清理所有现有处理器并关闭文件句柄，防止资源泄漏
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    if not enabled:
        # 禁用日志时设置最高级别，并添加空处理器避免"no handlers"警告
        logger.setLevel(logging.CRITICAL + 1)
        logger.addHandler(logging.NullHandler())
        return None

    logger.setLevel(logging.INFO)
    log_dir = Path(data_dir).resolve() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)  # 创建日志目录，如果不存在

    _remove_legacy_logs(log_dir)  # 清理旧版日志文件
    _prune_old_logs(log_dir, keep=max(0, MAX_LOG_FILES - 1))  # 保留最近的日志文件，避免数量超过限制

    # 生成带时间戳的唯一日志文件名
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = log_dir / f"{LOG_PREFIX}{stamp}.log"
    _session_log_path = log_file  # 记录当前会话的日志文件路径

    # 创建新的文件处理器并设置格式
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)

    _prune_old_logs(log_dir, keep=MAX_LOG_FILES)  # 再次清理，确保日志文件数量不超过限制
    return log_file


def get_logger(name: str | None = None) -> logging.Logger:
    """获取或创建一个日志记录器。

    参数:
        name (str | None): 日志记录器的名称，默认为None。
    返回:
        logging.Logger: 一个日志记录器对象。
    """
    if not name:  # 如果名称为空或未提供
        return logging.getLogger(LOGGER_ROOT)  # 返回根日志记录器
    return logging.getLogger(f"{LOGGER_ROOT}.{name}")  # 返回以指定名称命名的子日志记录器


def _prune_old_logs(log_dir: Path, keep: int) -> None:
    """修剪旧日志文件，保留最新的指定数量的日志文件。

    参数：
        log_dir (Path): 日志文件所在的目录路径。
        keep (int): 要保留的最新日志文件数量。

    返回：
        None: 无返回值。
    """
    # 获取日志目录下所有匹配的日志文件，并确保是文件
    files = [p for p in log_dir.glob(f"{LOG_PREFIX}*.log") if p.is_file()]
    # 按修改时间降序排序，最新的文件在前
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    # 遍历除了最新的keep个文件以外的所有文件进行删除，使用max确保索引非负
    for file_path in files[max(0, int(keep)) :]:
        try:
            # 删除日志文件
            file_path.unlink()
        except Exception:
            # 删除失败时忽略异常
            pass


def _remove_legacy_logs(log_dir: Path) -> None:
    """移除指定目录下的旧日志文件。

    此函数会删除指定日志目录中名为 'museplayer.log' 的主日志文件，
    以及所有以 'museplayer.log.' 开头的滚动日志文件（如 museplayer.log.1, museplayer.log.2 等）。
    删除过程中遇到任何异常都会被静默忽略。

    参数:
        log_dir (Path): 包含待删除日志文件的目录路径。

    返回:
        None: 此函数没有返回值。
    """
    # 构建待检查的文件路径列表，包括主日志文件和所有滚动日志文件
    legacy_candidates = [log_dir / "museplayer.log", *log_dir.glob("museplayer.log.*")]
    
    # 遍历所有候选文件路径
    for file_path in legacy_candidates:
        # 检查路径是否指向一个实际存在的文件，如果不是则跳过
        if not file_path.is_file():
            continue
        
        # 尝试删除文件
        try:
            file_path.unlink()
        # 捕获所有可能的异常（如权限错误、文件被占用等），并静默处理，确保函数不会因个别文件删除失败而中断
        except Exception:
            pass
