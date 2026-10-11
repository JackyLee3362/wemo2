import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
from pydantic import ValidationError

from wemo2_collector.controller import UiController
from wemo2_collector.models import (
    ActionResult,
    ControllerConfig,
    Expectation,
    LocatedTarget,
    Platform,
    Rect,
    Screenshot,
    Surface,
)
from wemo2_collector.pages import PageRegistry, PageSpec
from wemo2_collector.validation import UiValidator
from wemo2_collector.windows.driver import WindowsDriver


class UiControllerTests(unittest.TestCase):
    """验证输入只执行一次、只读重试、预期校验、失败证据与滑动坐标约束。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.surface = Surface(
            platform=Platform.windows,
            surface_id="123",
            bounds=Rect(x=7, y=10, width=100, height=100),
            scale=1.25,
        )
        self.before = self.make_frame("before")
        self.after = self.make_frame("after")
        self.pages = PageRegistry()
        self.pages.register_page(PageSpec(name="home", required=["home_mark"]))
        self.pages.register_page(PageSpec(name="done", required=["done_mark"]))
        self.pages.register_page(PageSpec(name="unverifiable"))
        self.driver = MagicMock()
        self.driver.prepare_capture.return_value = self.surface
        self.capture = MagicMock()
        self.capture.capture.side_effect = [self.before, self.after]
        self.locator = MagicMock()
        self.locator.locate.side_effect = self.locate
        self.config = ControllerConfig()
        self.validator = UiValidator(self.pages, self.locator, self.config)
        self.controller = UiController(
            self.driver,
            self.capture,
            self.locator,
            self.validator,
            self.config,
            self.path / "actions",
        )
        sleep = patch("wemo2_collector.controller.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def make_frame(self, name):
        path = self.path / f"{name}.png"
        with Image.new("RGB", (100, 100), "white") as image:
            image.save(path)
        return Screenshot(
            frame_id=name,
            captured_at=datetime.now(UTC),
            surface=self.surface,
            image_path=path,
        )

    def target(self, frame, name):
        return LocatedTarget(
            frame_id=frame.frame_id,
            name=name,
            bounds=Rect(x=10, y=10, width=60, height=60),
            score=1.0,
            method="test",
        )

    def locate(self, frame, name):
        if name == "button":
            return self.target(frame, name)
        if name == "home_mark" and frame.frame_id in ("before", "retry"):
            return self.target(frame, name)
        if name == "done_mark" and frame.frame_id == "after":
            return self.target(frame, name)
        return None

    def saved(self, result):
        return ActionResult.model_validate_json(
            (self.path / "actions" / f"action_{result.action_id}.json").read_text(
                encoding="utf-8"
            )
        )

    def test_click_has_fresh_frames_verifies_and_persists_execution_evidence(self):
        result = self.controller.click(
            "button", Expectation(kind="page", page="done"), source_page="home"
        )
        self.assertEqual(result.status, "verified")
        self.assertTrue(result.attempted)
        self.assertTrue(result.executed)
        self.assertEqual(result.before, self.before)
        self.assertEqual(result.after, self.after)
        self.driver.click.assert_called_once_with(self.before, result.target)
        self.assertEqual(self.saved(result), result)
        self.assertEqual(len(self.controller.artifacts()), 3)

    def test_click_without_expectation_only_reports_executed(self):
        result = self.controller.click("button")
        self.assertEqual(result.status, "executed")
        self.assertIsNone(result.validation)
        self.driver.click.assert_called_once()

    def test_page_without_markers_is_pending_not_verified(self):
        result = self.controller.click(
            "button", Expectation(kind="page", page="unverifiable")
        )
        self.assertEqual(result.status, "pending")
        self.assertTrue(result.executed)
        self.assertEqual(result.validation.status, "unavailable")
        self.assertEqual(self.capture.capture.call_count, 2)

    def test_missing_target_retries_observation_without_clicking(self):
        self.capture.capture.side_effect = [self.before, self.make_frame("retry")]
        self.locator.locate.return_value = None
        self.locator.locate.side_effect = None
        result = self.controller.click("missing")
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.attempted)
        self.assertFalse(result.executed)
        self.assertEqual(self.capture.capture.call_count, 2)
        self.driver.click.assert_not_called()
        self.assertEqual(self.saved(result).status, "failed")

    def test_missing_target_then_found_uses_new_frame_for_single_click(self):
        retry = self.make_frame("retry")
        self.capture.capture.side_effect = [self.before, retry, self.after]

        def locate(frame, name):
            if name == "button" and frame.frame_id == "before":
                return None
            return self.locate(frame, name)

        self.locator.locate.side_effect = locate
        result = self.controller.click("button", Expectation(kind="page", page="done"))
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.before, retry)
        self.driver.click.assert_called_once_with(retry, result.target)

    def test_verification_polls_until_success_without_resending_input(self):
        loading = self.make_frame("loading")
        self.capture.capture.side_effect = [self.before, loading, self.after]
        result = self.controller.click("button", Expectation(kind="page", page="done"))
        self.assertEqual(result.status, "verified")
        self.assertEqual(self.capture.capture.call_count, 3)
        self.driver.click.assert_called_once()

    def test_verification_timeout_keeps_executed_flag_and_never_reclicks(self):
        loading = self.make_frame("loading")
        self.capture.capture.side_effect = [self.before, loading, loading, loading]
        with patch(
            "wemo2_collector.controller.time.monotonic", side_effect=[0, 0.1, 0.2, 6]
        ):
            result = self.controller.click(
                "button", Expectation(kind="page", page="done")
            )
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.executed)
        self.assertEqual(result.after, loading)
        self.assertIn("验证超时", result.error)
        self.driver.click.assert_called_once()
        self.assertTrue(self.saved(result).executed)

    def test_input_exception_is_not_retried_and_records_uncertain_attempt(self):
        self.driver.click.side_effect = OSError("input interrupted")
        result = self.controller.click("button")
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.attempted)
        self.assertFalse(result.executed)
        self.driver.click.assert_called_once()
        self.assertEqual(self.capture.capture.call_count, 1)

    def test_post_action_capture_failure_retains_completed_input(self):
        self.capture.capture.side_effect = [
            self.before,
            OSError("capture failed"),
            OSError("capture failed"),
        ]
        result = self.controller.click("button")
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.executed)
        self.assertIsNone(result.after)
        self.driver.click.assert_called_once()
        self.assertEqual(self.saved(result).before, self.before)

    def test_capture_retry_is_bounded_and_preserves_failed_frame_paths(self):
        self.driver.verify_capture.side_effect = [RuntimeError("focus changed"), None]
        frame = self.controller.capture()
        self.assertEqual(frame, self.after)
        self.assertEqual(self.capture.capture.call_count, 2)
        self.driver.click.assert_not_called()
        self.assertEqual(
            self.controller.artifacts(), [self.before.image_path, self.after.image_path]
        )

    def test_stale_frame_wrong_frame_target_and_outside_target_are_rejected(self):
        cases = [
            (
                self.before.model_copy(
                    update={"captured_at": datetime.now(UTC) - timedelta(seconds=10)}
                ),
                self.target(self.before, "button"),
            ),
            (
                self.before,
                self.target(self.before, "button").model_copy(
                    update={"frame_id": "other"}
                ),
            ),
            (
                self.before,
                self.target(self.before, "button").model_copy(
                    update={"bounds": Rect(x=99, y=0, width=20, height=20)}
                ),
            ),
        ]
        for frame, target in cases:
            with self.subTest(frame=frame.frame_id, bounds=target.bounds):
                self.capture.capture.side_effect = None
                self.capture.capture.return_value = frame
                self.locator.locate.side_effect = None
                self.locator.locate.return_value = target
                result = self.controller.click("button")
                self.assertEqual(result.status, "failed")
                self.assertFalse(result.attempted)
        self.driver.click.assert_not_called()

    def test_changed_source_page_is_rejected_before_click(self):
        self.capture.capture.side_effect = [self.after]
        result = self.controller.click("button", source_page="home")
        self.assertEqual(result.status, "failed")
        self.assertIn("动作前页面", result.error)
        self.driver.click.assert_not_called()

    def test_invalid_expectation_region_and_timeout_do_not_execute(self):
        expectations = [
            Expectation(kind="page", page="missing"),
            Expectation(kind="change", region=Rect(x=99, y=0, width=10, height=10)),
        ]
        for expectation in expectations:
            self.capture.capture.side_effect = None
            self.capture.capture.return_value = self.before
            result = self.controller.click("button", expectation)
            self.assertEqual(result.status, "failed")
            self.assertFalse(result.attempted)
        for timeout in (-1, float("nan"), float("inf")):
            result = self.controller.click("button", timeout=timeout)
            self.assertEqual(result.status, "failed")
            self.assertFalse(result.attempted)
        self.driver.click.assert_not_called()
        with self.assertRaises(ValidationError):
            Expectation(kind="page")

    def test_swipe_checks_content_region_progress_and_runs_once(self):
        with Image.open(self.after.image_path) as image:
            image.paste((0, 0, 0), (20, 20, 40, 40))
            image.save(self.after.image_path)
        expectation = Expectation(
            kind="change", region=Rect(x=10, y=10, width=60, height=60)
        )
        result = self.controller.swipe("button", "up", 20, expectation)
        self.assertEqual(result.status, "verified")
        self.assertGreater(result.validation.change_ratio, 0.01)
        self.driver.swipe.assert_called_once_with(self.before, result.target, "up", 20)
        self.driver.click.assert_not_called()

    def test_swipe_no_progress_times_out_without_reswiping(self):
        self.capture.capture.side_effect = [self.before, self.after]
        with patch("wemo2_collector.controller.time.monotonic", side_effect=[0, 6]):
            result = self.controller.swipe(
                "button", "up", 20, Expectation(kind="change")
            )
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.executed)
        self.driver.swipe.assert_called_once()

    def test_region_validation_ignores_changes_elsewhere_and_rejects_window_changes(
        self,
    ):
        with Image.open(self.after.image_path) as image:
            image.paste((0, 0, 0), (80, 80, 100, 100))
            image.save(self.after.image_path)
        expectation = Expectation(
            kind="change", region=Rect(x=0, y=0, width=20, height=20)
        )
        result = self.validator.evaluate(self.before, self.after, expectation)
        self.assertEqual(result.status, "not_met")
        changed_surface = self.surface.model_copy(update={"surface_id": "456"})
        with self.assertRaisesRegex(RuntimeError, "窗口或区域已变化"):
            self.validator.evaluate(
                self.before,
                self.after.model_copy(update={"surface": changed_surface}),
                expectation,
            )

    def test_record_write_failure_does_not_hide_executed_input(self):
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = self.controller.click("button")
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.executed)
        self.assertIn("无法保存动作证据", result.error)
        self.driver.click.assert_called_once()

    def test_follow_foreground_is_only_requested_after_action(self):
        result = self.controller.click("button", follow_foreground=True)
        self.assertEqual(result.status, "executed")
        self.driver.follow_foreground.assert_called_once()

    def test_windows_swipe_converts_client_coordinates_and_refuses_outside_endpoints(
        self,
    ):
        driver = WindowsDriver()
        driver.device = MagicMock()
        driver.device.get_pos.return_value = (5, 8)
        target = self.target(self.before, "button")
        with patch.object(driver, "verify_capture"):
            driver.swipe(self.before, target, "up", 20)
        driver.device.swipe.assert_called_once_with((42, 42), (42, 22), duration=0.5)
        driver.device.swipe.reset_mock()
        with patch.object(driver, "verify_capture"):
            for direction, distance in (("up", 80), ("invalid", 10), ("down", 0)):
                with (
                    self.subTest(direction=direction, distance=distance),
                    self.assertRaises((ValueError, RuntimeError)),
                ):
                    driver.swipe(self.before, target, direction, distance)
        driver.device.swipe.assert_not_called()
