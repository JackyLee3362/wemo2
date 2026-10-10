# wemo2

朋友圈备份工具，使用 Python 实现自动采集、去重与持久化。

- 支持平台：Windows
- 首版交互：CLI；Tkinter 界面留待后续实现。
- 日志：Loguru
- 命令行：Typer
- 最终持久化与展示：Memos
- 项目与依赖管理：uv，Python 3.12 及以上

## 开始使用

```powershell
uv sync
uv run wemo2
```

默认按配置执行一次采集流程并退出。也可使用 `uv run python -m wemo2` 启动，
或使用 `uv run wemo2 --version` 查看版本。运行不依赖 Tcl/Tk。
成功结果以 JSON 输出到标准输出，日志写入标准错误；执行失败退出码为 1，参数或配置错误为 2。

## 最小采集流程

VS Code 调试入口为项目根目录的 `main.py`。先执行 `uv sync`，安装 VS Code 的
Python 和 Python Debugger 扩展，再按 F5 选择“wemo2: 调试 CLI”。配置使用项目
`.venv` 中的 Windows Python；可修改 `.vscode/launch.json` 的 `args` 传入命令行选项。

先运行完全由模拟组件组成的流程，无需文件、微信或数据库：

```powershell
uv run wemo2 --collector pseudo
```

默认组合为 `PseudoCollector + PseudoDeduplicator + PseudoStore`，内置三次投递
（两条动态，其中一条重复），输出新增 2 条、未变更 1 条。PseudoStore 只存内存，退出即丢失。

选择不同组件可混合运行，例如模拟采集 + 稳定源 ID 去重 + SQLite 存储：

```powershell
uv run wemo2 --collector pseudo --deduplicator stable --store sqlite
```

抽象契约位于 `src/wemo2/interfaces.py`：`BaseCollector.collect()`、
`BaseDeduplicator.resolve()`，以及 `BaseStore` 的查询、事务、保存和资源管理接口。
各实现显式继承抽象类；`Coordinator` 仅依赖抽象类，不含实现选择分支。
`build_coordinator()` 负责按名称组装，代码中也可直接把自定义实现传入 `Coordinator`。

| 组件 | 模拟实现 | 现有实现 | 选择参数 |
| --- | --- | --- | --- |
| 采集 | PseudoCollector，直接产生模型对象 | JsonCollector，文件导入 | `--collector pseudo/json` |
| 去重 | PseudoDeduplicator，只按 Observation ID 幂等 | Deduplicator，额外按稳定源 ID 匹配 | `--deduplicator pseudo/stable` |
| 存储 | PseudoStore，单线程内存存储 | SQLiteStore，本地持久化 | `--store pseudo/sqlite` |

模拟去重器不合并不同 Observation ID 的同一源动态；验证真实身份匹配请选 `stable`。

导入 JSON 文件：

```powershell
uv run wemo2 --import-json examples/observations.json
```

默认存储位置为 `data/wemo2.sqlite3`，使用 `--database PATH` 可更换。
再次导入同一文件不会新增动态；相同稳定源 ID 的新观察可更新完整字段并保留修订。

四个组件分别为 `JsonCollector`（JSON 数组采集）、`Deduplicator`（幂等与源身份去重）、
`SQLiteStore`（本地持久化）、`Coordinator`（串联执行）。协调器通过接口接受可替换组件。

数据结构集中在 `src/wemo2/models.py`，使用 Pydantic 定义和校验；组件间传递模型对象。
`Collector.collect()` 输出 `Iterable[Observation]`，既可一次返回列表，也可逐条产生记录。

`JsonCollector` 是导入/模拟采集器：它接收的是文件路径，文件中的 JSON 数组是一批采集记录，
每个元素表示一次对某条朋友圈动态的观察。它用于离线导入、流程验证和重复采集测试，
不是要求真实采集器输入 JSON。后续 Windows/Android 采集器从微信读取数据后，直接输出
`Observation` 对象即可。相同动态在不同时间被观察，可以对应不同的 Observation。

无稳定源 ID 的不同 Observation 保留为独立动态，避免相同正文的不同发布被误合并。
当前按字段完整度做顶层合并，不实现模糊匹配、评论子项合并、媒体下载、分页检查点或 Memos 同步；
媒体引用只保存元数据。真实 Windows/Android 采集器和 Memos 存储适配器尚未接入。

运行验证：`uv run python -m unittest discover -s tests -v`。
接口与数据结构见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 启动配置

优先级为 **命令行显式选项 > 配置文件 > 代码默认配置**。启动时自动尝试读取
当前目录的 `wemo2.toml`；没有该文件就使用默认值。显式指定的文件不存在、格式错误、
未知字段或无效选项都会报错。可复制 `wemo2.example.toml` 后修改：

```powershell
Copy-Item wemo2.example.toml wemo2.toml
uv run wemo2 --config wemo2.toml --store sqlite
```

配置文件采用 TOML 顶层字段：

```toml
collector = "pseudo"
deduplicator = "stable"
store = "sqlite"
database = "data/wemo2.sqlite3"
log_level = "INFO"
```

代码默认使用三个 `pseudo` 组件；JSON 采集时，未显式设置的去重器和存储器
分别默认 `stable` 和 `sqlite`。文件或命令行中明确填写的值始终优先，包括 `pseudo`。
当前只提供 CLI，已移除 `--mode` 和配置中的 `mode` 字段；已有配置文件需要删除该字段。

JSON 输入在文件中使用 `json_path`，命令行使用 `--import-json`。配置文件中的相对路径
以配置文件所在目录为基准；命令行和代码默认相对路径以当前工作目录为基准。
`--log-level` 可覆盖日志级别。配置模型与加载逻辑位于 `src/wemo2/config.py`，
Typer 参数定义位于 `src/wemo2/cli.py`，可用 `uv run wemo2 --help` 查看。

## 开发检查

编码规范见 [AGENTS.md](AGENTS.md)。Ruff 作为开发依赖随 `uv sync` 安装，负责
Python 静态检查、import 排序和格式化：

```powershell
uv run ruff check . --fix
uv run ruff format .
uv run ruff check .
uv run ruff format . --check
uv run python -m unittest discover -s tests -v
```
