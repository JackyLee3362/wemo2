"""启动配置：命令行显式选项 > TOML 文件 > 代码默认值。"""

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from wemo2.models import StopPolicy
from wemo2.registry import collectors, deduplicators, stores


class AppConfig(StopPolicy):
    """应用启动设置；未知键报错，防止配置拼写错误被悄悄忽略。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    collector: str = Field(
        default_factory=collectors.default_name, description="采集器注册名称"
    )
    deduplicator: str = Field(
        default_factory=deduplicators.default_name, description="去重器注册名称"
    )
    store: str = Field(
        default_factory=stores.default_name, description="存储器注册名称"
    )
    collector_options: dict[str, Any] = Field(
        default_factory=dict, description="采集器专属设置，由注册的采集器自行校验"
    )
    config_directory: Path = Field(
        default_factory=Path.cwd, description="专属配置相对路径的基准目录"
    )
    database: Path = Field(
        default=Path("data/wemo2.sqlite3"), description="SQLite 数据库文件"
    )
    log_file: Path = Field(default=Path("logs/wemo2.log"), description="运行日志文件")
    log_level: Literal[
        "TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"
    ] = "INFO"

    @model_validator(mode="after")
    def validate_components(self):
        settings = self.model_dump()
        collectors.validate(self.collector, settings)
        deduplicators.validate(self.deduplicator, settings)
        stores.validate(self.store, settings)
        return self


def _resolve_paths(values: dict, directory: Path) -> dict:
    result = dict(values)
    for key in ("database", "log_file"):
        value = result.get(key)
        if isinstance(value, (str, Path)):
            path = Path(value)
            if not path.is_absolute():
                path = (directory / path).resolve()
            result[key] = path
    return result


def load_config(
    overrides: dict | None = None,
    config_path: str | Path | None = None,
    *,
    cwd: Path | None = None,
) -> AppConfig:
    """默认尝试 cwd/wemo2.toml；显式指定的文件不存在时直接报错。"""
    directory = (cwd or Path.cwd()).resolve()
    path = directory / (config_path or "wemo2.toml")
    file_values = {}
    if config_path is not None or path.exists():
        with path.open("rb") as handle:
            file_values = _resolve_paths(tomllib.load(handle), path.resolve().parent)
    command_values = _resolve_paths(overrides or {}, directory)
    explicit = dict(file_values)
    explicit.update(command_values)
    # 保留 JSON 导入的原有默认组合；任何显式设置（即便等于默认值）都优先。
    defaults = AppConfig().model_dump()
    collector_name = explicit.get("collector", defaults["collector"])
    defaults.update(collectors.get(collector_name).defaults)
    values = _resolve_paths(defaults, directory)
    values.update(file_values)
    values.update(command_values)
    values["config_directory"] = path.resolve().parent
    if config_path is None and not path.exists():
        values["config_directory"] = directory
    if "collector_options" in command_values:
        values["config_directory"] = directory
    return AppConfig.model_validate(values)
