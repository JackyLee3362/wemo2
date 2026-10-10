import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from wemo2_collector.locating import MomentsEntryLocator
from wemo2_collector.models import (
    LocatedTarget,
    Page,
    Platform,
    Rect,
    RouteConfig,
    Screenshot,
    Surface,
)
from wemo2_collector.routing import WindowsPageRouter
from wemo2_collector.windows.driver import WindowsDriver


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.config = RouteConfig(target="moments")
        self.surface = Surface(
            platform=Platform.windows,
            surface_id="123",
            bounds=Rect(x=7, y=10, width=1100, height=800),
            scale=1.25,
        )
        self.frame = Screenshot(
            frame_id="frame",
            captured_at=datetime.now(UTC),
            surface=self.surface,
            image_path=self.path / "image.png",
        )

    def make_image(self, positions):
        with Image.open(self.config.entry_template) as template:
            image = Image.new("RGB", (1100, 800), (40, 40, 40))
            for position in positions:
                image.paste(template, position)
            image.save(self.frame.image_path)
            image.close()

    def test_template_finds_icon_and_rejects_missing_or_multiple_targets(self):
        locator = MomentsEntryLocator(self.config)
        self.make_image([(24, 306)])
        target = locator.locate(self.frame, "moments_entry")
        self.assertIsNotNone(target)
        self.assertEqual(target.bounds, Rect(x=24, y=306, width=28, height=28))
        self.assertGreater(target.score, 0.99)
        self.make_image([])
        self.assertIsNone(locator.locate(self.frame, "moments_entry"))
        self.make_image([(24, 306), (24, 500)])
        self.assertIsNone(locator.locate(self.frame, "moments_entry"))

    def test_sidebar_matching_ignores_same_icon_in_content_area(self):
        self.make_image([(400, 306)])
        self.assertIsNone(
            MomentsEntryLocator(self.config).locate(self.frame, "moments_entry")
        )

    def test_click_converts_to_screen_coordinates_after_validation(self):
        target = LocatedTarget(
            frame_id="frame",
            name="moments_entry",
            bounds=Rect(x=24, y=306, width=28, height=28),
            score=1,
            method="template",
        )
        driver = WindowsDriver()
        uia = MagicMock()
        with (
            patch.dict(sys.modules, {"uiautomation": uia}),
            patch.object(driver, "verify_capture") as verify,
        ):
            driver.click(self.frame, target)
        verify.assert_called_once_with(self.surface)
        uia.Click.assert_called_once_with(45, 330, waitTime=0.1)

    def test_click_rejects_old_frame_or_wrong_frame_id(self):
        driver = WindowsDriver()
        target = LocatedTarget(
            frame_id="other",
            name="moments_entry",
            bounds=Rect(x=24, y=306, width=28, height=28),
            score=1,
            method="template",
        )
        uia = MagicMock()
        with patch.dict(sys.modules, {"uiautomation": uia}):
            with self.assertRaisesRegex(RuntimeError, "不属于"):
                driver.click(self.frame, target)
            target = target.model_copy(update={"frame_id": "frame"})
            old = self.frame.model_copy(
                update={"captured_at": datetime.now(UTC) - timedelta(seconds=10)}
            )
            with self.assertRaisesRegex(RuntimeError, "过期"):
                driver.click(old, target)
        uia.Click.assert_not_called()

    def make_router(self, already_open=False, found=True):
        driver = MagicMock()
        driver.window.Name = self.config.window_title
        driver.switch_to_window.side_effect = [already_open, True]
        driver.current_surface.return_value = self.surface
        driver.prepare_capture.return_value = self.surface
        screenshotter = MagicMock()
        screenshotter.capture.return_value = self.frame
        locator = MagicMock()
        if found:
            locator.locate.return_value = LocatedTarget(
                frame_id="frame",
                name="moments_entry",
                bounds=Rect(x=24, y=306, width=28, height=28),
                score=1,
                method="template",
            )
        else:
            locator.locate.return_value = None
        return WindowsPageRouter(driver, screenshotter, locator, self.config)

    def test_route_clicks_once_and_verifies_target_window(self):
        router = self.make_router()
        self.assertEqual(router.ensure(Page.moments), self.frame)
        router.driver.click.assert_called_once()
        self.assertEqual(len(router.artifacts), 2)

    def test_already_open_route_does_not_click_again(self):
        router = self.make_router(already_open=True)
        self.assertEqual(router.ensure(Page.moments), self.frame)
        router.driver.click.assert_not_called()

    def test_missing_icon_does_not_click(self):
        router = self.make_router(found=False)
        with self.assertRaisesRegex(RuntimeError, "未可靠识别"):
            router.ensure(Page.moments)
        router.driver.click.assert_not_called()

    def test_route_timeout_does_not_repeat_click(self):
        router = self.make_router()
        router.driver.switch_to_window.side_effect = None
        router.driver.switch_to_window.return_value = False
        with patch("wemo2_collector.routing.time.monotonic", side_effect=[0, 6]):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                router.ensure(Page.moments)
        router.driver.click.assert_called_once()
