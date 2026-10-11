"""截图时间使用 IANA 时区，Windows 的时区数据由 tzdata 提供。"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger


def resolve_timezone(name: str) -> ZoneInfo:
    """解析时区名称，无效名称在连接设备之前明确报错。"""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        logger.exception("解析时区 {} 失败：{}", name, error)
        raise ValueError(f"无效的 IANA 时区：{name}") from error
