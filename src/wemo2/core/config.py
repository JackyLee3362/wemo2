"""两阶段命令的启动配置：命令行显式参数 > TOML > 代码默认值。"""

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wemo2.common.paths import resolve_path
from wemo2.common.windows_names import validate_directory_name
from wemo2.core.registry import collectors, stores


class AppConfig(BaseModel):
    """共用资源根目录和账号，专属界面参数只转交采集器的注册加载函数。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    collector: str = Field(default_factory=collectors.default_name)
    collector_options: dict[str, Any] = Field(default_factory=dict)
    store: str = Field(default_factory=stores.default_name)
    resource_dir: Path = Path("data/moments")
    account: str = "default"
    config_directory: Path = Field(default_factory=Path.cwd)
    log_file: Path = Path("logs/wemo2.log")
    log_level: Literal[
        "TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"
    ] = "INFO"

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str) -> str:
        """账号用作独立的 Windows 目录名，禁止越界路径及保留设备名。"""
        return validate_directory_name(value, "账号目录")

    @model_validator(mode="after")
    def validate_components(self):
        """按唯一注册表校验名称和专属配置，不自行维护实现名称枚举。"""
        settings = self.model_dump()
        collectors.validate(self.collector, settings)
        stores.validate(self.store, settings)
        return self


def _resolve_paths(values: dict, directory: Path) -> dict:
    result = dict(values)
    for key in ("resource_dir", "log_file"):
        value = result.get(key)
        if isinstance(value, (str, Path)):
            result[key] = resolve_path(value, directory)
    return result


def _migrate_resource_options(values: dict) -> dict:
    """兼容原 collector_options 中的资源目录和账号，统一为两命令共享的顶层字段。"""
    result = dict(values)
    options = dict(result.get("collector_options", {}))
    for key in ("resource_dir", "account"):
        if key in options:
            value = options.pop(key)
            if key in result and result[key] != value:
                raise ValueError(f"{key} 在顶层和 collector_options 中配置冲突")
            result[key] = value
    if "collector_options" in result:
        result["collector_options"] = options
    return result


def load_config(
    overrides: dict | None = None,
    config_path: str | Path | None = None,
    *,
    cwd: Path | None = None,
) -> AppConfig:
    """共享配置供两个命令使用；资源路径相对配置文件，CLI 日志路径相对工作目录。"""
    directory = (cwd or Path.cwd()).resolve()
    path = directory / (config_path or "wemo2.toml")
    file_values = {}
    config_directory = directory
    if config_path is not None or path.exists():
        with path.open("rb") as handle:
            file_values = _migrate_resource_options(tomllib.load(handle))
        config_directory = path.resolve().parent
        file_values = _resolve_paths(file_values, config_directory)
    values = _resolve_paths(AppConfig().model_dump(), directory)
    values.update(file_values)
    values.update(_resolve_paths(overrides or {}, directory))
    values["config_directory"] = config_directory
    return AppConfig.model_validate(values)
