import ast
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from loguru import logger
from typer.testing import CliRunner

from wemo2.cli import cli
from wemo2.logging import add_file_sink, initialize_startup_logging
from wemo2.pseudo import PseudoStore
from wemo2.store import SQLiteStore
from wemo2_collector.controller import UiController
from wemo2_collector.models import ControllerConfig, Platform, Rect, Screenshot, Surface


class ExceptionLoggingTests(unittest.TestCase):
    """验证异常日志实际落盘，覆盖启动、重试、回滚以及所有捕获分支的规范。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.log = self.path / "errors.log"
        logger.remove()
        add_file_sink(self.log, "ERROR")
        self.addCleanup(logger.remove)

    def test_all_exception_handlers_log_before_handling_error(self):
        root = Path(__file__).parents[1] / "src"
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                logged = False
                for statement in node.body:
                    if isinstance(statement, ast.Expr) and isinstance(
                        statement.value, ast.Call
                    ):
                        function = statement.value.func
                        if (
                            isinstance(function, ast.Attribute)
                            and isinstance(function.value, ast.Name)
                            and function.value.id == "logger"
                            and function.attr == "exception"
                        ):
                            logged = True
                    # 启动日志回退要先准备文件 sink，但不能未经记录就退出该分支。
                    if isinstance(statement, (ast.Raise, ast.Return, ast.Continue)):
                        self.assertTrue(logged, f"{path}:{node.lineno}")
                self.assertTrue(logged, f"{path}:{node.lineno}")

    def test_cli_config_error_writes_stack_without_terminal_traceback(self):
        config = self.path / "bad.toml"
        config.write_text(
            'collector = "windows_screenshot"\n[collector_options]\ntimezone = "Invalid/Timezone"\n',
            encoding="utf-8",
        )
        with patch("wemo2.config.Path.cwd", return_value=self.path):
            result = CliRunner().invoke(cli, ["--config", str(config)])
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn("Traceback", result.stderr)
        log = (self.path / "logs/wemo2.log").read_text(encoding="utf-8")
        self.assertIn("解析时区", log)
        self.assertIn("配置加载失败", log)
        self.assertIn("Invalid/Timezone", log)
        self.assertIn("Traceback", log)

    def test_invalid_running_log_path_is_recorded_in_startup_log(self):
        blocked = self.path / "blocked"
        blocked.write_text("file", encoding="utf-8")
        with patch("wemo2.config.Path.cwd", return_value=self.path):
            result = CliRunner().invoke(cli, ["--log-file", str(blocked / "run.log")])
        self.assertEqual(result.exit_code, 1)
        self.assertNotIn("Traceback", result.stderr)
        log = (self.path / "logs/wemo2.log").read_text(encoding="utf-8")
        self.assertIn("无法初始化运行日志文件", log)
        self.assertIn("blocked", log)
        self.assertIn("Traceback", log)

    def test_unwritable_startup_path_uses_fallback_and_logs_original_error(self):
        real_add = add_file_sink
        calls = 0

        def add(path, level):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError("startup directory denied")
            return real_add(path, level)

        with (
            patch("wemo2.logging.add_file_sink", side_effect=add),
            patch("wemo2.logging.gettempdir", return_value=str(self.path)),
        ):
            _, path = initialize_startup_logging()
        self.assertEqual(path, self.path / "wemo2/startup.log")
        self.assertIn(
            "PermissionError: startup directory denied",
            path.read_text(encoding="utf-8"),
        )

    def test_successful_capture_retry_logs_first_failure(self):
        surface = Surface(
            platform=Platform.windows,
            surface_id="123",
            bounds=Rect(x=0, y=0, width=10, height=10),
        )
        frame = Screenshot(
            frame_id="retry",
            captured_at=datetime.now(UTC),
            surface=surface,
            image_path=self.path / "retry.png",
        )
        driver = MagicMock()
        driver.prepare_capture.return_value = surface
        screenshotter = MagicMock()
        screenshotter.capture.side_effect = [
            OSError("temporary capture failure"),
            frame,
        ]
        controller = UiController(
            driver,
            screenshotter,
            MagicMock(),
            MagicMock(),
            ControllerConfig(capture_attempts=2),
        )
        with patch("wemo2_collector.controller.time.sleep"):
            self.assertEqual(controller.capture(), frame)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("第 1/2 次", log)
        self.assertIn("OSError: temporary capture failure", log)
        driver.click.assert_not_called()

    def test_transaction_rollback_logs_and_reraises_original_error(self):
        for store in (PseudoStore(), SQLiteStore(self.path / "store.sqlite3")):
            with store:
                with self.assertRaisesRegex(ValueError, "transaction failure"):
                    with store.transaction():
                        raise ValueError("transaction failure")
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("内存事务失败", log)
        self.assertIn("SQLite 事务失败", log)
        self.assertIn("ValueError: transaction failure", log)

    def test_help_and_version_do_not_initialize_startup_log(self):
        with patch("wemo2.cli.initialize_startup_logging") as initialize:
            for option in ("--help", "--version"):
                result = CliRunner().invoke(cli, [option])
                self.assertEqual(result.exit_code, 0)
            initialize.assert_not_called()
