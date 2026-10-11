# 朋友圈资源目录与元信息

采集器配置负责所有资源路径，应用层仅转交 `collector_options`。

```toml
[collector_options]
resource_dir = "data/moments"
account = "default"
timezone = "Asia/Shanghai"
```

`resource_dir` 相对配置文件所在目录解析，绝对路径直接使用。
`account` 是登录/备份账号的目录名，由用户明确配置，不是朋友圈作者。
Windows 非法路径字符和保留设备名在配置加载时拒绝。

```text
data/moments/
└── default/                         # 备份账号
    └── 2026-10-08/                  # 推断的发布时间，精确到日
        └── <人物与文案的 SHA-256>/   # 一条归档朋友圈
            ├── post.json
            ├── screenshot.png       # 主要朋友圈截图，可能是跨屏拼接图
            ├── screenshots/         # 历次采集的截图证据，按图像内容去重
            │   └── <hash>.png
            └── media/               # 实际另存为的媒体，按文件字节去重
                ├── <sha256>.jpg
                └── <sha256>.mp4
```

日期无法识别时使用 `unknown_date`。人物或正文识别不完整时使用
`unidentified_<UUID>` 文件夹，仍保存 JSON、截图及所有已关联的下载文件。
能够确认人物和文案、但日期尚未知的记录，后续识别到日期时迁入对应日期目录。

## JSON 契约

`records.py` 的 Pydantic 模型是唯一数据契约。以下为字段节选，不是完整的校验样例：

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

- 同账号内以规范化后的 `person + text` 去重：NFC Unicode、合并空白，用 JSON 数组编码两个字段后计算 SHA-256。
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
