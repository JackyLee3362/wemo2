import logging
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from wemo2.collectors.airtest_logging import AirtestLogHandler
from wemo2.collectors.windows.backend import create_device


class AirtestBackendTests(unittest.TestCase):
    """验证 Airtest 日志转接与延迟加载，确保普通 CLI 启动不连接平台设备。"""

    def test_device_logs_use_loguru_instead_of_terminal_handler(self):
        windows = MagicMock()
        airtest_logger = logging.getLogger("airtest")
        old_handlers = airtest_logger.handlers[:]
        old_propagate = airtest_logger.propagate
        try:
            with patch.dict(
                sys.modules, {"airtest.core.win": SimpleNamespace(Windows=windows)}
            ):
                self.assertEqual(create_device(handle=123), windows.return_value)
            windows.assert_called_once_with(handle=123)
            self.assertEqual(len(airtest_logger.handlers), 1)
            self.assertIsInstance(airtest_logger.handlers[0], AirtestLogHandler)
            self.assertFalse(airtest_logger.propagate)
            record = logging.LogRecord(
                "airtest", logging.INFO, "", 0, "test %s", ("message",), None
            )
            with patch("wemo2.collectors.airtest_logging.logger") as logger:
                airtest_logger.handlers[0].emit(record)
                logger.opt.return_value.log.assert_called_once_with(
                    "INFO", "test message"
                )
        finally:
            airtest_logger.handlers = old_handlers
            airtest_logger.propagate = old_propagate

    def test_cli_import_does_not_load_airtest_or_platform_libraries(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import wemo2.cli.commands; assert 'airtest' not in sys.modules; assert 'pywinauto' not in sys.modules",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
