"""共用 Windows 单层目录名规则；账号严格校验，人物目录按需清洗。"""

INVALID_CHARACTERS = '<>:"/\\|?*'
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"}
for number in range(1, 10):
    RESERVED_NAMES.add(f"COM{number}")
    RESERVED_NAMES.add(f"LPT{number}")


def is_reserved_name(value: str) -> bool:
    """检查扩展名前的设备名，Windows 设备名不区分大小写。"""
    return value.split(".")[0].upper() in RESERVED_NAMES


def is_invalid_character(character: str) -> bool:
    """识别路径分隔符、Windows 禁用标点和 ASCII 控制字符。"""
    return character in INVALID_CHARACTERS or ord(character) < 32


def validate_directory_name(value: str, label: str = "目录") -> str:
    """严格校验用户配置的目录名，不默默改写账号身份或创建多层路径。"""
    if not value or value in (".", "..") or value.rstrip(" .") != value:
        raise ValueError(f"{label}名不能为空，也不能以空格或点结尾")
    if any(is_invalid_character(character) for character in value):
        raise ValueError(f"{label}名不能包含路径分隔符或 Windows 非法字符")
    if is_reserved_name(value):
        raise ValueError(f"{label}名不能使用 Windows 保留设备名")
    return value


def sanitize_directory_name(value: str, fallback: str) -> str:
    """替换解析文本的非法字符，空结果用指定占位名，保留名加下划线前缀。"""
    characters = []
    for character in value:
        if is_invalid_character(character):
            characters.append("_")
        else:
            characters.append(character)
    name = "".join(characters).rstrip(" .")
    if not name or name in (".", ".."):
        return fallback
    if is_reserved_name(name):
        name = "_" + name
    return name
