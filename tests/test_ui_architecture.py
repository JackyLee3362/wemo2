import ast
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
from pydantic import ValidationError

from wemo2.composition import build_coordinator
from wemo2.config import AppConfig, load_config
from wemo2.pseudo import PseudoCollector
from wemo2.registry import collectors
from wemo2_collector import validation
from wemo2_collector.controller import UiController
from wemo2_collector.locating import AirtestLocator
from wemo2_collector.models import (
    ActionResult,
    ControllerConfig,
    MatchConfig,
    Platform,
    Rect,
    RouteConfig,
    Screenshot,
    Surface,
)
from wemo2_collector.profiles import PageSpec, RouteSpec, UiProfile, build_profile
from wemo2_collector.routing import PageRouter


class UiArchitectureTests(unittest.TestCase):
    """验证页面与主题扩展、配置边界、路由规划和独立组件的职责契约。"""

    def setUp(self):
        self.profile = build_profile()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        with Image.new("RGB", (100, 100), "white") as image:
            image.save(self.path / "frame.png")
        self.frame = Screenshot(
            frame_id="home",
            captured_at=datetime.now(UTC),
            image_path=self.path / "frame.png",
            surface=Surface(
                platform=Platform.windows,
                surface_id="123",
                bounds=Rect(x=0, y=0, width=100, height=100),
                scale=1.25,
            ),
        )

    def test_profile_rejects_wrong_names_missing_endpoints_and_unverifiable_routes(
        self,
    ):
        with self.assertRaises(ValidationError):
            UiProfile(pages={"home": PageSpec(name="other")})
        with self.assertRaises(ValidationError):
            UiProfile(pages={"unknown": PageSpec(name="unknown")})
        for destination, message in (
            ("missing", "未定义页面"),
            ("moments", "页面标志"),
        ):
            profile = self.profile.model_copy(deep=True)
            profile.routes["wrong"] = RouteSpec(
                name="wrong",
                source="home",
                destination=destination,
                target="moments_entry",
            )
            with self.assertRaisesRegex(ValueError, message):
                profile.validate_structure()

    def test_registered_new_page_works_without_router_changes(self):
        self.profile.pages["custom"] = PageSpec(
            name="custom", required=["custom_marker"]
        )
        self.profile.routes["custom_action"] = RouteSpec(
            name="custom_action",
            source="discover",
            destination="custom",
            target="custom_button",
        )
        discover = self.frame.model_copy(update={"frame_id": "discover"})
        custom = self.frame.model_copy(update={"frame_id": "custom"})
        interaction = MagicMock()
        interaction.capture.return_value = self.frame
        interaction.detect_page.return_value = "home"
        interaction.click.side_effect = [
            ActionResult(
                operation="click",
                name="moments_entry",
                status="verified",
                attempted=True,
                executed=True,
                before=self.frame,
                after=discover,
            ),
            ActionResult(
                operation="click",
                name="custom_button",
                status="verified",
                attempted=True,
                executed=True,
                before=discover,
                after=custom,
            ),
        ]
        router = PageRouter(interaction, self.profile, RouteConfig())
        with patch("wemo2_collector.controller.time.sleep"):
            self.assertEqual(router.run("custom_action"), custom)
        self.assertEqual(interaction.click.call_args_list[0].args[0], "moments_entry")
        self.assertEqual(interaction.click.call_args_list[1].args[0], "custom_button")

    def test_multiple_page_matches_are_unknown_and_do_not_click(self):
        self.profile.pages["conflict"] = PageSpec(
            name="conflict", required=["moments_entry"]
        )
        locator = MagicMock()
        locator.locate.side_effect = lambda frame, name: (
            MagicMock() if name == "moments_entry" else None
        )
        self.assertEqual(
            validation.detect_page(self.frame, self.profile, locator), "unknown"
        )
        controller = MagicMock()
        controller.capture.return_value = self.frame
        controller.detect_page.return_value = "unknown"
        router = PageRouter(controller, self.profile, RouteConfig())
        with self.assertRaisesRegex(RuntimeError, "当前页面未可靠识别"):
            router.run("discover")
        controller.click.assert_not_called()

    def test_themes_select_templates_without_locator_branches(self):
        # 使用原图测试主题数据选择，不表示已经提供浅色皮肤模板。
        alternate = dict(self.profile.themes["dark"])
        alternate["moments_entry"] = alternate["moments_entry"].model_copy(
            update={"region_ratio": 1.0}
        )
        self.profile.themes["test-skin"] = alternate
        self.profile.validate_theme("test-skin")
        template_path = self.profile.target("test-skin", "moments_entry").path
        with (
            Image.open(template_path) as template,
            Image.new("RGB", (100, 100), (40, 40, 40)) as image,
        ):
            image.paste(template, (3, 20))
            image.save(self.frame.image_path)
        target = AirtestLocator(self.profile, "test-skin", MatchConfig()).locate(
            self.frame, "moments_entry"
        )
        self.assertEqual(target.bounds.x, 3)
        self.assertEqual(target.bounds.y, 20)
        with self.assertRaises(ValueError):
            self.profile.target("test-skin", "missing")

    def test_theme_must_cover_page_and_route_targets(self):
        self.profile.themes["incomplete"] = {}
        with self.assertRaisesRegex(ValueError, "未定义目标"):
            self.profile.validate_theme("incomplete")

    def test_interactor_refuses_missing_target_and_preserves_capture_evidence(self):
        driver = MagicMock()
        screenshotter = MagicMock()
        screenshotter.capture.return_value = self.frame
        locator = MagicMock()
        locator.locate.return_value = None
        interaction = UiController(
            driver,
            screenshotter,
            locator,
            self.profile,
            ControllerConfig(capture_attempts=1, locate_attempts=1),
        )
        interaction.capture(follow_foreground=True)
        driver.follow_foreground.assert_called_once()
        driver.verify_capture.assert_called_once_with(
            driver.prepare_capture.return_value
        )
        result = interaction.click("missing")
        self.assertEqual(result.status, "failed")
        driver.click.assert_not_called()
        self.assertEqual(
            interaction.artifacts(), [self.frame.image_path, self.frame.image_path]
        )
        interaction.reset()
        self.assertEqual(interaction.artifacts(), [])

    def test_unverified_intermediate_page_is_rejected_before_any_click(self):
        self.profile.routes["load_feed"] = RouteSpec(
            name="load_feed",
            source="moments_selected",
            destination="moments",
            target="load_feed",
            verified=False,
        )
        self.profile.pages["later"] = PageSpec(name="later", required=["later_marker"])
        self.profile.routes["later_action"] = RouteSpec(
            name="later_action",
            source="moments",
            destination="later",
            target="next",
        )
        interaction = MagicMock()
        interaction.capture.return_value = self.frame
        interaction.detect_page.return_value = "home"
        router = PageRouter(interaction, self.profile, RouteConfig())
        with self.assertRaisesRegex(ValueError, "未验证页面"):
            router.run("later_action")
        interaction.click.assert_not_called()

    def test_core_config_has_no_ui_fields_or_ui_model_imports(self):
        for name in (
            "platform",
            "json_path",
            "screenshot_dir",
            "window_title",
            "window_process_id",
            "route",
            "theme",
        ):
            self.assertNotIn(name, AppConfig.model_fields)
        root = Path(__file__).parents[1] / "src/wemo2"
        for path in root.glob("*.py"):
            if path.name == "registry.py":  # 唯一允许绑定插件实现的组装登记处。
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(
                        (node.module or "").startswith("wemo2_collector"), path.name
                    )

    def test_ui_paths_are_resolved_by_collector_from_file_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "settings"
            folder.mkdir()
            path = folder / "config.toml"
            path.write_text(
                'collector = "windows_screenshot"\n[collector_options]\nscreenshot_dir = "captures"\n',
                encoding="utf-8",
            )
            config = load_config(config_path=path)
            source = collectors.create(config.collector, config.model_dump())
            self.assertEqual(source.config.screenshot_dir, folder / "captures")
            self.assertNotIn("screenshot_dir", config.model_dump())

    def test_unknown_ui_theme_route_and_option_fail_before_execution(self):
        for options in (
            {"theme": "light"},
            {"route": {"target": "missing"}},
            {"unknown": True},
        ):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                AppConfig(collector="windows_screenshot", collector_options=options)

    def test_new_collector_options_do_not_require_app_fields(self):
        received = []

        class CustomCollector(PseudoCollector):
            """接收任意专属参数，验证新增采集器不需要修改应用配置模型。"""

            def __init__(self, custom):
                received.append(custom)
                super().__init__()

        def prepare(options, directory):
            return {"custom": options["custom"]}

        with patch.dict(collectors._components):
            collectors.register(
                "custom-options", CustomCollector, prepare_options=prepare
            )
            config = AppConfig(
                collector="custom-options",
                collector_options={"custom": {"arbitrary": 42}},
            )
            coordinator = build_coordinator(
                config.collector, collector_options=config.collector_options
            )
            with coordinator.store:
                self.assertEqual(coordinator.run().created, 2)
            self.assertEqual(received, [{"arbitrary": 42}])

    def test_profile_paths_handle_cycles_and_missing_destinations(self):
        profile = UiProfile(
            pages={
                "a": PageSpec(name="a", required=["a"]),
                "b": PageSpec(name="b", required=["b"]),
                "c": PageSpec(name="c", required=["c"]),
            },
            routes={
                "ab": RouteSpec(name="ab", source="a", destination="b", target="ab"),
                "ba": RouteSpec(name="ba", source="b", destination="a", target="ba"),
            },
        )
        with self.assertRaisesRegex(ValueError, "没有从"):
            profile.path("a", "c")

    def test_timezone_config_reaches_screenshotter_and_defaults_to_shanghai(self):
        for options, expected in (({}, "Asia/Shanghai"), ({"timezone": "UTC"}, "UTC")):
            config = AppConfig(
                collector="windows_screenshot", collector_options=options
            )
            source = collectors.create(config.collector, config.model_dump())
            self.assertEqual(source.screenshotter.timezone.key, expected)
            self.assertEqual(source.config.timezone, expected)

    def test_invalid_timezone_is_rejected_before_connecting_device(self):
        for timezone in ("Asia/NotARealPlace", "/Asia/Shanghai", ""):
            with self.subTest(timezone=timezone):
                with patch(
                    "wemo2_collector.windows.driver.WindowsDriver.attach"
                ) as attach:
                    with self.assertRaises(ValidationError):
                        AppConfig(
                            collector="windows_screenshot",
                            collector_options={"timezone": timezone},
                        )
                attach.assert_not_called()
