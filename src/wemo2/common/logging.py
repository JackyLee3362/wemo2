"""默认控制台日志；按需同时写文件，配置加载前也能记录启动错误。"""

import sys
from pathlib import Path

from loguru import logger


def add_console_sink(level: str = "INFO") -> int:
    """输出到当前 stderr，与 Rich 的结果输出分开，保留异常堆栈。"""
    return logger.add(
        sys.stderr,
        level=level,
        diagnose=False,
        backtrace=False,
    )


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


def initialize_startup_logging() -> int:
    """配置尚未加载时先建立控制台错误日志，不创建文件或输出目录。"""
    logger.remove()
    return add_console_sink("ERROR")
