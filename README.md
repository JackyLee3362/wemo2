# wemo2

朋友圈备份工具，使用 Python 实现自动采集、去重与持久化。

- 支持平台：Windows
- 采集源配置：`platform = "windows"`，预留 `android`；当前真实界面采集尚未实现。
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
终端使用 Rich 展示结果表格和简短错误提示，运行日志默认写入 `logs/wemo2.log`，
不向终端打印日志或异常堆栈。执行失败退出码为 1，参数或配置错误为 2。
日志使用 UTF-8，超过 10 MB 自动轮转，轮转文件保留 7 天。

## 最小采集流程

朋友圈入口路由：

```powershell
uv run wemo2 --config wemo2.moments.toml
```

VS Code 可选择“wemo2_collector: 调试朋友圈路由”按 F5。路由器截取当前页面，
在左侧栏匹配指南针图标，按匹配中心点击，再查找同一微信进程的“朋友圈”窗口并截图验证。
已经打开该窗口时直接切换，不重复点击。匹配不唯一或跳转超时会停止并报错。

`[route]` 配置控制目标、模板、参考 DPI、匹配阈值、侧栏范围、目标窗口标题与超时。
内置模板取自用户提供截图的干净图标，参考缩放为 125%，支持小范围尺寸调整。
初版针对同主题、独立朋友圈窗口的布局；若新版本在主窗口内部展示朋友圈，需要新增对应页面验证规则。
点击前检查窗口、焦点、区域、帧 ID 及截图时效；不能用旧截图中的坐标直接点击当前桌面。

Windows 微信窗口截图：

```powershell
uv run wemo2 --config wemo2.screenshot.toml
```

截图保存到 `data/screenshot`，文件名包含时间和 UUID，同时保存同名 JSON 元数据。
先打开并还原微信窗口；默认按精确标题“微信”和微信进程定位，英文界面可修改 `window_title`。
多实例时在配置中设置 `window_process_id`。截图前会将微信置于前台，再按客户区物理坐标截图，
解决 HWND/GDI 截图无法读取实际渲染内容的问题；不执行点击、滚动或发消息。
请保持桌面解锁、微信完整位于屏幕内且没有其他窗口、悬浮窗或通知遮挡。
几乎全黑、尺寸异常或焦点变化会报错。当前只保存截图，尚不读取朋友圈内容，动态数量为 0。

VS Code 调试入口为项目根目录的 `main.py`。先执行 `uv sync`，安装 VS Code 的
Python 和 Python Debugger 扩展，再按 F5 选择“wemo2: 调试 CLI”。配置使用项目
`.venv` 中的 Windows Python；可修改 `.vscode/launch.json` 的 `args` 传入命令行选项。

独立调试采集器：在 VS Code 的运行与调试列表中选择“wemo2_collector: 调试微信截图”，
再按 F5。入口为 `src/wemo2_collector/main.py`，读取根目录的 `wemo2.screenshot.toml`，
只执行采集器，不创建去重器或存储器；截图仍保存到 `data/screenshot`。
可在 `collector.py`、`windows/driver.py`、`windows/screenshot.py` 中设置断点。
也可运行 `uv run python -m wemo2_collector.main`。

先运行完全由模拟组件组成的流程，无需文件、微信或数据库：

```powershell
uv run wemo2
```

默认组合为 `PseudoCollector + PseudoDeduplicator + PseudoStore`，内置三次投递
（两条动态，其中一条重复），输出新增 2 条、未变更 1 条。PseudoStore 只存内存，退出即丢失。

选择不同组件可混合运行，例如模拟采集 + 稳定源 ID 去重 + SQLite 存储：

在 `wemo2.toml` 中设置：

```toml
collector = "pseudo"
deduplicator = "stable"
store = "sqlite"
```

抽象契约位于 `src/wemo2/interfaces.py`：`BaseCollector.collect()`、
`BaseDeduplicator.resolve()`，以及 `BaseStore` 的查询、事务、保存和资源管理接口。
各实现显式继承抽象类；`Coordinator` 仅依赖抽象类，不含实现选择分支。
`build_coordinator()` 负责按名称组装，代码中也可直接把自定义实现传入 `Coordinator`。

| 组件 | 模拟实现 | 现有实现 | 配置字段 |
| --- | --- | --- | --- |
| 采集 | PseudoCollector，直接产生模型对象 | JsonCollector，文件导入 | `collector` |
| 去重 | PseudoDeduplicator，只按 Observation ID 幂等 | Deduplicator，额外按稳定源 ID 匹配 | `deduplicator` |
| 存储 | PseudoStore，单线程内存存储 | SQLiteStore，本地持久化 | `store` |

模拟去重器不合并不同 Observation ID 的同一源动态；验证真实身份匹配请选 `stable`。

导入 JSON 文件：

```powershell
uv run wemo2 --config wemo2.json.example.toml
```

默认存储位置为 `data/wemo2.sqlite3`，使用配置字段 `database` 可更换。
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
uv run wemo2 --config wemo2.toml
```

配置文件采用 TOML 顶层字段：

```toml
collector = "pseudo"
deduplicator = "stable"
store = "sqlite"
database = "data/wemo2.sqlite3"
log_level = "INFO"
log_file = "logs/wemo2.log"
```

代码默认使用三个 `pseudo` 组件；JSON 采集时，未显式设置的去重器和存储器
分别默认 `stable` 和 `sqlite`。文件中明确填写的值始终优先，包括 `pseudo`。
当前只提供 CLI，已移除 `--mode` 和配置中的 `mode` 字段；已有配置文件需要删除该字段。

CLI 只提供 `--help`、`--version`、`--config`、`--log-file`，其他业务配置由 TOML 文件控制。
JSON 输入在文件中使用 `json_path`。配置文件中的相对路径
以配置文件所在目录为基准；命令行和代码默认相对路径以当前工作目录为基准。
日志级别通过 `log_level` 设置，`--log-file PATH` 可覆盖日志文件。
日志路径遵循命令行 > 配置文件 > 默认值；业务配置遵循配置文件 > 默认值。
配置模型与加载逻辑位于 `src/wemo2/config.py`，
Typer 参数定义位于 `src/wemo2/cli.py`，可用 `uv run wemo2 --help` 查看。

## 重复停止策略

由去重器通过 `Decision.is_duplicate` 判断重复，由协调器根据配置决定是否继续消费采集记录。
不使用存储器的 `unchanged` 统计来判断重复，因为匹配到的已有动态可能产生内容修订。
触发停止的当前记录先完成保存，再停止，不再请求下一条数据。

| 配置值 | 行为 |
| --- | --- |
| `first` | 第一次重复立即停止 |
| `after_n` | 本次运行累计重复 N 次后停止，中间遇到新动态不清零 |
| `never` | 重复后继续采集，默认值 |

```toml
duplicate_stop = "after_n"
duplicate_limit = 3
```

TOML 使用 `duplicate_stop` 和 `duplicate_limit`，遵循配置文件 > 默认值。
计数在每次运行开始时重置。提前停止会展示原因，不表示已遍历全部数据。
策略不会根据正文相同自行猜测重复；能识别哪些重复取决于所选去重器。

采集器应逐条产生记录，且仅在协调器请求下一条时执行下一步读取。
协调器在正常结束、提前停止或异常时都会调用 `collector.stop()`；生成器也会关闭。
后台采集器应覆盖 `stop()` 停止后台任务并释放资源。JSON 导入器目前先加载并校验整个文件，
提前停止只影响后续处理，不能撤销已经完成的文件读取。

## 添加组件实现

界面采集架构与接口骨架位于 `src/wemo2_collector/`，详见
[采集器架构](src/wemo2_collector/ARCHITECTURE.md)。平台配置经组装入口与注册表传给采集器，
不增加 CLI 选项。后续实现将按平台驱动、页面路由、截图、定位、文字识别和卡片解析拆分。

组件名称和实现类统一维护在 `src/wemo2/registry.py`。新实现继承对应抽象类后，
在该文件导入并注册即可；不用同步修改 CLI 枚举、配置名称列表或组装分支：

```python
collectors.register("windows", WindowsCollector)
stores.register("local", LocalStore, parameters={"path": "database"})
```

`parameters` 是“构造参数名 → 配置字段名”。注册表据此创建实例，只传入实现需要的参数。
`defaults` 可指定采集器推荐的去重器和存储器，用户文件的显式配置仍优先。
`default=True` 指定该类组件的默认实现，`notice` 设置存储结果的展示提示。
重复名称、错误接口、未实现抽象方法和缺少构造参数均会报错。

注册后在配置文件中设置 `collector = "windows"` 即可。
如果新实现需要全新的配置字段，仍需在 `AppConfig` 中定义其类型和默认值；
已有配置字段可直接通过参数映射复用。上面的类名仅作扩展示例，当前尚无 Windows 采集实现。

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
