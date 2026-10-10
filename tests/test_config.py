import json
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
            'database = "saved.sqlite3"\njson_path = "input.json"\n',
            self.cwd / "settings/custom.toml",
        )
        config = load_config({"database": "override.sqlite3"}, path, cwd=self.cwd)
        self.assertEqual(config.database, self.cwd / "override.sqlite3")
        self.assertEqual(config.json_path, self.cwd / "settings/input.json")

    def test_explicit_missing_file_and_invalid_config_report_errors(self):
        with self.assertRaises(FileNotFoundError):
            load_config(config_path="missing.toml", cwd=self.cwd)
        for content in ('unknown = "value"', 'store = "memos"', 'collector = "json"'):
            with self.subTest(content=content):
                self.write_config(content)
                with self.assertRaises(ValidationError):
                    load_config(cwd=self.cwd)

    def test_json_defaults_do_not_override_explicit_file_options(self):
        self.write_config('collector = "json"\njson_path = "input.json"\n')
        config = load_config(cwd=self.cwd)
        self.assertEqual((config.deduplicator, config.store), ("stable", "sqlite"))
        self.write_config(
            'collector = "json"\njson_path = "input.json"\nstore = "pseudo"\n'
        )
        self.assertEqual(load_config(cwd=self.cwd).store, "pseudo")

    def test_typer_omitted_values_do_not_overwrite_file(self):
        self.write_config('store = "sqlite"\nlog_level = "DEBUG"\n')
        with patch("wemo2.config.Path.cwd", return_value=self.cwd):
            config = self.parse_cli(["--log-level", "ERROR"])
        self.assertEqual(config.store, "sqlite")
        self.assertEqual(config.log_level, "ERROR")

    def test_import_command_infers_json(self):
        with patch("wemo2.config.Path.cwd", return_value=self.cwd):
            config = self.parse_cli(["--import-json", "records.json"])
        self.assertEqual((config.collector, config.store), ("json", "sqlite"))
        self.assertEqual(config.json_path, self.cwd / "records.json")

    def test_invalid_cli_input_exits_before_execution(self):
        with (
            patch("wemo2.config.Path.cwd", return_value=self.cwd),
            patch("wemo2.app.launch") as application,
        ):
            result = CliRunner().invoke(cli, ["--collector", "json"])
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

    def test_no_arguments_runs_cli_and_outputs_json(self):
        with patch("wemo2.config.Path.cwd", return_value=self.cwd):
            result = CliRunner().invoke(cli, [])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "collected": 3,
                "created": 2,
                "merged": 0,
                "unchanged": 1,
            },
        )

    def test_execution_failure_exits_one_without_success_result(self):
        with (
            patch("wemo2.config.Path.cwd", return_value=self.cwd),
            patch("wemo2.app.run_config", side_effect=RuntimeError("failed")),
        ):
            result = CliRunner().invoke(cli, [])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout, "")

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

    def test_typer_rejects_unknown_choice_and_conflicting_options(self):
        for args in (
            ["--store", "invalid"],
            ["--collector", "pseudo", "--import-json", "input.json"],
        ):
            with self.subTest(args=args), patch("wemo2.app.launch") as application:
                result = CliRunner().invoke(cli, args)
                self.assertEqual(result.exit_code, 2)
                application.assert_not_called()
