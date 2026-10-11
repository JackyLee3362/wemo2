import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from typer.testing import CliRunner

from wemo2.app import run_config
from wemo2.cli import cli
from wemo2.config import load_config


class ConfigTests(unittest.TestCase):
    """验证配置优先级、路径基准、CLI 选项、文件日志和启动失败处理。"""

    def parse_cli(self, args):
        with patch("wemo2.app.launch") as application:
            result = CliRunner().invoke(cli, args)
        self.assertEqual(result.exit_code, 0, result.output)
        application.assert_called_once()
        return application.call_args.args[0]

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cwd = Path(self.directory.name).resolve()

    def write_config(self, content, path=None):
        path = path or self.cwd / "wemo2.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_code_defaults_when_auto_file_is_absent(self):
        config = load_config(cwd=self.cwd)
        self.assertEqual((config.collector, config.store), ("pseudo", "pseudo"))
        self.assertEqual(config.database, self.cwd / "data/wemo2.sqlite3")
        self.assertEqual(config.log_file, self.cwd / "logs/wemo2.log")

    def test_priority_and_explicit_default_value(self):
        self.write_config(
            'store = "sqlite"\ndeduplicator = "stable"\nlog_level = "DEBUG"\n'
        )
        config = load_config({"store": "pseudo", "log_level": "INFO"}, cwd=self.cwd)
        self.assertEqual(config.store, "pseudo")
        self.assertEqual(config.log_level, "INFO")
        self.assertEqual(config.deduplicator, "stable")
        self.assertEqual(config.collector, "pseudo")

    def test_file_and_cli_paths_have_distinct_base_directories(self):
        path = self.write_config(
            'collector = "json"\ndatabase = "saved.sqlite3"\n[collector_options]\npath = "input.json"\n',
            self.cwd / "settings/custom.toml",
        )
        config = load_config({"database": "override.sqlite3"}, path, cwd=self.cwd)
        self.assertEqual(config.database, self.cwd / "override.sqlite3")
        self.assertEqual(config.collector_options["path"], "input.json")

    def test_explicit_missing_file_and_invalid_config_report_errors(self):
        with self.assertRaises(FileNotFoundError):
            load_config(config_path="missing.toml", cwd=self.cwd)
        for content in ('unknown = "value"', 'store = "memos"', 'collector = "json"'):
            with self.subTest(content=content):
                self.write_config(content)
                with self.assertRaises(ValidationError):
                    load_config(cwd=self.cwd)

    def test_json_defaults_do_not_override_explicit_file_options(self):
        self.write_config(
            'collector = "json"\n[collector_options]\npath = "input.json"\n'
        )
        config = load_config(cwd=self.cwd)
        self.assertEqual((config.deduplicator, config.store), ("stable", "sqlite"))
        self.write_config(
            'collector = "json"\nstore = "pseudo"\n[collector_options]\npath = "input.json"\n'
        )
        self.assertEqual(load_config(cwd=self.cwd).store, "pseudo")

    def test_typer_omitted_values_do_not_overwrite_file(self):
        self.write_config('store = "sqlite"\nlog_level = "DEBUG"\n')
        with patch("wemo2.config.Path.cwd", return_value=self.cwd):
            config = self.parse_cli([])
        self.assertEqual(config.store, "sqlite")
        self.assertEqual(config.log_level, "DEBUG")

    def test_json_input_is_loaded_from_config(self):
        path = self.write_config(
            'collector = "json"\n[collector_options]\npath = "records.json"\n'
        )
        with patch("wemo2.config.Path.cwd", return_value=self.cwd):
            config = self.parse_cli(["--config", str(path)])
        self.assertEqual((config.collector, config.store), ("json", "sqlite"))
        self.assertEqual(config.collector_options["path"], "records.json")

    def test_invalid_cli_input_exits_before_execution(self):
        path = self.write_config('collector = "json"\n')
        with (
            patch("wemo2.config.Path.cwd", return_value=self.cwd),
            patch("wemo2.app.launch") as application,
        ):
            result = CliRunner().invoke(cli, ["--config", str(path)])
        self.assertEqual(result.exit_code, 2)
        application.assert_not_called()

    def test_cli_config_runs_selected_store(self):
        self.write_config(
            'store = "sqlite"\ndeduplicator = "stable"\ndatabase = "backup.sqlite3"\n'
        )
        config = load_config(cwd=self.cwd)
        self.assertEqual(run_config(config).created, 2)
        self.assertTrue(config.database.exists())
        self.assertEqual(run_config(config).unchanged, 3)

    def test_no_arguments_shows_rich_result_and_writes_file_log(self):
        with patch("wemo2.config.Path.cwd", return_value=self.cwd):
            result = CliRunner().invoke(cli, [])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("采集完成", result.stdout)
        self.assertIn("新增", result.stdout)
        self.assertIn("日志文件", result.stdout)
        self.assertNotIn("INFO", result.stdout)
        self.assertEqual(result.stderr, "")
        log = (self.cwd / "logs/wemo2.log").read_text(encoding="utf-8")
        self.assertIn("采集完成", log)
        self.assertIn("INFO", log)

    def test_execution_failure_exits_one_without_success_result(self):
        with (
            patch("wemo2.config.Path.cwd", return_value=self.cwd),
            patch("wemo2.app.run_config", side_effect=RuntimeError("failed")),
        ):
            result = CliRunner().invoke(cli, [])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("执行失败", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        log = (self.cwd / "logs/wemo2.log").read_text(encoding="utf-8")
        self.assertIn("RuntimeError: failed", log)

    def test_log_file_priority_and_relative_paths(self):
        path = self.write_config(
            'log_file = "logs/file.log"\n', self.cwd / "settings/custom.toml"
        )
        file_config = load_config(config_path=path, cwd=self.cwd)
        self.assertEqual(file_config.log_file, self.cwd / "settings/logs/file.log")
        with patch("wemo2.config.Path.cwd", return_value=self.cwd):
            config = self.parse_cli(
                ["--config", str(path), "--log-file", "logs/cli.log"]
            )
        self.assertEqual(config.log_file, self.cwd / "logs/cli.log")

    def test_invalid_log_path_reports_error_before_running(self):
        blocked = self.cwd / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        with (
            patch("wemo2.config.Path.cwd", return_value=self.cwd),
            patch("wemo2.app.run_config") as run,
        ):
            result = CliRunner().invoke(cli, ["--log-file", "blocked/wemo2.log"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("无法初始化日志文件", result.stderr)
        run.assert_not_called()

    def test_help_and_version_do_not_load_config_or_launch(self):
        for option in ("--help", "--version"):
            with (
                self.subTest(option=option),
                patch("wemo2.cli.load_config") as loader,
                patch("wemo2.app.launch") as application,
            ):
                result = CliRunner().invoke(cli, [option])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("wemo2", result.output)
                loader.assert_not_called()
                application.assert_not_called()

    def test_complex_settings_are_not_cli_options(self):
        for option in (
            "--collector",
            "--deduplicator",
            "--store",
            "--import-json",
            "--database",
            "--log-level",
            "--duplicate-stop",
            "--duplicate-limit",
        ):
            args = [option, "value"]
            with self.subTest(args=args), patch("wemo2.app.launch") as application:
                result = CliRunner().invoke(cli, args)
                self.assertEqual(result.exit_code, 2)
                application.assert_not_called()

    def test_help_only_lists_four_supported_options(self):
        result = CliRunner().invoke(cli, ["--help"])
        self.assertEqual(result.exit_code, 0)
        for option in ("--help", "--version", "--config", "--log-file"):
            self.assertIn(option, result.stdout)
        for option in ("--collector", "--store", "--log-level", "--duplicate-stop"):
            self.assertNotIn(option, result.stdout)
