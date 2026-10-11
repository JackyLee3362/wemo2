# 朋友圈资源目录与元信息

共用 AppConfig 维护资源根目录及账号，collect 和 save 读取同一份配置。截图、主题和路由专属配置仍位于 collector_options 中。

```toml
resource_dir = "data/moments"
account = "default"

[collector_options]
timezone = "Asia/Shanghai"
```

`resource_dir` 相对配置文件所在目录解析，绝对路径直接使用。
`account` 是登录/备份账号的目录名，由用户明确配置，不是朋友圈作者。
Windows 非法路径字符和保留设备名在配置加载时拒绝。

```text
data/moments/
└── default/                         # 配置中的备份账号
    └── 2026-10-11/                  # 推断的发布时间，精确到日
        └── 朋友-牛哥/               # 去掉 emoji 的人物名
            └── <人物与文案的 SHA-256>/
                ├── post.json
                ├── screenshot.png
                ├── screenshots/    # 历次截图证据
                └── media/          # 已保存的原图片、视频
                    ├── <sha256>.jpg
                    └── <sha256>.mp4
```

日期无法识别时使用 `unknown_date`。人物或正文识别不完整时使用
`unidentified_<UUID>` 文件夹，仍保存 JSON、截图及所有已关联的下载文件。
能够确认人物和文案、但日期尚未知的记录，后续识别到日期时迁入对应日期目录。

## JSON 契约

`src/wemo2/records.py` 的 Pydantic 模型是唯一数据契约。以下为字段节选，不是完整的校验样例：

```json
{
  "schema_version": "1.0",
  "account": "default",
  "post_id": "<去重键或临时标识>",
  "dedup_key": "<人物与文案哈希，未知时为 null>",
  "approximate_date": "2026-10-08",
  "observed_dates": ["2026-10-08"],
  "person": "高中-厉家成",
  "text": "和小狗喝茶",
  "text_status": "complete",
  "primary_screenshot": "screenshot.png",
  "screenshot_complete": true,
  "media_requested": true,
  "media_complete": null,
  "media": [
    {"path": "media/<sha256>.jpg", "kind": "image", "sha256": "<实际字节哈希>", "size": 102400}
  ],
  "evidence": [
    {
      "captured_at": "2026-10-09T12:00:00+08:00",
      "timezone": "Asia/Shanghai",
      "approximate_date": "2026-10-08",
      "date_raw": "23小时前",
      "screenshot": "screenshots/<hash>.png",
      "source_frames": ["<原始截图 ID>"],
      "screenshot_complete": true
    }
  ],
  "errors": []
}
```

`first_seen_at/last_seen_at` 记录采集时间，不能替代动态的发布时间。
日期从相对时间或日历日期原文推断；缺少年份的月日使用最近过去日期。
JSON 保留时间原文和时区，未知日期不拿采集当天冒充。
作者与正文通过 OCR 提取，正文限制在作者下方、图片/时间之前，排除图片广告文字、地点和评论。
蓝色昵称的相邻 OCR 片段合并，表情与低置信度文字仍可能不完整。
“全文”未展开、缺失底部或识别置信度不足时，正文标记 partial。

## 去重和完整度

- 同账号内以规范化后的 `person + text` 去重：人物先移除 emoji，再进行 NFC Unicode、合并空白，用 JSON 数组编码两个字段后计算 SHA-256；文案中的 emoji 不移除。
- 日期不参与去重。同人物、同文案在另一天再次出现时复用已有目录，`observed_dates` 和 `evidence` 保留所有观察。
- 不同人物或不同文案不会合并。未识别人名、未知文案或 partial 文案不生成身份键。
- 明确空文案 `""` 参与去重，因此同人物的纯图片动态会合并媒体；这是“人物 + 文案”规则的直接结果。
- 媒体以实际文件字节 SHA-256 去重，图片与视频拷贝到对应朋友圈的 media 目录，JSON 引用相对路径。
- 文件类型、大小、哈希与截图证据保留；保存成功但返回失败的文件也归档并记录错误。
- `media_complete=null` 表示尚未获得可靠媒体总数，不能仅凭“未找到候选”声称全部备份。
- 首尾片段及缺少人物顶部的截图保持不完整。当前“··”锚点切分不保证包含全部评论。
- 归档发生在采集之后；原始调试目录保留，不清理历史文件。跨运行扫描仍可能再次下载媒体，正式资源目录合并。

此版实现本地留痕，尚未将视觉记录转换为核心 Observation 或同步 Memos。
视频归档支持已保存的文件；视频查看器和播放完整性仍需实机验证。

日期目录统一为 `YYYY-MM-DD`，例如 `data/moments/default/2026-10-11/朋友-牛哥/<人物与完整文案的 SHA-256>/`；JSON 日期使用相同格式。默认账号是 `default`，不是朋友圈作者；资源根目录由 `resource_dir` 配置，相对路径以配置文件目录为基准。已有相同去重键的旧 `YYYYMMDD` 目录在复用时整体迁移，保留截图及媒体。

完整片段在当前屏的媒体保存后即归档，末尾未闭合片段在停止时归档；`screenshot_dir/cards_*/media/` 是下载暂存及调试证据，最终自包含资源位于上述账号目录。日期未知保留 `unknown_date`，人物/文案不完整保留 `unidentified_*`，不使用采集当天或截断正文伪造身份。

## 保存命令

`wemo2 collect --config wemo2.moments.toml` 生成并合并本地归档；`wemo2 save --memos --config wemo2.moments.toml` 读取同一资源目录和账号。去重已经由采集器完成，不再经过独立去重器。ArchiveReader 校验资源引用及媒体哈希后交给 BaseStore；--memos 当前选择 PseudoStore，只演示内存保存，不连接 Memos。后续目标版本为 0.31。

## 人物名称规范

`朋友-牛哥🐮` 规范为 `朋友-牛哥`，用于目录层级、post.json.person 与人物文案哈希；post.json.person_raw 保存解析原文。Windows 非法路径字符替换为下划线，保留设备名加下划线前缀；没有文字的人名使用 unknown_person，去重键仍为 null。普通文字、数字、中文和连字符不受 emoji 清洗影响。复合 emoji、肤色修饰和旗帜由 [emoji.replace_emoji](https://emoji-python.readthedocs.io/en/latest/api.html#emoji.replace_emoji) 处理。

保存命令可以读取日期/人物/哈希以及旧的日期/哈希目录。采集复用旧记录时会升级人物层级及清洗后的身份键，整条记录的截图、媒体和元信息一起迁移。
