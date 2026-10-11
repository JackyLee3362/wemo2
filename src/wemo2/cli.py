"""Typer 命令行入口；只把显式选项交给配置加载器。"""

from pathlib import Path

import typer
from loguru import logger

from wemo2 import __version__
from wemo2.config import load_config
from wemo2.console import console
from wemo2.logging import initialize_startup_logging

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
    log_file: Path | None = typer.Option(None, help="日志文件路径"),
    version: bool = typer.Option(
        False, "--version", callback=show_version, is_eager=True, help="显示版本并退出"
    ),
) -> None:
    # None 表示未指定，不能用 CLI 默认值覆盖文件配置。
    values = {}
    if log_file is not None:
        values["log_file"] = log_file
    _, startup_log = initialize_startup_logging()
    try:
        try:
            settings = load_config(values, config)
        except (OSError, ValueError, TypeError) as error:
            logger.exception("配置加载失败：{}", error)
            raise typer.BadParameter(f"{error}；详细日志：{startup_log}") from error

        # 延迟导入执行层，--help/--version 不会启动采集或创建日志。
        from wemo2.app import launch

        launch(settings)
    finally:
        logger.remove()
