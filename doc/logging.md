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
| 另存为、另存为提交、另存为等待 | 控件就绪重试、消息调用返回、对话框及文件状态、剩余时间、稳定文件及路径 | WindowsDriver |
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

## 2026-10-12 另存为等待修复

原“通过 Windows 保存按钮提交一次，等待文件写入”日志打印在 `click()` 调用之前，不能证明系统已处理保存。原流程还缺少限时消息调用和等待过程日志，因此无法从这一行区分按钮无响应和文件尚未生成。

当前保留 Windows 控件方案，以 `SendMessageTimeout(BM_CLICK)` 提交保存，并把控件准备、消息调用及文件轮询纳入共同等待预算。默认准备检查最多 3 次，单次消息最多 2 秒，共同预算 30 秒；通过 `[collector_options.save_dialog]` 调整。就绪检测失败记录异常及重试次数，提交返回后单独记录，再每 5 秒报告对话框是否打开、候选文件数、文件大小和剩余时间。超时抛错，由现有失败流程退出并留痕。

只对就绪检查及无副作用的 `WM_NULL` 探测重试。Windows 消息超时不能证明动作未发生，已进入处理的消息可能仍会完成，所以 `BM_CLICK` 超时后继续观察、不自动重发。参见 [Microsoft 消息超时说明](https://devblogs.microsoft.com/oldnewthing/20110915-00/?p=9643/) 和 [SendMessageTimeout 文档](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw)。

本次实机验证：通过微信图片右键菜单打开“保存”窗口，再运行实际 `WindowsDriver.save_dialog()`。22:51:08 消息返回后对话框关闭，约 0.8 秒完成提交及稳定文件校验。得到 478154 字节 JPG，并通过 Pillow 图片完整性检查。文件保存在 `data/debug/save-dialog-20261012/media/asset_ff97330d-683b-49a0-a38f-8ddb37696bd3.jpg`，SHA-256 为 `dca0284cae04f6db0e05c8143ca8f71e3bb02e60915153f5ace587d435403286`。本次仅验证单张图片保存，未验证视频和完整多媒体批次。

## 视频号、归档目录与完整截图

图片实测确认 Ctrl+S 可打开标准保存框。首次测试发现 Edit.set_focus 会改变前台窗口，已改用 set_keyboard_focus 并核对键盘焦点；修复后 Enter 提交、对话框关闭和稳定文件校验约 0.7 秒完成。478154 字节 JPG 已直接保存到 `resources/default/2026-10-12/华师-顾旖琪yi/dae5decbcf4e27dad42145203ae4464882b50c8976a73aef29f3b295d8eaa72b/media/asset_d0e499b9-5fd1-4f44-ba03-053042ad298e.jpg`，并立即登记该朋友圈的 post.json。此实测验证单张落盘；反向跨屏定位及视频号误打开后的关闭由自动测试覆盖，未完成真实多屏、多图片整批回归。

默认保存入口已调整为 Ctrl+S 和 Enter；消息提交仍可配置。新增日志 `[媒体跳过]`、`[视频号]`、`[媒体回看]` 和 `[归档跳过]`，分别说明跳过原因、已打开视频号关闭动作、反向定位进展和半张截图不进入主归档的原因。

在 `data/debug/media-policy-20261012` 实测一屏视频号分享：完整截图同时包含左上头像和右下“更多”按钮，JSON 的 screenshot_complete=true、media_skip_reason=video_channel、media=[]。首尾两个未闭合片段仅留作截图证据。资源位于 `resources/default/2026-10-12/华师-CR丹妮/455eadac46192430b5f687681f808496ddf6b3dac1605101f09151b2f6b82f73/`。本轮采集未打开播放器，未产生保存动作。
