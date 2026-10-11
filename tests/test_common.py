import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pydantic import BaseModel
from pydantic_core import PydanticSerializationError

from wemo2.common.files import sha256_file, write_model_json
from wemo2.common.names import normalize_person_with_raw
from wemo2.common.paths import require_within, resolve_path, resolve_resource_path
from wemo2.common.windows_names import sanitize_directory_name, validate_directory_name


class JsonPayload(BaseModel):
    """共用写入测试的数据，既验证中文内容也允许注入不可序列化值。"""

    value: Any


class CommonTests(unittest.TestCase):
    """验证提取后的共用函数保护原文件、保持路径约束和人物字段一致性。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_atomic_json_replaces_previous_content_and_keeps_unicode(self):
        path = self.root / "post.json"
        path.write_text('{"old": true}', encoding="utf-8")
        write_model_json(path, JsonPayload(value="朋友-牛哥"))
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")), {"value": "朋友-牛哥"}
        )
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_serialization_failure_keeps_old_json(self):
        path = self.root / "post.json"
        path.write_text("previous", encoding="utf-8")
        with self.assertRaises(PydanticSerializationError):
            write_model_json(path, JsonPayload(value=object()))
        self.assertEqual(path.read_text(encoding="utf-8"), "previous")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_replace_failure_keeps_old_json_and_cleans_own_temporary_file(self):
        path = self.root / "post.json"
        path.write_text("previous", encoding="utf-8")
        with (
            patch("pathlib.Path.replace", side_effect=OSError("无法替换")),
            self.assertRaisesRegex(OSError, "无法替换"),
        ):
            write_model_json(path, JsonPayload(value="new"))
        self.assertEqual(path.read_text(encoding="utf-8"), "previous")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_sha256_is_computed_from_file_bytes(self):
        path = self.root / "media.mp4"
        content = b"actual file bytes" * 100_000
        path.write_bytes(content)
        self.assertEqual(sha256_file(path), hashlib.sha256(content).hexdigest())

    def test_resource_paths_cannot_escape_to_sibling_or_absolute_directories(self):
        self.assertEqual(
            resolve_resource_path(self.root, "media/photo.png"),
            self.root / "media/photo.png",
        )
        for relative in (
            "../outside.png",
            "C:outside.png",
            str(self.root / "absolute.png"),
            "",
        ):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                resolve_resource_path(self.root, relative)
        with self.assertRaises(ValueError):
            require_within(self.root, self.root.with_name(self.root.name + "-sibling"))

    def test_config_paths_keep_the_correct_base_directory(self):
        self.assertEqual(resolve_path("backup", self.root), self.root / "backup")
        absolute = self.root / "absolute"
        self.assertEqual(resolve_path(absolute, self.root / "other"), absolute)

    def test_account_validation_is_strict_while_parsed_names_are_sanitized(self):
        self.assertEqual(validate_directory_name("default", "账号目录"), "default")
        for value in ("CON", "NUL.txt", "a/b", "a\\b", "a.", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_directory_name(value, "账号目录")
        self.assertEqual(sanitize_directory_name("张/三", "unknown"), "张_三")
        self.assertEqual(sanitize_directory_name("CON", "unknown"), "_CON")

    def test_raw_name_is_retained_only_for_the_same_normalized_person(self):
        self.assertEqual(
            normalize_person_with_raw("朋友-牛哥", "朋友-牛哥🐮"),
            ("朋友-牛哥", "朋友-牛哥🐮"),
        )
        self.assertEqual(normalize_person_with_raw("李四", "张三🐮"), ("李四", "李四"))
