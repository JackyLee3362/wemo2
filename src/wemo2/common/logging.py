"""文件日志初始化；配置尚未加载时也能记录启动错误。"""

from pathlib import Path
from tempfile import gettempdir

from loguru import logger


def add_file_sink(path: Path, level: str = "INFO") -> int:
    """添加文件 sink；新文件成功打开之前保留已有启动日志。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    return logger.add(
        path,
        level=level,
        encoding="utf-8",
        rotation="10 MB",
        retention="7 days",
        diagnose=False,
        backtrace=False,
    )


def initialize_startup_logging() -> tuple[int, Path]:
    """启动错误默认写入 logs/wemo2.log，目录不可写时使用系统临时目录。"""
    logger.remove()
    path = (Path.cwd() / "logs/wemo2.log").resolve()
    try:
        sink = add_file_sink(path, "ERROR")
    except (OSError, ValueError) as error:
        path = Path(gettempdir()) / "wemo2/startup.log"
        sink = add_file_sink(path, "ERROR")
        logger.exception("默认启动日志无法初始化，改写 {}：{}", path, error)
    return sink, path
