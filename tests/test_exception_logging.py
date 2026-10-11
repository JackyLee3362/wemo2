import ast
import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from loguru import logger
from PIL import Image
from typer.testing import CliRunner

from wemo2.cli.commands import cli
from wemo2.collectors.controller import UiController
from wemo2.collectors.models import (
    ControllerConfig,
    Platform,
    Rect,
    Screenshot,
    Surface,
)
from wemo2.collectors.profiles import UiProfile
from wemo2.common.logging import add_file_sink, initialize_startup_logging


class ExceptionLoggingTests(unittest.TestCase):
    """验证控制台与文件异常日志，覆盖启动、重试、回滚和所有捕获分支。"""

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
                    # 重新抛出、跳过或返回前，必须先留下异常堆栈。
                    if isinstance(statement, (ast.Raise, ast.Return, ast.Continue)):
                        self.assertTrue(logged, f"{path}:{node.lineno}")
                self.assertTrue(logged, f"{path}:{node.lineno}")

    def test_cli_config_error_prints_stack_to_console_without_creating_file(self):
        config = self.path / "bad.toml"
        config.write_text(
            'collector = "windows_screenshot"\n[collector_options]\ntimezone = "Invalid/Timezone"\n',
            encoding="utf-8",
        )
        with patch("wemo2.core.config.Path.cwd", return_value=self.path):
            result = CliRunner().invoke(cli, ["collect", "--config", str(config)])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("解析时区", result.stderr)
        self.assertIn("配置加载失败", result.stderr)
        self.assertIn("Invalid/Timezone", result.stderr)
        self.assertIn("Traceback", result.stderr)
        self.assertFalse((self.path / "logs").exists())

    def test_invalid_running_log_path_is_recorded_on_console(self):
        blocked = self.path / "blocked"
        blocked.write_text("file", encoding="utf-8")
        with patch("wemo2.core.config.Path.cwd", return_value=self.path):
            result = CliRunner().invoke(
                cli, ["collect", "--log-file", str(blocked / "run.log")]
            )
        self.assertEqual(result.exit_code, 1)
        self.assertIn("无法初始化运行日志", result.stderr)
        self.assertIn("blocked", result.stderr)
        self.assertIn("Traceback", result.stderr)
        self.assertFalse((self.path / "logs").exists())

    def test_startup_logging_uses_current_stderr_without_touching_disk(self):
        output = io.StringIO()
        with (
            patch("wemo2.common.logging.sys.stderr", output),
            patch("wemo2.common.logging.add_file_sink") as add_file,
        ):
            sink = initialize_startup_logging()
            logger.error("configuration unavailable")
            logger.remove(sink)
        add_file.assert_not_called()
        self.assertIn("configuration unavailable", output.getvalue())

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
        with Image.new("RGB", (10, 10), "white") as image:
            image.save(frame.image_path)
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
            UiProfile(),
            ControllerConfig(capture_attempts=2),
        )
        with patch("wemo2.collectors.controller.time.sleep"):
            self.assertEqual(controller.capture(), frame)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("第 1/2 次", log)
        self.assertIn("OSError: temporary capture failure", log)
        driver.click.assert_not_called()

    def test_help_and_version_do_not_initialize_startup_log(self):
        with patch("wemo2.cli.commands.initialize_startup_logging") as initialize:
            for option in ("--help", "--version"):
                result = CliRunner().invoke(cli, [option])
                self.assertEqual(result.exit_code, 0)
            initialize.assert_not_called()
