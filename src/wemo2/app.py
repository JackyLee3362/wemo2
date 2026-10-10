"""Tkinter 与命令行入口。"""

import sys
import tkinter as tk
from queue import Empty, Queue
from threading import Thread
from tkinter import filedialog, ttk

from loguru import logger

from wemo2 import __version__
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
    """命令行和桌面操作共用合并后的配置。"""
    coordinator = build_coordinator(
        config.collector, config.deduplicator, config.store,
        json_path=config.json_path, database=config.database,
    )
    with coordinator.store:
        return coordinator.run()


def create_window(database: str | None = None, *, config: AppConfig | None = None) -> tk.Tk:
    config = config or AppConfig()
    if database is not None:
        config = AppConfig.model_validate(config.model_dump() | {"database": database})
    root = tk.Tk()
    root.title("wemo2 · 朋友圈备份")
    root.geometry("620x420")
    root.minsize(480, 260)

    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="wemo2", font=("Microsoft YaHei UI", 20)).pack(anchor="w")
    ttk.Label(frame, text="朋友圈备份工具", padding=(0, 8)).pack(anchor="w")
    ttk.Separator(frame).pack(fill="x", pady=12)
    ttk.Label(frame, text="采集平台: Windows").pack(anchor="w", pady=4)
    ttk.Label(frame, text="持久化与展示: Memos").pack(anchor="w", pady=4)
    ttk.Label(frame, text=f"当前组合：{config.collector} / {config.deduplicator} / {config.store}").pack(anchor="w", pady=4)
    ttk.Label(frame, text=f"SQLite 路径：{config.database}").pack(anchor="w", pady=4)
    status = tk.StringVar(value="运行当前配置，或选择 JSON 文件后沿用当前去重器和存储器。")
    ttk.Label(frame, textvariable=status, wraplength=560).pack(anchor="w", pady=12)
    messages: Queue[str] = Queue()

    def worker(job: AppConfig) -> None:
        try:
            result = run_config(job)
            messages.put(
                ("完成（内存）：" if job.store == "pseudo" else "完成（SQLite）：")
                + f"读取 {result.collected} 条，新增 {result.created} 条，"
                f"更新 {result.merged} 条，未变更 {result.unchanged} 条"
            )
        except Exception as error:
            logger.exception("流程执行失败")
            messages.put(f"执行失败：{error}")

    def choose_file() -> None:
        path = filedialog.askopenfilename(
            parent=root, title="选择采集记录", filetypes=[("JSON 文件", "*.json")]
        )
        if path:
            start(path)

    def start(path: str | None = None) -> None:
        job = config
        if path is not None:
            job = AppConfig.model_validate(config.model_dump() | {"collector": "json", "json_path": path})
        elif config.collector == "json" and config.json_path is None:
            choose_file()
            return
        import_button.configure(state="disabled")
        demo_button.configure(state="disabled")
        close_button.configure(state="disabled")
        root.protocol("WM_DELETE_WINDOW", lambda: None)
        status.set("正在采集、去重并保存…")
        Thread(target=worker, args=(job,), daemon=False).start()
        root.after(100, poll)

    def poll() -> None:
        try:
            message = messages.get_nowait()
        except Empty:
            root.after(100, poll)
            return
        status.set(message)
        import_button.configure(state="normal")
        demo_button.configure(state="normal")
        close_button.configure(state="normal")
        root.protocol("WM_DELETE_WINDOW", root.destroy)

    import_button = ttk.Button(frame, text="导入 JSON", command=choose_file)
    import_button.pack(anchor="w")
    demo_button = ttk.Button(frame, text="运行当前配置", command=start)
    demo_button.pack(anchor="w", pady=8)
    close_button = ttk.Button(frame, text="关闭", command=root.destroy)
    close_button.pack(anchor="e")
    return root


def launch(config: AppConfig) -> None:
    """使用最终配置启动桌面或执行采集流程。"""
    logger.remove()
    logger.add(sys.stderr, level=config.log_level)
    if config.mode == "cli":
        try:
            result = run_config(config)
        except Exception:
            logger.exception("流程执行失败")
            raise SystemExit(1) from None
        print(result.model_dump_json())
        return
    logger.info("启动 wemo2 {}", __version__)
    try:
        root = create_window(config=config)
        root.mainloop()
    except tk.TclError:
        logger.exception("无法启动桌面窗口，请检查 Python 的 Tk 支持及桌面环境")
        raise SystemExit(1) from None
    logger.info("wemo2 已退出")


def main() -> None:
    from wemo2.cli import cli

    cli()
