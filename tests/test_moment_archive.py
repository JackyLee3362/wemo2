import hashlib
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image
from pydantic import ValidationError

from wemo2_collector.archive import MomentArchive, moment_key
from wemo2_collector.collector import WindowsScreenshotCollector
from wemo2_collector.config import WindowsCollectorConfig, load_windows_options
from wemo2_collector.dates import approximate_day
from wemo2_collector.media import MediaCapture, SavedMedia, SavedPreview
from wemo2_collector.models import (
    FeedCaptureConfig,
    FeedCaptureResult,
    FeedSegment,
    Platform,
    Rect,
    Screenshot,
    Surface,
)
from wemo2_collector.recognition import TextLine, TextRecognizer
from wemo2_collector.records import MomentDetails, MomentRecord


class MomentArchiveTests(unittest.TestCase):
    """验证按账号、日期、朋友圈归档，自包含资源、跨次去重和失败留痕。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.time = datetime(2026, 10, 11, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        image = self.path / "frame.png"
        with Image.fromarray(
            np.random.default_rng(7).integers(20, 240, (300, 200, 3), dtype=np.uint8)
        ) as pixels:
            pixels.save(image)
        self.frame = Screenshot(
            frame_id="frame",
            captured_at=self.time,
            surface=Surface(
                platform=Platform.windows,
                surface_id="wx",
                bounds=Rect(x=0, y=0, width=200, height=300),
                scale=1.25,
            ),
            image_path=image,
        )
        self.segment = FeedSegment(
            image_path=image,
            source_frames=["frame"],
            partial_start=False,
            partial_end=False,
            stream_start=0,
            stream_end=300,
        )
        self.result = FeedCaptureResult(
            frames=[self.frame], segments=[self.segment], stop_reason="max_screens"
        )
        self.media = MediaCapture(MagicMock(), FeedCaptureConfig(), self.path)
        self.details = MomentDetails(
            person="张三",
            text="散步\n真开心",
            text_status="complete",
            approximate_date=date(2026, 10, 10),
            date_raw="23小时前",
            header_top=0,
        )
        self.media.reader = MagicMock()
        self.media.reader.post_details.return_value = self.details
        asset = self.path / "asset.png"
        asset.write_bytes(image.read_bytes())
        self.saved = self.saved_file(asset, "image")
        self.media.previews = [
            SavedPreview(
                source=self.frame,
                bounds=Rect(x=70, y=50, width=80, height=80),
                stream_y=50,
                file=self.saved,
            )
        ]
        self.root = self.path / "resources"

    def saved_file(self, path, kind):
        return SavedMedia(
            path=path,
            kind=kind,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size=path.stat().st_size,
        )

    def export(self, account="账号A"):
        archive = MomentArchive(self.root, account)
        archive.export(self.result, self.media)
        return archive

    def record(self, account="账号A"):
        paths = list((self.root / account).glob("*/*/post.json"))
        self.assertEqual(len(paths), 1)
        return paths[0], MomentRecord.model_validate_json(
            paths[0].read_text(encoding="utf-8")
        )

    def test_one_folder_contains_metadata_screenshot_images_and_video(self):
        video = self.path / "clip.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"video fixture")
        self.media.previews.append(
            SavedPreview(
                source=self.frame,
                bounds=Rect(x=70, y=150, width=80, height=80),
                stream_y=150,
                file=self.saved_file(video, "video"),
            )
        )
        archive = self.export()
        path, record = self.record()
        self.assertEqual(path.parent.parent.name, "2026-10-10")
        self.assertEqual(path.parent.parent.parent.name, "账号A")
        self.assertEqual(record.person, "张三")
        self.assertEqual(record.text, "散步\n真开心")
        self.assertEqual(record.approximate_date, date(2026, 10, 10))
        self.assertEqual(len(record.media), 2)
        self.assertIsNone(record.media_complete)
        for resource in [record.primary_screenshot, record.evidence[0].screenshot] + [
            item.path for item in record.media
        ]:
            self.assertTrue((path.parent / resource).is_file())
            self.assertFalse(Path(resource).is_absolute())
        self.assertIn(path, archive.paths)

    def test_same_person_and_text_merge_across_runs_and_dates(self):
        self.export()
        path, old = self.record()
        self.details.text = "散步 真开心"
        self.details.approximate_date = date(2026, 10, 11)
        self.frame = self.frame.model_copy(
            update={"captured_at": self.time + timedelta(days=1)}
        )
        self.result.frames = [self.frame]
        self.export()
        updated_path, updated = self.record()
        self.assertEqual(path, updated_path)
        self.assertEqual(len(updated.media), 1)
        self.assertEqual(len(updated.evidence), 2)
        self.assertEqual(
            updated.observed_dates, [date(2026, 10, 10), date(2026, 10, 11)]
        )
        self.assertEqual(updated.first_seen_at, old.first_seen_at)
        self.assertEqual(updated.last_seen_at, self.frame.captured_at)

    def test_default_account_and_iso_date_use_configured_resource_root(self):
        config = WindowsCollectorConfig(resource_dir=self.root)
        self.details.approximate_date = date(2026, 10, 11)
        archive = MomentArchive(config.resource_dir, config.account)
        archive.export(self.result, self.media)
        path, record = self.record("default")
        key = moment_key(self.details.person, self.details.text, "complete")
        self.assertEqual(path, self.root / "default" / "2026-10-11" / key / "post.json")
        self.assertTrue((path.parent / record.media[0].path).is_file())

    def test_compact_date_directory_migrates_without_duplicate_metadata_or_lost_media(
        self,
    ):
        self.export()
        path, record = self.record()
        old = path.parent.parent.with_name("20261010") / path.parent.name
        old.parent.mkdir()
        path.parent.rename(old)
        self.export()
        new, updated = self.record()
        self.assertEqual(new.parent.parent.name, "2026-10-10")
        self.assertFalse(old.exists())
        self.assertEqual(updated.media, record.media)
        self.assertTrue((new.parent / updated.media[0].path).is_file())

    def test_incremental_export_does_not_create_duplicate_unidentified_folders(self):
        self.details.person = None
        archive = MomentArchive(self.root, "default")
        archive.export(self.result, self.media)
        archive.export(self.result, self.media)
        self.record("default")
        self.media.reader.post_details.assert_called_once()

    def test_accounts_do_not_share_dedup_or_resources(self):
        self.export("账号A")
        self.export("账号B")
        first, _ = self.record("账号A")
        second, _ = self.record("账号B")
        self.assertNotEqual(first, second)

    def test_unknown_date_moves_to_recognized_date_without_losing_files(self):
        self.details.approximate_date = None
        self.export()
        path, _ = self.record()
        self.assertEqual(path.parent.parent.name, "unknown_date")
        self.details.approximate_date = date(2026, 10, 10)
        self.export()
        new, record = self.record()
        self.assertFalse(path.exists())
        self.assertEqual(new.parent.parent.name, "2026-10-10")
        self.assertTrue((new.parent / record.media[0].path).exists())

    def test_unknown_person_or_partial_text_are_preserved_without_dedup(self):
        self.details.text_status = "partial"
        self.export()
        self.export()
        paths = list((self.root / "账号A").glob("*/*/post.json"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            record = MomentRecord.model_validate_json(path.read_text(encoding="utf-8"))
            self.assertIsNone(record.dedup_key)
            self.assertTrue(path.parent.name.startswith("unidentified_"))

    def test_media_from_other_segment_is_not_assigned_to_this_post(self):
        self.media.previews[0].stream_y = 350
        self.export()
        _, record = self.record()
        self.assertEqual(record.media, [])

    def test_saved_file_is_retained_when_return_to_feed_failed(self):
        self.media.previews[0].workflow_complete = False
        self.result.error = "返回列表失败"
        self.export()
        _, record = self.record()
        self.assertEqual(len(record.media), 1)
        self.assertFalse(record.media_complete)
        self.assertIn("返回列表失败", record.errors)

    def test_corrupted_download_keeps_metadata_error_and_original_evidence(self):
        self.saved.path.write_bytes(b"corrupted")
        with self.assertRaisesRegex(RuntimeError, "哈希或大小不一致"):
            self.export()
        path, record = self.record()
        self.assertFalse(record.media_complete)
        self.assertTrue(record.errors)
        self.assertTrue((path.parent / record.primary_screenshot).exists())

    def test_dedup_key_separates_fields_and_distinguishes_unknown_from_empty_text(self):
        self.assertNotEqual(
            moment_key("ab", "c", "complete"), moment_key("a", "bc", "complete")
        )
        self.assertIsNone(moment_key("张三", None, "unknown"))
        self.assertIsNotNone(moment_key("张三", "", "complete"))
        self.assertEqual(
            moment_key("张三", "春天\n真好", "complete"),
            moment_key("张三", "春天 真好", "complete"),
        )

    def test_resource_config_resolves_relative_paths_and_rejects_unsafe_accounts(self):
        config = load_windows_options(
            {"resource_dir": "backup", "account": "账号A"}, self.path
        )["config"]
        self.assertEqual(config.resource_dir, self.path / "backup")
        for account in ("../other", "CON", "a/b", "a\\b", "NUL.txt", "", "a."):
            with self.subTest(account=account), self.assertRaises(ValidationError):
                WindowsCollectorConfig(account=account)

    def test_collector_exports_resources_automatically_and_preserves_capture_failure(
        self,
    ):
        for fail in (False, True):
            with self.subTest(fail=fail):
                collector = WindowsScreenshotCollector(
                    WindowsCollectorConfig(
                        resource_dir=self.root,
                        account="账号A",
                        screenshot_dir=self.path,
                        feed=FeedCaptureConfig(enabled=True),
                        route={"target": "none"},
                    )
                )
                collector.driver = MagicMock()
                collector.controller.capture = MagicMock(return_value=self.frame)

                def capture(feed, frame, *, fail=fail):
                    feed.result = self.result
                    feed.media.reader = self.media.reader
                    feed.media.previews = self.media.previews
                    if fail:
                        feed.result.error = "采集输入失败"
                        raise RuntimeError("采集输入失败")

                with patch(
                    "wemo2_collector.collector.FeedCapture.run",
                    autospec=True,
                    side_effect=capture,
                ):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "采集输入失败"):
                            collector.collect()
                    else:
                        self.assertEqual(collector.collect(), [])
                path, record = self.record()
                self.assertIn(path, collector.artifacts())
                self.assertEqual(len(record.media), 1)
                collector.driver.close.assert_called_once()
                if fail:
                    self.assertIn("采集输入失败", record.errors)


class MomentRecognitionTests(unittest.TestCase):
    """验证以采集时区推断到日，以及正文与图片文字、地点、评论的区分。"""

    def test_relative_days_and_calendar_year_boundary(self):
        captured = datetime(2026, 1, 1, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        for raw, expected in (
            ("23小时前", date(2025, 12, 31)),
            ("昨天", date(2025, 12, 31)),
            ("12月31日周三", date(2025, 12, 31)),
            ("2025年10月8日", date(2025, 10, 8)),
            ("2025-10-08", date(2025, 10, 8)),
            ("刚刚", date(2026, 1, 1)),
            ("没有时间", None),
            ("2月30日", None),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(approximate_day(raw, captured), expected)

    def test_caption_excludes_photo_ocr_location_and_comments(self):
        reader = TextRecognizer()
        name = TextLine(
            text="张三", score=0.99, bounds=Rect(x=70, y=10, width=50, height=20)
        )
        body = TextLine(
            text="今天散步", score=0.99, bounds=Rect(x=70, y=40, width=80, height=20)
        )
        poster = TextLine(
            text="海报广告", score=0.99, bounds=Rect(x=70, y=100, width=80, height=20)
        )
        location = TextLine(
            text="北京", score=0.99, bounds=Rect(x=70, y=165, width=50, height=15)
        )
        time = TextLine(
            text="23小时前", score=0.99, bounds=Rect(x=70, y=185, width=60, height=15)
        )
        comment = TextLine(
            text="评论不是正文",
            score=0.99,
            bounds=Rect(x=70, y=215, width=100, height=20),
        )
        reader.read = MagicMock(
            return_value=[name, body, poster, location, time, comment]
        )
        reader.nickname = MagicMock(return_value=name)
        with Image.new("RGB", (250, 300), (30, 30, 30)) as image:
            image.paste((130, 145, 170), (70, 10, 120, 30))
            image.paste((210, 180, 70), (70, 80, 150, 160))
            image.paste((130, 145, 170), (70, 165, 120, 180))
            details = reader.post_details(
                image,
                datetime(2026, 10, 11, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
                1.25,
            )
        self.assertEqual(details.person, "张三")
        self.assertEqual(details.text, "今天散步")
        self.assertEqual(details.text_status, "complete")
        self.assertEqual(details.approximate_date, date(2026, 10, 10))

    def test_missing_author_still_retains_known_date_and_does_not_invent_caption(self):
        reader = TextRecognizer()
        reader.nickname = MagicMock(return_value=None)
        reader.read = MagicMock(
            return_value=[
                TextLine(
                    text="昨天",
                    score=0.99,
                    bounds=Rect(x=20, y=50, width=40, height=15),
                )
            ]
        )
        with Image.new("RGB", (200, 100), (30, 30, 30)) as image:
            details = reader.post_details(
                image,
                datetime(2026, 10, 11, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
                1.25,
            )
        self.assertEqual(details.approximate_date, date(2026, 10, 10))
        self.assertIsNone(details.person)
        self.assertIsNone(details.text)

    def test_today_in_white_caption_is_not_misread_as_gray_publication_label(self):
        name = TextLine(
            text="张三", score=0.99, bounds=Rect(x=70, y=10, width=50, height=20)
        )
        body = TextLine(
            text="今天", score=0.99, bounds=Rect(x=70, y=40, width=50, height=20)
        )
        footer = TextLine(
            text="昨天", score=0.99, bounds=Rect(x=70, y=180, width=50, height=15)
        )
        reader = TextRecognizer()
        reader.nickname = MagicMock(return_value=name)
        reader.read = MagicMock(return_value=[name, body, footer])
        with Image.new("RGB", (200, 250), (30, 30, 30)) as image:
            image.paste((130, 145, 170), (70, 10, 120, 30))
            image.paste((220, 220, 220), (70, 40, 120, 60))
            image.paste((130, 130, 130), (70, 180, 120, 195))
            details = reader.post_details(
                image,
                datetime(2026, 10, 11, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
                1.25,
            )
        self.assertEqual(details.text, "今天")
        self.assertEqual(details.date_raw, "昨天")
        self.assertEqual(details.approximate_date, date(2026, 10, 10))

    def test_unexpanded_full_text_is_partial_but_literal_ellipsis_is_valid_caption(
        self,
    ):
        name = TextLine(
            text="张三", score=0.99, bounds=Rect(x=70, y=10, width=50, height=20)
        )
        body = TextLine(
            text="…今天散步", score=0.99, bounds=Rect(x=70, y=40, width=80, height=20)
        )
        date_line = TextLine(
            text="昨天", score=0.99, bounds=Rect(x=70, y=180, width=50, height=15)
        )
        reader = TextRecognizer()
        reader.nickname = MagicMock(return_value=name)
        for expanded, expected in ((False, "partial"), (True, "complete")):
            with self.subTest(expanded=expanded):
                lines = [name, body, date_line]
                if not expanded:
                    lines.append(
                        TextLine(
                            text="全文",
                            score=0.99,
                            bounds=Rect(x=70, y=65, width=50, height=15),
                        )
                    )
                reader.read = MagicMock(return_value=lines)
                with Image.new("RGB", (200, 250), (30, 30, 30)) as image:
                    image.paste((130, 145, 170), (70, 10, 120, 30))
                    details = reader.post_details(
                        image,
                        datetime(2026, 10, 11, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
                        1.25,
                    )
                self.assertEqual(details.text_status, expected)
                self.assertEqual(details.text, "…今天散步")
