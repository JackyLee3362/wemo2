import copy
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from wemo2.collector import JsonCollector
from wemo2.coordinator import Coordinator
from wemo2.deduplicator import Deduplicator
from wemo2.models import Observation
from wemo2.interfaces import BaseCollector
from wemo2.store import SQLiteStore


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.record = json.loads(
            (Path(__file__).parents[1] / "examples/observations.json").read_text(
                encoding="utf-8"
            )
        )[0]

    def run_records(self, records):
        source = self.path / "input.json"
        source.write_text(json.dumps(records), encoding="utf-8")
        with SQLiteStore(self.path / "backup.sqlite3") as store:
            return Coordinator(JsonCollector(source), Deduplicator(), store).run()

    def posts(self):
        with SQLiteStore(self.path / "backup.sqlite3") as store:
            return store.list_posts()

    def test_replay_survives_reopening_store(self):
        self.assertEqual(self.run_records([self.record]).created, 1)
        self.assertEqual(self.run_records([self.record]).unchanged, 1)
        self.assertEqual(len(self.posts()), 1)

    def test_changed_content_preserves_revision_and_raw_observation(self):
        self.run_records([self.record])
        updated = copy.deepcopy(self.record)
        updated["observation_id"] = "obs-new"
        updated["content"]["text"] = "新的正文"
        self.assertEqual(self.run_records([updated]).merged, 1)
        self.assertEqual(self.posts()[0].revision, 2)
        self.assertEqual(self.posts()[0].content.text, "新的正文")
        with SQLiteStore(self.path / "backup.sqlite3") as store:
            self.assertEqual(
                store.connection.execute("SELECT count(*) FROM revisions").fetchone()[
                    0
                ],
                2,
            )
            saved = store.get_observation(
                self.record["account_scope"], self.record["observation_id"]
            )
            self.assertEqual(Observation.model_validate_json(saved.payload),
                             Observation.model_validate(self.record))

    def test_partial_observation_does_not_clear_complete_content(self):
        self.run_records([self.record])
        partial = copy.deepcopy(self.record)
        partial["observation_id"] = "obs-partial"
        partial["content"]["text"] = ""
        partial["field_status"]["text"] = "partial"
        self.assertEqual(self.run_records([partial]).unchanged, 1)
        self.assertEqual(
            self.posts()[0].content.text, self.record["content"]["text"]
        )

    def test_idempotency_conflict_does_not_corrupt_store(self):
        self.run_records([self.record])
        changed = copy.deepcopy(self.record)
        changed["content"]["text"] = "冲突"
        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            self.run_records([changed])
        self.assertEqual(self.posts()[0].revision, 1)
        self.assertEqual(self.run_records([self.record]).unchanged, 1)

    def test_scope_and_native_ids_do_not_merge_identical_text(self):
        other = copy.deepcopy(self.record)
        other["observation_id"] = "obs-other"
        other["source"]["native_post_id"] = "different-post"
        account = copy.deepcopy(self.record)
        account["account_scope"] = "different-account"
        self.assertEqual(self.run_records([self.record, other, account]).created, 3)

    def test_unknown_identity_keeps_distinct_observations(self):
        self.record["source"]["native_post_id_stable"] = False
        other = copy.deepcopy(self.record)
        other["observation_id"] = "obs-other"
        self.assertEqual(self.run_records([self.record, other]).created, 2)
        self.assertEqual(self.run_records([self.record, other]).unchanged, 2)

    def test_validation_occurs_before_any_record_is_saved(self):
        invalid = copy.deepcopy(self.record)
        invalid["source"]["native_post_id"] = None
        with self.assertRaises(ValueError):
            self.run_records([self.record, invalid])
        self.assertEqual(self.posts(), [])

    def test_collector_returns_nested_models_and_preserves_missing_fields(self):
        source = self.path / "input.json"
        source.write_text(json.dumps([self.record]), encoding="utf-8")
        observation = JsonCollector(source).collect()[0]
        self.assertIsInstance(observation, Observation)
        self.assertEqual(observation.content.author.display_name, "示例用户")
        dumped = observation.model_dump(mode="json", exclude_unset=True)
        self.assertNotIn("comments", dumped["content"])
        self.assertEqual(dumped["content"]["media"], [])

    def test_model_rejects_invalid_nested_fields_and_naive_time(self):
        for field, value in (("observed_at", "2026-10-10T13:00:00"),
                             ("observation_id", "  "), ("schema_version", "9.0.0")):
            invalid = copy.deepcopy(self.record)
            invalid[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                Observation.model_validate(invalid)
        invalid = copy.deepcopy(self.record)
        invalid["content"]["media"] = [{"kind": "image", "position": -1}]
        with self.assertRaises(ValidationError):
            Observation.model_validate(invalid)
        invalid["content"]["media"] = []
        invalid["source"]["native_post_id_stable"] = "true"
        with self.assertRaises(ValidationError):
            Observation.model_validate(invalid)

    def test_non_json_collector_can_supply_models_directly(self):
        observation = Observation.model_validate(self.record)

        class MemoryCollector(BaseCollector):
            def collect(self):
                yield observation

        with SQLiteStore(self.path / "backup.sqlite3") as store:
            result = Coordinator(MemoryCollector(), Deduplicator(), store).run()
            self.assertEqual(result.created, 1)

    def test_legacy_payload_replays_without_false_conflict(self):
        self.run_records([self.record])
        with SQLiteStore(self.path / "backup.sqlite3") as store:
            with store.transaction():
                store.connection.execute("UPDATE observations SET payload=?",
                                         (json.dumps(self.record),))
        self.assertEqual(self.run_records([self.record]).unchanged, 1)


if __name__ == "__main__":
    unittest.main()
