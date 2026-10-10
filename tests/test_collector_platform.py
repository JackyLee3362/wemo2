import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from wemo2.app import run_config
from wemo2.composition import build_coordinator
from wemo2.config import load_config
from wemo2.pseudo import PseudoCollector
from wemo2.registry import collectors
from wemo2_collector.models import Platform


class CollectorPlatformTests(unittest.TestCase):
    def test_platform_reaches_registered_collector_from_file(self):
        received = []

        class Collector(PseudoCollector):
            def __init__(self, platform):
                received.append(platform)
                super().__init__(platform=platform)

        with (
            patch.dict(collectors._components),
            tempfile.TemporaryDirectory() as directory,
        ):
            collectors.register(
                "platform-test", Collector, parameters={"platform": "platform"}
            )
            cwd = Path(directory)
            (cwd / "wemo2.toml").write_text(
                'collector = "platform-test"\nplatform = "android"\n', encoding="utf-8"
            )
            config = load_config(cwd=cwd)
            self.assertEqual(run_config(config).created, 2)
            self.assertEqual(received, [Platform.android])

    def test_default_platform_and_unknown_platform_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(cwd=Path(directory))
            self.assertEqual(config.platform, Platform.windows)
            for value in ("andriod", "linux"):
                with self.subTest(platform=value), self.assertRaises(ValidationError):
                    load_config({"platform": value}, cwd=Path(directory))

    def test_json_platform_parameter_preserves_record_source(self):
        path = Path(__file__).parents[1] / "examples/observations.json"
        original = json.loads(path.read_text(encoding="utf-8"))[0]
        coordinator = build_coordinator(
            "json", json_path=path, platform=Platform.android
        )
        with coordinator.store:
            self.assertEqual(coordinator.collector.platform, Platform.android)
            records = coordinator.collector.collect()
            source = records[0].source.model_dump(mode="json", exclude_unset=True)
            self.assertEqual(source, original["source"])
            self.assertEqual(coordinator.run().created, 1)
