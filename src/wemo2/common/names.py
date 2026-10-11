"""人物名规范化和 Windows 目录名清洗，不改变朋友圈文案。"""

import unicodedata

import emoji

from wemo2.common.windows_names import sanitize_directory_name


def normalize_person_with_raw(
    person: str | None, raw: str | None
) -> tuple[str | None, str | None]:
    """清洗人物名并保留同一人物的解析原文，字段改变时避免遗留不相符的旧原文。"""
    normalized = normalize_person(person)
    if raw is None or normalize_person(raw) != normalized:
        raw = person
    return normalized, raw


def normalize_person(value: str | None) -> str | None:
    """去除完整 emoji 序列及残留显示控制符，保留文字、数字和普通标点。"""
    if value is None:
        return None
    value = emoji.replace_emoji(value, replace="")
    for marker in ("\ufe0e", "\ufe0f", "\u200d"):
        value = value.replace(marker, "")
    value = " ".join(unicodedata.normalize("NFC", value).split())
    return value or None


def person_directory(value: str | None) -> str:
    """将清洗后的人名用作单层目录，替换非法字符并避开 Windows 保留名。"""
    person = normalize_person(value)
    if person is None:
        return "unknown_person"
    return sanitize_directory_name(person, "unknown_person")
