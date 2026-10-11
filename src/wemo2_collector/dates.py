"""以带时区的采集时间为参照，将微信发布时间原文推断到日。"""

import re
from datetime import date, datetime, timedelta

from loguru import logger


def approximate_day(raw: str, captured_at: datetime) -> date | None:
    """解析常见相对时间及年月日；缺少年份取最近过去日期，无效日期保留未知。"""
    text = "".join(raw.split())
    if text in ("刚刚", "今天"):
        return captured_at.date()
    if text == "昨天":
        return captured_at.date() - timedelta(days=1)
    if text == "前天":
        return captured_at.date() - timedelta(days=2)
    matched = re.fullmatch(r"(\d+)(分钟|小时|天)前", text)
    if matched:
        number = int(matched[1])
        unit = matched[2]
        try:
            if unit == "分钟":
                delta = timedelta(minutes=number)
            elif unit == "小时":
                delta = timedelta(hours=number)
            else:
                delta = timedelta(days=number)
            return (captured_at - delta).date()
        except OverflowError as error:
            logger.exception("发布时间超出可表示日期，保留未知：{}，{}", raw, error)
            return None
    matched = re.fullmatch(
        r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日?(?:周[一二三四五六日天])?", text
    )
    if not matched:
        matched = re.fullmatch(r"(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})", text)
    if not matched:
        return None
    year = int(matched[1]) if matched[1] else captured_at.year
    try:
        value = date(year, int(matched[2]), int(matched[3]))
        if not matched[1] and value > captured_at.date():
            value = value.replace(year=year - 1)
        return value
    except ValueError as error:
        logger.exception("发布时间日期无效，保留原文且不推断：{}，{}", raw, error)
        return None
