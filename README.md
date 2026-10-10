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

## 最小采集流程

桌面窗口点击“导入 JSON”，或运行：

```powershell
uv run wemo2 --import-json examples/observations.json
```

默认存储位置为 `data/wemo2.sqlite3`，使用 `--database PATH` 可更换。
再次导入同一文件不会新增动态；相同稳定源 ID 的新观察可更新完整字段并保留修订。

四个组件分别为 `JsonCollector`（JSON 数组采集）、`Deduplicator`（幂等与源身份去重）、
`SQLiteStore`（本地持久化）、`Coordinator`（串联执行）。协调器通过接口接受可替换组件。

无稳定源 ID 的不同 Observation 保留为独立动态，避免相同正文的不同发布被误合并。
当前按字段完整度做顶层合并，不实现模糊匹配、评论子项合并、媒体下载、分页检查点或 Memos 同步；
媒体引用只保存元数据。真实 Windows/Android 采集器和 Memos 存储适配器尚未接入。

运行验证：`uv run python -m unittest discover -s tests -v`。
接口与数据结构见 [ARCHITECTURE.md](ARCHITECTURE.md)。
