"""Tkinter 应用入口；采集和 Memos 接入将在后续实现。"""

import argparse
import sys
import tkinter as tk
from tkinter import ttk

from loguru import logger

from wemo2 import __version__


def create_window() -> tk.Tk:
    root = tk.Tk()
    root.title("wemo2 · 朋友圈备份")
    root.geometry("560x300")
    root.minsize(480, 260)

    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="wemo2", font=("Microsoft YaHei UI", 20)).pack(anchor="w")
    ttk.Label(frame, text="朋友圈备份工具", padding=(0, 8)).pack(anchor="w")
    ttk.Separator(frame).pack(fill="x", pady=12)
    ttk.Label(frame, text="采集平台：Windows（后续支持 Android）").pack(anchor="w", pady=4)
    ttk.Label(frame, text="持久化与展示：Memos（待接入）").pack(anchor="w", pady=4)
    ttk.Label(frame, text="项目已初始化，采集与备份功能尚未实现。").pack(anchor="w", pady=12)
    ttk.Button(frame, text="关闭", command=root.destroy).pack(anchor="e")
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description="wemo2 朋友圈备份工具")
    parser.add_argument("--version", action="version", version=f"wemo2 {__version__}")
    parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.info("启动 wemo2 {}", __version__)
    try:
        root = create_window()
        root.mainloop()
    except tk.TclError:
        logger.exception("无法启动桌面窗口，请检查 Python 的 Tk 支持及桌面环境")
        raise SystemExit(1) from None
    logger.info("wemo2 已退出")
