"""Tkinter 与命令行入口。"""

import argparse
import json
import sys
import tkinter as tk
from dataclasses import asdict
from queue import Empty, Queue
from threading import Thread
from tkinter import filedialog, ttk

from loguru import logger

from wemo2 import __version__
from wemo2.collector import JsonCollector
from wemo2.coordinator import Coordinator, RunResult
from wemo2.deduplicator import Deduplicator
from wemo2.store import SQLiteStore


def import_json(path: str, database: str) -> RunResult:
    with SQLiteStore(database) as store:
        return Coordinator(JsonCollector(path), Deduplicator(), store).run()


def create_window(database: str = "data/wemo2.sqlite3") -> tk.Tk:
    root = tk.Tk()
    root.title("wemo2 · 朋友圈备份")
    root.geometry("620x380")
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

    def worker(path: str) -> None:
        try:
            result = import_json(path, database)
            messages.put(
                f"读取 {result.collected} 条，新增 {result.created} 条，"
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
            import_button.configure(state="disabled")
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
        close_button.configure(state="normal")
        root.protocol("WM_DELETE_WINDOW", root.destroy)

    import_button = ttk.Button(frame, text="导入 JSON", command=choose_file)
    import_button.pack(anchor="w")
    close_button = ttk.Button(frame, text="关闭", command=root.destroy)
    close_button.pack(anchor="e")
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="wemo2 朋友圈备份工具")
    parser.add_argument("--version", action="version", version=f"wemo2 {__version__}")
    parser.add_argument("--import-json", metavar="PATH", help="导入 JSON 文件后退出")
    parser.add_argument(
        "--database", default="data/wemo2.sqlite3", help="SQLite 文件路径"
    )
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO")
    if args.import_json:
        try:
            result = import_json(args.import_json, args.database)
        except Exception:
            logger.exception("导入失败，已提交记录保留，可重试")
            raise SystemExit(1) from None
        print(json.dumps(asdict(result), ensure_ascii=False))
        return
    logger.info("启动 wemo2 {}", __version__)
    try:
        root = create_window(args.database)
        root.mainloop()
    except tk.TclError:
        logger.exception("无法启动桌面窗口，请检查 Python 的 Tk 支持及桌面环境")
        raise SystemExit(1) from None
    logger.info("wemo2 已退出")
