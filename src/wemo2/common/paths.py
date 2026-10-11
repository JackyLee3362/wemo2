"""相对路径解析及资源范围检查，不包含归档目录布局或界面模型。"""

from pathlib import Path


def resolve_path(value: str | Path, directory: Path) -> Path:
    """相对路径以指定目录为基准，绝对路径保持自身基准并规范化。"""
    path = Path(value)
    if not path.is_absolute():
        path = directory / path
    return path.resolve()


def require_within(directory: Path, target: Path) -> Path:
    """校验解析符号链接后的目标仍处于指定目录，供读写和迁移共用。"""
    resolved = target.resolve()
    if not resolved.is_relative_to(directory.resolve()):
        raise ValueError("资源路径超出指定目录范围")
    return resolved


def resolve_resource_path(directory: Path, relative: str) -> Path:
    """只接受非空相对资源引用，拒绝绝对路径、盘符及目录穿越。"""
    path = Path(relative)
    if not relative or path.is_absolute() or path.drive:
        raise ValueError("归档资源路径必须是非空相对路径")
    return require_within(directory, directory / path)
