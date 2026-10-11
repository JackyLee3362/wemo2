"""Typer 两阶段命令入口，复杂设置从 TOML 读取。"""

from pathlib import Path

import typer
from loguru import logger
from pydantic import BaseModel

from wemo2 import __version__
from wemo2.cli.console import console
from wemo2.common.logging import initialize_startup_logging
from wemo2.core.config import load_config

cli = typer.Typer(
    help="wemo2 朋友圈采集与保存工具",
    add_completion=False,
    pretty_exceptions_enable=False,
)


class CliOptions(BaseModel):
    """根命令的显式选项，仅保存参数，帮助和版本阶段不读取文件或创建日志。"""

    config: Path | None = None
    log_file: Path | None = None


def show_version(value: bool) -> None:
    """提前显示版本并退出，不连接微信或存储器。"""
    if value:
        console.print(f"wemo2 {__version__}")
        raise typer.Exit()


@cli.callback(invoke_without_command=True)
def entry(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None, "--config", help="TOML 文件，默认尝试 wemo2.toml"
    ),
    log_file: Path | None = typer.Option(
        None, "--log-file", help="同时写入日志文件，默认只输出控制台"
    ),
    version: bool = typer.Option(
        False, "--version", callback=show_version, is_eager=True, help="显示版本并退出"
    ),
) -> None:
    """保存共用 CLI 选项，未选择子命令时只显示帮助。"""
    ctx.obj = CliOptions(config=config, log_file=log_file)
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())


def _execute(
    ctx: typer.Context,
    command: str,
    config: Path | None,
    log_file: Path | None,
    use_memos: bool = False,
) -> None:
    root = ctx.obj or CliOptions()
    if config is None:
        config = root.config
    if log_file is None:
        log_file = root.log_file
    overrides = {}
    if log_file is not None:
        overrides["log_file"] = log_file
    startup_sink = initialize_startup_logging()
    try:
        try:
            settings = load_config(overrides, config)
        except (OSError, ValueError, TypeError) as error:
            logger.exception("配置加载失败，停止执行命令：{}", error)
            raise typer.BadParameter(f"配置无效：{error}") from error
        # 执行层只在实际命令启动时导入，帮助和版本不会运行采集或保存。
        from wemo2.cli.runtime import launch

        launch(
            command,
            settings,
            use_memos,
            startup_sink=startup_sink,
        )
    finally:
        logger.remove()


@cli.command()
def collect(
    ctx: typer.Context,
    config: Path | None = typer.Option(None, "--config", help="TOML 配置文件"),
    log_file: Path | None = typer.Option(
        None, "--log-file", help="同时写入日志文件，默认只输出控制台"
    ),
) -> None:
    """采集朋友圈并按账号、日期、人物文案哈希去重归档。"""
    _execute(ctx, "collect", config, log_file)


@cli.command()
def save(
    ctx: typer.Context,
    memos: bool = typer.Option(
        False, "--memos", help="Memos 目标，当前用 pseudo 演示，尚未上传"
    ),
    config: Path | None = typer.Option(None, "--config", help="TOML 配置文件"),
    log_file: Path | None = typer.Option(
        None, "--log-file", help="同时写入日志文件，默认只输出控制台"
    ),
) -> None:
    """读取已采集的朋友圈文件夹并交给存储器，不启动微信。"""
    _execute(ctx, "save", config, log_file, memos)


def main() -> None:
    """启动命令行，与根 main.py 和安装后的 wemo2 命令共用入口。"""
    cli()
