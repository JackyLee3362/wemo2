import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from typer.testing import CliRunner

from wemo2.cli.commands import cli
from wemo2.core.config import AppConfig, load_config
from wemo2.core.registry import collectors


class ConfigTests(unittest.TestCase):
    """验证配置优先级、帮助、默认控制台日志及显式文件输出。"""

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
        with patch("wemo2.core.config.Path.cwd", return_value=self.cwd):
            return CliRunner().invoke(cli, args)

    def test_default_collector_and_shared_resource_paths(self):
        config = load_config(cwd=self.cwd)
        self.assertEqual(config.collector, "windows_screenshot")
        self.assertEqual(config.store, "pseudo")
        self.assertEqual(config.resource_dir, self.cwd / "data/moments")
        self.assertEqual(config.account, "default")
        self.assertIsNone(config.log_file)

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

    def test_maximize_defaults_on_and_file_can_disable_it(self):
        config = load_config(cwd=self.cwd)
        collector = collectors.create(config.collector, config.model_dump())
        self.assertTrue(collector.driver.maximize_window)
        path = self.write_config("[collector_options]\nmaximize_window=false\n")
        config = load_config(config_path=path, cwd=self.cwd)
        collector = collectors.create(config.collector, config.model_dump())
        self.assertFalse(collector.driver.maximize_window)

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
            with self.subTest(args=args), patch("wemo2.cli.runtime.launch") as launch:
                result = self.invoke(args)
                self.assertEqual(result.exit_code, 0, result.output)
                settings = launch.call_args.args[1]
                self.assertEqual(settings.collector, "pseudo")
                self.assertEqual(settings.log_level, "DEBUG")

    def test_save_memos_selects_explicit_destination(self):
        path = self.write_config('collector="pseudo"\n')
        with patch("wemo2.cli.runtime.launch") as launch:
            result = self.invoke(["save", "--memos", "--config", str(path)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(launch.call_args.args[0], "save")
        self.assertTrue(launch.call_args.args[2])

    def test_user_option_overrides_account_and_subcommand_overrides_root(self):
        path = self.write_config('collector="pseudo"\naccount="configured"\n')
        for args, expected in (
            (["collect"], "configured"),
            (["collect", "--user", "debug-1"], "debug-1"),
            (["--user", "debug-2", "collect"], "debug-2"),
            (["--user", "root", "collect", "--user", "child"], "child"),
            (["save", "--user", "debug-1"], "debug-1"),
        ):
            with self.subTest(args=args), patch("wemo2.cli.runtime.launch") as launch:
                result = self.invoke(["--config", str(path), *args])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(launch.call_args.args[1].account, expected)

    def test_user_option_rejects_invalid_directory_before_running(self):
        for user in ("../default", "", "CON", "A/B"):
            with self.subTest(user=user), patch("wemo2.cli.runtime.launch") as launch:
                result = self.invoke(["collect", "--user", user])
                self.assertEqual(result.exit_code, 2, result.output)
                launch.assert_not_called()

    def test_user_option_writes_separate_archive_without_touching_default(self):
        path = self.write_config('collector="pseudo"\nresource_dir="resources"\n')
        self.assertEqual(self.invoke(["collect", "--config", str(path)]).exit_code, 0)
        original = self.cwd / "resources" / "default"
        before = {}
        for file in original.rglob("*"):
            if file.is_file():
                before[file.relative_to(original)] = file.read_bytes()
        self.assertTrue(before)
        result = self.invoke(["collect", "--config", str(path), "--user", "debug-new"])
        self.assertEqual(result.exit_code, 0, result.output)
        debug = self.cwd / "resources" / "debug-new"
        self.assertTrue(list(debug.rglob("post.json")))
        for relative, content in before.items():
            self.assertEqual((original / relative).read_bytes(), content)

    def test_cli_log_path_overrides_file_without_overriding_business_settings(self):
        path = self.write_config(
            'collector="pseudo"\nlog_file="logs/file.log"\naccount="A"\n'
        )
        with patch("wemo2.cli.runtime.launch") as launch:
            result = self.invoke(
                ["collect", "--config", str(path), "--log-file", "logs/cli.log"]
            )
        self.assertEqual(result.exit_code, 0, result.output)
        config = launch.call_args.args[1]
        self.assertEqual(config.log_file, self.cwd / "logs/cli.log")
        self.assertEqual(config.account, "A")

    def test_no_arguments_only_show_help_without_logs_or_components(self):
        with (
            patch("wemo2.cli.commands.initialize_startup_logging") as initialize,
            patch("wemo2.core.services.run_collect") as run,
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
                patch("wemo2.cli.commands.load_config") as loader,
                patch("wemo2.cli.commands.initialize_startup_logging") as initialize,
            ):
                result = self.invoke(args)
                self.assertEqual(result.exit_code, 0, result.output)
                loader.assert_not_called()
                initialize.assert_not_called()

    def test_collect_failure_has_nonzero_exit_and_no_success_result(self):
        path = self.write_config('collector="pseudo"\n')
        with patch(
            "wemo2.core.services.run_collect", side_effect=RuntimeError("failed")
        ):
            result = self.invoke(["collect", "--config", str(path)])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("执行失败", result.stderr)
        self.assertIn("Traceback", result.stderr)
        self.assertIn("RuntimeError: failed", result.stderr)
        self.assertFalse((self.cwd / "logs").exists())

    def test_save_missing_archive_does_not_report_success(self):
        path = self.write_config('collector="pseudo"\n')
        result = self.invoke(["save", "--memos", "--config", str(path)])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("未找到朋友圈归档", result.stderr)
        self.assertNotIn("保存流程完成", result.stdout)

    def test_invalid_configuration_stops_before_execution_and_logs(self):
        path = self.write_config('unknown="value"\n')
        with patch("wemo2.cli.runtime.launch") as launch:
            result = self.invoke(["collect", "--config", str(path)])
        self.assertEqual(result.exit_code, 2)
        launch.assert_not_called()
        self.assertIn("配置加载失败", result.stderr)
        self.assertFalse((self.cwd / "logs").exists())
        with self.assertRaises(FileNotFoundError):
            load_config(config_path="missing.toml", cwd=self.cwd)

    def test_file_log_initialization_failure_keeps_console_error_log(self):
        blocked = self.cwd / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        with patch("wemo2.core.services.run_collect") as run:
            result = self.invoke(["collect", "--log-file", "blocked/run.log"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("无法初始化日志输出", result.stderr)
        self.assertIn("无法初始化运行日志", result.stderr)
        self.assertIn("Traceback", result.stderr)
        run.assert_not_called()

    def test_default_cli_prints_steps_to_stderr_without_creating_log_files(self):
        path = self.write_config('collector="pseudo"\n')
        result = self.invoke(["collect", "--config", str(path)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("采集完成", result.stdout)
        self.assertIn("[命令] 开始执行 collect", result.stderr)
        self.assertIn("[采集] 已创建采集器", result.stderr)
        self.assertIn("[命令] collect 执行完成", result.stderr)
        self.assertNotIn("日志文件", result.stdout)
        self.assertFalse((self.cwd / "logs").exists())

    def test_file_log_is_optional_and_does_not_disable_console_output(self):
        path = self.write_config('collector="pseudo"\nlog_file="logs/from-file.log"\n')
        result = self.invoke(
            ["collect", "--config", str(path), "--log-file", "logs/from-cli.log"]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("[命令] 开始执行 collect", result.stderr)
        content = (self.cwd / "logs/from-cli.log").read_text(encoding="utf-8")
        self.assertIn("[命令] 开始执行 collect", content)
        self.assertIn("[命令] collect 执行完成", content)
        self.assertFalse((self.cwd / "logs/from-file.log").exists())

    def test_each_invocation_outputs_each_command_step_only_once(self):
        path = self.write_config('collector="pseudo"\n')
        for _ in range(2):
            result = self.invoke(["collect", "--config", str(path)])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(result.stderr.count("[命令] 开始执行 collect"), 1)

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
