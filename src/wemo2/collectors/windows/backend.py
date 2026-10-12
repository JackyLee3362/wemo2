"""Windows 后端的延迟加载、日志接入及限时控件消息。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from wemo2.collectors.airtest_logging import configure_airtest_logging

if TYPE_CHECKING:
    from airtest.core.win import Windows


def create_device(**options: object) -> Windows:
    """创建独立设备实例，不使用 Airtest 的全局当前设备。"""
    # Airtest 导入会设置标准日志输出；替换为项目统一的 Loguru 通道。
    from airtest.core.win import Windows

    configure_airtest_logging()
    return Windows(**options)


def send_control_message(handle: int, message: int, timeout: float) -> None:
    """限时发送零参数控件消息；返回仅表示消息处理返回，保存仍需校验文件。"""
    # 复用 Airtest/pywinauto 已有的 pywin32，避免在非 Windows 或帮助阶段加载。
    import pywintypes
    import win32con
    import win32gui

    if timeout <= 0:
        raise TimeoutError("控件消息的等待时间已经耗尽")
    try:
        win32gui.SendMessageTimeout(
            handle,
            message,
            0,
            0,
            win32con.SMTO_ABORTIFHUNG | win32con.SMTO_BLOCK,
            max(1, int(timeout * 1000)),
        )
    except pywintypes.error as error:
        logger.exception(
            "控件消息调用失败，窗口={}，消息={}：{}", handle, message, error
        )
        if error.winerror in (0, 1460):
            raise TimeoutError("控件消息超时或未确认处理结果") from error
        raise
