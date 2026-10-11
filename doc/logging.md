# 日志输出与关键步骤

## 默认输出

日志使用 Loguru，默认 INFO 级别输出到控制台 stderr。Rich 的命令结果输出到 stdout。log_file 默认为空，不创建日志目录或文件。

```powershell
uv run wemo2 collect --config wemo2.moments.toml
```

需要同时保存文件时，在 TOML 顶层设置 log_file，或显式传入命令行参数：

```toml
log_level = "INFO"
log_file = "logs/wemo2.log"
```

```powershell
uv run wemo2 collect --config wemo2.moments.toml --log-file logs/debug.log
```

命令行日志路径优先于配置文件，相对命令行路径以工作目录为基准；配置中的相对路径以配置文件目录为基准。启用文件输出后，控制台输出仍保留。帮助与版本不初始化日志；配置加载前先建立控制台 ERROR 日志，配置错误与文件日志初始化错误也能留下堆栈。

## 步骤与负责组件

| 标签 | 记录内容 | 负责组件 |
| --- | --- | --- |
| 命令、采集、存储 | 命令开始/完成、组件选择、归档提交和数量 | cli.runtime、core.services |
| 窗口连接 | 目标标题/进程、符合条件的窗口数、连接结果和客户区尺寸 | WindowsDriver |
| 截图 | 截图开始、窗口尺寸、PNG 与元信息实际保存路径 | WindowsScreenshotter |
| 动作、定位、校验 | 动作 ID、目标范围、输入尝试与执行事实、后置校验结果 | UiController |
| 滚动、滑动 | 本次输入的步数或方向/距离 | UiController |
| 滚动采集、拼接 | 屏数、锚点数量、新增像素、停止原因及清单 | FeedCapture |
| 人物识别、卡片留痕 | 识别结果、截图/元信息路径、卡片状态 | MediaCapture |
| 媒体流程、媒体加载 | 当前步骤、前置/后置状态、等待时间及上限 | UiController |
| 另存为 | Windows 控件确认、单次提交、文件写入稳定及路径 | WindowsDriver |
| 媒体保存 | 已验证重叠的文件复用、类型/大小/哈希、文件和查看器校验结果 | MediaCapture |
| 归档、归档去重、归档媒体 | 作者/日期/目录、已有记录复用、资源拷贝、JSON 写入及错误数量 | MomentArchive |

默认 INFO 展示关键步骤；逐轮校验结果使用 DEBUG。所有捕获异常使用 logger.exception，保留堆栈和处理方式。日志中的 executed 表示输入调用已返回，不等同于校验通过；status=verified 才表示后置条件通过。输入失败或后置校验失败仍不自动重发。

## 2026-10-12 验证记录

- 默认模拟采集与模拟保存都输出控制台步骤，未生成日志文件；显式文件输出、参数优先级和异常堆栈由测试覆盖。
- 微信实测先保存单张截图，再运行最多 3 屏的采集。2 次滚动的 ActionResult 均为 verified、attempted=true、executed=true。
- 滚动每次新增 225 像素，3 屏切出 4 个片段；产生 4 个归档目录，包含待识别或未闭合片段。截图、识别、归档及停止原因日志完整，停止原因为 max_screens。
- 保存命令成功读取这 4 个归档并交给模拟存储器，未连接 Memos。
- 实机输出位于 data/debug/logging-20261012。此次调试设置 save_media=false，未进行真实图片/视频下载；媒体保存相关日志随已有保存流程测试验证。
- 实机存在两个微信实例，调试配置通过 window_process_id 选择已观察到的主窗口。进程号只用于本次调试，不写入正式配置。

实机调试需要访问当前用户桌面；受限执行环境可能枚举不到微信窗口。多开时须先确认目标实例，再通过配置选择对应进程。
