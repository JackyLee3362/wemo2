"""CLI 运行边界：文件日志、业务服务调用、Rich 展示及失败退出码。"""

from pathlib import Path

from loguru import logger
from rich.text import Text

from wemo2.cli.console import error_console, show_collect_result, show_save_result
from wemo2.common.logging import add_file_sink
from wemo2.core import services
from wemo2.core.config import AppConfig
from wemo2.core.registry import stores


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
            result = services.run_collect(config)
            show_collect_result(result, config.log_file)
        elif command == "save":
            saved = services.run_save(config, use_memos)
            show_save_result(saved, stores.get(saved.store).notice, config.log_file)
        else:
            raise ValueError(f"未知命令：{command}")
    except Exception as error:
        logger.exception("{} 执行失败，已完成的本地文件保留：{}", command, error)
        error_console.print(Text(f"执行失败：{error}", style="red"))
        error_console.print(Text(f"详细日志：{config.log_file}"))
        raise SystemExit(1) from None
