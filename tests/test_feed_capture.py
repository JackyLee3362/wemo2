import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image
from pydantic import ValidationError

from wemo2.collectors.controller import UiController
from wemo2.collectors.feed import FeedCapture, overlap_shift
from wemo2.collectors.locating import AirtestLocator
from wemo2.collectors.media import MediaCapture
from wemo2.collectors.media_navigation import (
    ArrowImageStrategy,
    ReopenImageStrategy,
    image_save_strategy,
)
from wemo2.collectors.models import (
    ActionResult,
    ControllerConfig,
    FeedCaptureConfig,
    LocatedTarget,
    MatchConfig,
    Platform,
    Rect,
    SaveDialogConfig,
    Screenshot,
    Surface,
    ValidationResult,
)
from wemo2.collectors.profiles import MediaTargetSpec, build_profile
from wemo2.collectors.recognition import (
    TextLine,
    TextRecognizer,
    file_kind,
    media_tiles,
)
from wemo2.collectors.windows.driver import WindowsDriver
from wemo2.core.config import AppConfig


class FeedCaptureTests(unittest.TestCase):
    """验证锚点分段、跨屏连续性、停止条件及媒体输入失败后的证据保留。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        self.surface = Surface(
            platform=Platform.windows,
            surface_id="feed",
            bounds=Rect(x=10, y=20, width=200, height=300),
            scale=1.25,
        )
        self.pixels = np.random.default_rng(4).integers(
            20, 220, (800, 200, 3), dtype=np.uint8
        )
        self.first = self.frame("first", self.pixels[:300])
        self.second = self.frame("second", self.pixels[100:400])
        self.controller = MagicMock()
        self.controller.profile = build_profile()
        self.controller.locator.locate_all.side_effect = [
            [self.anchor(self.first, 70), self.anchor(self.first, 220)],
            [self.anchor(self.second, 120), self.anchor(self.second, 250)],
        ]
        self.controller.scroll.return_value = ActionResult(
            operation="scroll",
            name="feed",
            status="verified",
            attempted=True,
            executed=True,
            before=self.first,
            after=self.second,
        )

    def frame(self, name, pixels):
        path = self.path / f"{name}.png"
        with Image.fromarray(pixels) as image:
            image.save(path)
        return Screenshot(
            frame_id=name,
            captured_at=datetime.now(UTC),
            surface=self.surface,
            image_path=path,
        )

    def anchor(self, frame, y):
        return LocatedTarget(
            frame_id=frame.frame_id,
            name="moments_anchor",
            bounds=Rect(x=150, y=y, width=40, height=25),
            score=1,
            method="test",
        )

    def scanner(self, **options):
        return FeedCapture(
            self.controller,
            FeedCaptureConfig(
                region=Rect(x=0, y=0, width=200, height=300),
                max_screens=2,
                recognize_nickname=False,
                save_media=False,
                **options,
            ),
            self.path,
        )

    def test_date_limit_stops_before_next_scroll_and_allows_existing_downloads(self):
        scan = self.scanner()
        scan.media = MagicMock()

        def stop_at_date(result, media):
            result.stop_reason = "date_limit"

        scan.on_progress = stop_at_date
        scan.run(self.first)
        self.assertEqual(scan.result.stop_reason, "date_limit")
        self.controller.scroll.assert_not_called()
        scan.media.process.reset_mock()
        self.controller.capture.return_value = self.first
        self.controller.locator.locate_all.side_effect = None
        self.controller.locator.locate_all.return_value = []
        scan.download_media()
        scan.media.process.assert_called_once()
        self.assertEqual(scan.result.media_stop_reason, "completed_visible_range")

    def test_overlap_checks_content_and_rejects_unrelated_frames(self):
        with (
            Image.open(self.first.image_path) as first,
            Image.open(self.second.image_path) as second,
        ):
            self.assertEqual(overlap_shift(first, second), 100)
            self.assertIsNone(overlap_shift(second, first))
            with Image.new("RGB", first.size, "white") as blank:
                self.assertIsNone(overlap_shift(blank, blank))
                self.assertIsNone(overlap_shift(first, blank))

    def test_stitches_and_splits_without_saving_overlap_twice(self):
        scan = self.scanner(boundary_padding=0)
        result = scan.run(self.first)
        self.assertEqual(result.stop_reason, "max_screens")
        self.assertEqual(len(result.segments), 4)
        self.assertTrue(result.segments[0].partial_start)
        self.assertTrue(result.segments[-1].partial_end)
        self.assertFalse(result.segments[1].partial_start)
        self.assertFalse(result.segments[1].partial_end)
        images = []
        for segment in result.segments:
            with Image.open(segment.image_path) as image:
                images.append(np.array(image))
        np.testing.assert_array_equal(np.concatenate(images), self.pixels[:400])
        self.controller.scroll.assert_called_once()
        manifest = json.loads(
            (scan.directory / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest["frames"]), 2)

    def test_video_playback_does_not_break_static_sidebar_overlap(self):
        changed = self.pixels[100:400].copy()
        changed[:, 90:] = np.random.default_rng(91).integers(
            20, 220, (300, 110, 3), dtype=np.uint8
        )
        with (
            Image.fromarray(self.pixels[:300]) as before,
            Image.fromarray(changed) as after,
        ):
            self.assertEqual(overlap_shift(before, after, static_width=90), 100)
            self.assertIsNone(overlap_shift(before, after))

    def test_completed_segments_are_archived_before_next_scroll(self):
        scan = self.scanner(boundary_padding=0)
        scan.media = MagicMock()
        scan.config = scan.config.model_copy(update={"max_screens": 1})
        scan.media.reader.nickname.return_value = None
        observed = []

        def archive(result, media):
            media.process.assert_called_once()
            self.controller.scroll.assert_not_called()
            observed.extend(result.segments)

        scan.on_progress = archive
        scan.run(self.first)
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(not segment.partial_end for segment in observed))

    def test_overlap_can_use_middle_strip_when_bottom_media_finishes_loading(self):
        changed = self.pixels[30:330].copy()
        changed[190:270] = np.random.default_rng(8).integers(
            20, 220, (80, 200, 3), dtype=np.uint8
        )
        with (
            Image.fromarray(self.pixels[:300]) as old,
            Image.fromarray(changed) as current,
        ):
            self.assertEqual(overlap_shift(old, current), 30)

    def test_overlap_allows_one_pixel_dpi_rounding_but_rejects_conflicting_motion(self):
        for middle_shift, expected in ((31, 30), (35, None)):
            with self.subTest(middle_shift=middle_shift):
                changed = self.pixels[30:330].copy()
                top = 100 - middle_shift
                changed[top : top + 100] = self.pixels[100:200]
                with (
                    Image.fromarray(self.pixels[:300]) as old,
                    Image.fromarray(changed) as current,
                ):
                    self.assertEqual(overlap_shift(old, current), expected)

    def test_missing_anchor_does_not_scroll_and_preserves_partial_image(self):
        self.controller.locator.locate_all.side_effect = None
        self.controller.locator.locate_all.return_value = []
        result = self.scanner().run(self.first)
        self.assertEqual(result.stop_reason, "anchor_missing")
        self.controller.scroll.assert_not_called()
        self.assertTrue(result.segments[0].partial_start)
        self.assertTrue(result.segments[0].partial_end)

    def test_verified_feed_cover_without_anchor_can_scroll_to_first_card(self):
        self.controller.locator.locate_all.side_effect = [
            [],
            [self.anchor(self.second, 250)],
        ]
        self.controller.detect_page.return_value = "moments_selected"
        result = self.scanner().run(self.first)
        self.assertEqual(result.stop_reason, "max_screens")
        self.assertEqual(len(result.frames), 2)
        self.controller.scroll.assert_called_once()

    def test_feed_layout_scales_insets_and_keeps_content_when_window_widens(self):
        profile = build_profile()
        surface = self.surface.model_copy(
            update={"bounds": Rect(x=0, y=0, width=1045, height=809)}
        )
        self.assertEqual(
            profile.feed_bounds(surface), Rect(x=375, y=40, width=664, height=760)
        )
        wide = surface.model_copy(
            update={"bounds": Rect(x=0, y=0, width=1245, height=809)}
        )
        self.assertEqual(profile.feed_bounds(wide).x, 375)
        self.assertEqual(profile.feed_bounds(wide).width, 864)
        normal = surface.model_copy(
            update={"scale": 1, "bounds": Rect(x=0, y=0, width=836, height=647)}
        )
        self.assertEqual(
            profile.feed_bounds(normal), Rect(x=300, y=32, width=531, height=608)
        )

    def test_embedded_viewer_requires_larger_version_of_same_thumbnail(self):
        profile = build_profile()
        locator = AirtestLocator(profile, "dark", MatchConfig())
        surface = self.surface.model_copy(
            update={"bounds": Rect(x=0, y=0, width=600, height=500)}
        )
        thumbnail = Image.fromarray(self.pixels[:80, :80])
        before = Image.new("RGB", (600, 500), (30, 30, 30))
        after = before.copy()
        self.addCleanup(thumbnail.close)
        self.addCleanup(before.close)
        self.addCleanup(after.close)
        before.paste(thumbnail, (410, 350))
        with thumbnail.resize((240, 240), Image.Resampling.BILINEAR) as enlarged:
            after.paste(enlarged, (170, 100))
        before.save(self.path / "source.png")
        after.save(self.path / "viewer.png")
        source = self.first.model_copy(
            update={"surface": surface, "image_path": self.path / "source.png"}
        )
        current = source.model_copy(update={"image_path": self.path / "viewer.png"})
        bounds = Rect(x=410, y=350, width=80, height=80)
        self.assertFalse(
            locator.is_enlarged_preview(source, source, bounds, (0, 0, 1, 1), 0.9, 1.5)
        )
        self.assertTrue(
            locator.is_enlarged_preview(source, current, bounds, (0, 0, 1, 1), 0.9, 1.5)
        )
        moved = current.model_copy(
            update={"surface": surface.model_copy(update={"surface_id": "other"})}
        )
        self.assertFalse(
            locator.is_enlarged_preview(source, moved, bounds, (0, 0, 1, 1), 0.9, 1.5)
        )
        self.assertTrue(
            locator.is_enlarged_preview(
                source, moved, bounds, (0, 0, 1, 1), 0.9, 1.5, allow_independent=True
            )
        )

    def test_unverifiable_overlap_stops_and_retains_both_raw_frames(self):
        unrelated = self.frame("unrelated", self.pixels[400:700])
        self.controller.scroll.return_value.after = unrelated
        result = self.scanner().run(self.first)
        self.assertEqual(result.stop_reason, "overlap_unverified")
        self.assertEqual(result.frames, [self.first, unrelated])
        self.assertTrue(result.segments[-1].partial_end)

    def test_no_progress_stops_without_claiming_end_of_history(self):
        self.controller.scroll.return_value = ActionResult(
            operation="scroll",
            name="feed",
            status="failed",
            attempted=True,
            executed=True,
            before=self.first,
            after=self.first,
            validation=ValidationResult(
                status="not_met", message="没有变化", change_ratio=0
            ),
            error="动作后验证超时：没有变化",
        )
        result = self.scanner().run(self.first)
        self.assertEqual(result.stop_reason, "no_progress")
        self.controller.scroll.assert_called_once()

    def test_input_failure_is_not_retried_and_manifest_records_error(self):
        self.controller.scroll.return_value = ActionResult(
            operation="scroll",
            name="feed",
            status="failed",
            attempted=True,
            executed=False,
            error="滚轮输入失败",
        )
        scan = self.scanner()
        with self.assertRaisesRegex(RuntimeError, "滚轮输入失败"):
            scan.run(self.first)
        self.controller.scroll.assert_called_once()
        manifest = json.loads(
            (scan.directory / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["stop_reason"], "failed")
        self.assertGreater(len(manifest["segments"]), 0)

    def test_multiple_anchors_use_actual_dpi_and_respect_search_region(self):
        profile = build_profile()
        with Image.open(profile.target("dark", "moments_anchor").path) as anchor:
            with Image.new("RGB", (200, 300), (30, 30, 30)) as image:
                image.paste(anchor, (150, 60))
                image.paste(anchor, (150, 170))
                image.save(self.first.image_path)
        locator = AirtestLocator(profile, "dark", MatchConfig())
        targets = locator.locate_all(self.first, "moments_anchor")
        self.assertEqual([target.bounds.y for target in targets], [60, 170])
        self.assertEqual(
            locator.locate_all(
                self.first, "moments_anchor", Rect(x=0, y=0, width=100, height=300)
            ),
            [],
        )
        with self.assertRaisesRegex(ValueError, "超出截图"):
            locator.locate_all(
                self.first, "moments_anchor", Rect(x=190, y=0, width=50, height=100)
            )

    def test_scroll_uses_screen_coordinates_and_does_not_drag(self):
        driver = WindowsDriver(save_options=SaveDialogConfig(submit="message"))
        driver.device = MagicMock()
        target = self.anchor(self.first, 70)
        with patch.object(driver, "verify_capture"):
            driver.scroll(self.first, target, 1)
        driver.device.mouse.scroll.assert_called_once_with(
            coords=(168, 102), wheel_dist=-1
        )
        driver.device.swipe.assert_not_called()

    def test_controller_scroll_preserves_once_only_input_and_action_evidence(self):
        driver = MagicMock()
        screenshotter = MagicMock()
        driver.prepare_capture.return_value = self.surface
        screenshotter.capture.side_effect = [self.first, self.second, self.second]
        controller = UiController(
            driver, screenshotter, MagicMock(), build_profile(), directory=self.path
        )
        with patch("wemo2.collectors.controller.time.sleep"):
            action = controller.scroll(Rect(x=0, y=0, width=200, height=300), 1)
        self.assertEqual(action.status, "verified")
        self.assertEqual(action.wheel_steps, 1)
        driver.scroll.assert_called_once()
        self.assertEqual(len(controller.results), 1)
        self.assertTrue((self.path / f"action_{action.action_id}.json").exists())

    def test_invalid_scroll_region_cannot_send_input(self):
        driver = MagicMock()
        driver.prepare_capture.return_value = self.surface
        screenshotter = MagicMock()
        screenshotter.capture.return_value = self.first
        controller = UiController(driver, screenshotter, MagicMock(), build_profile())
        action = controller.scroll(Rect(x=180, y=0, width=100, height=300), 1)
        self.assertEqual(action.status, "failed")
        self.assertFalse(action.attempted)
        driver.scroll.assert_not_called()

    def test_scroll_waits_until_animation_stops_before_returning_coordinates(self):
        driver = MagicMock()
        driver.prepare_capture.return_value = self.surface
        moving = self.frame("moving", self.pixels[70:370])
        screenshotter = MagicMock()
        screenshotter.capture.side_effect = [
            self.first,
            moving,
            self.second,
            self.second,
        ]
        controller = UiController(
            driver, screenshotter, MagicMock(), build_profile(), directory=self.path
        )
        with patch("wemo2.collectors.controller.time.sleep"):
            action = controller.scroll(Rect(x=0, y=0, width=200, height=300), 1)
        self.assertEqual(action.status, "verified")
        self.assertEqual(action.after, self.second)
        self.assertEqual(screenshotter.capture.call_count, 4)
        driver.scroll.assert_called_once()
        self.assertEqual(len(controller.results), 1)

    def test_playing_video_cannot_prevent_layout_stability_after_scroll(self):
        changed = self.pixels[100:400].copy()
        changed[:, 90:] = 210
        playing = self.frame("playing", changed)
        driver = MagicMock()
        driver.prepare_capture.return_value = self.surface
        screenshotter = MagicMock()
        screenshotter.capture.side_effect = [self.first, self.second, playing]
        controller = UiController(driver, screenshotter, MagicMock(), build_profile())
        with patch("wemo2.collectors.controller.time.sleep"):
            result = controller.scroll(Rect(x=0, y=0, width=200, height=300), 3)
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.after, playing)
        driver.scroll.assert_called_once()

    def test_only_video_motion_is_not_scroll_progress(self):
        changed = self.pixels[:300].copy()
        changed[:, 90:] = 210
        playing = self.frame("playing", changed)
        driver = MagicMock()
        driver.prepare_capture.return_value = self.surface
        screenshotter = MagicMock()
        screenshotter.capture.side_effect = [self.first, playing]
        controller = UiController(driver, screenshotter, MagicMock(), build_profile())
        with (
            patch("wemo2.collectors.controller.time.monotonic", side_effect=[0, 3]),
            patch("wemo2.collectors.controller.time.sleep"),
        ):
            result = controller.scroll(Rect(x=0, y=0, width=200, height=300), 3)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.validation.change_ratio, 0)
        self.assertEqual(result.after, playing)
        driver.scroll.assert_called_once()

    def test_failed_stability_retains_last_raw_frame_in_manifest(self):
        self.controller.scroll.return_value.status = "failed"
        self.controller.scroll.return_value.error = "布局稳定等待超时"
        scan = self.scanner()
        with self.assertRaisesRegex(RuntimeError, "布局稳定等待超时"):
            scan.run(self.first)
        self.assertEqual(scan.result.frames, [self.first, self.second])

    def test_media_input_failure_saves_card_metadata_before_propagating(self):
        media = MediaCapture(self.controller, FeedCaptureConfig(), self.path)
        media.reader = MagicMock()
        media.reader.channel_header.return_value = None
        media.reader.is_channel_card.return_value = False
        media.reader.nickname.return_value = TextLine(
            text="测试昵称", score=0.9, bounds=Rect(x=10, y=5, width=60, height=20)
        )
        with (
            patch(
                "wemo2.collectors.media.media_tiles",
                return_value=[Rect(x=5, y=40, width=80, height=40)],
            ),
            patch.object(
                media, "_save_media", side_effect=RuntimeError("保存失败")
            ) as save,
        ):
            with self.assertRaisesRegex(RuntimeError, "保存失败"):
                media.process(
                    self.first,
                    Rect(x=0, y=0, width=200, height=300),
                    [self.anchor(self.first, 200)],
                )
        save.assert_called_once()
        metadata = list(media.directory.glob("card_*.json"))
        self.assertEqual(len(metadata), 1)
        card = json.loads(metadata[0].read_text(encoding="utf-8"))
        self.assertEqual(card["nickname"]["text"], "测试昵称")
        self.assertEqual(card["status"], "failed")
        self.assertEqual(card["media"], [])

    def test_visible_media_of_long_post_is_saved_before_end_anchor_appears(self):
        media = MediaCapture(self.controller, FeedCaptureConfig(), self.path)
        media.reader = MagicMock()
        media.reader.channel_header.return_value = None
        media.reader.is_channel_card.return_value = False
        media.reader.nickname.return_value = None
        with (
            patch(
                "wemo2.collectors.media.media_tiles",
                return_value=[Rect(x=70, y=50, width=80, height=80)],
            ),
            patch.object(media, "_save_media") as save,
        ):
            save.return_value = self.make_media_capture()._save_media(
                self.first, Rect(x=10, y=40, width=80, height=80), 40
            )
            media.process(self.first, Rect(x=0, y=0, width=200, height=300), [])
        save.assert_called_once()
        metadata = list(media.directory.glob("card*.json"))
        self.assertEqual(len(metadata), 1)
        card = json.loads(metadata[0].read_text(encoding="utf-8"))
        self.assertTrue(card["partial_end"])
        self.assertEqual(len(card["media"]), 1)

    def test_image_file_validation_rejects_non_media(self):
        self.assertEqual(file_kind(self.first.image_path), "image")
        bad = self.path / "fake.jpg"
        bad.write_bytes(b"not an image")
        with self.assertRaisesRegex(RuntimeError, "无法识别"):
            file_kind(bad)

    def test_media_regions_use_library_components_without_avatar_or_text(self):
        with Image.new("RGB", (500, 400), (30, 30, 30)) as image:
            image.paste((120, 30, 80), (10, 10, 60, 60))  # 头像不是媒体。
            for x in (80, 224, 368):
                image.paste((120, 50, 180), (x, 150, x + 140, 290))
            tiles = media_tiles(image, 1.25)
        self.assertEqual(len(tiles), 3)
        self.assertEqual([tile.x for tile in tiles], [80, 224, 368])

    def test_channel_footer_is_recognized_without_clicking_cover(self):
        reader = TextRecognizer()
        footer = TextLine(
            text="1小时前 视频号·作者",
            score=0.99,
            bounds=Rect(x=80, y=350, width=170, height=20),
        )
        with Image.new("RGB", (400, 400), (30, 30, 30)) as image:
            image.paste((140, 140, 140), (80, 350, 250, 370))
            self.assertTrue(reader.is_channel_card(image, [footer], []))
            caption = footer.model_copy(update={"text": "我今天在视频号看了电影"})
            self.assertFalse(reader.is_channel_card(image, [caption], []))

    def test_channel_card_never_opens_media(self):
        media = MediaCapture(self.controller, FeedCaptureConfig(), self.path)
        media.reader = MagicMock()
        media.reader.nickname.return_value = None
        media.reader.is_channel_card.return_value = True
        media.image_strategy = MagicMock()
        with patch(
            "wemo2.collectors.media.media_tiles",
            return_value=[Rect(x=50, y=50, width=80, height=100)],
        ):
            media.process(self.first, Rect(x=0, y=0, width=200, height=300), [])
        self.assertEqual(media.image_strategy.save.call_args.args[2], [])
        self.controller.run_media_workflow.assert_not_called()

    def test_reverse_scroll_checks_overlap_and_preserves_stream_coordinates(self):
        scan = self.scanner()
        scan.directory.mkdir()
        scan.result.frames = [self.first, self.second]
        scan.result.stop_reason = "max_screens"
        scan.stream_height = 400
        scan.media = MagicMock()
        self.controller.capture.return_value = self.second
        self.controller.scroll.return_value.after = self.first
        self.controller.locator.locate_all.side_effect = [[], []]
        scan.download_media()
        self.assertEqual(
            [call.args[3] for call in scan.media.process.call_args_list], [100, 0]
        )
        self.assertEqual(self.controller.scroll.call_args.args[1], -1)
        self.assertEqual(scan.result.media_stop_reason, "completed_visible_range")

    def test_reverse_scroll_rejects_unrelated_content_before_next_download(self):
        scan = self.scanner()
        scan.directory.mkdir()
        scan.result.frames = [self.first, self.second]
        scan.result.stop_reason = "max_screens"
        scan.stream_height = 400
        scan.media = MagicMock()
        self.controller.capture.return_value = self.second
        self.controller.locator.locate_all.side_effect = [[]]
        self.controller.scroll.return_value.after = self.frame(
            "unrelated", np.zeros((300, 200, 3), dtype=np.uint8)
        )
        with self.assertRaisesRegex(RuntimeError, "无法验证相邻截图重叠"):
            scan.download_media()
        scan.media.process.assert_called_once()
        self.assertEqual(scan.result.media_stop_reason, "failed")

    def test_nickname_uses_top_blue_line_and_retains_confidence(self):
        reader = TextRecognizer()
        nickname = TextLine(
            text="昵称2", score=0.95, bounds=Rect(x=70, y=10, width=60, height=20)
        )
        body = TextLine(
            text="正文", score=0.99, bounds=Rect(x=70, y=40, width=60, height=20)
        )
        reader.read = MagicMock(return_value=[nickname, body])
        with Image.new("RGB", (200, 300), (30, 30, 30)) as image:
            image.paste((200, 150, 70), (10, 10, 60, 35))
            image.paste((130, 145, 170), (70, 10, 130, 30))
            image.paste((220, 220, 220), (70, 40, 130, 60))
            self.assertEqual(reader.nickname(image, 1.25), nickname)

    def test_feed_config_rejects_unbounded_limits_and_invalid_regions(self):
        for options in (
            {"max_screens": 0},
            {"max_screens": 101},
            {"wheel_steps": 0},
            {"region": {"x": 0, "y": 0, "width": 0, "height": 300}},
        ):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                FeedCaptureConfig.model_validate(options)

    def make_media_capture(self, *, embedded=False):
        state = {"page": "original"}
        profile = build_profile()
        workflow = profile.media_workflow("save_media")
        for name, step in workflow.steps.items():
            workflow.steps[name] = step.model_copy(
                update={"timeout_seconds": 0.01, "settle_seconds": 0}
            )
        driver = MagicMock()
        frames = {
            "original": self.first,
            "viewer": self.second.model_copy(
                update={
                    "surface": self.surface.model_copy(update={"surface_id": "viewer"})
                }
            ),
            "menu": self.second.model_copy(
                update={
                    "surface": self.surface.model_copy(update={"surface_id": "viewer"})
                }
            ),
            "dialog": self.second.model_copy(
                update={
                    "surface": self.surface.model_copy(update={"surface_id": "dialog"})
                }
            ),
        }
        if embedded:
            frames["viewer"] = frames["viewer"].model_copy(
                update={"surface": self.surface}
            )
            frames["menu"] = frames["menu"].model_copy(update={"surface": self.surface})
        controller = UiController(
            driver,
            MagicMock(),
            MagicMock(),
            profile,
            ControllerConfig(capture_attempts=1, locate_attempts=1),
            self.path / "actions",
        )
        controller.capture = MagicMock(
            side_effect=lambda **kwargs: frames[state["page"]].model_copy(
                update={"captured_at": datetime.now(UTC)}
            )
        )

        def click(frame, target):
            if target.name == "channel_close":
                state["page"] = "original"
            else:
                state["page"] = "viewer" if state["page"] == "original" else "dialog"

        def right_click(frame, target):
            state["page"] = "menu"

        def save(directory):
            state["page"] = "viewer"
            return self.second.image_path

        def close(original, *, allow_embedded=False):
            if embedded:
                self.assertTrue(allow_embedded)
            state["page"] = "original"

        driver.click.side_effect = click
        driver.right_click.side_effect = right_click
        driver.save_dialog.side_effect = save
        driver.close_viewer.side_effect = close
        driver.is_save_dialog.side_effect = lambda: state["page"] == "dialog"
        driver.open_save_dialog.side_effect = lambda: state.update(page="dialog")
        driver.current_surface.side_effect = lambda: frames[state["page"]].surface
        controller.locator.is_enlarged_preview.return_value = embedded
        media = MediaCapture(
            controller,
            FeedCaptureConfig(
                media_load_wait_seconds=0,
                media_load_timeout_seconds=0.01,
                media_workflow="save_media",
            ),
            self.path,
        )
        media.reader = MagicMock()
        media.reader.channel_header.return_value = None
        media.reader.is_channel_card.return_value = False
        media.reader.read.return_value = [
            TextLine(
                text="另存为", score=0.99, bounds=Rect(x=10, y=20, width=70, height=20)
            )
        ]
        sleep = patch("wemo2.collectors.controller.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        return media

    def test_embedded_media_save_returns_only_after_verified_preview(self):
        media = self.make_media_capture(embedded=True)
        saved = media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        self.assertEqual(saved.kind, "image")
        media.controller.driver.close_viewer.assert_called_once_with(
            self.surface, allow_embedded=True
        )
        self.assertTrue(
            all(result.status == "verified" for result in media.controller.results)
        )

    def test_shortcut_workflow_bypasses_context_menu(self):
        media = self.make_media_capture(embedded=True)
        media.config = media.config.model_copy(
            update={"media_workflow": "save_media_shortcut"}
        )
        saved = media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        self.assertEqual(saved.kind, "image")
        driver = media.controller.driver
        driver.open_save_dialog.assert_called_once()
        driver.right_click.assert_not_called()
        driver.click.assert_called_once()
        driver.save_dialog.assert_called_once()

    def test_opened_channel_closes_tab_without_save_or_right_click(self):
        from wemo2.collectors.controller import VideoChannelSkipped

        media = self.make_media_capture(embedded=True)
        label = TextLine(
            text="视频号", score=0.99, bounds=Rect(x=50, y=20, width=50, height=20)
        )
        with Image.open(self.second.image_path) as image:
            viewer_pixels = image.tobytes()
        media.reader.channel_header.side_effect = lambda image, scale: (
            label if image.tobytes() == viewer_pixels else None
        )
        close = self.anchor(self.second, 30).model_copy(
            update={"name": "channel_close"}
        )
        media.controller.locator.locate_all.return_value = [close]
        with self.assertRaises(VideoChannelSkipped):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        driver = media.controller.driver
        self.assertEqual(driver.click.call_count, 2)
        driver.right_click.assert_not_called()
        driver.open_save_dialog.assert_not_called()
        driver.save_dialog.assert_not_called()

    def test_unconfirmed_embedded_viewer_stops_without_right_click_or_repeat(self):
        media = self.make_media_capture(embedded=True)
        media.controller.locator.is_enlarged_preview.return_value = False
        with self.assertRaisesRegex(RuntimeError, "验证超时"):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        media.controller.driver.click.assert_called_once()
        media.controller.driver.right_click.assert_not_called()
        self.assertTrue(media.controller.results[-1].executed)

    def test_nickname_search_can_skip_cover_above_first_card(self):
        reader = TextRecognizer()
        line = TextLine(
            text="昵称", score=0.99, bounds=Rect(x=90, y=200, width=60, height=20)
        )
        reader.read = MagicMock(
            side_effect=lambda image: [line] if image.height > 220 else []
        )
        with Image.new("RGB", (200, 300), (30, 30, 30)) as image:
            with Image.fromarray(self.pixels[:50, :50]) as avatar:
                image.paste(avatar, (28, 194))
            image.paste((130, 145, 170), (90, 200, 150, 220))
            self.assertIsNone(reader.nickname(image, 1.25))
            self.assertEqual(
                reader.nickname(image, 1.25, search_height=image.height), line
            )

    def test_media_tiles_uses_card_background_below_bright_cover(self):
        with Image.new("RGB", (400, 400), (30, 30, 30)) as image:
            image.paste((220, 40, 20), (0, 0, 400, 100))
            image.paste((70, 160, 220), (95, 180, 175, 260))
            image.paste((220, 150, 70), (182, 180, 262, 260))
            self.assertEqual(
                media_tiles(image, 1.25),
                [
                    Rect(x=95, y=180, width=80, height=80),
                    Rect(x=182, y=180, width=80, height=80),
                ],
            )

    def test_media_save_flow_checks_real_file_then_returns_to_list(self):
        media = self.make_media_capture()
        saved = media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        self.assertEqual(saved.kind, "image")
        self.assertEqual(saved.size, self.second.image_path.stat().st_size)
        self.assertEqual(len(saved.sha256), 64)
        driver = media.controller.driver
        self.assertEqual(driver.click.call_count, 2)
        driver.right_click.assert_called_once()
        driver.save_dialog.assert_called_once()
        driver.close_viewer.assert_called_once()
        self.assertEqual(
            [result.name for result in media.controller.results],
            [
                "open_viewer",
                "wait_ready",
                "open_menu",
                "choose_save_as",
                "write_file",
                "return_to_feed",
            ],
        )
        self.assertTrue(
            all(result.status == "verified" for result in media.controller.results)
        )

    def test_media_waits_while_loading_before_right_click(self):
        media = self.make_media_capture()
        calls = 0
        menu = TextLine(
            text="另存为", score=0.99, bounds=Rect(x=10, y=20, width=70, height=20)
        )
        loading = menu.model_copy(update={"text": "加载中"})

        def read(image):
            nonlocal calls
            calls += 1
            if calls == 1:
                media.controller.driver.right_click.assert_not_called()
                return [loading]
            return [menu]

        media.reader.read.side_effect = read
        # 测试关注加载提示消失后的输入顺序，不让证据文件写入速度决定超时。
        with patch("wemo2.collectors.controller.time.monotonic", return_value=0):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        wait = media.controller.results[1]
        self.assertEqual(wait.operation, "wait_ready")
        self.assertEqual(wait.status, "verified")
        self.assertFalse(wait.attempted)
        self.assertFalse(wait.executed)
        media.controller.driver.right_click.assert_called_once()

    def test_loading_timeout_prevents_right_click_and_save(self):
        media = self.make_media_capture()
        media.reader.read.return_value = [
            TextLine(
                text="正在加载",
                score=0.99,
                bounds=Rect(x=10, y=20, width=70, height=20),
            )
        ]
        with self.assertRaisesRegex(RuntimeError, "媒体仍在加载"):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        media.controller.driver.right_click.assert_not_called()
        media.controller.driver.save_dialog.assert_not_called()
        self.assertEqual(media.controller.results[-1].operation, "wait_ready")

    def test_three_images_save_with_two_arrows_and_one_viewer_close(self):
        media = self.make_media_capture()
        media.controller.locator.is_enlarged_preview.return_value = True
        tiles = [
            Rect(x=10, y=40, width=50, height=50),
            Rect(x=70, y=40, width=50, height=50),
            Rect(x=130, y=40, width=50, height=50),
        ]
        with patch("wemo2.collectors.media.media_tiles", return_value=tiles):
            media.process(self.first, Rect(x=0, y=0, width=200, height=300), [])
        driver = media.controller.driver
        self.assertEqual(driver.click.call_count, 4)  # 一次打开图片与三次点击另存为。
        self.assertEqual(driver.switch_image.call_count, 2)
        self.assertEqual(driver.right_click.call_count, 3)
        self.assertEqual(driver.save_dialog.call_count, 3)
        driver.close_viewer.assert_called_once()
        self.assertIsNone(media.viewer)
        self.assertEqual(len(media.previews), 3)
        self.assertTrue(all(item.workflow_complete for item in media.previews))
        record = json.loads(
            next(media.directory.glob("card_*.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(len(record["media"]), 3)
        self.assertEqual([item.bounds.x for item in media.previews], [10, 70, 130])

    def test_arrow_failure_reopens_remaining_images_without_resaving_first(self):
        media = self.make_media_capture()
        media.controller.locator.is_enlarged_preview.return_value = False
        media.controller.config = media.controller.config.model_copy(
            update={"verification_timeout": 0.01}
        )
        tiles = [
            Rect(x=10, y=40, width=50, height=50),
            Rect(x=70, y=40, width=50, height=50),
            Rect(x=130, y=40, width=50, height=50),
        ]
        with patch("wemo2.collectors.media.media_tiles", return_value=tiles):
            media.process(self.first, Rect(x=0, y=0, width=200, height=300), [])
        driver = media.controller.driver
        driver.switch_image.assert_called_once()
        self.assertEqual(driver.save_dialog.call_count, 3)
        self.assertEqual(driver.close_viewer.call_count, 3)
        self.assertEqual(driver.click.call_count, 6)
        switch = next(
            item
            for item in media.controller.results
            if item.operation == "switch_image"
        )
        self.assertTrue(switch.executed)
        self.assertEqual(switch.status, "failed")
        self.assertEqual(len(media.previews), 3)
        self.assertTrue(all(item.workflow_complete for item in media.previews))

    def test_file_save_failure_never_uses_navigation_fallback_or_cache(self):
        media = self.make_media_capture()
        media.controller.locator.is_enlarged_preview.return_value = True
        calls = 0

        original_save = media.controller.driver.save_dialog.side_effect

        def checked_save(directory):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("第二张保存失败")
            return original_save(directory)

        media.controller.driver.save_dialog.side_effect = checked_save
        tiles = [
            Rect(x=10, y=40, width=50, height=50),
            Rect(x=70, y=40, width=50, height=50),
        ]
        with (
            patch("wemo2.collectors.media.media_tiles", return_value=tiles),
            self.assertRaisesRegex(RuntimeError, "第二张保存失败"),
        ):
            media.process(self.first, Rect(x=0, y=0, width=200, height=300), [])
        driver = media.controller.driver
        self.assertEqual(driver.save_dialog.call_count, 2)
        self.assertEqual(driver.click.call_count, 3)
        driver.close_viewer.assert_not_called()
        self.assertEqual(media.saved, {})
        self.assertFalse(media.previews[0].workflow_complete)
        record = json.loads(
            next(media.directory.glob("card_*.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(len(record["media"]), 1)
        self.assertEqual(record["status"], "failed")

    def test_reopen_strategy_can_be_selected_in_configuration(self):
        self.assertIsInstance(
            image_save_strategy(FeedCaptureConfig().use_image_arrows),
            ArrowImageStrategy,
        )
        self.assertIsInstance(image_save_strategy(False), ReopenImageStrategy)
        media = self.make_media_capture()
        media.image_strategy = image_save_strategy(False)
        tiles = [
            Rect(x=10, y=40, width=50, height=50),
            Rect(x=70, y=40, width=50, height=50),
        ]
        with patch("wemo2.collectors.media.media_tiles", return_value=tiles):
            media.process(self.first, Rect(x=0, y=0, width=200, height=300), [])
        driver = media.controller.driver
        driver.switch_image.assert_not_called()
        self.assertEqual(driver.close_viewer.call_count, 2)
        self.assertEqual(driver.click.call_count, 4)

    def test_video_closes_viewer_and_next_image_reopens_without_arrow(self):
        media = self.make_media_capture()
        video = self.path / "fixture.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"video fixture")
        original_save = media.controller.driver.save_dialog.side_effect
        calls = 0

        def save(directory):
            nonlocal calls
            calls += 1
            path = original_save(directory)
            return video if calls == 1 else path

        media.controller.driver.save_dialog.side_effect = save
        tiles = [
            Rect(x=10, y=40, width=50, height=50),
            Rect(x=70, y=40, width=50, height=50),
        ]
        with patch("wemo2.collectors.media.media_tiles", return_value=tiles):
            media.process(self.first, Rect(x=0, y=0, width=200, height=300), [])
        driver = media.controller.driver
        driver.switch_image.assert_not_called()
        self.assertEqual(driver.close_viewer.call_count, 2)
        self.assertEqual(
            [item.file.kind for item in media.previews], ["video", "image"]
        )
        self.assertTrue(all(item.workflow_complete for item in media.previews))

    def test_changed_window_prevents_arrow_and_automatic_exit(self):
        media = self.make_media_capture()
        bounds = Rect(x=10, y=40, width=50, height=50)
        media._save_media(self.first, bounds, 40, keep_viewer=True)
        unrelated = self.second.model_copy(
            update={
                "surface": self.surface.model_copy(update={"surface_id": "unrelated"}),
                "captured_at": datetime.now(UTC),
            }
        )
        media.controller.capture = MagicMock(return_value=unrelated)
        with self.assertRaisesRegex(RuntimeError, "图片查看器状态已变化"):
            media.image_strategy.save(
                media,
                MagicMock(source=self.first, media=[]),
                [Rect(x=70, y=40, width=50, height=50)],
                0,
                Rect(x=0, y=0, width=200, height=300),
            )
        driver = media.controller.driver
        driver.switch_image.assert_not_called()
        driver.close_viewer.assert_not_called()
        self.assertEqual(driver.click.call_count, 2)
        self.assertFalse(media.previews[0].workflow_complete)

    def test_driver_image_navigation_uses_left_and_right_keys_after_focus_check(self):
        driver = WindowsDriver(save_options=SaveDialogConfig(submit="message"))
        driver.device = MagicMock()
        with patch.object(driver, "verify_capture") as verify:
            driver.switch_image(self.first, "right")
            driver.switch_image(self.first, "left")
            self.assertEqual(verify.call_count, 2)
            with self.assertRaisesRegex(ValueError, "left 或 right"):
                driver.switch_image(self.first, "up")
        self.assertEqual(
            [call.args[0] for call in driver.device.keyevent.call_args_list],
            ["{RIGHT}", "{LEFT}"],
        )

    def test_changed_media_content_cannot_be_clicked(self):
        media = MediaCapture(self.controller, FeedCaptureConfig(), self.path)
        self.controller.capture.return_value = self.second
        with self.assertRaisesRegex(RuntimeError, "媒体内容已变化"):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        self.controller.click_region.assert_not_called()

    def test_media_cache_reuses_same_preview_with_dpi_rounding_and_render_changes(self):
        media = self.make_media_capture()
        bounds = Rect(x=10, y=40, width=80, height=80)
        saved = media._save_media(self.first, bounds, 40)
        changed = self.frame("render", self.pixels[:300] + 1)
        media.controller.capture = MagicMock(return_value=changed)
        media.controller.locator = AirtestLocator(
            media.controller.profile, "dark", MatchConfig()
        )
        self.assertEqual(media._save_media(changed, bounds, 41), saved)
        self.assertEqual(media.controller.driver.click.call_count, 2)
        self.assertEqual(len(media.previews), 1)

    def test_media_cache_rejects_other_image_at_nearby_position(self):
        media = self.make_media_capture()
        bounds = Rect(x=10, y=40, width=80, height=80)
        media._save_media(self.first, bounds, 40)
        media.controller.capture = MagicMock(return_value=self.second)
        media.controller.locator = AirtestLocator(
            media.controller.profile, "dark", MatchConfig()
        )
        media.controller.run_media_workflow = MagicMock(
            side_effect=RuntimeError("需要新下载")
        )
        with self.assertRaisesRegex(RuntimeError, "需要新下载"):
            media._save_media(self.second, bounds, 41)
        media.controller.run_media_workflow.assert_called_once()

    def test_unrecognized_menu_does_not_guess_save_coordinates(self):
        media = self.make_media_capture()
        media.reader.read.return_value = []
        with self.assertRaisesRegex(RuntimeError, "未唯一识别"):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        driver = media.controller.driver
        driver.click.assert_called_once()
        driver.right_click.assert_called_once()
        driver.save_dialog.assert_not_called()
        self.assertTrue(media.controller.results[-1].executed)
        self.assertEqual(media.controller.results[-1].status, "failed")

    def test_standard_save_dialog_with_ambiguous_edits_does_not_type(self):
        driver = WindowsDriver(save_options=SaveDialogConfig(submit="message"))
        driver.device = MagicMock()
        dialog = MagicMock()
        dialog.class_name.return_value = "#32770"
        dialog.window_text.return_value = "另存为"
        dialog.descendants.return_value = [MagicMock(), MagicMock()]
        for edit in dialog.descendants.return_value:
            edit.class_name.return_value = "Edit"
            edit.control_id.return_value = 1001
        with (
            patch.object(driver, "verify_capture"),
            patch.object(driver, "current_surface"),
            patch("pywinauto.Desktop") as desktop,
        ):
            desktop.return_value.window.return_value.wrapper_object.return_value = (
                dialog
            )
            with self.assertRaisesRegex(RuntimeError, "无法唯一识别"):
                driver.save_dialog(self.path)
        driver.device.keyevent.assert_not_called()
        for edit in dialog.descendants.return_value:
            edit.set_edit_text.assert_not_called()

    def test_blue_poster_text_without_adjacent_avatar_is_not_nickname(self):
        reader = TextRecognizer()
        reader.read = MagicMock(
            return_value=[
                TextLine(
                    text="海报标题",
                    score=0.99,
                    bounds=Rect(x=70, y=10, width=60, height=20),
                )
            ]
        )
        with Image.new("RGB", (200, 300), (30, 30, 30)) as image:
            image.paste((130, 145, 170), (70, 10, 130, 30))
            self.assertIsNone(reader.nickname(image, 1.25))

    def test_save_dialog_uses_unique_path_and_waits_for_actual_file(self):
        driver = WindowsDriver(save_options=SaveDialogConfig(submit="message"))
        driver.device = MagicMock()
        driver.device.handle = 123
        driver.user32 = MagicMock()
        driver.user32.GetForegroundWindow.return_value = 456
        driver.user32.IsWindow.return_value = False

        def process_id(handle, pointer):
            pointer._obj.value = 42
            return 1

        driver.user32.GetWindowThreadProcessId.side_effect = process_id
        dialog = MagicMock()
        dialog.class_name.return_value = "#32770"
        dialog.window_text.return_value = "另存为"
        edit = MagicMock()
        edit.class_name.return_value = "Edit"
        edit.control_id.return_value = 1001
        search = MagicMock()
        search.class_name.return_value = "Edit"
        search.control_id.return_value = 0
        toolbar = MagicMock()
        toolbar.class_name.return_value = "ToolbarWindow32"
        toolbar.control_id.return_value = 1001
        dialog.descendants.return_value = [toolbar, search, edit]
        button = MagicMock()
        button.class_name.return_value = "Button"
        button.control_id.return_value = 1
        button.handle = 789
        dialog.descendants.return_value.append(button)

        def send(handle, message, timeout):
            if message == 0x00F5:  # BM_CLICK；WM_NULL 只探测响应，不能产生文件。
                destination = Path(edit.set_edit_text.call_args.args[0]).with_suffix(
                    ".png"
                )
                destination.write_bytes(self.first.image_path.read_bytes())

        old_device = driver.device
        with (
            patch.object(driver, "verify_capture"),
            patch.object(driver, "current_surface"),
            patch("pywinauto.Desktop") as desktop,
            patch("wemo2.collectors.windows.backend.create_device") as create,
            patch(
                "wemo2.collectors.windows.backend.send_control_message",
                side_effect=send,
            ) as message,
            patch("wemo2.collectors.windows.driver.time.sleep"),
        ):
            desktop.return_value.window.return_value.wrapper_object.return_value = (
                dialog
            )
            path = driver.save_dialog(self.path / "media")
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, self.path / "media")
        self.assertTrue(path.name.startswith("asset_"))
        search.set_edit_text.assert_not_called()
        toolbar.set_edit_text.assert_not_called()
        old_device.keyevent.assert_not_called()
        self.assertEqual(message.call_args_list[0].args[:2], (789, 0))
        self.assertEqual(message.call_args_list[1].args[:2], (789, 0x00F5))
        button.click.assert_not_called()
        old_device.screen.close.assert_called_once()
        create.assert_called_once_with(handle=456, foreground=False)

    def test_saved_file_and_executed_fact_survive_post_action_capture_failure(self):
        driver = MagicMock()
        driver.prepare_capture.return_value = self.surface
        screenshotter = MagicMock()
        screenshotter.capture.side_effect = [self.first, RuntimeError("操作后截图失败")]
        controller = UiController(
            driver,
            screenshotter,
            MagicMock(),
            build_profile(),
            ControllerConfig(capture_attempts=1),
            self.path / "actions",
        )
        save = MagicMock(return_value=self.second.image_path)
        with self.assertRaisesRegex(RuntimeError, "操作后截图失败"):
            controller.platform_step("save_file", save)
        save.assert_called_once()
        result = controller.results[-1]
        self.assertTrue(result.attempted)
        self.assertTrue(result.executed)
        self.assertEqual(result.status, "failed")
        self.assertIn(self.second.image_path, result.evidence)

    def test_follow_foreground_keeps_process_boundary_after_previous_window_closes(
        self,
    ):
        driver = WindowsDriver(save_options=SaveDialogConfig(submit="message"))
        driver.device = MagicMock()
        driver.device.handle = 123
        driver.session_process_id = 42
        driver.user32 = MagicMock()
        driver.user32.GetForegroundWindow.return_value = 456

        def process_id(handle, pointer):
            self.assertEqual(handle, 456)  # 不再向已关闭的菜单窗口查询进程。
            pointer._obj.value = 42
            return 1

        driver.user32.GetWindowThreadProcessId.side_effect = process_id
        with (
            patch.object(driver, "current_surface"),
            patch("wemo2.collectors.windows.backend.create_device") as create,
        ):
            driver.follow_foreground()
        create.assert_called_once_with(handle=456, foreground=False)

    def test_media_targets_and_workflow_can_change_without_executor_branches(self):
        media = self.make_media_capture()
        profile = media.controller.profile
        profile.media_workflows.pop("save_media_shortcut")
        workflow = profile.media_workflow("save_media")
        profile.media_targets["custom_export"] = MediaTargetSpec(
            kind="ocr",
            texts=["导出原文件"],
            threshold=0.95,
            region=(0.1, 0.1, 0.8, 0.8),
        )
        profile.media_targets["custom_preview"] = profile.media_targets.pop(
            "media_preview"
        )
        workflow.steps["open_viewer"] = workflow.steps["open_viewer"].model_copy(
            update={"target": "custom_preview"}
        )
        workflow.steps["choose_save_as"] = workflow.steps["choose_save_as"].model_copy(
            update={"target": "custom_export"}
        )
        profile.media_workflows["save_media"] = workflow.model_copy(
            update={"menu_target": "custom_export"}
        )
        profile.media_workflows["custom_flow"] = profile.media_workflows["save_media"]
        media.config = media.config.model_copy(update={"media_workflow": "custom_flow"})
        media.reader.read.return_value = [
            TextLine(
                text="导出原文件",
                score=0.99,
                bounds=Rect(x=10, y=20, width=70, height=20),
            )
        ]
        media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        chosen = media.controller.driver.click.call_args_list[1].args[1]
        self.assertEqual(chosen.bounds.x, 30)
        self.assertEqual(chosen.bounds.y, 50)
        self.assertEqual(chosen.name, "custom_export")
        self.assertTrue(
            all(result.workflow == "custom_flow" for result in media.controller.results)
        )

    def test_invalid_workflow_references_and_cycles_are_rejected_before_input(self):
        for change in (
            {"next": "missing"},
            {"next": "open_viewer"},
            {"target": "missing"},
            {"before": "viewer"},
        ):
            with self.subTest(change=change):
                media = self.make_media_capture()
                profile = media.controller.profile
                workflow = profile.media_workflow("save_media")
                workflow.steps["open_viewer"] = workflow.steps[
                    "open_viewer"
                ].model_copy(update=change)
                with self.assertRaises(ValueError):
                    media.controller.run_media_workflow(
                        self.first,
                        Rect(x=10, y=40, width=80, height=80),
                        self.path,
                        media.reader,
                        MagicMock(),
                    )
                media.controller.driver.click.assert_not_called()
                media.controller.driver.right_click.assert_not_called()

    def test_invalid_region_and_unknown_workflow_fail_configuration_validation(self):
        with self.assertRaises(ValidationError):
            MediaTargetSpec(kind="region", region=(0.9, 0, 0.5, 1))
        with self.assertRaises(ValidationError):
            AppConfig(
                collector="windows_screenshot",
                collector_options={
                    "feed": {"enabled": True, "media_workflow": "missing"}
                },
            )

    def test_save_dialog_not_recognized_prevents_filename_input(self):
        media = self.make_media_capture()
        media.controller.driver.is_save_dialog.return_value = False
        media.controller.driver.is_save_dialog.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "对话框状态"):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        media.controller.driver.save_dialog.assert_not_called()
        self.assertEqual(media.controller.driver.click.call_count, 2)
        self.assertTrue(media.controller.results[-1].executed)

    def test_media_save_exception_preserves_attempt_without_repeating_input(self):
        media = self.make_media_capture()
        media.controller.driver.save_dialog.side_effect = OSError("保存调用失败")
        with self.assertRaisesRegex(RuntimeError, "保存调用失败"):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        media.controller.driver.save_dialog.assert_called_once()
        media.controller.driver.close_viewer.assert_not_called()
        result = media.controller.results[-1]
        self.assertTrue(result.attempted)
        self.assertFalse(result.executed)
        self.assertEqual(result.status, "failed")

    def test_return_failure_keeps_verified_file_and_executed_action(self):
        media = self.make_media_capture()
        media.controller.driver.close_viewer.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "返回原列表"):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        self.assertIsNotNone(media.last_saved)
        self.assertEqual(media.last_saved.path, self.second.image_path)
        self.assertIn(self.second.image_path, media.paths)
        media.controller.driver.close_viewer.assert_called_once()
        result = media.controller.results[-1]
        self.assertTrue(result.executed)
        self.assertEqual(result.status, "failed")
        persisted = json.loads(
            (self.path / "actions" / f"action_{result.action_id}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(persisted["workflow"], "save_media")
        self.assertTrue(persisted["executed"])

    def test_low_confidence_and_duplicate_menu_targets_do_not_save(self):
        line = TextLine(
            text="另存为", score=0.99, bounds=Rect(x=10, y=20, width=70, height=20)
        )
        for lines in ([line.model_copy(update={"score": 0.7})], [line, line]):
            with self.subTest(lines=lines):
                media = self.make_media_capture()
                media.reader.read.return_value = lines
                with self.assertRaisesRegex(RuntimeError, "未唯一识别"):
                    media._save_media(
                        self.first, Rect(x=10, y=40, width=80, height=80), 40
                    )
                media.controller.driver.save_dialog.assert_not_called()
                media.controller.driver.click.assert_called_once()
                media.controller.driver.right_click.assert_called_once()

    def test_workflow_step_limit_is_checked_before_input(self):
        media = self.make_media_capture()
        profile = media.controller.profile
        profile.media_workflows["save_media"] = profile.media_workflow(
            "save_media"
        ).model_copy(update={"max_steps": 2})
        with self.assertRaisesRegex(ValueError, "上限"):
            media.controller.run_media_workflow(
                self.first,
                Rect(x=10, y=40, width=80, height=80),
                self.path,
                media.reader,
                MagicMock(),
            )
        media.controller.capture.assert_not_called()
        media.controller.driver.click.assert_not_called()
