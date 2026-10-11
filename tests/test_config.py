import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from typer.testing import CliRunner

from wemo2.cli import cli
from wemo2.config import AppConfig, load_config


class ConfigTests(unittest.TestCase):
    """验证两个子命令的配置优先级、共用路径、帮助与文件日志。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cwd = Path(self.directory.name).resolve()

    def write_config(self, content, path=None):
        path = path or self.cwd / "wemo2.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def invoke(self, args):
        with patch("wemo2.config.Path.cwd", return_value=self.cwd):
            return CliRunner().invoke(cli, args)

    def test_default_collector_and_shared_resource_paths(self):
        config = load_config(cwd=self.cwd)
        self.assertEqual(config.collector, "windows_screenshot")
        self.assertEqual(config.store, "pseudo")
        self.assertEqual(config.resource_dir, self.cwd / "data/moments")
        self.assertEqual(config.account, "default")
        self.assertEqual(config.log_file, self.cwd / "logs/wemo2.log")

    def test_priority_and_explicit_default_value(self):
        path = self.write_config(
            'collector="pseudo"\nstore="memos"\nlog_level="DEBUG"\n'
        )
        config = load_config(
            {"store": "pseudo", "log_level": "INFO"}, path, cwd=self.cwd
        )
        self.assertEqual(config.store, "pseudo")
        self.assertEqual(config.log_level, "INFO")
        self.assertEqual(config.collector, "pseudo")

    def test_file_paths_and_cli_paths_use_separate_base_directories(self):
        path = self.write_config(
            'resource_dir="backup"\nlog_file="logs/file.log"\n',
            self.cwd / "settings/custom.toml",
        )
        config = load_config({"log_file": "logs/cli.log"}, path, cwd=self.cwd)
        self.assertEqual(config.resource_dir, self.cwd / "settings/backup")
        self.assertEqual(config.log_file, self.cwd / "logs/cli.log")

    def test_legacy_resource_options_migrate_to_shared_fields(self):
        path = self.write_config(
            '[collector_options]\nresource_dir="backup"\naccount="账号A"\n'
        )
        config = load_config(config_path=path, cwd=self.cwd)
        self.assertEqual(config.resource_dir, self.cwd / "backup")
        self.assertEqual(config.account, "账号A")
        self.assertNotIn("resource_dir", config.collector_options)
        self.assertNotIn("account", config.collector_options)

    def test_conflicting_resource_configuration_is_rejected(self):
        path = self.write_config('account="A"\n[collector_options]\naccount="B"\n')
        with self.assertRaisesRegex(ValueError, "配置冲突"):
            load_config(config_path=path, cwd=self.cwd)

    def test_removed_deduplication_and_database_options_are_rejected(self):
        for values in (
            {"deduplicator": "pseudo"},
            {"duplicate_stop": "never"},
            {"database": "old.db"},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AppConfig(**values)

    def test_config_placement_and_omitted_cli_parameters(self):
        path = self.write_config('collector="pseudo"\nlog_level="DEBUG"\n')
        for args in (
            ["collect", "--config", str(path)],
            ["--config", str(path), "collect"],
            ["save", "--config", str(path)],
        ):
            with self.subTest(args=args), patch("wemo2.app.launch") as launch:
                result = self.invoke(args)
                self.assertEqual(result.exit_code, 0, result.output)
                settings = launch.call_args.args[1]
                self.assertEqual(settings.collector, "pseudo")
                self.assertEqual(settings.log_level, "DEBUG")

    def test_save_memos_selects_explicit_destination(self):
        path = self.write_config('collector="pseudo"\n')
        with patch("wemo2.app.launch") as launch:
            result = self.invoke(["save", "--memos", "--config", str(path)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(launch.call_args.args[0], "save")
        self.assertTrue(launch.call_args.args[2])

    def test_cli_log_path_overrides_file_without_overriding_business_settings(self):
        path = self.write_config(
            'collector="pseudo"\nlog_file="logs/file.log"\naccount="A"\n'
        )
        with patch("wemo2.app.launch") as launch:
            result = self.invoke(
                ["collect", "--config", str(path), "--log-file", "logs/cli.log"]
            )
        self.assertEqual(result.exit_code, 0, result.output)
        config = launch.call_args.args[1]
        self.assertEqual(config.log_file, self.cwd / "logs/cli.log")
        self.assertEqual(config.account, "A")

    def test_no_arguments_only_show_help_without_logs_or_components(self):
        with (
            patch("wemo2.cli.initialize_startup_logging") as initialize,
            patch("wemo2.app.run_collect") as run,
        ):
            result = self.invoke([])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("collect", result.stdout)
        self.assertIn("save", result.stdout)
        initialize.assert_not_called()
        run.assert_not_called()

    def test_help_and_version_do_not_load_configuration(self):
        for args in (
            ["--help"],
            ["--version"],
            ["collect", "--help"],
            ["save", "--help"],
        ):
            with (
                self.subTest(args=args),
                patch("wemo2.cli.load_config") as loader,
                patch("wemo2.cli.initialize_startup_logging") as initialize,
            ):
                result = self.invoke(args)
                self.assertEqual(result.exit_code, 0, result.output)
                loader.assert_not_called()
                initialize.assert_not_called()

    def test_collect_failure_has_nonzero_exit_and_no_success_result(self):
        path = self.write_config('collector="pseudo"\n')
        with patch("wemo2.app.run_collect", side_effect=RuntimeError("failed")):
            result = self.invoke(["collect", "--config", str(path)])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("执行失败", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn(
            "RuntimeError: failed",
            (self.cwd / "logs/wemo2.log").read_text(encoding="utf-8"),
        )

    def test_save_missing_archive_does_not_report_success(self):
        path = self.write_config('collector="pseudo"\n')
        result = self.invoke(["save", "--memos", "--config", str(path)])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("未找到朋友圈归档", result.stderr)
        self.assertNotIn("保存流程完成", result.stdout)

    def test_invalid_configuration_stops_before_execution_and_logs(self):
        path = self.write_config('unknown="value"\n')
        with patch("wemo2.app.launch") as launch:
            result = self.invoke(["collect", "--config", str(path)])
        self.assertEqual(result.exit_code, 2)
        launch.assert_not_called()
        self.assertIn(
            "配置加载失败", (self.cwd / "logs/wemo2.log").read_text(encoding="utf-8")
        )
        with self.assertRaises(FileNotFoundError):
            load_config(config_path="missing.toml", cwd=self.cwd)

    def test_log_initialization_failure_retains_startup_log(self):
        blocked = self.cwd / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        with patch("wemo2.app.run_collect") as run:
            result = self.invoke(["collect", "--log-file", "blocked/run.log"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("无法初始化日志文件", result.stderr)
        self.assertIn("详细日志", result.stderr)
        run.assert_not_called()

    def test_business_options_remain_in_toml(self):
        for option in (
            "--collector",
            "--store",
            "--database",
            "--log-level",
            "--duplicate-stop",
        ):
            with self.subTest(option=option):
                self.assertEqual(self.invoke(["collect", option, "value"]).exit_code, 2)
