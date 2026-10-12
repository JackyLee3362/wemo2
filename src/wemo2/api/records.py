"""每条朋友圈的本地归档契约，不包含窗口操作实现。"""

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from wemo2.common.names import normalize_person_with_raw


class ArchiveMedia(BaseModel):
    """已拷贝到朋友圈文件夹的媒体，路径相对该文件夹，哈希对应实际字节。"""

    model_config = ConfigDict(extra="forbid")
    path: str
    kind: Literal["image", "video"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0)


class MomentEvidence(BaseModel):
    """一次采集留下的截图及发布时间原文，重复采集不会覆盖已有证据。"""

    model_config = ConfigDict(extra="forbid")
    captured_at: AwareDatetime
    timezone: str
    approximate_date: date | None
    date_raw: str | None
    screenshot: str
    source_frames: list[str]
    screenshot_complete: bool


class MomentRecord(BaseModel):
    """一个朋友圈文件夹的元信息；按人物和完整文案去重，日期不参与身份键。"""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    post_id: str
    account: str  # 备份账号命名空间，隔离不同账号的去重与资源。
    dedup_key: str | None  # 不完整识别不生成去重键，防止未知数据错误合并。
    person: str | None
    person_raw: str | None = None  # OCR 原始人名留痕；不参与目录或身份去重。
    text: str | None
    text_status: Literal["unknown", "partial", "complete"]
    approximate_date: date | None  # 推断到日；推断不了就为空。
    observed_dates: list[date] = Field(default_factory=list)
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    primary_screenshot: str = "screenshot.png"
    screenshot_complete: bool = False
    media_complete: bool | None = None  # 未获得实际媒体总数时不能声明全部下载。
    media_requested: bool
    media_skip_reason: str | None = None  # video_channel 表示只保留分享卡片截图。
    media: list[ArchiveMedia] = Field(default_factory=list)
    evidence: list[MomentEvidence] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_name(self):
        """统一元信息中的人名，读取旧 JSON 时保留原始 emoji 名称作为证据。"""
        person, raw = normalize_person_with_raw(self.person, self.person_raw)
        self.person = person
        self.person_raw = raw
        return self
