import ast
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from wemo2.composition import build_coordinator
from wemo2.config import AppConfig, load_config
from wemo2.pseudo import PseudoCollector
from wemo2.registry import collectors
from wemo2_collector.controller import UiController
from wemo2_collector.locating import RegistryLocator
from wemo2_collector.models import (
    ActionResult,
    ControllerConfig,
    Platform,
    Rect,
    RouteConfig,
    Screenshot,
    Surface,
)
from wemo2_collector.pages import PageRegistry, PageSpec, RouteSpec
from wemo2_collector.profiles import build_profiles
from wemo2_collector.routing import PageRouter
from wemo2_collector.themes import ThemeRegistry, ThemeSpec
from wemo2_collector.validation import UiValidator


class UiArchitectureTests(unittest.TestCase):
    """验证页面与主题扩展、配置边界、路由规划和独立组件的职责契约。"""

    def setUp(self):
        self.pages, self.themes = build_profiles()
        self.frame = Screenshot(
            frame_id="home",
            captured_at=datetime.now(UTC),
            image_path=Path("unused.png"),
            surface=Surface(
                platform=Platform.windows,
                surface_id="123",
                bounds=Rect(x=0, y=0, width=100, height=100),
            ),
        )

    def test_registration_rejects_duplicate_and_unverifiable_pages(self):
        with self.assertRaises(ValueError):
            self.pages.register_page(PageSpec(name="home"))
        with self.assertRaises(ValueError):
            self.pages.register_route(
                RouteSpec(
                    name="discover", source="home", destination="discover", target="x"
                )
            )
        with self.assertRaises(ValueError):
            self.pages.register_route(
                RouteSpec(
                    name="wrong", source="home", destination="missing", target="x"
                )
            )
        with self.assertRaisesRegex(ValueError, "页面标志"):
            self.pages.register_route(
                RouteSpec(
                    name="wrong", source="discover", destination="moments", target="x"
                )
            )

    def test_registered_new_page_works_without_router_changes(self):
        self.pages.register_page(PageSpec(name="custom", required=["custom_marker"]))
        self.pages.register_route(
            RouteSpec(
                name="custom_action",
                source="discover",
                destination="custom",
                target="custom_button",
            )
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
        router = PageRouter(interaction, self.pages, RouteConfig())
        with patch("wemo2_collector.controller.time.sleep"):
            self.assertEqual(router.run("custom_action"), custom)
        self.assertEqual(interaction.click.call_args_list[0].args[0], "moments_entry")
        self.assertEqual(interaction.click.call_args_list[1].args[0], "custom_button")

    def test_multiple_page_matches_are_unknown_and_do_not_click(self):
        self.pages.register_page(PageSpec(name="conflict", required=["moments_entry"]))
        locator = MagicMock()
        locator.locate.side_effect = lambda frame, name: (
            MagicMock() if name == "moments_entry" else None
        )
        validator = UiValidator(self.pages, locator, ControllerConfig())
        self.assertEqual(validator.detect_page(self.frame), "unknown")
        controller = MagicMock()
        controller.capture.return_value = self.frame
        controller.detect_page.return_value = "unknown"
        router = PageRouter(controller, self.pages, RouteConfig())
        with self.assertRaisesRegex(RuntimeError, "当前页面未可靠识别"):
            router.run("discover")
        controller.click.assert_not_called()

    def test_themes_select_templates_without_locator_branches(self):
        # 测试注册机制；复用资源不表示已经制作浅色皮肤模板。
        alternate = self.themes.get("dark").model_copy(update={"name": "test-skin"})
        self.themes.register(alternate)
        self.themes.validate("test-skin", self.pages)
        matcher = MagicMock()
        locator = RegistryLocator(self.themes, "test-skin", matcher)
        locator.locate(self.frame, "moments_menu")
        matcher.match.assert_called_once_with(
            self.frame, "moments_menu", alternate.targets["moments_menu"]
        )
        with self.assertRaises(ValueError):
            self.themes.register(alternate)
        with self.assertRaises(ValueError):
            self.themes.target("test-skin", "missing")

    def test_theme_must_cover_page_and_route_targets(self):
        themes = ThemeRegistry()
        themes.register(ThemeSpec(name="incomplete", targets={}))
        with self.assertRaisesRegex(ValueError, "未定义目标"):
            themes.validate("incomplete", self.pages)

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
            MagicMock(),
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
        self.pages.register_page(PageSpec(name="later", required=["later_marker"]))
        self.pages.register_route(
            RouteSpec(
                name="later_action",
                source="moments",
                destination="later",
                target="next",
            )
        )
        interaction = MagicMock()
        interaction.capture.return_value = self.frame
        interaction.detect_page.return_value = "home"
        router = PageRouter(interaction, self.pages, RouteConfig())
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

    def test_registered_paths_handle_cycles_and_missing_destinations(self):
        pages = PageRegistry()
        pages.register_page(PageSpec(name="a", required=["a"]))
        pages.register_page(PageSpec(name="b", required=["b"]))
        pages.register_page(PageSpec(name="c", required=["c"]))
        pages.register_route(
            RouteSpec(name="ab", source="a", destination="b", target="ab")
        )
        pages.register_route(
            RouteSpec(name="ba", source="b", destination="a", target="ba")
        )
        with self.assertRaisesRegex(ValueError, "没有从"):
            pages.path("a", "c")
