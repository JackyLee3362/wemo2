"""人物名规范化和 Windows 目录名清洗，不改变朋友圈文案。"""

import unicodedata

import emoji


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
    characters = []
    for character in person:
        if character in '<>:"/\\|?*' or ord(character) < 32:
            characters.append("_")
        else:
            characters.append(character)
    name = "".join(characters).rstrip(" .")
    if not name or name in (".", ".."):
        return "unknown_person"
    reserved = {"CON", "PRN", "AUX", "NUL"}
    for number in range(1, 10):
        reserved.add(f"COM{number}")
        reserved.add(f"LPT{number}")
    if name.split(".")[0].upper() in reserved:
        name = "_" + name
    return name
