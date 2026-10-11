"""采集与保存命令的通用结果，不包含窗口或图像匹配配置。"""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from wemo2.records import MomentRecord


class Platform(StrEnum):
    """数据来源平台，输入和截图细节由采集模块维护。"""

    windows = "windows"
    android = "android"


class CollectResult(BaseModel):
    """采集交付的本地归档与诊断文件，归档去重已经由采集器完成。"""

    model_config = ConfigDict(extra="forbid")
    posts: int = Field(default=0, ge=0)
    artifacts: list[Path] = Field(default_factory=list)
    stop_reason: str = "completed"


class ArchivedPost(BaseModel):
    """从一个朋友圈文件夹读取并校验后的元信息及实际资源路径。"""

    model_config = ConfigDict(extra="forbid")
    metadata_path: Path
    record: MomentRecord
    screenshot: Path
    media_files: list[Path] = Field(default_factory=list)
    evidence_files: list[Path] = Field(default_factory=list)


class SaveResult(BaseModel):
    """交给目标存储器的归档数量；模拟存储的提示由注册信息明确声明。"""

    model_config = ConfigDict(extra="forbid")
    posts: int = Field(default=0, ge=0)
    media: int = Field(default=0, ge=0)
    store: str
