import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wemo2.cli import cli
from wemo2.config import load_config
from wemo2.interfaces import BaseCollector
from wemo2.pseudo import PseudoCollector, PseudoDeduplicator, PseudoStore
from wemo2.registry import ComponentRegistry, collectors, deduplicators, stores


class RegistryTests(unittest.TestCase):
    def test_duplicate_name_and_invalid_implementation_are_rejected(self):
        registry = ComponentRegistry("采集器", BaseCollector)
        registry.register("sample", PseudoCollector, default=True)
        with self.assertRaises(ValueError):
            registry.register("sample", PseudoCollector)
        with self.assertRaises(TypeError):
            registry.register("wrong", PseudoStore)
        with self.assertRaises(TypeError):
            registry.register("abstract", BaseCollector)
        with self.assertRaises(ValueError):
            registry.register("second-default", PseudoCollector, default=True)
        self.assertEqual(registry.names(), ["sample"])

    def test_unknown_name_lists_available_components(self):
        registry = ComponentRegistry("采集器", BaseCollector)
        registry.register("sample", PseudoCollector)
        with self.assertRaisesRegex(ValueError, "可选：sample"):
            registry.get("missing")

    def test_constructor_receives_only_declared_parameters(self):
        class Collector(PseudoCollector):
            def __init__(self, observations):
                super().__init__(observations)

        registry = ComponentRegistry("采集器", BaseCollector)
        registry.register("custom", Collector, parameters={"observations": "records"})
        with self.assertRaisesRegex(ValueError, "records"):
            registry.create("custom", {})
        instance = registry.create("custom", {"records": [], "unrelated": "ignored"})
        self.assertEqual(list(instance.collect()), [])

    def test_registered_components_work_with_config_cli_and_composition(self):
        created = []

        class NewCollector(PseudoCollector):
            def __init__(self):
                created.append("collector")
                super().__init__()

        class NewDeduplicator(PseudoDeduplicator):
            def __init__(self):
                created.append("deduplicator")

        class NewStore(PseudoStore):
            def __init__(self, path):
                created.append(Path(path))
                super().__init__()

        with (
            patch.dict(collectors._components),
            patch.dict(deduplicators._components),
            patch.dict(stores._components),
            tempfile.TemporaryDirectory() as directory,
        ):
            collectors.register(
                "new-source",
                NewCollector,
                defaults={"deduplicator": "new-rule", "store": "new-store"},
            )
            deduplicators.register("new-rule", NewDeduplicator)
            stores.register("new-store", NewStore, parameters={"path": "database"})
            cwd = Path(directory).resolve()
            config_path = cwd / "wemo2.toml"
            config_path.write_text('collector = "new-source"\n', encoding="utf-8")
            config = load_config(cwd=cwd)
            self.assertEqual(config.deduplicator, "new-rule")
            self.assertEqual(config.store, "new-store")
            with patch("wemo2.config.Path.cwd", return_value=cwd):
                result = CliRunner().invoke(cli, ["--config", str(config_path)])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("采集完成", result.stdout)
            self.assertEqual(
                created, ["collector", "deduplicator", cwd / "data/wemo2.sqlite3"]
            )
            self.assertNotIn("模拟存储", result.stdout)
