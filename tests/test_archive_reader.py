import hashlib
import tempfile
import unittest
from pathlib import Path

from wemo2.archive_reader import ArchiveReader
from wemo2.pseudo import PseudoCollector
from wemo2.records import ArchiveMedia, MomentRecord


class ArchiveReaderTests(unittest.TestCase):
    """验证归档交付的元信息、图片视频、账号隔离及路径和哈希校验。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        result = PseudoCollector(self.root).collect()
        self.metadata = result.artifacts[0]
        self.record = MomentRecord.model_validate_json(
            self.metadata.read_text(encoding="utf-8")
        )
        self.reader = ArchiveReader(self.root, "default")

    def write(self):
        self.metadata.write_text(
            self.record.model_dump_json(indent=2), encoding="utf-8"
        )

    def add_media(self, name, kind, content):
        path = self.metadata.parent / "media" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        self.record.media.append(
            ArchiveMedia(
                path=path.relative_to(self.metadata.parent).as_posix(),
                kind=kind,
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
        self.write()
        return path

    def test_read_preserves_metadata_and_actual_image_and_video_files(self):
        image = self.add_media(
            "photo.png", "image", (self.metadata.parent / "screenshot.png").read_bytes()
        )
        video = self.add_media("video.mp4", "video", b"\x00\x00\x00\x18ftypisomvideo")
        post = next(self.reader.read())
        self.assertEqual(post.metadata_path, self.metadata)
        self.assertEqual(post.record, self.record)
        self.assertEqual(post.media_files, [image, video])
        self.assertTrue(post.screenshot.is_file())

    def test_other_accounts_are_not_included_in_selected_account(self):
        PseudoCollector(self.root, "other").collect()
        posts = list(self.reader.read())
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].record.account, "default")

    def test_new_person_layout_and_legacy_layout_are_both_readable(self):
        self.assertEqual(self.metadata.parent.parent.name, "示例人物")
        previous = self.metadata
        destination = self.metadata.parents[2] / self.metadata.parent.name
        self.metadata.parent.rename(destination)
        post = next(self.reader.read())
        self.assertEqual(post.metadata_path, destination / "post.json")
        self.assertFalse(previous.exists())

    def test_new_layout_person_must_match_metadata(self):
        self.record.person = "其他人物"
        self.write()
        with self.assertRaisesRegex(ValueError, "人物目录"):
            list(self.reader.read())

    def test_missing_archive_and_primary_screenshot_fail(self):
        with self.assertRaisesRegex(FileNotFoundError, "请先执行 collect"):
            list(ArchiveReader(self.root, "missing").read())
        (self.metadata.parent / "screenshot.png").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "资源缺失"):
            list(self.reader.read())

    def test_invalid_metadata_and_folder_identity_fail_before_storage(self):
        for field, value in (("account", "other"), ("post_id", "other")):
            with self.subTest(field=field):
                original = self.record.model_dump()
                original[field] = value
                self.metadata.write_text(
                    MomentRecord.model_validate(original).model_dump_json(),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "账号或朋友圈 ID"):
                    list(self.reader.read())
        self.metadata.write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            list(self.reader.read())

    def test_outside_and_absolute_resource_paths_are_rejected(self):
        for relative in (
            "../../outside.png",
            str(self.root / "outside.png"),
            "C:outside.png",
            "",
        ):
            with self.subTest(relative=relative):
                self.record.primary_screenshot = relative
                self.write()
                with self.assertRaises(ValueError):
                    list(self.reader.read())

    def test_modified_media_size_or_content_fail_hash_validation(self):
        path = self.add_media("file.png", "image", b"12345")
        for changed, message in (
            (b"more bytes", "大小不一致"),
            (b"54321", "哈希不一致"),
        ):
            with self.subTest(changed=changed):
                path.write_bytes(changed)
                with self.assertRaisesRegex(ValueError, message):
                    list(self.reader.read())

    def test_missing_media_is_not_silently_skipped(self):
        path = self.add_media("missing.png", "image", b"fixture")
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            list(self.reader.read())

    def test_unknown_date_and_incomplete_text_still_remain_readable(self):
        self.record.approximate_date = None
        self.record.text_status = "partial"
        self.record.dedup_key = None
        self.write()
        destination = self.root / "default/unknown_date" / self.metadata.parent.name
        destination.parent.mkdir()
        self.metadata.parent.rename(destination)
        posts = list(self.reader.read())
        self.assertEqual(posts[0].record.text_status, "partial")
        self.assertIsNone(posts[0].record.approximate_date)
