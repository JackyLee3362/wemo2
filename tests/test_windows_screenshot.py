import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image
from typer.testing import CliRunner

from wemo2.cli import cli
from wemo2.composition import build_coordinator
from wemo2_collector.models import Platform, Rect, Screenshot, Surface
from wemo2_collector.windows.driver import WindowsDriver
from wemo2_collector.windows.screenshot import WindowsScreenshotter


class WindowsScreenshotTests(unittest.TestCase):
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
            grab.assert_called_with(window=123)
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
                "wemo2_collector.windows.driver.WindowsDriver.current_surface",
                return_value=self.surface,
            ),
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
