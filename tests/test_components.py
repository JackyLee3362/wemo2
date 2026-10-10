import tempfile
import unittest
from itertools import product
from pathlib import Path

from wemo2.composition import build_coordinator
from wemo2.coordinator import Coordinator
from wemo2.deduplicator import Deduplicator
from wemo2.interfaces import BaseCollector, BaseDeduplicator, BaseStore
from wemo2.pseudo import PseudoCollector, PseudoDeduplicator, PseudoStore


class ComponentTests(unittest.TestCase):
    def test_interfaces_cannot_be_instantiated(self):
        for component in (BaseCollector, BaseDeduplicator, BaseStore):
            with self.subTest(component=component), self.assertRaises(TypeError):
                component()

    def test_pseudo_pipeline_and_repeat_run(self):
        coordinator = build_coordinator()
        result = coordinator.run()
        self.assertEqual(
            result.model_dump(),
            {
                "collected": 3,
                "created": 2,
                "merged": 0,
                "unchanged": 1,
            },
        )
        self.assertEqual(len(coordinator.store.list_posts()), 2)
        self.assertEqual(coordinator.run().unchanged, 3)

    def test_all_implementation_combinations(self):
        with tempfile.TemporaryDirectory() as directory:
            for collector, deduplicator, store in product(
                ("pseudo", "json"), ("pseudo", "stable"), ("pseudo", "sqlite")
            ):
                with self.subTest(
                    collector=collector, deduplicator=deduplicator, store=store
                ):
                    coordinator = build_coordinator(
                        collector,
                        deduplicator,
                        store,
                        json_path=Path(__file__).parents[1]
                        / "examples/observations.json",
                        database=Path(directory)
                        / f"{collector}-{deduplicator}.sqlite3",
                    )
                    with coordinator.store:
                        result = coordinator.run()
                        expected = 2 if collector == "pseudo" else 1
                        self.assertEqual(result.created, expected)
                        self.assertEqual(len(coordinator.store.list_posts()), expected)
                        self.assertEqual(coordinator.run().created, 0)

    def test_real_deduplicator_updates_pseudo_store(self):
        first = list(PseudoCollector().collect())[0]
        updated = first.model_copy(deep=True)
        updated.observation_id = "updated"
        updated.content.text = "更新后的正文"
        store = PseudoStore()
        result = Coordinator(
            PseudoCollector([first, updated]), Deduplicator(), store
        ).run()
        self.assertEqual(result.merged, 1)
        self.assertEqual(store.list_posts()[0].revision, 2)
        self.assertEqual(store.list_posts()[0].content.text, "更新后的正文")

    def test_failed_save_rolls_back_memory_store(self):
        class FailingStore(PseudoStore):
            def save(self, observation, decision):
                super().save(observation, decision)
                raise RuntimeError("模拟保存失败")

        store = FailingStore()
        with self.assertRaisesRegex(RuntimeError, "模拟保存失败"):
            Coordinator(PseudoCollector(), PseudoDeduplicator(), store).run()
        self.assertEqual(store.list_posts(), [])
        self.assertIsNone(store.get_observation("pseudo-account", "pseudo-obs-001"))

    def test_returned_models_do_not_mutate_memory_store_or_collector(self):
        collector = PseudoCollector()
        first = list(collector.collect())[0]
        first.content.text = "外部修改"
        self.assertNotEqual(list(collector.collect())[0].content.text, "外部修改")
        store = PseudoStore()
        Coordinator(collector, PseudoDeduplicator(), store).run()
        posts = store.list_posts()
        posts[0].content.text = "外部修改"
        self.assertNotEqual(store.list_posts()[0].content.text, "外部修改")
