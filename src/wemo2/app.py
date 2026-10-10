"""CLI 应用执行入口。"""

import sys

from loguru import logger

from wemo2.composition import build_coordinator
from wemo2.config import AppConfig
from wemo2.models import RunResult


def import_json(path: str, database: str) -> RunResult:
    coordinator = build_coordinator("json", "stable", "sqlite", json_path=path, database=database)
    with coordinator.store:
        return coordinator.run()


def run_demo() -> RunResult:
    coordinator = build_coordinator()
    with coordinator.store:
        return coordinator.run()


def run_config(config: AppConfig) -> RunResult:
    """使用合并后的配置组装组件并执行一次流程。"""
    coordinator = build_coordinator(
        config.collector, config.deduplicator, config.store,
        json_path=config.json_path, database=config.database,
    )
    with coordinator.store:
        return coordinator.run()


def launch(config: AppConfig) -> None:
    """日志输出到 stderr，成功结果以 JSON 输出到 stdout。"""
    logger.remove()
    sink = logger.add(sys.stderr, level=config.log_level)
    try:
        try:
            result = run_config(config)
        except Exception:
            logger.exception("流程执行失败")
            raise SystemExit(1) from None
        print(result.model_dump_json())
    finally:
        logger.remove(sink)


def main() -> None:
    from wemo2.cli import cli

    cli()
