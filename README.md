# wemo2

wemo2 将朋友圈备份拆成采集和保存两个命令。采集阶段完成本地归档及去重，保存阶段读取这些文件夹；不再设置独立去重器或协调器。

## 运行

使用 Python 3.12+ 和 uv：

```powershell
uv sync
uv run wemo2 collect --config wemo2.moments.toml
uv run wemo2 save --memos --config wemo2.moments.toml
```

`collect` 从 Windows 微信导航到朋友圈，截图、识别人物和文案、保存图片/视频，并归档。`save` 不启动微信，读取同一配置的资源目录和账号。

**本次 Memos 仍为模拟存储。** `--memos` 选择 Memos 目标占位，实际使用 PseudoStore 在内存中演示接收元信息与文件，不请求网络、不上传、不生成远端持久化回执。后续适配 Memos 0.31。

不连接微信的离线演示：

```powershell
uv run wemo2 collect --config wemo2.example.toml
uv run wemo2 save --memos --config wemo2.example.toml
```

示例采集器生成真实 `post.json` 和示例截图，重复采集复用同一朋友圈文件夹。实际 UI 采集使用 `wemo2.moments.toml`；仅测试截图使用 `wemo2.screenshot.toml`。

## 配置

配置优先级为命令行显式参数 > 配置文件 > 默认值。默认尝试工作目录的 `wemo2.toml`。共用参数写在顶层：

```toml
collector = "windows_screenshot"
store = "pseudo"
resource_dir = "data/moments"
account = "default"
# 默认只输出控制台；需要同时写文件时启用：
# log_file = "logs/wemo2.log"
log_level = "INFO"

[collector_options]
platform = "windows"
screenshot_dir = "data/screenshot"
window_title = "微信"
timezone = "Asia/Shanghai"
theme = "dark"

[collector_options.route]
target = "moments_entry"

[collector_options.feed]
enabled = true
max_screens = 20
wheel_steps = 3
recognize_nickname = true
save_media = true
use_image_arrows = true
media_load_wait_seconds = 5
media_load_timeout_seconds = 30
```

相对资源路径与截图路径以配置文件目录为基准。账号是备份账号，不是朋友圈作者。旧配置中 `collector_options.resource_dir` 和 `collector_options.account` 可兼容读取并提升为顶层字段；重复配置且冲突会报错。

`--config`、`--log-file` 可放在子命令前或后，`save` 额外接受 `--memos`。业务选项保持在 TOML，不增加用于组件、目录、账号、日志级别的命令行参数。未指定子命令只展示帮助。

日志默认由 Loguru 输出到控制台 stderr，Rich 的交互和结果显示在 stdout。未指定 `log_file` 时不创建日志文件；配置 `log_file` 或传入 `--log-file` 后，同时输出到控制台和文件。配置加载错误也会在控制台留下异常堆栈；帮助与版本不初始化运行日志。

默认 INFO 记录截图落盘、目标定位、动作执行和校验、滚动拼接、媒体加载、Windows 另存为、文件校验及归档步骤。需要查看逐轮校验时，在配置中设置 `log_level = "DEBUG"`。

日志步骤、可选文件输出和实机调试记录见 `doc/logging.md`。

旧 `deduplicator`、`duplicate_stop`、`duplicate_limit`、`database` 和 Observation JSON/SQLite 导入配置已经移除；本地归档是新的数据交付契约。调整旧配置时参考现有示例文件。

## 归档和去重

```text
资源目录/default/2026-10-11/朋友-牛哥/<人物与完整文案的 SHA-256>/
├── post.json
├── screenshot.png
├── screenshots/       # 历次证据
└── media/             # 实际下载的图片、视频
```

JSON 记录粗略日期、人物、文案、完整度、截图证据及所有已关联媒体。日期目录使用 `YYYY-MM-DD`。人物名统一去掉 emoji，例如 `朋友-牛哥🐮` 变成 `朋友-牛哥`，目录及去重使用清洗后的名字；JSON 的 `person_raw` 保留解析原文。目录中的 Windows 非法字符替换为下划线，清洗后没有文字的名字使用 `unknown_person`。同账号下按人物与完整文案合并归档；日期不参与身份哈希，媒体按文件字节哈希去重。未知日期保留 `unknown_date`，人物或文案不完整保留 `unidentified_*`，不凭未知信息错误合并。

先滚动拼接整条朋友圈，识别头像、昵称、文案、日期和底部“更多”边界，再逐条归档。缺少头像或底部边界的片段只在截图目录留痕，不写作归档主截图。媒体阶段从扫描终点向上回看，用相邻截图重叠确认位置，直接下载到 `resource_dir/account/YYYY-MM-DD/person/hash/media/`，每个文件落盘后立即登记 `post.json`。日期或身份无法确认时保留截图，不猜目录下载。`data/screenshot` 用于原始截图和动作证据。

保存命令扫描配置账号目录下的 `post.json`，校验元信息身份、资源路径、文件存在性以及媒体大小和 SHA-256。遇到错误停止并记录异常日志，不跳过后宣称全部保存成功。

## 界面采集

原 `wemo2_collector` 已合并到 `src/wemo2/collectors/`，继续维护页面、主题、路由、控制器和 Windows 驱动。通用元信息模型在 `src/wemo2/api/records.py`，存储阶段通过 `ArchivedPost` 接收 JSON 和文件路径，不导入 UI 模型。

图片默认保持查看器打开，用右方向键切换下一张，并核对该卡片目标缩略图的放大图；切换失败则验证退出后逐张重开。设置 `use_image_arrows = false` 可直接使用重开策略。视频等待加载后保存并退出。保存失败不触发图片切换兜底；点击、滚动和方向键均不自动重发。

默认使用 `Ctrl+S → 输入绝对路径 → Enter`。文件名通过 `pywinauto` 定位标准 Edit ID 1001 控件填写，系统保存框不依赖主题颜色或按钮坐标。`collector_options.feed.media_workflow = "save_media"` 可选原右键另存为流程，`collector_options.save_dialog.submit = "message"` 可选限时 `BM_CLICK` 按钮消息。快捷键结果未确认时停止并留痕，不连续叠加另一种保存输入。

视频号分享保留完整朋友圈截图和元数据，`media_skip_reason` 记录 `video_channel`，不下载视频号内容。若已打开视频号，识别顶部标题及相邻关闭图标后关闭该标签；关闭不能确认时停止。朋友圈直接发布的视频仍按普通媒体流程处理。

`[collector_options.save_dialog]` 可配置保存等待：`timeout_seconds = 30` 为控件准备、提交和文件轮询共用的预算，`message_timeout_seconds = 2` 为单次控件消息上限，`prepare_attempts = 3` 为包含首次检查的就绪探测次数，`poll_interval = 0.2` 为重试和轮询间隔。仅对尚未就绪的控件和无副作用的响应探测重试；保存消息超时后继续观察结果，不重复提交。每 5 秒记录对话框、文件和剩余时间，超时会区分“对话框仍未关闭”与“文件未落盘或不稳定”，并停止流程。

默认滚轮三个步长，在列表左侧空隙输入；头像侧栏用于进展、稳定和视口重叠验证，避免视频播放被当成滚动。达到屏数上限或无法验证进展时停止并保存证据，不声称已遍历全部历史。

## 调试和检查

VS Code 的 F5 调试统一通过项目 `main.py` 启动，可分别选择采集、截图、模拟采集和模拟 Memos 保存。

```powershell
uv run ruff check . --fix
uv run ruff format .
uv run ruff check .
uv run ruff format . --check
uv run python -m unittest discover -s tests -v
```

代码按 `api`、`core`、`cli`、`collectors`、`stores`、`archives`、`common` 分包，组件统一在 `src/wemo2/core/registry.py` 登记。`api` 是 Python 接口与数据契约，不提供 HTTP 服务；`common` 只维护共用的基础函数。

更详细的架构见 `ARCHITECTURE.md`，包职责及复用说明见 `doc/code-organization.md`，采集内部设计见 `src/wemo2/collectors/ARCHITECTURE.md`，文件契约见 `doc/moment-resources.md`。

新目录层级为 `resource_dir/account/YYYY-MM-DD/person/hash/`。保存命令兼容读取原来的 `日期/hash` 布局；采集再次遇到旧归档时会升级到人物层级，同时保留截图、媒体和相对资源引用。去掉人物 emoji 后，旧人物哈希也会相应升级，正文中的 emoji 仍保留并参与去重。
