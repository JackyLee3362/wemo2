"""CLI 应用执行入口。"""

from loguru import logger
from rich.text import Text

from wemo2.composition import build_coordinator
from wemo2.config import AppConfig
from wemo2.console import error_console, show_result
from wemo2.logging import add_file_sink, initialize_startup_logging
from wemo2.models import RunResult, StopPolicy
from wemo2.registry import IMPORT_COLLECTOR, collectors, stores


def import_json(path: str, database: str) -> RunResult:
    defaults = collectors.get(IMPORT_COLLECTOR).defaults
    coordinator = build_coordinator(
        IMPORT_COLLECTOR,
        defaults["deduplicator"],
        defaults["store"],
        collector_options={"path": path},
        database=database,
    )
    with coordinator.store:
        return coordinator.run()


def run_demo() -> RunResult:
    coordinator = build_coordinator()
    with coordinator.store:
        return coordinator.run()


def run_config(config: AppConfig) -> RunResult:
    """使用合并后的配置组装组件并执行一次流程。"""
    policy = StopPolicy(
        duplicate_stop=config.duplicate_stop, duplicate_limit=config.duplicate_limit
    )
    coordinator = build_coordinator(
        config.collector,
        config.deduplicator,
        config.store,
        collector_options=config.collector_options,
        config_directory=config.config_directory,
        database=config.database,
        stop_policy=policy,
    )
    with coordinator.store:
        return coordinator.run()


def launch(config: AppConfig) -> None:
    """终端展示交互结果，运行日志及异常详情写入文件。"""
    startup_sink, startup_log = initialize_startup_logging()
    try:
        try:
            add_file_sink(config.log_file, config.log_level)
        except (OSError, ValueError) as error:
            logger.exception("无法初始化运行日志文件 {}：{}", config.log_file, error)
            error_console.print(Text(f"无法初始化日志文件：{error}", style="red"))
            error_console.print(Text(f"详细日志：{startup_log}"))
            raise SystemExit(1) from None
        logger.remove(startup_sink)
        try:
            result = run_config(config)
            storage_notice = stores.get(config.store).notice
            show_result(result, storage_notice, config.log_file)
        except Exception as error:
            logger.exception("流程执行失败：{}", error)
            error_console.print(Text(f"执行失败：{error}", style="red"))
            error_console.print(Text(f"详细日志：{config.log_file}"))
            raise SystemExit(1) from None
    finally:
        logger.remove()


def main() -> None:
    from wemo2.cli import cli

    cli()
