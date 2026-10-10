"""只读连接微信窗口；不激活窗口或模拟输入。"""

import ctypes
import sys
from ctypes import wintypes

from wemo2_collector.interfaces import BaseDriver
from wemo2_collector.models import LocatedTarget, Platform, Rect, Screenshot, Surface


class WindowsDriver(BaseDriver):
    def __init__(self, window_title: str = "微信", process_id: int | None = None):
        self.window_title = window_title
        self.process_id = process_id
        self.window = None
        self.user32 = None
        self.initializer = None
        self.previous_dpi = None

    def attach(self) -> Surface:
        if sys.platform != "win32":
            raise RuntimeError("Windows 截图仅支持 Windows 主机")
        # Windows 专属模块在连接时加载，help/version 不初始化 UI Automation。
        import psutil
        import uiautomation as uia

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
        self.initializer = uia.UIAutomationInitializerInThread()
        self.initializer.__enter__()
        matches = []
        for window in uia.GetRootControl().GetChildren():
            if window.Name != self.window_title or not window.NativeWindowHandle:
                continue
            if not self.user32.IsWindowVisible(window.NativeWindowHandle):
                continue
            if self.process_id is not None and window.ProcessId != self.process_id:
                continue
            try:
                process_name = psutil.Process(window.ProcessId).name().lower()
            except psutil.Error:
                continue
            if process_name in ("wechat.exe", "weixin.exe"):
                matches.append(window)
        if not matches:
            raise RuntimeError("未找到微信窗口，请打开微信窗口并检查 window_title 配置")
        if len(matches) != 1:
            raise RuntimeError(
                "找到多个微信窗口，请通过 window_process_id 指定目标进程"
            )
        self.window = matches[0]
        return self.current_surface()

    def current_surface(self) -> Surface:
        if self.window is None or self.user32 is None:
            raise RuntimeError("微信窗口尚未连接")
        handle = self.window.NativeWindowHandle
        if not self.user32.IsWindow(handle) or not self.user32.IsWindowVisible(handle):
            raise RuntimeError("微信窗口已经关闭或不可见")
        if self.user32.IsIconic(handle):
            raise RuntimeError("微信窗口已最小化，请先还原窗口")
        rect = wintypes.RECT()
        # Pillow 的 HWND 截图使用客户区，不能与整个窗口外框的尺寸比较。
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

    def click(self, frame: Screenshot, target: LocatedTarget) -> None:
        raise NotImplementedError("当前阶段只支持截图，不执行点击")

    def scroll(self, surface: Surface, distance: int) -> None:
        raise NotImplementedError("当前阶段只支持截图，不执行滚动")

    def close(self) -> None:
        try:
            if self.initializer is not None:
                self.initializer.__exit__(None, None, None)
                self.initializer = None
        finally:
            if self.previous_dpi is not None:
                self.user32.SetThreadDpiAwarenessContext(self.previous_dpi)
                self.previous_dpi = None
            self.window = None
