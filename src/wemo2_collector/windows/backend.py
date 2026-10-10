"""Airtest 的延迟加载与日志接入。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from airtest.core.win import Windows


class AirtestLogHandler(logging.Handler):
    """将 Airtest 日志交给应用的 Loguru 文件 sink。"""

    def emit(self, record: logging.LogRecord) -> None:
        logger.opt(exception=record.exc_info).log(record.levelname, record.getMessage())


def create_device(**options: object) -> Windows:
    """创建独立设备实例，不使用 Airtest 的全局当前设备。"""
    # Airtest 导入会设置标准日志输出；替换为项目的文件日志通道。
    from airtest.core.win import Windows

    airtest_logger = logging.getLogger("airtest")
    airtest_logger.handlers = [AirtestLogHandler()]
    airtest_logger.propagate = False
    return Windows(**options)
