"""采集器独立调试入口，只运行采集器，不创建去重器或存储器。"""

from pathlib import Path

from loguru import logger
from rich.text import Text

from wemo2.collector import Collector
from wemo2.config import AppConfig, load_config
from wemo2.console import console, error_console
from wemo2.logging import add_file_sink, initialize_startup_logging
from wemo2.registry import collectors


def run_collector(config: AppConfig) -> list[Path]:
    """执行所选采集器并返回截图路径，调试时不持久化动态记录。"""
    collector: Collector = collectors.create(config.collector, config.model_dump())
    try:
        # 消费完整迭代器，兼容返回列表和逐条产出记录的采集器。
        for _ in collector.collect():
            pass
        return collector.artifacts()
    finally:
        collector.stop()


def main(config_path: Path | None = None) -> None:
    """读取项目根目录的截图配置，便于 VS Code 直接启动。"""
    if config_path is None:
        config_path = Path(__file__).resolve().parents[2] / "wemo2.screenshot.toml"
    startup_sink, startup_log = initialize_startup_logging()
    try:
        try:
            config = load_config(config_path=config_path)
            add_file_sink(config.log_file, config.log_level)
        except (OSError, ValueError, TypeError) as error:
            logger.exception("采集器调试配置或日志初始化失败：{}", error)
            error_console.print(Text(f"采集器启动失败：{error}", style="red"))
            error_console.print(Text(f"详细日志：{startup_log}"))
            raise SystemExit(1) from None
        logger.remove(startup_sink)
        try:
            paths = run_collector(config)
            console.print(
                Text(
                    "界面采集已完成，截图、识别及媒体结果见证据清单；尚未接入动态持久化。"
                )
            )
            for path in paths:
                console.print(Text(f"证据已保存：{path}"))
            console.print(Text(f"日志文件：{config.log_file}"))
        except Exception as error:
            logger.exception("采集器调试失败：{}", error)
            error_console.print(Text(f"采集器调试失败：{error}", style="red"))
            error_console.print(Text(f"详细日志：{config.log_file}"))
            raise SystemExit(1) from None
    finally:
        logger.remove()


if __name__ == "__main__":
    main()
