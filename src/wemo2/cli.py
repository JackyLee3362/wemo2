"""Typer 命令行入口；只把显式选项交给配置加载器。"""

from enum import StrEnum
from pathlib import Path

import typer

from wemo2 import __version__
from wemo2.config import load_config
from wemo2.console import console


class CollectorName(StrEnum):
    pseudo = "pseudo"
    json = "json"


class DeduplicatorName(StrEnum):
    pseudo = "pseudo"
    stable = "stable"


class StoreName(StrEnum):
    pseudo = "pseudo"
    sqlite = "sqlite"


class LogLevel(StrEnum):
    trace = "TRACE"
    debug = "DEBUG"
    info = "INFO"
    success = "SUCCESS"
    warning = "WARNING"
    error = "ERROR"
    critical = "CRITICAL"


cli = typer.Typer(
    help="wemo2 朋友圈备份工具", add_completion=False, pretty_exceptions_enable=False
)


def show_version(value: bool) -> None:
    if value:
        console.print(f"wemo2 {__version__}")
        raise typer.Exit()


@cli.command()
def start(
    config: Path | None = typer.Option(
        None, "--config", help="TOML 文件，默认尝试 wemo2.toml"
    ),
    collector: CollectorName | None = typer.Option(None, help="选择采集器"),
    deduplicator: DeduplicatorName | None = typer.Option(None, help="选择去重器"),
    store: StoreName | None = typer.Option(None, help="选择存储器"),
    json_path: Path | None = typer.Option(None, "--import-json", help="JSON 输入文件"),
    database: Path | None = typer.Option(None, help="SQLite 文件路径"),
    log_level: LogLevel | None = typer.Option(None, help="日志级别"),
    log_file: Path | None = typer.Option(None, help="日志文件路径"),
    version: bool = typer.Option(
        False, "--version", callback=show_version, is_eager=True, help="显示版本并退出"
    ),
) -> None:
    # None 表示未指定，不能用 CLI 默认值覆盖文件配置。
    values = {}
    if collector is not None:
        values["collector"] = collector.value
    if deduplicator is not None:
        values["deduplicator"] = deduplicator.value
    if store is not None:
        values["store"] = store.value
    if json_path is not None:
        values["json_path"] = json_path
    if database is not None:
        values["database"] = database
    if log_level is not None:
        values["log_level"] = log_level.value
    if log_file is not None:
        values["log_file"] = log_file
    if json_path is not None:
        if collector == CollectorName.pseudo:
            raise typer.BadParameter("--collector pseudo 不能同时指定 --import-json")
        if collector is None:
            values["collector"] = "json"
    try:
        settings = load_config(values, config)
    except (OSError, ValueError, TypeError) as error:
        raise typer.BadParameter(str(error)) from error

    # 延迟导入执行层，--help/--version 不会启动采集流程。
    from wemo2.app import launch

    launch(settings)
