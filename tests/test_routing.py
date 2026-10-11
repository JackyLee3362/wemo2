import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from wemo2_collector.controller import UiController
from wemo2_collector.locating import RegistryLocator
from wemo2_collector.matching import TemplateMatcher
from wemo2_collector.models import (
    ControllerConfig,
    LocatedTarget,
    MatchConfig,
    Platform,
    Rect,
    RouteConfig,
    Screenshot,
    Surface,
    ValidationResult,
)
from wemo2_collector.profiles import build_profiles
from wemo2_collector.routing import PageRouter
from wemo2_collector.validation import UiValidator
from wemo2_collector.windows.driver import WindowsDriver


class RoutingTests(unittest.TestCase):
    """验证静态模板定位、页面判断、导航步骤和校验后的平台坐标转换。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.config = RouteConfig(target="discover")
        self.pages, self.themes = build_profiles()
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
        with Image.new("RGB", (1100, 800), "white") as image:
            image.save(self.frame.image_path)

    def make_image(self, positions):
        with Image.open(self.themes.target("dark", "moments_entry").path) as template:
            image = Image.new("RGB", (1100, 800), (40, 40, 40))
            for position in positions:
                image.paste(template, position)
            image.save(self.frame.image_path)
            image.close()

    def test_template_finds_icon_and_rejects_missing_or_multiple_targets(self):
        locator = RegistryLocator(self.themes, "dark", TemplateMatcher(MatchConfig()))
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
            RegistryLocator(self.themes, "dark", TemplateMatcher(MatchConfig())).locate(
                self.frame, "moments_entry"
            )
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
        driver.device = MagicMock()
        driver.device.get_pos.return_value = (5, 8)
        with (
            patch.object(driver, "verify_capture") as verify,
        ):
            driver.click(self.frame, target)
        verify.assert_called_once_with(self.surface)
        driver.device.touch.assert_called_once_with((40, 322), duration=0.1)

    def test_click_rejects_old_frame_or_wrong_frame_id(self):
        driver = WindowsDriver()
        target = LocatedTarget(
            frame_id="other",
            name="moments_entry",
            bounds=Rect(x=24, y=306, width=28, height=28),
            score=1,
            method="template",
        )
        driver.device = MagicMock()
        driver.device.get_pos.return_value = (5, 8)
        with patch.object(driver, "verify_capture") as verify:
            with self.assertRaisesRegex(RuntimeError, "不属于"):
                driver.click(self.frame, target)
            target = target.model_copy(update={"frame_id": "frame"})
            old = self.frame.model_copy(
                update={"captured_at": datetime.now(UTC) - timedelta(seconds=10)}
            )
            with self.assertRaisesRegex(RuntimeError, "过期"):
                driver.click(old, target)
        driver.device.touch.assert_not_called()
        verify.assert_not_called()

    def make_router(self, already_open=False, found=True):
        driver = MagicMock()
        driver.current_surface.return_value = self.surface
        driver.prepare_capture.return_value = self.surface
        screenshotter = MagicMock()
        screenshotter.capture.return_value = self.frame
        locator = MagicMock()

        def locate(frame, name):
            discover_visible = already_open or driver.click.call_count > 0
            if name == "moments_entry" and (not found or discover_visible):
                return None
            if name != "moments_entry" and not discover_visible:
                return None
            return LocatedTarget(
                frame_id="frame",
                name=name,
                bounds=Rect(x=24, y=306, width=28, height=28),
                score=1,
                method="template",
            )

        locator.locate.side_effect = locate
        policy = ControllerConfig(capture_attempts=1, locate_attempts=1)
        controller = UiController(
            driver,
            screenshotter,
            locator,
            UiValidator(self.pages, locator, policy),
            policy,
        )
        return PageRouter(controller, self.pages, self.config)

    def test_route_clicks_once_and_verifies_page_screenshot(self):
        router = self.make_router()
        self.assertEqual(router.ensure("discover"), self.frame)
        router.controller.driver.click.assert_called_once()
        self.assertEqual(len(router.controller.artifacts()), 3)

    def test_already_open_route_does_not_click_again(self):
        router = self.make_router(already_open=True)
        self.assertEqual(router.ensure("discover"), self.frame)
        router.controller.driver.click.assert_not_called()

    def test_missing_icon_does_not_click(self):
        router = self.make_router(found=False)
        with self.assertRaisesRegex(RuntimeError, "未可靠识别"):
            router.ensure("discover")
        router.controller.driver.click.assert_not_called()

    def test_route_timeout_does_not_repeat_click(self):
        router = self.make_router()
        with (
            patch("wemo2_collector.controller.time.monotonic", side_effect=[0, 6]),
            patch.object(
                router.controller.validator,
                "evaluate",
                return_value=ValidationResult(status="not_met", message="目标页未出现"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                router.ensure("discover")
        router.controller.driver.click.assert_called_once()

    def test_detect_requires_both_selected_icon_and_discovery_menu(self):
        driver = MagicMock()
        driver.current_surface.return_value = self.surface
        locator = RegistryLocator(self.themes, "dark", TemplateMatcher(MatchConfig()))
        router = PageRouter(
            UiController(
                driver,
                MagicMock(),
                locator,
                UiValidator(self.pages, locator, ControllerConfig()),
            ),
            self.pages,
            self.config,
        )
        with (
            Image.open(
                self.themes.target("dark", "discover_selected").path
            ) as selected,
            Image.open(self.themes.target("dark", "discover_menu").path) as menu,
        ):
            image = Image.new("RGB", (1100, 800), (40, 40, 40))
            image.paste(selected, (24, 306))
            image.save(self.frame.image_path)
            self.assertEqual(router.detect(self.frame), "unknown")
            image.paste(menu, (88, 52))
            image.save(self.frame.image_path)
            self.assertEqual(router.detect(self.frame), "discover")
            self.assertNotEqual(router.detect(self.frame), "moments")
            image.close()
        self.make_image([(24, 306)])
        self.assertEqual(router.detect(self.frame), "home")

    def test_route_does_not_claim_feed_from_discovery_reference(self):
        router = self.make_router()
        with self.assertRaisesRegex(ValueError, "动态列表"):
            router.ensure("moments")
        router.controller.driver.click.assert_not_called()

    def test_moments_menu_locator_finds_row_and_rejects_multiple_rows(self):
        locator = RegistryLocator(self.themes, "dark", TemplateMatcher(MatchConfig()))
        with Image.open(self.themes.target("dark", "moments_menu").path) as template:
            image = Image.new("RGB", (1100, 800), (40, 40, 40))
            image.paste(template, (94, 116))
            image.save(self.frame.image_path)
            target = locator.locate(self.frame, "moments_menu")
            self.assertIsNotNone(target)
            self.assertEqual(target.bounds, Rect(x=94, y=116, width=93, height=29))
            image.paste(template, (94, 400))
            image.save(self.frame.image_path)
            self.assertIsNone(locator.locate(self.frame, "moments_menu"))
            image.close()

    def test_menu_action_clicks_once_then_saves_unverified_result(self):
        router = self.make_router(already_open=True)
        with patch("wemo2_collector.controller.time.sleep"):
            self.assertEqual(router.run("moments_entry"), self.frame)
        router.controller.driver.click.assert_called_once()
        clicked = router.controller.driver.click.call_args.args[1]
        self.assertEqual(clicked.name, "moments_menu")
        router.controller.driver.follow_foreground.assert_called_once()
        self.assertEqual(len(router.controller.artifacts()), 3)
        # 当前 discover 截图不能被当作朋友圈动态列表。
        self.assertEqual(router.detect(self.frame), "discover")

    def test_missing_menu_action_does_not_click(self):
        router = self.make_router(already_open=True)
        original_locate = router.controller.locator.locate.side_effect

        def locate(frame, name):
            if name == "moments_menu":
                return None
            return original_locate(frame, name)

        router.controller.locator.locate.side_effect = locate
        with self.assertRaisesRegex(RuntimeError, "moments_menu 未可靠识别"):
            router.run("moments_entry")
        router.controller.driver.click.assert_not_called()

    def test_action_follows_both_navigation_steps_without_repeating_clicks(self):
        router = self.make_router()
        with patch("wemo2_collector.controller.time.sleep"):
            router.run("moments_entry")
        self.assertEqual(router.controller.driver.click.call_count, 2)
        self.assertEqual(
            router.controller.driver.click.call_args_list[0].args[1].name,
            "moments_entry",
        )
        self.assertEqual(
            router.controller.driver.click.call_args_list[1].args[1].name,
            "moments_menu",
        )
