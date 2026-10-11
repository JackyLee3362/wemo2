"""采集器独立调试入口，只运行采集器，不创建去重器或存储器。"""

from pathlib import Path

from loguru import logger
from rich.text import Text

from wemo2.config import AppConfig, load_config
from wemo2.console import console, error_console
from wemo2.registry import collectors


def run_collector(config: AppConfig) -> list[Path]:
    """执行所选采集器并返回截图路径，调试时不持久化动态记录。"""
    collector = collectors.create(config.collector, config.model_dump())
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
    config = load_config(config_path=config_path)
    logger.remove()
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
    try:
        paths = run_collector(config)
        console.print(Text("当前只调试界面采集，页面验证结果见日志，尚不解析动态。"))
        for path in paths:
            console.print(Text(f"证据已保存：{path}"))
        console.print(Text(f"日志文件：{config.log_file}"))
    except Exception as error:
        logger.exception("采集器调试失败")
        error_console.print(Text(f"采集器调试失败：{error}", style="red"))
        error_console.print(Text(f"详细日志：{config.log_file}"))
        raise SystemExit(1) from None
    finally:
        logger.remove(sink)


if __name__ == "__main__":
    main()
