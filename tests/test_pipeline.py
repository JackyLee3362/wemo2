import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from wemo2.app import run_collect, run_save
from wemo2.cli import cli
from wemo2.config import AppConfig
from wemo2.pseudo import PseudoCollector, PseudoStore


class PipelineTests(unittest.TestCase):
    """验证通过本地文件夹交付的两阶段流程，不再创建去重器或协调器。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.config = AppConfig(
            collector="pseudo", resource_dir=self.path / "resources"
        )

    def test_collect_does_not_create_store_and_save_does_not_start_collector(self):
        with patch("wemo2.registry.stores.create") as store:
            result = run_collect(self.config)
        store.assert_not_called()
        self.assertEqual(result.posts, 1)
        with patch("wemo2.registry.collectors.create") as collector:
            result = run_save(self.config, use_memos=True)
        collector.assert_not_called()
        self.assertEqual(result.store, "memos")
        self.assertEqual(result.posts, 1)

    def test_cli_collect_then_save_memos_use_the_same_archive(self):
        path = self.path / "config.toml"
        path.write_text(
            'collector="pseudo"\nresource_dir="resources"\naccount="demo"\n',
            encoding="utf-8",
        )
        with patch("wemo2.config.Path.cwd", return_value=self.path):
            collected = CliRunner().invoke(cli, ["collect", "--config", str(path)])
            saved = CliRunner().invoke(cli, ["save", "--memos", "--config", str(path)])
        self.assertEqual(collected.exit_code, 0, collected.output)
        self.assertEqual(saved.exit_code, 0, saved.output)
        self.assertIn("采集完成", collected.stdout)
        self.assertIn("尚未接入", saved.stdout)
        self.assertIn("未上传任何数据", saved.stdout)
        self.assertEqual(
            len(list((self.path / "resources/demo").rglob("post.json"))), 1
        )

    def test_collect_failure_always_releases_collector(self):
        source = MagicMock()
        source.collect.side_effect = RuntimeError("采集失败")
        with (
            patch("wemo2.registry.collectors.create", return_value=source),
            self.assertRaisesRegex(RuntimeError, "采集失败"),
        ):
            run_collect(self.config)
        source.stop.assert_called_once()

    def test_save_failure_closes_store_and_is_not_automatically_replayed(self):
        PseudoCollector(self.config.resource_dir).collect()
        store = MagicMock(spec=PseudoStore)
        store.save.side_effect = RuntimeError("保存失败")
        with (
            patch("wemo2.registry.stores.create", return_value=store),
            self.assertRaisesRegex(RuntimeError, "保存失败"),
        ):
            run_save(self.config)
        store.save.assert_called_once()
        store.close.assert_called_once()

    def test_save_failure_has_file_log_and_no_success_message(self):
        PseudoCollector(self.config.resource_dir).collect()
        path = self.path / "config.toml"
        path.write_text(
            'collector="pseudo"\nresource_dir="resources"\n', encoding="utf-8"
        )
        with (
            patch("wemo2.config.Path.cwd", return_value=self.path),
            patch(
                "wemo2.pseudo.PseudoStore.save", side_effect=RuntimeError("远端失败")
            ),
        ):
            result = CliRunner().invoke(cli, ["save", "--memos", "--config", str(path)])
        self.assertEqual(result.exit_code, 1)
        self.assertNotIn("保存流程完成", result.stdout)
        self.assertIn("远端失败", result.stderr)
        self.assertIn(
            "RuntimeError: 远端失败",
            (self.path / "logs/wemo2.log").read_text(encoding="utf-8"),
        )
