# wemo2 功能包整理与代码复用

本次整理将应用、终端、契约、归档规则和具体组件分开，保持 collect/save 命令、配置格式及已归档 JSON 格式。按用户选择，不新增三方依赖。

## 目录与职责

```text
src/wemo2/
├── __init__.py                  # 版本
├── __main__.py                  # python -m wemo2 入口
├── api/
│   ├── interfaces.py           # BaseCollector / BaseStore
│   ├── models.py               # 两阶段结果与归档文件交付
│   └── records.py              # post.json 的 Pydantic 契约
├── core/
│   ├── config.py               # 配置优先级、校验与路径解析
│   ├── registry.py             # 组件名、实现类、参数映射的唯一登记处
│   └── services.py             # run_collect / run_save 应用流程
├── cli/
│   ├── commands.py             # Typer 声明及命令参数
│   ├── runtime.py              # 执行、控制台/可选文件日志和失败退出
│   └── console.py              # Rich 结果展示
├── collectors/
│   ├── pseudo.py               # 离线示例采集
│   ├── collector.py            # Windows 采集组件组装
│   ├── archive.py              # UI 分段与媒体转为通用归档
│   └── ...                     # 原页面、路由、控制器和平台驱动
├── stores/
│   └── pseudo.py               # 内存保存；Memos 当前映射到该占位实现
├── archives/
│   ├── identity.py             # 人物与完整文案的身份哈希
│   ├── paths.py                # 日期/人物/哈希布局及旧目录升级
│   └── reader.py               # 读取并校验归档与资源文件
└── common/
    ├── files.py                # 流式文件 SHA-256、原子模型 JSON 写入
    ├── paths.py                # 相对路径解析、目录范围检查
    ├── names.py                # 人物 emoji 清洗及原始名称一致性
    ├── windows_names.py        # 严格校验或清洗 Windows 单层目录名
    └── logging.py              # 控制台启动日志及可选 Loguru 文件 sink
```

api 表示 Python 组件接口和数据契约，没有新增 HTTP 服务。core/services.py 不依赖 Typer 或 Rich，可以被其他入口调用。cli 负责参数和终端展示；只有 core/registry.py 绑定具体采集器与存储器。

collectors/archive.py 保留在采集包，因为它处理 FeedSegment、MomentDetails、SavedPreview 等采集私有对象。可由其他采集实现共用的身份、布局、读取规则放在 archives；文件与路径操作放在 common。common 不导入业务包，也不维护朋友圈的目录层级。

新增采集器或存储器时，实现 api/interfaces.py 的对应接口，然后在 core/registry.py 登记名称、构造参数映射和必要的 prepare_options。配置校验和应用组装读取同一个注册表。

## 提取的共用代码

| 共用函数 | 调用位置 | 保持的约束 |
| --- | --- | --- |
| files.sha256_file | 媒体下载、采集归档、归档读取 | 计算实际文件字节哈希，流式读取视频 |
| files.write_model_json | 实际采集归档、旧目录升级、模拟采集 | 先写同目录临时文件，成功后替换 JSON；失败保留原文件并记录异常 |
| paths.resolve_path | 应用配置、采集配置 | 配置文件参数以配置目录为基准，CLI 覆盖以工作目录为基准 |
| paths.require_within / resolve_resource_path | 归档读取、资源拷贝、旧目录升级 | 拒绝越界；资源引用必须是非空相对路径；解析符号链接后再检查 |
| names.normalize_person_with_raw | MomentRecord、MomentDetails、采集归档 | 清洗 emoji，同时避免人物字段改变后遗留另一个人的原文 |
| windows_names.validate_directory_name / sanitize_directory_name | 配置账号、人名目录 | 用户配置的非法账号报错；解析出来的人名替换非法字符并避开保留设备名 |

保留简单函数，没有为共用操作新增工厂、基类或插件框架。人物文案身份规则放在 archives/identity.py，Pydantic 模型只维护字段与校验。

JSON 原子替换仅保护一次文件写入，不保护整个“读取、合并、保存”过程。当前仍应避免多个采集进程同时写同一个账号，未新增跨进程锁或归档事务。

## 三方包候选与本次决定

用户已选择本次不引入三方包，pyproject.toml 只调整命令入口，依赖及 uv.lock 不变。现有 Airtest、Pydantic、emoji、RapidOCR、Loguru、Typer/Rich 继续承担各自职责。

| 候选 | 可替代的代码 | 适用条件与决定 |
| --- | --- | --- |
| [pathvalidate](https://pathvalidate.readthedocs.io/en/latest/pages/reference/function.html) | common/windows_names.py 的非法字符、保留名校验与清洗 | 官方提供文件名校验和清洗；仍需保留本项目账号严格报错、人名容错和回退名称规则。本次不引入。 |
| [filelock](https://py-filelock.readthedocs.io/en/latest/) | 为同账号的归档读取、合并、目录升级及写入增加进程锁 | 官方提供跨进程文件锁。真正支持并发采集时，应锁住完整归档操作；原子 JSON 写入无法替代这个锁。本次不引入。 |

不建议仅为当前少量重试和注册代码新增 Tenacity、状态机或插件发现框架。UiController 对输入不自动重发的约束需要显式维护，通用自动重试可能改变这个行为。页面和媒体步骤已经由 UiProfile 配置驱动，无需另建一套流程框架。

## 验证范围

保留原流程测试，并新增共用函数的 JSON 写入失败保护、哈希、路径范围、名称一致性测试，以及包依赖边界测试。CLI 的帮助、版本、模拟采集和模拟保存通过离线流程检查；安装包检查入口和采集模板是否完整。此次验证不等同于 Windows 微信实机采集验证。
