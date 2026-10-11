"""Airtest 图像与设备日志共用 Loguru 文件输出，不建立额外终端日志。"""

import logging

from loguru import logger


class AirtestLogHandler(logging.Handler):
    """将 Airtest 和 OCR 的标准日志交给应用的 Loguru 文件 sink。"""

    def emit(self, record: logging.LogRecord) -> None:
        logger.opt(exception=record.exc_info).log(record.levelname, record.getMessage())


def configure_airtest_logging() -> None:
    """在 Airtest 延迟导入后替换它的默认终端 handler。"""
    airtest_logger = logging.getLogger("airtest")
    airtest_logger.handlers = [AirtestLogHandler()]
    airtest_logger.propagate = False
    logging.captureWarnings(True)
    warning_logger = logging.getLogger("py.warnings")
    warning_logger.handlers = [AirtestLogHandler()]
    warning_logger.propagate = False
