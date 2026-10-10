"""Tkinter 与命令行入口。"""

import argparse
import sys
import tkinter as tk
from queue import Empty, Queue
from threading import Thread
from tkinter import filedialog, ttk

from loguru import logger

from wemo2 import __version__
from wemo2.composition import build_coordinator
from wemo2.models import RunResult


def import_json(path: str, database: str) -> RunResult:
    coordinator = build_coordinator("json", "stable", "sqlite", json_path=path, database=database)
    with coordinator.store:
        return coordinator.run()


def run_demo() -> RunResult:
    coordinator = build_coordinator()
    with coordinator.store:
        return coordinator.run()


def create_window(database: str = "data/wemo2.sqlite3") -> tk.Tk:
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
    ttk.Label(frame, text=f"当前本地存储：{database}").pack(anchor="w", pady=4)
    status = tk.StringVar(value="选择 JSON 导入；Windows 采集和 Memos 同步待接入。")
    ttk.Label(frame, textvariable=status, wraplength=560).pack(anchor="w", pady=12)
    messages: Queue[str] = Queue()

    def worker(path: str | None) -> None:
        try:
            result = import_json(path, database) if path is not None else run_demo()
            messages.put(
                ("模拟流程（内存）：" if path is None else "导入完成：")
                + f"读取 {result.collected} 条，新增 {result.created} 条，"
                f"更新 {result.merged} 条，未变更 {result.unchanged} 条"
            )
        except Exception as error:
            logger.exception("导入失败")
            messages.put(f"导入失败：{error}。已提交记录保留，可重试。")

    def choose_file() -> None:
        path = filedialog.askopenfilename(
            parent=root, title="选择采集记录", filetypes=[("JSON 文件", "*.json")]
        )
        if path:
            start(path)

    def start(path: str | None = None) -> None:
        import_button.configure(state="disabled")
        demo_button.configure(state="disabled")
        close_button.configure(state="disabled")
        root.protocol("WM_DELETE_WINDOW", lambda: None)
        status.set("正在采集、去重并保存…")
        Thread(target=worker, args=(path,), daemon=False).start()
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
    demo_button = ttk.Button(frame, text="运行模拟流程", command=start)
    demo_button.pack(anchor="w", pady=8)
    close_button = ttk.Button(frame, text="关闭", command=root.destroy)
    close_button.pack(anchor="e")
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="wemo2 朋友圈备份工具")
    parser.add_argument("--version", action="version", version=f"wemo2 {__version__}")
    parser.add_argument("--import-json", metavar="PATH", help="导入 JSON 文件后退出")
    parser.add_argument("--collector", choices=("pseudo", "json"), help="选择采集器并执行")
    parser.add_argument("--deduplicator", choices=("pseudo", "stable"), help="选择去重器")
    parser.add_argument("--store", choices=("pseudo", "sqlite"), help="选择存储器")
    parser.add_argument(
        "--database", default="data/wemo2.sqlite3", help="SQLite 文件路径"
    )
    args = parser.parse_args()
    collector = args.collector or ("json" if args.import_json else None)
    if collector == "json" and not args.import_json:
        parser.error("JSON 采集器需要 --import-json PATH")
    if collector == "pseudo" and args.import_json:
        parser.error("--collector pseudo 不能同时指定 --import-json")
    if collector is None and (args.deduplicator or args.store):
        parser.error("请选择 --collector 或提供 --import-json")

    logger.remove()
    logger.add(sys.stderr, level="INFO")
    if collector is not None:
        try:
            coordinator = build_coordinator(
                collector,
                args.deduplicator or ("pseudo" if collector == "pseudo" else "stable"),
                args.store or ("pseudo" if collector == "pseudo" else "sqlite"),
                json_path=args.import_json, database=args.database,
            )
            with coordinator.store:
                result = coordinator.run()
        except Exception:
            logger.exception("导入失败，已提交记录保留，可重试")
            raise SystemExit(1) from None
        print(result.model_dump_json())
        return
    logger.info("启动 wemo2 {}", __version__)
    try:
        root = create_window(args.database)
        root.mainloop()
    except tk.TclError:
        logger.exception("无法启动桌面窗口，请检查 Python 的 Tk 支持及桌面环境")
        raise SystemExit(1) from None
    logger.info("wemo2 已退出")
