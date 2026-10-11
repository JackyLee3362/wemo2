import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from typer.testing import CliRunner

from wemo2.cli import cli
from wemo2.config import load_config
from wemo2.coordinator import Coordinator
from wemo2.deduplicator import Deduplicator
from wemo2.models import Content, StopPolicy
from wemo2.pseudo import PseudoCollector, PseudoDeduplicator, PseudoStore


class TrackingCollector(PseudoCollector):
    """跟踪记录消费数量、停止调用和生成器关闭，验证提前停止的资源处理。"""

    def __init__(self, records):
        super().__init__(records)
        self.yielded = 0
        self.stopped = False
        self.closed = False

    def collect(self):
        try:
            for observation in super().collect():
                self.yielded += 1
                yield observation
        finally:
            self.closed = True

    def stop(self):
        self.stopped = True


class StopPolicyTests(unittest.TestCase):
    """验证重复停止规则、计数重置、保存顺序及异常时的采集器释放。"""

    def setUp(self):
        records = list(PseudoCollector().collect())
        self.first = records[0]
        self.second = records[2]

    def test_first_duplicate_stops_without_reading_next_record(self):
        collector = TrackingCollector([self.first, self.first, self.second])
        coordinator = Coordinator(
            collector,
            PseudoDeduplicator(),
            PseudoStore(),
            StopPolicy(duplicate_stop="first"),
        )
        result = coordinator.run()
        self.assertEqual(result.collected, 2)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(result.stop_reason, "duplicate_limit")
        self.assertEqual(collector.yielded, 2)
        self.assertTrue(collector.stopped)
        self.assertTrue(collector.closed)

    def test_after_n_counts_total_duplicates_and_resets_each_run(self):
        collector = TrackingCollector(
            [self.first, self.first, self.second, self.first, self.second]
        )
        coordinator = Coordinator(
            collector,
            PseudoDeduplicator(),
            PseudoStore(),
            StopPolicy(duplicate_stop="after_n", duplicate_limit=2),
        )
        first_result = coordinator.run()
        self.assertEqual(first_result.collected, 4)
        self.assertEqual(first_result.created, 2)
        self.assertEqual(first_result.duplicates, 2)
        self.assertEqual(first_result.stop_reason, "duplicate_limit")
        second_result = coordinator.run()
        self.assertEqual(second_result.collected, 2)
        self.assertEqual(second_result.duplicates, 2)

    def test_never_reads_all_records_and_releases_collector(self):
        collector = TrackingCollector([self.first, self.first, self.second])
        result = Coordinator(collector, PseudoDeduplicator(), PseudoStore()).run()
        self.assertEqual(result.collected, 3)
        self.assertEqual(result.stop_reason, "completed")
        self.assertTrue(collector.closed)
        self.assertTrue(collector.stopped)

    def test_matching_updated_post_is_committed_before_stopping(self):
        updated = self.first.model_copy(deep=True)
        updated.observation_id = "updated-observation"
        updated.content = Content(text="新的正文")
        store = PseudoStore()
        collector = TrackingCollector([self.first, updated, self.second])
        result = Coordinator(
            collector, Deduplicator(), store, StopPolicy(duplicate_stop="first")
        ).run()
        self.assertEqual(result.merged, 1)
        self.assertEqual(result.duplicates, 1)
        self.assertEqual(collector.yielded, 2)
        self.assertEqual(store.list_posts()[0].content.text, "新的正文")
        self.assertEqual(store.list_posts()[0].revision, 2)

    def test_save_failure_still_releases_collector_and_rolls_back(self):
        class FailingStore(PseudoStore):
            """在保存后主动抛错，验证协调器回滚数据并关闭采集器。"""

            def save(self, observation, decision):
                super().save(observation, decision)
                raise RuntimeError("failed")

        collector = TrackingCollector([self.first, self.second])
        store = FailingStore()
        with self.assertRaises(RuntimeError):
            Coordinator(collector, PseudoDeduplicator(), store).run()
        self.assertTrue(collector.stopped)
        self.assertTrue(collector.closed)
        self.assertEqual(store.list_posts(), [])

    def test_invalid_policy_is_rejected(self):
        for options in (
            {"duplicate_stop": "unknown"},
            {"duplicate_limit": 0},
            {"duplicate_limit": True},
        ):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                StopPolicy(**options)

    def test_cli_loads_file_policy_and_runs_it(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            path = cwd / "wemo2.toml"
            path.write_text(
                'duplicate_stop = "after_n"\nduplicate_limit = 5\n', encoding="utf-8"
            )
            config = load_config({"duplicate_limit": 2}, cwd=cwd)
            self.assertEqual(config.duplicate_stop, "after_n")
            self.assertEqual(config.duplicate_limit, 2)
            path.write_text('duplicate_stop = "first"\n', encoding="utf-8")
            with patch("wemo2.config.Path.cwd", return_value=cwd):
                result = CliRunner().invoke(cli, ["--config", str(path)])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("已按重复停止策略结束采集", result.stdout)

    def test_empty_collector_completes_without_duplicates(self):
        collector = TrackingCollector([])
        result = Coordinator(
            collector,
            PseudoDeduplicator(),
            PseudoStore(),
            StopPolicy(duplicate_stop="first"),
        ).run()
        self.assertEqual(result.collected, 0)
        self.assertEqual(result.stop_reason, "completed")
        self.assertTrue(collector.stopped)
