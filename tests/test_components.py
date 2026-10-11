import tempfile
import unittest
from pathlib import Path

from wemo2.api.interfaces import BaseCollector, BaseStore
from wemo2.archives.reader import ArchiveReader
from wemo2.collectors.pseudo import PseudoCollector
from wemo2.stores.pseudo import PseudoStore


class ComponentTests(unittest.TestCase):
    """验证采集器自行合并归档、存储器接收完整交付及两种抽象接口。"""

    def test_abstract_components_cannot_be_instantiated(self):
        for component in (BaseCollector, BaseStore):
            with self.subTest(component=component), self.assertRaises(TypeError):
                component()

    def test_pseudo_collection_deduplicates_by_folder_and_saves_real_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            source = PseudoCollector(Path(directory))
            first = source.collect()
            second = source.collect()
            self.assertEqual(first.posts, 1)
            self.assertEqual(first.artifacts, second.artifacts)
            self.assertEqual(len(list(Path(directory).rglob("post.json"))), 1)
            post = next(ArchiveReader(Path(directory), "default").read())
            self.assertEqual(post.record.person, "示例人物")
            self.assertTrue(post.screenshot.is_file())

    def test_pseudo_store_receives_a_copy_without_doing_identity_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            PseudoCollector(Path(directory)).collect()
            post = next(ArchiveReader(Path(directory), "default").read())
            store = PseudoStore()
            store.save(post)
            store.save(post)
            post.record.person = "修改调用者的副本"
            self.assertEqual(len(store.posts), 2)
            self.assertEqual(store.posts[0].record.person, "示例人物")
