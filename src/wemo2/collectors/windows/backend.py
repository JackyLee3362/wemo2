"""Airtest 的延迟加载与日志接入。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wemo2.collectors.airtest_logging import configure_airtest_logging

if TYPE_CHECKING:
    from airtest.core.win import Windows


def create_device(**options: object) -> Windows:
    """创建独立设备实例，不使用 Airtest 的全局当前设备。"""
    # Airtest 导入会设置标准日志输出；替换为项目的文件日志通道。
    from airtest.core.win import Windows

    configure_airtest_logging()
    return Windows(**options)
