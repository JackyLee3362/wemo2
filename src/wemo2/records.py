"""每条朋友圈的本地归档契约，不包含窗口操作实现。"""

import hashlib
import json
import unicodedata
from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


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
    media: list[ArchiveMedia] = Field(default_factory=list)
    evidence: list[MomentEvidence] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def moment_key(person: str | None, text: str | None, text_status: str) -> str | None:
    """按人物和完整文案生成身份键；未知和截断正文不参与本地归档合并。"""
    if not person or not person.strip() or text is None or text_status != "complete":
        return None
    person = " ".join(unicodedata.normalize("NFC", person).split())
    text = " ".join(unicodedata.normalize("NFC", text).split())
    payload = json.dumps([person, text], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
