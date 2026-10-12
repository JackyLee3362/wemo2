import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image
from pydantic import ValidationError

from wemo2.api.records import MomentRecord
from wemo2.archives.identity import moment_key
from wemo2.collectors.archive import MomentArchive
from wemo2.collectors.collector import WindowsScreenshotCollector
from wemo2.collectors.config import WindowsCollectorConfig
from wemo2.collectors.dates import approximate_day
from wemo2.collectors.media import MediaCapture, SavedMedia, SavedPreview
from wemo2.collectors.models import (
    FeedCaptureConfig,
    FeedCaptureResult,
    FeedSegment,
    MomentDetails,
    Platform,
    Rect,
    Screenshot,
    Surface,
)
from wemo2.collectors.recognition import TextLine, TextRecognizer
from wemo2.core.config import AppConfig, load_config


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
        paths = list((self.root / account).glob("*/*/*/post.json"))
        self.assertEqual(len(paths), 1)
        return paths[0], MomentRecord.model_validate_json(
            paths[0].read_text(encoding="utf-8")
        )

    def test_advertisement_is_not_archived_or_scheduled_for_download(self):
        self.details.is_advertisement = True
        archive = MomentArchive(self.root, "广告测试", recent_days=1)
        archive.export(self.result, self.media)
        self.assertEqual(archive.paths, [])
        self.assertEqual(archive.downloads, [])
        self.assertFalse(self.root.exists())
        self.assertEqual(self.result.stop_reason, "max_screens")
        self.assertTrue(self.segment.image_path.exists())

    def test_recent_days_include_boundary_and_stop_before_older_post(self):
        for days in (1, 2, 3):
            with self.subTest(days=days):
                self.result.stop_reason = "running"
                self.result.segments = [self.segment]
                archive = MomentArchive(self.root, f"最近{days}天", recent_days=days)
                cutoff = self.time.date() - timedelta(days=days - 1)
                self.details.approximate_date = cutoff
                archive.export(self.result, self.media)
                self.assertEqual(len(archive.downloads), 1)
                # 下一条已超过日期范围；其后即使还有条目也不再归档。
                older = self.path / f"older-{days}.png"
                older.write_bytes(self.segment.image_path.read_bytes())
                segment = self.segment.model_copy(update={"image_path": older})
                self.result.segments = [segment]
                self.details.approximate_date = cutoff - timedelta(days=1)
                archive.export(self.result, self.media)
                self.assertEqual(self.result.stop_reason, "date_limit")
                self.assertEqual(len(archive.downloads), 1)
                self.details.approximate_date = cutoff
                archive.export(self.result, self.media)
                self.assertEqual(len(archive.downloads), 1)

    def test_unknown_date_does_not_trigger_date_stop(self):
        self.details.approximate_date = None
        archive = MomentArchive(self.root, "未知日期", recent_days=1)
        archive.export(self.result, self.media)
        self.assertEqual(self.result.stop_reason, "max_screens")
        self.assertTrue(archive.paths)
        self.assertEqual(archive.downloads, [])

    def test_cutoff_stays_at_first_capture_day_when_run_crosses_midnight(self):
        archive = MomentArchive(self.root, "跨天", recent_days=2)
        self.result.segments = []
        archive.export(self.result, self.media)
        self.assertEqual(archive.cutoff_date, date(2026, 10, 10))
        self.result.frames.append(
            self.frame.model_copy(
                update={
                    "frame_id": "next-day",
                    "captured_at": self.time + timedelta(days=1),
                }
            )
        )
        self.result.segments = [self.segment]
        archive.export(self.result, self.media)
        self.assertEqual(archive.cutoff_date, date(2026, 10, 10))
        self.assertEqual(len(archive.downloads), 1)

    def test_recent_days_validates_positive_integer_and_requires_recognition(self):
        self.assertIsNone(FeedCaptureConfig().recent_days)
        for value in (0, -1, True, 1.5, "3"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                FeedCaptureConfig(recent_days=value)
        with self.assertRaisesRegex(ValidationError, "recognize_nickname"):
            FeedCaptureConfig(recent_days=1, recognize_nickname=False)

    def test_download_is_registered_in_final_folder_without_second_copy(self):
        self.media.previews = []
        archive = self.export()
        plan = archive.downloads[0]
        self.assertEqual(plan.directory.parent.parent.name, "张三")
        self.assertEqual(plan.directory.parents[2].name, "2026-10-10")
        plan.directory.mkdir()
        actual = plan.directory / "asset_download.png"
        actual.write_bytes(self.frame.image_path.read_bytes())
        archive.register_download(self.saved_file(actual, "image"), 100)
        _, record = self.record()
        self.assertEqual(record.media[0].path, "media/asset_download.png")
        self.assertEqual(list(plan.directory.iterdir()), [actual])

    def test_half_post_remains_evidence_without_primary_archive(self):
        self.segment.partial_end = True
        archive = self.export()
        self.assertEqual(archive.downloads, [])
        self.assertEqual(list(self.root.rglob("post.json")), [])
        self.assertTrue(self.segment.image_path.is_file())

    def test_channel_keeps_complete_screenshot_and_excludes_download_plan(self):
        self.details.is_video_channel = True
        self.media.previews = []
        archive = self.export()
        path, record = self.record()
        self.assertTrue(record.screenshot_complete)
        self.assertEqual(record.media_skip_reason, "video_channel")
        self.assertEqual(record.media, [])
        self.assertTrue((path.parent / record.primary_screenshot).is_file())
        self.media.downloads = archive.downloads
        self.assertIsNone(self.media.destination(100))

    def test_channel_discovered_in_viewer_stays_skipped_in_later_frames(self):
        archive = self.export()
        self.media.downloads = archive.downloads
        self.assertIsNotNone(self.media.destination(100))
        archive.register_skip(100, "video_channel")
        self.assertIsNone(self.media.destination(100))
        _, record = self.record()
        self.assertEqual(record.media_skip_reason, "video_channel")

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
        self.assertEqual(path.parent.parent.name, "张三")
        self.assertEqual(path.parents[2].name, "2026-10-10")
        self.assertEqual(path.parents[3].name, "账号A")
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

    def test_requested_person_directory_and_emoji_free_metadata_deduplicate(self):
        self.details.person = "朋友-牛哥🐮"
        self.details.approximate_date = date(2026, 10, 11)
        self.export("default")
        path, record = self.record("default")
        key = moment_key("朋友-牛哥", self.details.text, "complete")
        self.assertEqual(
            path, self.root / "default" / "2026-10-11" / "朋友-牛哥" / key / "post.json"
        )
        self.assertEqual(record.person, "朋友-牛哥")
        self.assertEqual(record.person_raw, "朋友-牛哥🐮")
        self.assertTrue((path.parent / record.primary_screenshot).is_file())
        self.assertTrue((path.parent / record.media[0].path).is_file())
        self.details.person = "朋友-牛哥"
        self.export("default")
        new, updated = self.record("default")
        self.assertEqual(new, path)
        self.assertEqual(len(updated.media), 1)

    def test_legacy_emoji_identity_moves_without_losing_media_or_raw_name(self):
        self.export("default")
        path, record = self.record("default")
        raw_name = "张三🐮"
        payload = json.dumps(
            [raw_name, "散步 真开心"], ensure_ascii=False, separators=(",", ":")
        )
        old_key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        old = self.root / "default" / "2026-10-10" / old_key
        path.parent.rename(old)
        metadata = record.model_dump(mode="json")
        metadata["person"] = raw_name
        metadata.pop("person_raw")
        metadata["post_id"] = old_key
        metadata["dedup_key"] = old_key
        (old / "post.json").write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        self.export("default")
        new, updated = self.record("default")
        self.assertFalse(old.exists())
        self.assertEqual(new.parent.parent.name, "张三")
        self.assertEqual(updated.person, "张三")
        self.assertEqual(updated.person_raw, raw_name)
        self.assertEqual(updated.post_id, new.parent.name)
        self.assertEqual(
            updated.dedup_key, moment_key("张三", updated.text, "complete")
        )
        self.assertEqual(
            (new.parent / updated.media[0].path).read_bytes(),
            self.saved.path.read_bytes(),
        )

    def test_emoji_only_person_uses_unknown_person_and_has_no_dedup_key(self):
        self.details.person = "🐮"
        self.export()
        path, record = self.record()
        self.assertEqual(path.parent.parent.name, "unknown_person")
        self.assertIsNone(record.person)
        self.assertIsNone(record.dedup_key)

    def test_default_account_and_iso_date_use_configured_resource_root(self):
        config = AppConfig(resource_dir=self.root)
        self.details.approximate_date = date(2026, 10, 11)
        archive = MomentArchive(config.resource_dir, config.account)
        archive.export(self.result, self.media)
        path, record = self.record("default")
        key = moment_key(self.details.person, self.details.text, "complete")
        self.assertEqual(
            path, self.root / "default" / "2026-10-11" / "张三" / key / "post.json"
        )
        self.assertTrue((path.parent / record.media[0].path).is_file())

    def test_compact_date_directory_migrates_without_duplicate_metadata_or_lost_media(
        self,
    ):
        self.export()
        path, record = self.record()
        old = path.parents[2].with_name("20261010") / path.parent.name
        old.parent.mkdir()
        path.parent.rename(old)
        self.export()
        new, updated = self.record()
        self.assertEqual(new.parents[2].name, "2026-10-10")
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
        self.assertEqual(path.parents[2].name, "unknown_date")
        self.details.approximate_date = date(2026, 10, 10)
        self.export()
        new, record = self.record()
        self.assertFalse(path.exists())
        self.assertEqual(new.parents[2].name, "2026-10-10")
        self.assertTrue((new.parent / record.media[0].path).exists())

    def test_unknown_person_or_partial_text_are_preserved_without_dedup(self):
        self.details.text_status = "partial"
        self.export()
        self.export()
        paths = list((self.root / "账号A").glob("*/*/*/post.json"))
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
        config = load_config(
            {"resource_dir": "backup", "account": "账号A"}, cwd=self.path
        )
        self.assertEqual(config.resource_dir, self.path / "backup")
        for account in ("../other", "CON", "a/b", "a\\b", "NUL.txt", "", "a."):
            with self.subTest(account=account), self.assertRaises(ValidationError):
                AppConfig(account=account)

    def test_collector_exports_resources_automatically_and_preserves_capture_failure(
        self,
    ):
        for fail in (False, True):
            with self.subTest(fail=fail):
                collector = WindowsScreenshotCollector(
                    WindowsCollectorConfig(
                        screenshot_dir=self.path,
                        feed=FeedCaptureConfig(enabled=True),
                        route={"target": "none"},
                    ),
                    resource_dir=self.root,
                    account="账号A",
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

                with (
                    patch(
                        "wemo2.collectors.collector.FeedCapture.run",
                        autospec=True,
                        side_effect=capture,
                    ),
                    patch(
                        "wemo2.collectors.collector.FeedCapture.download_media"
                    ) as download,
                    patch.object(collector.controller, "dismiss_channel"),
                ):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "采集输入失败"):
                            collector.collect()
                    else:
                        self.assertEqual(collector.collect().posts, 1)
                        download.assert_called_once()
                path, record = self.record()
                self.assertIn(path, collector.artifacts())
                self.assertEqual(len(record.media), 1)
                collector.driver.close.assert_called_once()
                if fail:
                    self.assertIn("采集输入失败", record.errors)


class MomentRecognitionTests(unittest.TestCase):
    """验证以采集时区推断到日，以及正文与图片文字、地点、评论的区分。"""

    def test_advertisement_recognition_only_reads_right_header(self):
        reader = TextRecognizer()
        name = TextLine(
            text="昵称", score=1, bounds=Rect(x=80, y=24, width=100, height=25)
        )
        label = TextLine(
            text="广告", score=0.99, bounds=Rect(x=10, y=10, width=40, height=20)
        )
        with Image.new("RGB", (640, 450), (30, 30, 30)) as image:

            def read_header(cropped):
                self.assertEqual(cropped.size, (500, 220))
                return [label]

            reader.read = MagicMock(side_effect=read_header)
            self.assertTrue(reader.is_advertisement(image, name, 1.25))
            reader.read = MagicMock(
                return_value=[label.model_copy(update={"text": "广告设计作品"})]
            )
            self.assertFalse(reader.is_advertisement(image, name, 1.25))
            reader.read = MagicMock(
                return_value=[label.model_copy(update={"score": 0.5})]
            )
            self.assertFalse(reader.is_advertisement(image, name, 1.25))

    def test_post_details_marks_advertisement_before_parsing_caption(self):
        reader = TextRecognizer()
        name = TextLine(
            text="昵称", score=1, bounds=Rect(x=80, y=24, width=100, height=25)
        )
        reader.read = MagicMock(return_value=[name])
        reader.nickname = MagicMock(return_value=name)
        reader.is_advertisement = MagicMock(return_value=True)
        with Image.new("RGB", (640, 450), (30, 30, 30)) as image:
            details = reader.post_details(
                image, datetime.now(ZoneInfo("Asia/Shanghai")), 1.25
            )
        self.assertTrue(details.is_advertisement)
        self.assertIsNone(details.text)

    def test_ocr_name_fragments_are_cleaned_before_returning_details(self):
        reader = TextRecognizer()
        name = TextLine(
            text="朋友-牛哥🐮",
            score=0.99,
            bounds=Rect(x=70, y=10, width=140, height=20),
        )
        footer = TextLine(
            text="昨天", score=0.99, bounds=Rect(x=70, y=180, width=50, height=15)
        )
        reader.read = MagicMock(return_value=[name, footer])
        reader.nickname = MagicMock(return_value=name)
        with Image.new("RGB", (260, 250), (30, 30, 30)) as image:
            image.paste((130, 145, 170), (70, 10, 210, 30))
            details = reader.post_details(
                image,
                datetime(2026, 10, 11, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
                1.25,
            )
        self.assertEqual(details.person, "朋友-牛哥")
        self.assertEqual(details.person_raw, "朋友-牛哥🐮")

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
