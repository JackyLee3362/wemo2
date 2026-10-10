"""Rich 终端展示；运行日志由 Loguru 单独写入文件。"""

from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from wemo2.models import RunResult

console = Console()
error_console = Console(stderr=True)


def show_result(result: RunResult, storage_notice: str | None, log_file: Path) -> None:
    table = Table(title="采集完成")
    table.add_column("读取", justify="right")
    table.add_column("新增", justify="right")
    table.add_column("更新", justify="right")
    table.add_column("未变更", justify="right")
    table.add_column("重复", justify="right")
    table.add_row(
        str(result.collected),
        str(result.created),
        str(result.merged),
        str(result.unchanged),
        str(result.duplicates),
    )
    console.print(table)
    for path in result.artifacts:
        console.print(Text(f"截图已保存：{path}"))
    if result.stop_reason == "duplicate_limit":
        console.print("已按重复停止策略结束采集。", style="yellow")
    if storage_notice is not None:
        console.print(storage_notice, style="yellow")
    console.print(Text(f"日志文件：{log_file}"))
