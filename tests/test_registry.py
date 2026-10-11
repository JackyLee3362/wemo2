import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wemo2.app import run_collect, run_save
from wemo2.config import AppConfig
from wemo2.interfaces import BaseCollector
from wemo2.pseudo import PseudoCollector, PseudoStore
from wemo2.registry import ComponentRegistry, collectors, stores


class RegistryTests(unittest.TestCase):
    """验证唯一组件登记处、参数映射及两阶段分别构造所需实现。"""

    def test_duplicate_names_and_invalid_implementations_are_rejected(self):
        registry = ComponentRegistry("采集器", BaseCollector)
        registry.register("sample", PseudoCollector, default=True)
        with self.assertRaises(ValueError):
            registry.register("sample", PseudoCollector)
        with self.assertRaises(TypeError):
            registry.register("wrong", PseudoStore)
        with self.assertRaises(TypeError):
            registry.register("abstract", BaseCollector)
        with self.assertRaises(ValueError):
            registry.register("second", PseudoCollector, default=True)
        with self.assertRaisesRegex(ValueError, "可选：sample"):
            registry.get("missing")

    def test_parameter_mapping_does_not_pass_unrelated_fields(self):
        registry = ComponentRegistry("采集器", BaseCollector)
        registry.register(
            "custom", PseudoCollector, parameters={"resource_dir": "root"}
        )
        with self.assertRaisesRegex(ValueError, "root"):
            registry.create("custom", {})
        with tempfile.TemporaryDirectory() as directory:
            source = registry.create("custom", {"root": Path(directory), "unknown": 42})
            self.assertEqual(source.collect().posts, 1)

    def test_registered_new_collector_and_store_need_no_command_branches(self):
        created = []

        class CustomCollector(PseudoCollector):
            """记录实例化时机，验证采集命令只需要登记新的实现。"""

            def __init__(self, resource_dir, account):
                created.append("collector")
                super().__init__(resource_dir, account)

        class CustomStore(PseudoStore):
            """记录被存储的数据，验证保存命令通过注册表构造目标。"""

            def __init__(self):
                created.append("store")
                super().__init__()

        with (
            patch.dict(collectors._components),
            patch.dict(stores._components),
            tempfile.TemporaryDirectory() as directory,
        ):
            collectors.register(
                "custom",
                CustomCollector,
                parameters={"resource_dir": "resource_dir", "account": "account"},
            )
            stores.register("custom", CustomStore)
            config = AppConfig(
                collector="custom", store="custom", resource_dir=Path(directory)
            )
            self.assertEqual(run_collect(config).posts, 1)
            self.assertEqual(created, ["collector"])
            self.assertEqual(run_save(config).posts, 1)
            self.assertEqual(created, ["collector", "store"])
