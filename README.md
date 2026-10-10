# wemo2

朋友圈备份工具，使用 Python 实现自动采集、去重与持久化。

- 支持平台：Windows
- 桌面交互：Tkinter
- 日志：Loguru
- 最终持久化与展示：Memos
- 项目与依赖管理：uv，Python 3.12 及以上

## 开始使用

```powershell
uv sync
uv run wemo2
```

也可使用 `uv run python -m wemo2` 启动，或使用 `uv run wemo2 --version` 查看版本。
Python 发行版需要包含 Tcl/Tk；可通过 `uv run python -m tkinter` 检查。

当前仅提供项目骨架、日志和桌面入口，尚未实现真实采集、去重或 Memos 连接。
接口与数据结构见 [ARCHITECTURE.md](ARCHITECTURE.md)。
