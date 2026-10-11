"""独立执行采集或保存；文件夹是两阶段之间唯一的数据交付。"""

from pathlib import Path

from loguru import logger
from rich.text import Text

from wemo2.archive_reader import ArchiveReader
from wemo2.config import AppConfig
from wemo2.console import error_console, show_collect_result, show_save_result
from wemo2.logging import add_file_sink
from wemo2.models import CollectResult, SaveResult
from wemo2.registry import MEMOS_STORE, collectors, stores


def run_collect(config: AppConfig) -> CollectResult:
    """只实例化采集器，由采集器完成本地归档和去重，不创建存储器。"""
    collector = collectors.create(config.collector, config.model_dump())
    try:
        return collector.collect()
    finally:
        collector.stop()


def run_save(config: AppConfig, use_memos: bool = False) -> SaveResult:
    """读取配置账号的归档，交给存储器；不实例化或操作微信采集器。"""
    name = MEMOS_STORE if use_memos else config.store
    store = stores.create(name, config.model_dump())
    result = SaveResult(store=name)
    try:
        reader = ArchiveReader(config.resource_dir, config.account)
        for post in reader.read():
            store.save(post)
            result.posts += 1
            result.media += len(post.media_files)
        return result
    finally:
        store.close()


def launch(
    command: str,
    config: AppConfig,
    use_memos: bool = False,
    *,
    startup_sink: int | None = None,
    startup_log: Path | None = None,
) -> None:
    """建立正式日志后执行命令，失败退出非零，终端不展示异常堆栈。"""
    try:
        add_file_sink(config.log_file, config.log_level)
    except (OSError, ValueError) as error:
        logger.exception("无法初始化运行日志文件，保留启动日志：{}", error)
        error_console.print(Text(f"无法初始化日志文件：{error}", style="red"))
        if startup_log is not None:
            error_console.print(Text(f"详细日志：{startup_log}"))
        raise SystemExit(1) from None
    if startup_sink is not None:
        logger.remove(startup_sink)
    try:
        if command == "collect":
            result = run_collect(config)
            show_collect_result(result, config.log_file)
        elif command == "save":
            saved = run_save(config, use_memos)
            show_save_result(saved, stores.get(saved.store).notice, config.log_file)
        else:
            raise ValueError(f"未知命令：{command}")
    except Exception as error:
        logger.exception("{} 执行失败，已完成的本地文件保留：{}", command, error)
        error_console.print(Text(f"执行失败：{error}", style="red"))
        error_console.print(Text(f"详细日志：{config.log_file}"))
        raise SystemExit(1) from None


def main() -> None:
    """延迟导入 Typer，保持帮助与版本命令没有执行副作用。"""
    from wemo2.cli import cli

    cli()
