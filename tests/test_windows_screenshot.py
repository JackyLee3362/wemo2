import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image
from typer.testing import CliRunner

from wemo2.cli import cli
from wemo2.composition import build_coordinator
from wemo2.config import AppConfig
from wemo2_collector.main import run_collector
from wemo2_collector.models import ControllerConfig, Platform, Rect, Screenshot, Surface
from wemo2_collector.profiles import build_profiles
from wemo2_collector.validation import UiValidator
from wemo2_collector.windows.driver import WindowsDriver
from wemo2_collector.windows.screenshot import WindowsScreenshotter


class WindowsScreenshotTests(unittest.TestCase):
    """验证 Windows 窗口筛选、截图裁切、颜色转换、焦点检查和会话释放。"""

    def test_near_black_frame_with_one_white_pixel_is_rejected(self):
        image = Image.new("RGB", (80, 60), "black")
        image.putpixel((0, 0), (255, 255, 255))
        with (
            patch("wemo2_collector.windows.screenshot.sys.platform", "win32"),
            patch(
                "wemo2_collector.windows.screenshot.WindowsScreenshotter._capture_image",
                return_value=image,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "几乎全黑"):
                WindowsScreenshotter(self.path).capture(self.surface)
        self.assertEqual(list(self.path.glob("*.png")), [])

    def test_prepare_capture_activates_target_and_accepts_negative_monitor_coordinates(
        self,
    ):
        driver = WindowsDriver()
        driver.device = MagicMock()
        driver.user32 = MagicMock()
        driver.user32.GetForegroundWindow.return_value = 123
        metrics = {76: -200, 77: 0, 78: 1200, 79: 800}
        driver.user32.GetSystemMetrics.side_effect = metrics.__getitem__
        with (
            patch.object(driver, "current_surface", return_value=self.surface),
            patch("wemo2_collector.windows.driver.time.sleep"),
        ):
            self.assertEqual(driver.prepare_capture(), self.surface)
        driver.device.set_foreground.assert_called_once()

    def test_prepare_capture_rejects_window_outside_virtual_screen(self):
        driver = WindowsDriver()
        driver.device = MagicMock()
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
        driver.device = SimpleNamespace(handle=123)
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
            patch(
                "wemo2_collector.windows.screenshot.WindowsScreenshotter._capture_image"
            ) as grab,
        ):
            for _ in range(2):
                grab.return_value = Image.new("RGB", (80, 60), "white")
                frames.append(screenshotter.capture(self.surface))
            grab.assert_called_with(self.surface)
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
                    "wemo2_collector.windows.screenshot.WindowsScreenshotter._capture_image",
                    return_value=Image.new("RGB", size, color),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    WindowsScreenshotter(self.path).capture(self.surface)
        self.assertEqual(list(self.path.glob("*.png")), [])

    def test_screenshot_collector_rejects_android(self):
        with self.assertRaisesRegex(ValueError, "Android"):
            build_coordinator(
                "windows_screenshot", collector_options={"platform": "android"}
            )

    def test_cli_saves_screenshot_without_fake_observations(self):
        config = self.path / "config.toml"
        config.write_text(
            'collector = "windows_screenshot"\n[collector_options]\nscreenshot_dir = "captures"\nwindow_process_id = 42\n',
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
                "wemo2_collector.windows.screenshot.WindowsScreenshotter._capture_image",
                return_value=Image.new("RGB", (80, 60), "white"),
            ),
        ):
            result = CliRunner().invoke(cli, ["--config", str(config)])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("采集文件已保存", result.stdout)
        self.assertEqual(len(list((self.path / "captures").glob("*.png"))), 1)
        coordinator = build_coordinator(
            "windows_screenshot", collector_options={"window_process_id": 42}
        )
        self.assertEqual(coordinator.collector.driver.process_id, 42)
        coordinator.store.close()

    def test_capture_failure_releases_driver(self):
        coordinator = build_coordinator(
            "windows_screenshot", collector_options={"screenshot_dir": self.path}
        )
        driver = MagicMock()
        driver.attach.return_value = self.surface
        driver.prepare_capture.return_value = self.surface
        coordinator.collector.driver = driver
        coordinator.collector.controller.driver = driver
        with patch(
            "wemo2_collector.windows.screenshot.WindowsScreenshotter._capture_image",
            side_effect=OSError("capture failed"),
        ):
            with self.assertRaises((RuntimeError, OSError)):
                coordinator.run()
        driver.close.assert_called()
        self.assertEqual(coordinator.collector.artifacts(), [])
        coordinator.store.close()

    def test_driver_reports_missing_and_ambiguous_wechat_windows(self):
        window = SimpleNamespace(handle=123, process_id=42)
        for windows, message in (([], "未找到"), ([window, window], "多个")):
            findwindows = MagicMock()
            findwindows.find_elements.return_value = windows
            psutil = MagicMock()
            psutil.Error = RuntimeError
            psutil.Process.return_value.name.return_value = "Weixin.exe"
            user32 = MagicMock()
            user32.SetThreadDpiAwarenessContext.return_value = 1
            with (
                self.subTest(message=message),
                patch.dict(
                    "sys.modules",
                    {
                        "pywinauto": SimpleNamespace(findwindows=findwindows),
                        "psutil": psutil,
                    },
                ),
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
                user32.SetThreadDpiAwarenessContext.assert_any_call(1)

    def test_driver_does_not_accept_unrelated_process_with_same_title(self):
        findwindows = MagicMock()
        findwindows.find_elements.return_value = [
            SimpleNamespace(handle=123, process_id=42)
        ]
        psutil = MagicMock()
        psutil.Error = RuntimeError
        psutil.Process.return_value.name.return_value = "notepad.exe"
        user32 = MagicMock()
        user32.SetThreadDpiAwarenessContext.return_value = 1
        with (
            patch.dict(
                "sys.modules",
                {
                    "pywinauto": SimpleNamespace(findwindows=findwindows),
                    "psutil": psutil,
                },
            ),
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

    def test_airtest_desktop_crop_preserves_negative_origin_and_rgb_colors(self):
        device = MagicMock()
        device.screen.monitors = [
            {"left": -120, "top": 40, "width": 120, "height": 100}
        ]
        pixels = np.zeros((100, 120, 3), dtype=np.uint8)
        pixels[10, 20] = (11, 22, 33)
        device.snapshot.return_value = pixels
        with patch(
            "wemo2_collector.windows.screenshot.create_device", return_value=device
        ):
            image = WindowsScreenshotter(self.path)._capture_image(self.surface)
        try:
            self.assertEqual(image.size, (80, 60))
            self.assertEqual(image.getpixel((0, 0)), (33, 22, 11))
        finally:
            image.close()
        device.snapshot.assert_called_once_with()
        device.screen.close.assert_called_once()

    def test_airtest_invalid_desktop_frames_release_resources(self):
        for pixels in (
            None,
            np.zeros((100, 120), dtype=np.uint8),
            np.zeros((50, 120, 3), dtype=np.uint8),
        ):
            device = MagicMock()
            device.screen.monitors = [
                {"left": -120, "top": 40, "width": 120, "height": 100}
            ]
            device.snapshot.return_value = pixels
            with patch(
                "wemo2_collector.windows.screenshot.create_device", return_value=device
            ):
                with self.assertRaises(RuntimeError):
                    WindowsScreenshotter(self.path)._capture_image(self.surface)
            device.screen.close.assert_called_once()

    def test_airtest_capture_error_releases_desktop_device(self):
        device = MagicMock()
        device.snapshot.side_effect = OSError("capture failed")
        with patch(
            "wemo2_collector.windows.screenshot.create_device", return_value=device
        ):
            with self.assertRaises(OSError):
                WindowsScreenshotter(self.path)._capture_image(self.surface)
        device.screen.close.assert_called_once()

    def test_driver_connects_only_configured_wechat_process_through_airtest(self):
        findwindows = MagicMock()
        findwindows.find_elements.return_value = [
            SimpleNamespace(handle=111, process_id=10),
            SimpleNamespace(handle=123, process_id=42),
        ]
        psutil = MagicMock()
        psutil.Error = RuntimeError
        psutil.Process.return_value.name.return_value = "Weixin.exe"
        user32 = MagicMock()
        user32.SetThreadDpiAwarenessContext.return_value = 1
        device = MagicMock()
        with (
            patch.dict(
                "sys.modules",
                {
                    "pywinauto": SimpleNamespace(findwindows=findwindows),
                    "psutil": psutil,
                },
            ),
            patch("wemo2_collector.windows.driver.sys.platform", "win32"),
            patch(
                "wemo2_collector.windows.driver.ctypes.WinDLL",
                return_value=user32,
                create=True,
            ),
            patch(
                "wemo2_collector.windows.backend.create_device", return_value=device
            ) as create,
        ):
            driver = WindowsDriver(process_id=42)
            with patch.object(driver, "current_surface", return_value=self.surface):
                self.assertEqual(driver.attach(), self.surface)
            create.assert_called_once_with(handle=123, foreground=False)
            psutil.Process.assert_called_once_with(42)
            driver.close()
            driver.close()
        device.screen.close.assert_called_once()
        device.stop_app.assert_not_called()
        user32.SetThreadDpiAwarenessContext.assert_any_call(1)

    def test_follow_foreground_connects_same_process_window_and_releases_old_device(
        self,
    ):
        driver = WindowsDriver()
        old_device = MagicMock()
        old_device.handle = 123
        new_device = MagicMock()
        driver.device = old_device
        driver.user32 = MagicMock()
        driver.user32.GetForegroundWindow.return_value = 456

        def read_pid(handle, pointer):
            pointer._obj.value = 42
            return 1

        driver.user32.GetWindowThreadProcessId.side_effect = read_pid
        with (
            patch(
                "wemo2_collector.windows.backend.create_device", return_value=new_device
            ) as create,
            patch.object(driver, "current_surface", return_value=self.surface),
        ):
            driver.follow_foreground()
        create.assert_called_once_with(handle=456, foreground=False)
        old_device.screen.close.assert_called_once()
        self.assertIs(driver.device, new_device)

    def test_follow_foreground_rejects_other_process_without_connecting(self):
        driver = WindowsDriver()
        driver.device = MagicMock()
        driver.device.handle = 123
        driver.user32 = MagicMock()
        driver.user32.GetForegroundWindow.return_value = 456

        def read_pid(handle, pointer):
            pointer._obj.value = handle
            return 1

        driver.user32.GetWindowThreadProcessId.side_effect = read_pid
        with patch("wemo2_collector.windows.backend.create_device") as create:
            with self.assertRaisesRegex(RuntimeError, "不属于原微信进程"):
                driver.follow_foreground()
        create.assert_not_called()
        driver.device.screen.close.assert_not_called()

    def test_screenshot_default_timezone_is_shanghai(self):
        self.assertEqual(WindowsScreenshotter(self.path).timezone.key, "Asia/Shanghai")

    def test_filename_and_metadata_use_configured_timezone_including_dst(self):
        cases = [
            (
                "Asia/Shanghai",
                datetime(2026, 1, 10, tzinfo=UTC),
                "20260110T080000+0800",
                timedelta(hours=8),
            ),
            (
                "UTC",
                datetime(2026, 1, 10, tzinfo=UTC),
                "20260110T000000+0000",
                timedelta(0),
            ),
            (
                "America/New_York",
                datetime(2026, 1, 10, tzinfo=UTC),
                "20260109T190000-0500",
                timedelta(hours=-5),
            ),
            (
                "America/New_York",
                datetime(2026, 7, 10, tzinfo=UTC),
                "20260709T200000-0400",
                timedelta(hours=-4),
            ),
        ]
        for timezone, instant, prefix, offset in cases:
            with (
                self.subTest(timezone=timezone, instant=instant),
                patch("wemo2_collector.windows.screenshot.sys.platform", "win32"),
                patch(
                    "wemo2_collector.windows.screenshot.WindowsScreenshotter._capture_image",
                    return_value=Image.new("RGB", (80, 60), "white"),
                ),
                patch("wemo2_collector.windows.screenshot.datetime") as clock,
            ):
                clock.now.return_value = instant.astimezone(ZoneInfo(timezone))
                frame = WindowsScreenshotter(self.path, timezone).capture(self.surface)
                clock.now.assert_called_once_with(ZoneInfo(timezone))
            self.assertTrue(frame.image_path.name.startswith(f"wechat_{prefix}_"))
            self.assertEqual(frame.timezone, timezone)
            self.assertEqual(frame.captured_at.utcoffset(), offset)
            self.assertEqual(frame.captured_at.astimezone(UTC), instant)
            saved = Screenshot.model_validate_json(
                frame.image_path.with_suffix(".json").read_text(encoding="utf-8")
            )
            self.assertEqual(saved.timezone, timezone)
            self.assertEqual(saved.captured_at.utcoffset(), offset)

    def test_legacy_metadata_without_timezone_name_remains_readable(self):
        instant = datetime(2026, 1, 10, tzinfo=UTC)
        original = Screenshot(
            frame_id="legacy",
            captured_at=instant,
            surface=self.surface,
            image_path=self.path / "legacy.png",
        )
        legacy_json = original.model_dump_json(exclude={"timezone"})
        loaded = Screenshot.model_validate_json(legacy_json)
        self.assertIsNone(loaded.timezone)
        self.assertEqual(loaded.captured_at, instant)

    def test_frame_age_validation_compares_instants_across_timezones(self):
        path = self.path / "shanghai.png"
        with Image.new("RGB", (80, 60), "white") as image:
            image.save(path)
        now = datetime(2026, 1, 10, 0, 0, 1, tzinfo=UTC)
        captured = datetime(2026, 1, 10, 8, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        frame = Screenshot(
            frame_id="local",
            captured_at=captured,
            timezone="Asia/Shanghai",
            surface=self.surface,
            image_path=path,
        )
        pages, _ = build_profiles()
        validator = UiValidator(pages, MagicMock(), ControllerConfig())
        with patch("wemo2_collector.validation.datetime") as clock:
            clock.now.return_value = now
            validator.validate_frame(frame)
            clock.now.return_value = now + timedelta(seconds=5)
            with self.assertRaisesRegex(RuntimeError, "已经过期"):
                validator.validate_frame(frame)
