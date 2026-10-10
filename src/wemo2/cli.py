"""Typer 命令行入口；只把显式选项交给配置加载器。"""

from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from wemo2 import __version__
from wemo2.config import load_config


class CollectorName(str, Enum):
    pseudo = "pseudo"
    json = "json"


class DeduplicatorName(str, Enum):
    pseudo = "pseudo"
    stable = "stable"


class StoreName(str, Enum):
    pseudo = "pseudo"
    sqlite = "sqlite"


class LogLevel(str, Enum):
    trace = "TRACE"
    debug = "DEBUG"
    info = "INFO"
    success = "SUCCESS"
    warning = "WARNING"
    error = "ERROR"
    critical = "CRITICAL"


cli = typer.Typer(help="wemo2 朋友圈备份工具", add_completion=False, pretty_exceptions_enable=False)


def show_version(value: bool) -> None:
    if value:
        typer.echo(f"wemo2 {__version__}")
        raise typer.Exit()


@cli.command()
def start(
    config: Annotated[Path | None, typer.Option("--config", help="TOML 文件，默认尝试 wemo2.toml")] = None,
    collector: Annotated[CollectorName | None, typer.Option(help="选择采集器")] = None,
    deduplicator: Annotated[DeduplicatorName | None, typer.Option(help="选择去重器")] = None,
    store: Annotated[StoreName | None, typer.Option(help="选择存储器")] = None,
    json_path: Annotated[Path | None, typer.Option("--import-json", help="JSON 输入文件")] = None,
    database: Annotated[Path | None, typer.Option(help="SQLite 文件路径")] = None,
    log_level: Annotated[LogLevel | None, typer.Option(help="日志级别")] = None,
    version: Annotated[bool, typer.Option("--version", callback=show_version, is_eager=True, help="显示版本并退出")] = False,
) -> None:
    # None 表示未指定，不能用 CLI 默认值覆盖文件配置。
    values = {
        key: value.value if isinstance(value, Enum) else value
        for key, value in {
            "collector": collector, "deduplicator": deduplicator,
            "store": store, "json_path": json_path, "database": database, "log_level": log_level,
        }.items() if value is not None
    }
    if json_path is not None:
        if collector == CollectorName.pseudo:
            raise typer.BadParameter("--collector pseudo 不能同时指定 --import-json")
        values.setdefault("collector", "json")
    try:
        settings = load_config(values, config)
    except (OSError, ValueError, TypeError) as error:
        raise typer.BadParameter(str(error)) from error

    # 延迟导入执行层，--help/--version 不会启动采集流程。
    from wemo2.app import launch

    launch(settings)
