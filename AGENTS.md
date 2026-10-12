# wemo2 开发规范

本文件适用于整个仓库。修改代码时遵循以下约定。

## 技术与模块边界

- 使用 Python 3.12+ 和 uv 管理运行环境及依赖。运行依赖用 `uv add`，开发工具用 `uv add --dev`。
- 当前优先实现 CLI，命令行使用 Typer，日志使用 Loguru；不添加 Tkinter 界面。
- 组件间的数据结构使用 Pydantic 定义。模型写明用途，关键字段注明身份、时间、完整度等语义。
- 代码按职责分包：api 定义抽象接口和 Pydantic 数据契约；core 负责配置、组件注册及应用流程；cli 负责 Typer/Rich 交互；collectors 和 stores 分别实现采集和保存；archives 维护本地归档身份、布局与读取；common 放真正共用的基础函数。不要把业务流程或 UI 模型放进 common。
- api 和 common 不依赖 core、cli、collectors、stores 或 archives；core 不依赖 Typer/Rich 或 cli。archives 与 stores 不依赖采集实现；collectors 通过 api、archives 和 common 共享归档规则。
- 归档目录使用 resource_dir/account/YYYY-MM-DD/person/hash。人物名通过 common.names.normalize_person 去除 emoji，再用于元信息和去重；原始人物名保留在 person_raw。目录通过 common.names.person_directory 清洗 Windows 非法字符。正文中的 emoji 不清洗。
- 采集器和存储器实现各自的抽象接口。采集器负责本地归档及人物、文案去重，不设置独立去重器或协调器；collect 与 save 通过归档文件夹交付数据。
- 组件名称与实现类统一在 `src/wemo2/core/registry.py` 注册；CLI、配置校验和组装入口读取注册表，不另外维护枚举或逐个实现的分支。注册时声明构造参数映射。
- 界面采集的窗口、截图、主题、模板和路由字段只能定义在 `wemo2.collectors`；应用层使用通用 `collector_options` 转交配置，通过注册的 `prepare_options` 校验和解析相对路径。除唯一组件登记处 `core/registry.py` 外，应用和存储模块不导入界面采集实现或模型。共用 resource_dir、account 由应用配置维护，归档契约由 api/records.py 定义。
- 页面、路由、主题及语义目标模板统一维护在 `wemo2/collectors/profiles.py` 的 `UiProfile`。定位使用 Airtest 的图像匹配能力，适配器只补充搜索范围、DPI 和歧义检查，不重新实现匹配矩阵或候选抑制算法。内部单实现组件优先直接用具体类或函数，不为未来功能提前建立空抽象接口；平台驱动仍使用 `BaseDriver`。
- `UiController` 统一截图、定位、输入和操作后等待；`validation.py` 的无状态函数校验截图、页面与区域进展，共用规则而不复制校验代码。截图/定位可以有限重试，点击/滑动不能自动重发；输入异常要记录 attempted，后续验证失败不能丢失 executed。动作证据与状态保存在 `ActionResult`。
- 配置优先级始终为命令行显式参数 > 配置文件 > 代码默认值。不要让未传入的 CLI 参数覆盖配置文件。
- CLI 提供 collect 和 save 两个子命令，共用 --help、--version、--config、--log-file、--user；--user 显式覆盖 account，便于隔离调试；save 额外支持 --memos。采集器、资源根目录、账号、日志级别、界面参数等业务设置由配置文件控制。Memos 0.31 暂未实现，--memos 必须明确提示使用模拟存储，不能声称已经上传。
- CLI 交互展示统一使用 Rich，运行日志由 Loguru 默认输出到控制台 stderr。log_file 默认为空；配置 log_file 或传入 --log-file 时同时写入文件，配置优先级保持不变。失败返回非零退出码，不输出成功结果。
- 截图、定位、点击/滑动/滚动、媒体加载、Windows 另存为、文件校验与归档等关键节点记录步骤日志。区分开始、输入执行事实、后置校验及实际落盘；失败不能输出成功日志，轮询细节使用 DEBUG。允许使用已登录微信进行实机调试，调试输出使用 data/debug 下的独立目录。

## Python 编码与 import

- 使用四个空格缩进，名称清晰，公开接口尽量注明参数和返回类型。
- 优先使用直观的 `if`、`for` 和逐项赋值。避免嵌套推导式、动态 `getattr/setattr`、布尔值转整数等压缩写法；中间步骤使用有意义的变量名。简单直接的类型声明和框架必要装饰器可正常使用。
- 每个类都必须有中文类文档字符串，包括抽象类、模型、具体组件、测试类和测试替身；说明职责、用途及必要的边界，不只重复类名。公共函数也补充有实际含义的文档字符串；注释说明约束或原因，不重复显然的代码。
- 每个 `except` 分支必须记录错误日志，使用 Loguru 的 `logger.exception` 保留原始异常与堆栈，并说明失败步骤及重试、跳过、回滚等处理。不能用 `pass`、直接 `continue` 或仅保存错误字符串代替日志；重新抛出或转换异常也必须先记录。应用启动先建立控制台错误日志，配置或正式日志初始化失败仍须输出异常记录；不为启动默认创建日志文件。
- import 按标准库、第三方库、项目内部模块分组，各组空行分隔，组内顺序交给 Ruff 整理。
- 项目内部使用 `from wemo2...` 绝对导入，禁止通配符导入，删除未使用的导入和变量。
- import 默认放在模块顶部。确需避免循环依赖或启动副作用时，可以延迟导入，并说明原因。
- 不手工维护与格式化工具冲突的样式。行宽目标为 88，具体换行由 Ruff formatter 决定。
- 不使用无理由的 `noqa`、大范围规则忽略或不安全自动修复来掩盖检查问题。

## 自动检查与修复

统一使用 Ruff 作为 Python linter、import 排序器和 formatter，配置以 `pyproject.toml` 为准。
启用基础错误、未使用符号、import 排序、Python 现代语法和常见缺陷检查。

修改 Python 代码后执行：

```powershell
uv run ruff check . --fix
uv run ruff format .
uv run ruff check .
uv run ruff format . --check
uv run python -m unittest discover -s tests -v
```

- 自动修复后检查差异，手动修复剩余 linter 问题，直到检查通过。
- 不使用 `--unsafe-fixes`，除非已确认具体变更的行为影响。
- 修改数据、配置、去重或存储逻辑时，补充能够验证行为的测试，避免只照抄实现。
- 完成前运行相关测试及上述静态检查；若因环境问题无法运行，明确说明未验证的部分。
- 不编辑虚拟环境、自动下载的 Python 运行时或生成文件来满足检查。
