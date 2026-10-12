import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pywintypes
from pydantic import ValidationError

from wemo2.collectors.models import SaveDialogConfig
from wemo2.collectors.windows.backend import send_control_message
from wemo2.collectors.windows.driver import WindowsDriver
from wemo2.core.config import AppConfig
from wemo2.core.registry import collectors


class SaveDialogTests(unittest.TestCase):
    """验证就绪重试、消息超时后的观察、明确的退出原因以及保存消息不重发。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.clock = SimpleNamespace(now=0.0)
        self.driver = WindowsDriver(
            save_options=SaveDialogConfig(
                timeout_seconds=6, poll_interval=0.2, submit="message"
            )
        )
        self.driver.device = MagicMock()
        self.driver.device.handle = 100
        self.driver.user32 = MagicMock()
        self.driver.user32.IsWindow.return_value = False
        self.driver.user32.GetForegroundWindow.return_value = 300

        def process_id(handle, pointer):
            pointer._obj.value = 42
            return 1

        self.driver.user32.GetWindowThreadProcessId.side_effect = process_id
        self.dialog = MagicMock()
        self.dialog.class_name.return_value = "#32770"
        self.dialog.window_text.return_value = "保存"
        self.edit = MagicMock()
        self.edit.class_name.return_value = "Edit"
        self.edit.control_id.return_value = 1001
        self.button = MagicMock()
        self.button.class_name.return_value = "Button"
        self.button.control_id.return_value = 1
        self.button.handle = 200
        self.dialog.descendants.return_value = [self.edit, self.button]

    def write_saved_file(self):
        """按文件名控件已收到的目标路径模拟系统补全扩展名并落盘。"""
        destination = Path(self.edit.set_edit_text.call_args.args[0]).with_suffix(
            ".jpg"
        )
        destination.write_bytes(b"saved media bytes")
        return destination

    def run_dialog(self, on_message):
        """模拟窗口和时间，运行真实的保存等待流程。"""

        def sleep(seconds):
            self.clock.now += seconds

        with (
            patch.object(self.driver, "current_surface"),
            patch.object(self.driver, "verify_capture"),
            patch("pywinauto.Desktop") as desktop,
            patch(
                "wemo2.collectors.windows.backend.send_control_message",
                side_effect=on_message,
            ) as send,
            patch("wemo2.collectors.windows.backend.create_device"),
            patch(
                "wemo2.collectors.windows.driver.time.monotonic",
                side_effect=lambda: self.clock.now,
            ),
            patch("wemo2.collectors.windows.driver.time.sleep", side_effect=sleep),
        ):
            desktop.return_value.window.return_value.wrapper_object.return_value = (
                self.dialog
            )
            self.send = send
            return self.driver.save_dialog(self.directory)

    def test_disabled_button_becomes_ready_before_single_submission(self):
        self.button.is_enabled.side_effect = [False, True, True]

        def send(handle, message, timeout):
            if message == 0x00F5:
                self.write_saved_file()

        path = self.run_dialog(send)
        self.assertTrue(path.is_file())
        self.assertGreater(self.clock.now, 0)
        self.assertEqual(
            [call.args[1] for call in self.send.call_args_list], [0, 0x00F5]
        )

    def test_unresponsive_ready_probe_retries_then_saves_once(self):
        probes = 0

        def send(handle, message, timeout):
            nonlocal probes
            if message == 0:
                probes += 1
                if probes < 3:
                    raise TimeoutError("按钮暂时无响应")
            else:
                self.write_saved_file()

        self.assertTrue(self.run_dialog(send).is_file())
        self.assertEqual(probes, 3)
        self.assertEqual(
            [call.args[1] for call in self.send.call_args_list], [0, 0, 0, 0x00F5]
        )

    def test_ready_probe_exhaustion_never_types_or_submits(self):
        def unresponsive(handle, message, timeout):
            raise TimeoutError("暂时无响应")

        with self.assertRaisesRegex(RuntimeError, "就绪重试耗尽"):
            self.run_dialog(unresponsive)
        self.assertEqual(self.send.call_count, 3)
        self.edit.set_edit_text.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_submission_timeout_can_finish_later_without_repeating_message(self):
        def send(handle, message, timeout):
            if message == 0x00F5:
                self.write_saved_file()
                raise TimeoutError("接收线程仍在处理")

        self.assertTrue(self.run_dialog(send).is_file())
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(
            [call.args[1] for call in self.send.call_args_list], [0, 0x00F5]
        )

    def test_expired_preparation_budget_never_submits_save(self):
        def send(handle, message, timeout):
            self.clock.now = 6

        with self.assertRaisesRegex(RuntimeError, "准备阶段耗尽等待时间"):
            self.run_dialog(send)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.send.call_args.args[1], 0)

    def test_open_dialog_exits_at_deadline_without_repeating_submission(self):
        self.driver.user32.IsWindow.return_value = True
        self.driver.user32.IsWindowVisible.return_value = True
        with self.assertRaisesRegex(RuntimeError, "对话框仍未关闭"):
            self.run_dialog(lambda *args: None)
        self.assertEqual(self.clock.now, 6)
        self.assertEqual(self.send.call_count, 2)

    def test_closed_dialog_without_file_has_distinct_timeout_reason(self):
        with self.assertRaisesRegex(RuntimeError, "对话框已关闭"):
            self.run_dialog(lambda *args: None)
        self.assertEqual(self.clock.now, 6)
        self.assertEqual(self.send.call_count, 2)

    def test_submission_timeout_waits_to_deadline_without_retrying_input(self):
        def send(handle, message, timeout):
            if message == 0x00F5:
                raise TimeoutError("提交结果未知")

        with self.assertRaisesRegex(RuntimeError, "对话框已关闭"):
            self.run_dialog(send)
        self.assertEqual(self.clock.now, 6)
        self.assertEqual(self.send.call_count, 2)

    def test_empty_file_is_not_successful_save(self):
        def send(handle, message, timeout):
            if message == 0x00F5:
                self.write_saved_file().write_bytes(b"")

        with self.assertRaisesRegex(RuntimeError, "非空文件"):
            self.run_dialog(send)
        self.assertEqual(self.send.call_count, 2)

    def test_saved_file_does_not_hide_switch_to_other_process(self):
        def send(handle, message, timeout):
            if message == 0x00F5:
                self.write_saved_file()
                self.driver.user32.GetWindowThreadProcessId.return_value = 0
                self.driver.user32.GetWindowThreadProcessId.side_effect = None

        with self.assertRaisesRegex(RuntimeError, "不属于原微信进程"):
            self.run_dialog(send)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(len(list(self.directory.glob("*.jpg"))), 1)

    def test_message_api_zero_reply_is_valid_and_timeout_is_not_swallowed(self):
        with patch("win32gui.SendMessageTimeout", return_value=(1, 0)) as send:
            send_control_message(200, 0x00F5, 2)
        self.assertEqual(send.call_args.args[-1], 2000)
        with patch(
            "win32gui.SendMessageTimeout",
            side_effect=pywintypes.error(1460, "SendMessageTimeout", "timeout"),
        ):
            with self.assertRaises(TimeoutError) as caught:
                send_control_message(200, 0x00F5, 2)
        self.assertIsInstance(caught.exception.__cause__, pywintypes.error)

    def test_windows_options_are_validated_and_passed_to_driver(self):
        config = AppConfig(
            collector_options={
                "save_dialog": {"timeout_seconds": 10, "prepare_attempts": 2}
            }
        )
        source = collectors.create(config.collector, config.model_dump())
        self.assertEqual(source.driver.save_options.timeout_seconds, 10)
        self.assertEqual(source.driver.save_options.prepare_attempts, 2)
        for values in (
            {"timeout_seconds": 0},
            {"prepare_attempts": True},
            {"message_timeout_seconds": 6},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AppConfig(collector_options={"save_dialog": values})

    def test_enter_submission_focuses_filename_and_waits_for_real_file(self):
        self.driver.save_options = SaveDialogConfig(timeout_seconds=6)
        device = self.driver.device

        def keyevent(key):
            self.edit.set_keyboard_focus.assert_called_once()
            self.assertEqual(key, "{ENTER}")
            self.write_saved_file()

        device.keyevent.side_effect = keyevent
        self.assertTrue(self.run_dialog(lambda *args: None).is_file())
        device.keyevent.assert_called_once_with("{ENTER}")
        self.edit.set_focus.assert_not_called()
        self.assertEqual(self.send.call_count, 1)  # 只有无副作用的 WM_NULL。

    def test_ctrl_s_is_sent_once_only_to_verified_window(self):
        with (
            patch.object(self.driver, "verify_capture") as verify,
            patch.object(self.driver, "current_surface"),
        ):
            self.driver.open_save_dialog()
        verify.assert_called_once()
        self.driver.device.keyevent.assert_called_once_with("^s")

    def test_missing_keyboard_focus_never_sends_enter(self):
        self.driver.save_options = SaveDialogConfig(timeout_seconds=6)
        self.edit.has_keyboard_focus.return_value = False
        with self.assertRaisesRegex(RuntimeError, "未获得键盘焦点"):
            self.run_dialog(lambda *args: None)
        self.driver.device.keyevent.assert_not_called()
