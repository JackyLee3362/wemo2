import unittest

from wemo2.collectors.models import MomentDetails
from wemo2.names import normalize_person, person_directory
from wemo2.records import moment_key


class PersonNameTests(unittest.TestCase):
    """验证复合 emoji 清洗、目录名安全及去重时只忽略人物 emoji。"""

    def test_emoji_sequences_are_removed_without_losing_text(self):
        for source, expected in (
            ("朋友-牛哥🐮", "朋友-牛哥"),
            ("张三👨‍👩‍👧‍👦", "张三"),
            ("李四👍🏽", "李四"),
            ("🇨🇳王五❤️", "王五"),
            ("同事1️⃣赵六", "同事赵六"),
            ("  张三  😊 ", "张三"),
            ("朋友-牛哥_123", "朋友-牛哥_123"),
            ("👨‍💻🐮", None),
            (None, None),
        ):
            with self.subTest(source=source):
                self.assertEqual(normalize_person(source), expected)

    def test_emoji_is_ignored_in_identity_but_caption_emoji_is_preserved(self):
        self.assertEqual(
            moment_key("朋友-牛哥🐮", "你好", "complete"),
            moment_key("朋友-牛哥", "你好", "complete"),
        )
        self.assertNotEqual(
            moment_key("张三", "你好😊", "complete"),
            moment_key("张三", "你好", "complete"),
        )
        self.assertIsNone(moment_key("😊", "你好", "complete"))

    def test_model_keeps_original_name_as_evidence(self):
        details = MomentDetails(person="朋友-牛哥🐮")
        self.assertEqual(details.person, "朋友-牛哥")
        self.assertEqual(details.person_raw, "朋友-牛哥🐮")

    def test_directory_is_single_safe_component(self):
        for source, expected in (
            ("朋友-牛哥🐮", "朋友-牛哥"),
            ("张/三:朋友?", "张_三_朋友_"),
            ("a\\b", "a_b"),
            ("CON", "_CON"),
            ("nul.txt", "_nul.txt"),
            ("..", "unknown_person"),
            ("😊", "unknown_person"),
        ):
            with self.subTest(source=source):
                self.assertEqual(person_directory(source), expected)
