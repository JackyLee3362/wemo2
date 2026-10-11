"""使用 Airtest 连接微信窗口，验证客户区并执行导航点击。"""

import ctypes
import sys
import time
from ctypes import wintypes
from datetime import UTC, datetime

from loguru import logger

from wemo2_collector.interfaces import BaseDriver
from wemo2_collector.models import (
    Direction,
    LocatedTarget,
    Platform,
    Rect,
    Screenshot,
    Surface,
)


class WindowsDriver(BaseDriver):
    """通过 Airtest 管理微信窗口会话，并执行校验后的点击和区域内拖动。

    负责物理坐标、DPI、焦点及同进程窗口跟随；释放会话时不关闭用户微信。
    """

    def __init__(self, window_title: str = "微信", process_id: int | None = None):
        self.window_title = window_title
        self.process_id = process_id
        self.device = None
        self.user32 = None
        self.previous_dpi = None

    def attach(self) -> Surface:
        if sys.platform != "win32":
            raise RuntimeError("Windows 截图仅支持 Windows 主机")
        # Windows 专属模块延迟加载，help/version 不连接窗口。
        import psutil
        from pywinauto import findwindows

        from wemo2_collector.windows.backend import create_device

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        self.previous_dpi = self.user32.SetThreadDpiAwarenessContext(
            ctypes.c_void_p(-4)
        )
        if self.previous_dpi is None:
            raise ctypes.WinError(ctypes.get_last_error())
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.GetClientRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self.user32.ClientToScreen.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.POINT),
        ]
        self.user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        matches = []
        windows = findwindows.find_elements(
            title=self.window_title, visible_only=True, backend="win32"
        )
        for window in windows:
            if self.process_id is not None and window.process_id != self.process_id:
                continue
            try:
                process_name = psutil.Process(window.process_id).name().lower()
            except psutil.Error as error:
                logger.exception(
                    "读取候选窗口进程 {} 失败，跳过该候选：{}", window.process_id, error
                )
                continue
            if process_name in ("wechat.exe", "weixin.exe"):
                matches.append(window)
        if not matches:
            raise RuntimeError("未找到微信窗口，请打开微信窗口并检查 window_title 配置")
        if len(matches) != 1:
            raise RuntimeError(
                "找到多个微信窗口，请通过 window_process_id 指定目标进程"
            )
        self.device = create_device(handle=matches[0].handle, foreground=False)
        return self.current_surface()

    def current_surface(self) -> Surface:
        if self.device is None or self.user32 is None:
            raise RuntimeError("微信窗口尚未连接")
        handle = self.device.handle
        if not self.user32.IsWindow(handle) or not self.user32.IsWindowVisible(handle):
            raise RuntimeError("微信窗口已经关闭或不可见")
        if self.user32.IsIconic(handle):
            raise RuntimeError("微信窗口已最小化，请先还原窗口")
        rect = wintypes.RECT()
        # 读取客户区的物理像素范围，供屏幕截图裁切。
        if not self.user32.GetClientRect(handle, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        origin = wintypes.POINT(rect.left, rect.top)
        if not self.user32.ClientToScreen(handle, ctypes.byref(origin)):
            raise ctypes.WinError(ctypes.get_last_error())
        dpi = self.user32.GetDpiForWindow(handle)
        if dpi == 0:
            raise RuntimeError("无法获取微信窗口 DPI")
        return Surface(
            platform=Platform.windows,
            surface_id=str(handle),
            bounds=Rect(
                x=origin.x,
                y=origin.y,
                width=rect.right - rect.left,
                height=rect.bottom - rect.top,
            ),
            scale=dpi / 96,
        )

    def prepare_capture(self) -> Surface:
        """切到前台并确认整个客户区在显示器范围内，避免截到编辑器或空白区域。"""
        surface = self.current_surface()
        self.device.set_foreground()
        for _ in range(10):
            if self.user32.GetForegroundWindow() == int(surface.surface_id):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("无法将微信切到前台，请手动打开微信后重试")
        time.sleep(0.2)  # 等待窗口合成器完成前台重绘。
        surface = self.current_surface()
        if self.user32.GetForegroundWindow() != int(surface.surface_id):
            raise RuntimeError("截图前微信失去前台焦点，请重新运行")
        left = self.user32.GetSystemMetrics(76)
        top = self.user32.GetSystemMetrics(77)
        right = left + self.user32.GetSystemMetrics(78)
        bottom = top + self.user32.GetSystemMetrics(79)
        bounds = surface.bounds
        if (
            bounds.x < left
            or bounds.y < top
            or bounds.x + bounds.width > right
            or bounds.y + bounds.height > bottom
        ):
            raise RuntimeError("微信客户区超出屏幕范围，请将窗口完整移入屏幕后重试")
        return surface

    def verify_capture(self, surface: Surface) -> None:
        """保存前后确认目标范围和前台窗口没有变化。"""
        if self.current_surface() != surface:
            raise RuntimeError("截图时微信窗口发生变化，请重试")
        if self.user32.GetForegroundWindow() != int(surface.surface_id):
            raise RuntimeError("截图时微信失去前台焦点，请重试")

    def click(self, frame: Screenshot, target: LocatedTarget) -> None:
        self._validate_target(frame, target)
        bounds = target.bounds
        x = frame.surface.bounds.x + bounds.x + bounds.width // 2
        y = frame.surface.bounds.y + bounds.y + bounds.height // 2
        # Airtest 的窗口点击坐标相对外框；我们的定位结果相对客户区。
        # DPI 感知已设为物理像素，Airtest dpifactor 保持 1，避免重复缩放。
        left, top = self.device.get_pos()
        self.device.touch((x - left, y - top), duration=0.1)

    def follow_foreground(self) -> None:
        """跟随点击后同进程的前台窗口，不切回旧窗口或连接其他应用。"""
        if self.device is None:
            raise RuntimeError("微信窗口尚未连接")
        handle = self.user32.GetForegroundWindow()
        if handle == self.device.handle:
            return
        original_pid = wintypes.DWORD()
        foreground_pid = wintypes.DWORD()
        if not self.user32.GetWindowThreadProcessId(
            self.device.handle, ctypes.byref(original_pid)
        ):
            raise RuntimeError("无法读取原微信窗口进程")
        if not self.user32.GetWindowThreadProcessId(
            handle, ctypes.byref(foreground_pid)
        ):
            raise RuntimeError("无法读取点击后的前台进程")
        if original_pid.value != foreground_pid.value:
            raise RuntimeError("点击后前台窗口不属于原微信进程，请检查微信界面")
        from wemo2_collector.windows.backend import create_device

        new_device = create_device(handle=handle, foreground=False)
        old_device = self.device
        self.device = new_device
        try:
            self.current_surface()
        except Exception as error:
            logger.exception("新前台窗口校验失败，恢复原会话：{}", error)
            new_device.screen.close()
            self.device = old_device
            raise
        old_device.screen.close()

    def _validate_target(self, frame: Screenshot, target: LocatedTarget) -> None:
        if target.frame_id != frame.frame_id:
            raise RuntimeError("定位结果不属于当前截图，拒绝点击")
        age = (datetime.now(UTC) - frame.captured_at).total_seconds()
        if age < 0 or age > 5:
            raise RuntimeError("截图已经过期，请重新定位后点击")
        bounds = target.bounds
        if (
            bounds.x < 0
            or bounds.y < 0
            or bounds.x + bounds.width > frame.surface.bounds.width
            or bounds.y + bounds.height > frame.surface.bounds.height
        ):
            raise RuntimeError("定位目标超出客户区，拒绝点击")
        self.verify_capture(frame.surface)

    def swipe(
        self,
        frame: Screenshot,
        target: LocatedTarget,
        direction: Direction,
        distance: int,
    ) -> None:
        """Airtest Windows 的滑动是区域内鼠标拖动，不是鼠标滚轮。"""
        self._validate_target(frame, target)
        if type(distance) is not int or distance <= 0:
            raise ValueError("滑动距离必须是正整数物理像素")
        bounds = target.bounds
        x = bounds.x + bounds.width // 2
        y = bounds.y + bounds.height // 2
        end_x, end_y = x, y
        if direction == "up":
            end_y -= distance
        elif direction == "down":
            end_y += distance
        elif direction == "left":
            end_x -= distance
        elif direction == "right":
            end_x += distance
        else:
            raise ValueError("未知滑动方向")
        if not (
            bounds.x <= end_x < bounds.x + bounds.width
            and bounds.y <= end_y < bounds.y + bounds.height
        ):
            raise RuntimeError("滑动端点超出定位区域，拒绝执行")
        left, top = self.device.get_pos()
        offset_x = frame.surface.bounds.x - left
        offset_y = frame.surface.bounds.y - top
        self.device.swipe(
            (offset_x + x, offset_y + y),
            (offset_x + end_x, offset_y + end_y),
            duration=0.5,
        )

    def close(self) -> None:
        """只释放截图资源并恢复线程 DPI，不退出微信。"""
        try:
            if self.device is not None:
                self.device.screen.close()
        finally:
            self.device = None
            if self.previous_dpi is not None:
                self.user32.SetThreadDpiAwarenessContext(self.previous_dpi)
                self.previous_dpi = None
