import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image
from pydantic import ValidationError

from wemo2.config import AppConfig
from wemo2_collector.controller import UiController
from wemo2_collector.feed import FeedCapture, overlap_shift
from wemo2_collector.locating import AirtestLocator
from wemo2_collector.media import MediaCapture
from wemo2_collector.models import (
    ActionResult,
    ControllerConfig,
    FeedCaptureConfig,
    LocatedTarget,
    MatchConfig,
    Platform,
    Rect,
    Screenshot,
    Surface,
    ValidationResult,
)
from wemo2_collector.profiles import MediaTargetSpec, build_profile
from wemo2_collector.recognition import TextLine, TextRecognizer, file_kind, media_tiles
from wemo2_collector.windows.driver import WindowsDriver


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
                max_screens=2, recognize_nickname=False, save_media=False, **options
            ),
            self.path,
        )

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

    def test_missing_anchor_does_not_scroll_and_preserves_partial_image(self):
        self.controller.locator.locate_all.side_effect = None
        self.controller.locator.locate_all.return_value = []
        result = self.scanner().run(self.first)
        self.assertEqual(result.stop_reason, "anchor_missing")
        self.controller.scroll.assert_not_called()
        self.assertTrue(result.segments[0].partial_start)
        self.assertTrue(result.segments[0].partial_end)

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
        driver = WindowsDriver()
        driver.device = MagicMock()
        target = self.anchor(self.first, 70)
        with patch.object(driver, "verify_capture"):
            driver.scroll(self.first, target, 1)
        driver.device.mouse.scroll.assert_called_once_with(
            coords=(180, 102), wheel_dist=-1
        )
        driver.device.swipe.assert_not_called()

    def test_controller_scroll_preserves_once_only_input_and_action_evidence(self):
        driver = MagicMock()
        screenshotter = MagicMock()
        driver.prepare_capture.return_value = self.surface
        screenshotter.capture.side_effect = [self.first, self.second]
        controller = UiController(
            driver, screenshotter, MagicMock(), build_profile(), directory=self.path
        )
        with patch("wemo2_collector.controller.time.sleep"):
            action = controller.scroll(Rect(x=0, y=0, width=200, height=300), 1)
        self.assertEqual(action.status, "verified")
        self.assertEqual(action.wheel_steps, 1)
        driver.scroll.assert_called_once()
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

    def test_media_input_failure_saves_card_metadata_before_propagating(self):
        media = MediaCapture(self.controller, FeedCaptureConfig(), self.path)
        media.reader = MagicMock()
        media.reader.nickname.return_value = TextLine(
            text="测试昵称", score=0.9, bounds=Rect(x=10, y=5, width=60, height=20)
        )
        with (
            patch(
                "wemo2_collector.media.media_tiles",
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

    def make_media_capture(self):
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
            state["page"] = "viewer" if state["page"] == "original" else "dialog"

        def right_click(frame, target):
            state["page"] = "menu"

        def save(directory):
            state["page"] = "viewer"
            return self.second.image_path

        def close(original):
            state["page"] = "original"

        driver.click.side_effect = click
        driver.right_click.side_effect = right_click
        driver.save_dialog.side_effect = save
        driver.close_viewer.side_effect = close
        driver.is_save_dialog.side_effect = lambda: state["page"] == "dialog"
        media = MediaCapture(controller, FeedCaptureConfig(), self.path)
        media.reader = MagicMock()
        media.reader.read.return_value = [
            TextLine(
                text="另存为", score=0.99, bounds=Rect(x=10, y=20, width=70, height=20)
            )
        ]
        sleep = patch("wemo2_collector.controller.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        return media

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
                "open_menu",
                "choose_save_as",
                "write_file",
                "return_to_feed",
            ],
        )
        self.assertTrue(
            all(result.status == "verified" for result in media.controller.results)
        )

    def test_changed_media_content_cannot_be_clicked(self):
        media = MediaCapture(self.controller, FeedCaptureConfig(), self.path)
        self.controller.capture.return_value = self.second
        with self.assertRaisesRegex(RuntimeError, "媒体内容已变化"):
            media._save_media(self.first, Rect(x=10, y=40, width=80, height=80), 40)
        self.controller.click_region.assert_not_called()

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
        driver = WindowsDriver()
        driver.device = MagicMock()
        dialog = MagicMock()
        dialog.class_name.return_value = "#32770"
        dialog.window_text.return_value = "另存为"
        dialog.descendants.return_value = [MagicMock(), MagicMock()]
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
        driver = WindowsDriver()
        driver.device = MagicMock()
        driver.device.handle = 123
        driver.user32 = MagicMock()
        driver.user32.GetForegroundWindow.return_value = 456

        def process_id(handle, pointer):
            pointer._obj.value = 42
            return 1

        driver.user32.GetWindowThreadProcessId.side_effect = process_id
        dialog = MagicMock()
        dialog.class_name.return_value = "#32770"
        dialog.window_text.return_value = "另存为"
        edit = MagicMock()
        dialog.descendants.return_value = [edit]

        def save(key):
            destination = Path(edit.set_edit_text.call_args.args[0]).with_suffix(".png")
            destination.write_bytes(self.first.image_path.read_bytes())

        driver.device.keyevent.side_effect = save
        old_device = driver.device
        with (
            patch.object(driver, "verify_capture"),
            patch.object(driver, "current_surface"),
            patch("pywinauto.Desktop") as desktop,
            patch("wemo2_collector.windows.backend.create_device") as create,
            patch("wemo2_collector.windows.driver.time.sleep"),
        ):
            desktop.return_value.window.return_value.wrapper_object.return_value = (
                dialog
            )
            path = driver.save_dialog(self.path / "media")
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, self.path / "media")
        self.assertTrue(path.name.startswith("asset_"))
        old_device.keyevent.assert_called_once_with("{ENTER}")
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
        driver = WindowsDriver()
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
            patch("wemo2_collector.windows.backend.create_device") as create,
        ):
            driver.follow_foreground()
        create.assert_called_once_with(handle=456, foreground=False)

    def test_media_targets_and_workflow_can_change_without_executor_branches(self):
        media = self.make_media_capture()
        profile = media.controller.profile
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
