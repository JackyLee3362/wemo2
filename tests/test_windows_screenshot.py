import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image
from typer.testing import CliRunner

from wemo2.cli import cli
from wemo2.composition import build_coordinator
from wemo2.config import AppConfig
from wemo2_collector.main import run_collector
from wemo2_collector.models import Platform, Rect, Screenshot, Surface
from wemo2_collector.windows.driver import WindowsDriver
from wemo2_collector.windows.screenshot import WindowsScreenshotter


class WindowsScreenshotTests(unittest.TestCase):
    def test_near_black_frame_with_one_white_pixel_is_rejected(self):
        image = Image.new("RGB", (80, 60), "black")
        image.putpixel((0, 0), (255, 255, 255))
        with (
            patch("wemo2_collector.windows.screenshot.sys.platform", "win32"),
            patch(
                "wemo2_collector.windows.screenshot.ImageGrab.grab", return_value=image
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "几乎全黑"):
                WindowsScreenshotter(self.path).capture(self.surface)
        self.assertEqual(list(self.path.glob("*.png")), [])

    def test_prepare_capture_activates_target_and_accepts_negative_monitor_coordinates(
        self,
    ):
        driver = WindowsDriver()
        driver.window = MagicMock()
        driver.user32 = MagicMock()
        driver.user32.GetForegroundWindow.return_value = 123
        metrics = {76: -200, 77: 0, 78: 1200, 79: 800}
        driver.user32.GetSystemMetrics.side_effect = metrics.__getitem__
        with (
            patch.object(driver, "current_surface", return_value=self.surface),
            patch("wemo2_collector.windows.driver.time.sleep"),
        ):
            self.assertEqual(driver.prepare_capture(), self.surface)
        driver.window.SetActive.assert_called_once()

    def test_prepare_capture_rejects_window_outside_virtual_screen(self):
        driver = WindowsDriver()
        driver.window = MagicMock()
        driver.user32 = MagicMock()
        driver.user32.GetForegroundWindow.return_value = 123
        metrics = {76: 0, 77: 0, 78: 1000, 79: 800}
        driver.user32.GetSystemMetrics.side_effect = metrics.__getitem__
        with (
            patch.object(driver, "current_surface", return_value=self.surface),
            patch("wemo2_collector.windows.driver.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "超出屏幕"):
                driver.prepare_capture()

    def test_capture_is_rejected_if_foreground_focus_changes(self):
        driver = WindowsDriver()
        driver.user32 = MagicMock()
        driver.user32.GetForegroundWindow.return_value = 456
        with patch.object(driver, "current_surface", return_value=self.surface):
            with self.assertRaisesRegex(RuntimeError, "失去前台焦点"):
                driver.verify_capture(self.surface)

    def test_surface_uses_client_size_and_screen_origin_instead_of_window_frame(self):
        user32 = MagicMock()
        user32.IsWindow.return_value = True
        user32.IsWindowVisible.return_value = True
        user32.IsIconic.return_value = False
        user32.GetDpiForWindow.return_value = 144

        def get_client_rect(handle, pointer):
            rect = pointer._obj
            rect.left = 0
            rect.top = 0
            rect.right = 80
            rect.bottom = 60
            return True

        def client_to_screen(handle, pointer):
            point = pointer._obj
            point.x = -92
            point.y = 81
            return True

        user32.GetClientRect.side_effect = get_client_rect
        user32.ClientToScreen.side_effect = client_to_screen
        driver = WindowsDriver()
        driver.user32 = user32
        driver.window = SimpleNamespace(NativeWindowHandle=123)
        surface = driver.current_surface()
        self.assertEqual(surface.bounds, Rect(x=-92, y=81, width=80, height=60))
        self.assertEqual(surface.scale, 1.5)
        user32.GetWindowRect.assert_not_called()

    def test_debug_entry_only_runs_collector_and_releases_it(self):
        collector = MagicMock()
        collector.collect.return_value = []
        collector.artifacts.return_value = [Path("data/screenshot/example.png")]
        config = AppConfig(collector="windows_screenshot")
        with (
            patch("wemo2_collector.main.collectors.create", return_value=collector),
            patch("wemo2.registry.stores.create") as store,
            patch("wemo2.registry.deduplicators.create") as deduplicator,
        ):
            paths = run_collector(config)
        self.assertEqual(paths, [Path("data/screenshot/example.png")])
        collector.collect.assert_called_once()
        collector.stop.assert_called_once()
        store.assert_not_called()
        deduplicator.assert_not_called()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.surface = Surface(
            platform=Platform.windows,
            surface_id="123",
            bounds=Rect(x=-100, y=50, width=80, height=60),
        )

    def test_capture_saves_png_and_matching_metadata_without_overwriting(self):
        screenshotter = WindowsScreenshotter(self.path)
        frames = []
        with (
            patch("wemo2_collector.windows.screenshot.sys.platform", "win32"),
            patch("wemo2_collector.windows.screenshot.ImageGrab.grab") as grab,
        ):
            for _ in range(2):
                grab.return_value = Image.new("RGB", (80, 60), "white")
                frames.append(screenshotter.capture(self.surface))
            grab.assert_called_with(
                bbox=(-100, 50, -20, 110),
                all_screens=True,
                include_layered_windows=True,
            )
        self.assertNotEqual(frames[0].image_path, frames[1].image_path)
        with Image.open(frames[0].image_path) as image:
            self.assertEqual(image.size, (80, 60))
        saved = Screenshot.model_validate_json(
            frames[0].image_path.with_suffix(".json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved, frames[0])

    def test_black_and_wrong_size_images_are_not_saved(self):
        for size, color in (((80, 60), "black"), ((20, 20), "white")):
            with (
                self.subTest(size=size, color=color),
                patch("wemo2_collector.windows.screenshot.sys.platform", "win32"),
                patch(
                    "wemo2_collector.windows.screenshot.ImageGrab.grab",
                    return_value=Image.new("RGB", size, color),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    WindowsScreenshotter(self.path).capture(self.surface)
        self.assertEqual(list(self.path.glob("*.png")), [])

    def test_screenshot_collector_rejects_android(self):
        with self.assertRaisesRegex(ValueError, "Android"):
            build_coordinator("windows_screenshot", platform=Platform.android)

    def test_cli_saves_screenshot_without_fake_observations(self):
        config = self.path / "config.toml"
        config.write_text(
            'collector = "windows_screenshot"\nscreenshot_dir = "captures"\nwindow_process_id = 42\n',
            encoding="utf-8",
        )
        with (
            patch(
                "wemo2_collector.windows.driver.WindowsDriver.attach",
                return_value=self.surface,
            ),
            patch(
                "wemo2_collector.windows.driver.WindowsDriver.prepare_capture",
                return_value=self.surface,
            ),
            patch("wemo2_collector.windows.driver.WindowsDriver.verify_capture"),
            patch("wemo2_collector.windows.screenshot.sys.platform", "win32"),
            patch(
                "wemo2_collector.windows.screenshot.ImageGrab.grab",
                return_value=Image.new("RGB", (80, 60), "white"),
            ),
        ):
            result = CliRunner().invoke(cli, ["--config", str(config)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("截图已保存", result.stdout)
        self.assertEqual(len(list((self.path / "captures").glob("*.png"))), 1)
        coordinator = build_coordinator("windows_screenshot", window_process_id=42)
        self.assertEqual(coordinator.collector.driver.process_id, 42)
        coordinator.store.close()

    def test_capture_failure_releases_driver(self):
        coordinator = build_coordinator("windows_screenshot", screenshot_dir=self.path)
        driver = MagicMock()
        driver.attach.return_value = self.surface
        driver.prepare_capture.return_value = self.surface
        coordinator.collector.driver = driver
        with patch(
            "wemo2_collector.windows.screenshot.ImageGrab.grab",
            side_effect=OSError("capture failed"),
        ):
            with self.assertRaises((RuntimeError, OSError)):
                coordinator.run()
        driver.close.assert_called()
        self.assertEqual(coordinator.collector.artifacts(), [])
        coordinator.store.close()

    def test_driver_reports_missing_and_ambiguous_wechat_windows(self):
        window = SimpleNamespace(Name="微信", NativeWindowHandle=123, ProcessId=42)
        for windows, message in (([], "未找到"), ([window, window], "多个")):
            uia = MagicMock()
            uia.GetRootControl.return_value.GetChildren.return_value = windows
            psutil = MagicMock()
            psutil.Error = RuntimeError
            psutil.Process.return_value.name.return_value = "Weixin.exe"
            user32 = MagicMock()
            user32.SetThreadDpiAwarenessContext.return_value = 1
            with (
                self.subTest(message=message),
                patch.dict("sys.modules", {"uiautomation": uia, "psutil": psutil}),
                patch("wemo2_collector.windows.driver.sys.platform", "win32"),
                patch(
                    "wemo2_collector.windows.driver.ctypes.WinDLL",
                    return_value=user32,
                    create=True,
                ),
            ):
                driver = WindowsDriver()
                try:
                    with self.assertRaisesRegex(RuntimeError, message):
                        driver.attach()
                finally:
                    driver.close()
                uia.UIAutomationInitializerInThread.return_value.__exit__.assert_called_once()

    def test_driver_does_not_accept_unrelated_process_with_same_title(self):
        uia = MagicMock()
        uia.GetRootControl.return_value.GetChildren.return_value = [
            SimpleNamespace(Name="微信", NativeWindowHandle=123, ProcessId=42)
        ]
        psutil = MagicMock()
        psutil.Error = RuntimeError
        psutil.Process.return_value.name.return_value = "notepad.exe"
        user32 = MagicMock()
        user32.SetThreadDpiAwarenessContext.return_value = 1
        with (
            patch.dict("sys.modules", {"uiautomation": uia, "psutil": psutil}),
            patch("wemo2_collector.windows.driver.sys.platform", "win32"),
            patch(
                "wemo2_collector.windows.driver.ctypes.WinDLL",
                return_value=user32,
                create=True,
            ),
        ):
            driver = WindowsDriver()
            try:
                with self.assertRaisesRegex(RuntimeError, "未找到"):
                    driver.attach()
            finally:
                driver.close()
