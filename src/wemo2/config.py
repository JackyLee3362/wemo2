"""启动配置：命令行显式选项 > TOML 文件 > 代码默认值。"""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator



class AppConfig(BaseModel):
    """应用启动设置；未知键报错，防止配置拼写错误被悄悄忽略。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["gui", "cli"] = Field(
        default="gui", description="桌面模式或执行后退出"
    )
    collector: Literal["pseudo", "json"] = Field(
        default="pseudo", description="采集器实现"
    )
    deduplicator: Literal["pseudo", "stable"] = Field(
        default="pseudo", description="去重器实现"
    )
    store: Literal["pseudo", "sqlite"] = Field(
        default="pseudo", description="存储器实现"
    )
    json_path: Path | None = Field(default=None, description="JSON 采集器的输入文件")
    database: Path = Field(
        default=Path("data/wemo2.sqlite3"), description="SQLite 数据库文件"
    )
    log_level: Literal[
        "TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"
    ] = "INFO"

    @model_validator(mode="after")
    def require_cli_input(self):
        if self.mode == "cli" and self.collector == "json" and self.json_path is None:
            raise ValueError("命令行 JSON 采集器需要 json_path 或 --import-json PATH")
        return self


def _resolve_paths(values: dict, directory: Path) -> dict:
    result = dict(values)
    for key in ("database", "json_path"):
        value = result.get(key)
        if isinstance(value, (str, Path)):
            path = Path(value)
            result[key] = (
                (directory / path).resolve() if not path.is_absolute() else path
            )
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
    explicit = file_values | _resolve_paths(overrides or {}, directory)
    # 保留 JSON 导入的原有默认组合；任何显式设置（即便等于默认值）都优先。
    defaults = AppConfig().model_dump()
    if explicit.get("collector", defaults["collector"]) == "json":
        defaults.update(deduplicator="stable", store="sqlite")
    return AppConfig.model_validate(_resolve_paths(defaults, directory) | explicit)


