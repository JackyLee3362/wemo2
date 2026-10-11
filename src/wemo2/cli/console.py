"""Rich 命令结果展示；Loguru 默认输出控制台，文件输出为可选项。"""

from pathlib import Path

from rich.console import Console
from rich.text import Text

from wemo2.api.models import CollectResult, SaveResult

console = Console()
error_console = Console(stderr=True)


def show_collect_result(result: CollectResult, log_file: Path | None) -> None:
    """展示本次归档数量、停止原因和文件位置，不能声称已经上传远端。"""
    console.print(
        Text(
            f"采集完成：本次归档 {result.posts} 条朋友圈，停止原因：{result.stop_reason}"
        )
    )
    for path in result.artifacts:
        if path.name == "post.json":
            console.print(Text(f"朋友圈目录：{path.parent}"))
        elif result.posts == 0:
            console.print(Text(f"采集文件已保存：{path}"))
    if log_file is not None:
        console.print(Text(f"日志文件：{log_file}"))


def show_save_result(
    result: SaveResult, notice: str | None, log_file: Path | None
) -> None:
    """说明目标存储和模拟状态，成功仅代表存储实现正常返回。"""
    console.print(
        Text(
            f"保存流程完成：{result.posts} 条朋友圈，{result.media} 个媒体文件，目标：{result.store}"
        )
    )
    if notice is not None:
        console.print(Text(notice, style="yellow"))
    if log_file is not None:
        console.print(Text(f"日志文件：{log_file}"))
