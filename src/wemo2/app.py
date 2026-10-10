"""CLI 应用执行入口。"""

from loguru import logger
from rich.text import Text

from wemo2.composition import build_coordinator
from wemo2.config import AppConfig
from wemo2.console import error_console, show_result
from wemo2.models import RunResult


def import_json(path: str, database: str) -> RunResult:
    coordinator = build_coordinator(
        "json", "stable", "sqlite", json_path=path, database=database
    )
    with coordinator.store:
        return coordinator.run()


def run_demo() -> RunResult:
    coordinator = build_coordinator()
    with coordinator.store:
        return coordinator.run()


def run_config(config: AppConfig) -> RunResult:
    """使用合并后的配置组装组件并执行一次流程。"""
    coordinator = build_coordinator(
        config.collector,
        config.deduplicator,
        config.store,
        json_path=config.json_path,
        database=config.database,
    )
    with coordinator.store:
        return coordinator.run()


def launch(config: AppConfig) -> None:
    """终端展示交互结果，运行日志及异常详情写入文件。"""
    logger.remove()
    try:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        sink = logger.add(
            config.log_file,
            level=config.log_level,
            encoding="utf-8",
            rotation="10 MB",
            retention="7 days",
            diagnose=False,
            backtrace=False,
        )
    except (OSError, ValueError) as error:
        error_console.print(Text(f"无法初始化日志文件：{error}", style="red"))
        raise SystemExit(1) from None
    try:
        result = run_config(config)
        show_result(result, config.store, config.log_file)
    except Exception as error:
        logger.exception("流程执行失败")
        error_console.print(Text(f"执行失败：{error}", style="red"))
        error_console.print(Text(f"详细日志：{config.log_file}"))
        raise SystemExit(1) from None
    finally:
        logger.remove(sink)


def main() -> None:
    from wemo2.cli import cli

    cli()
