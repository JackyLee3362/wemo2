"""界面采集器专属配置；wemo2 不解释这些字段。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wemo2.collectors.models import (
    ControllerConfig,
    FeedCaptureConfig,
    MatchConfig,
    Platform,
    RouteConfig,
    SaveDialogConfig,
)
from wemo2.collectors.profiles import build_profile
from wemo2.collectors.timezones import resolve_timezone
from wemo2.common.paths import resolve_path


class WindowsCollectorConfig(BaseModel):
    """窗口、截图、主题和导航设置，仅由界面采集包维护。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: Platform = Platform.windows
    screenshot_dir: Path = Path("data/screenshot")
    timezone: str = Field(
        default="Asia/Shanghai", min_length=1, description="截图时间使用的 IANA 时区"
    )
    window_title: str = Field(default="微信", min_length=1)
    window_process_id: int | None = Field(default=None, gt=0)
    maximize_window: bool = Field(
        default=True, description="连接微信时最大化主窗口；关闭后保留原窗口大小"
    )
    theme: str = "dark"
    controller: ControllerConfig = Field(default_factory=ControllerConfig)
    matcher: MatchConfig = Field(default_factory=MatchConfig)
    route: RouteConfig = Field(default_factory=RouteConfig)
    feed: FeedCaptureConfig = Field(default_factory=FeedCaptureConfig)
    save_dialog: SaveDialogConfig = Field(default_factory=SaveDialogConfig)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """校验时区名称，保留字符串以便配置序列化。"""
        resolve_timezone(value)
        return value


def load_windows_options(options: dict, directory: Path) -> dict:
    """解析界面专属设置，常规采集默认进入朋友圈并滚动，截图调试须显式禁用。"""
    values = dict(options)
    values.setdefault("route", {"target": "moments_entry"})
    values.setdefault("feed", {"enabled": True})
    config = WindowsCollectorConfig.model_validate(values)
    if config.platform != Platform.windows:
        raise ValueError("windows_screenshot 采集器不支持 Android")
    profile = build_profile()
    profile.validate_theme(config.theme)
    if config.feed.enabled and config.feed.save_media:
        profile.media_workflow(config.feed.media_workflow)
    if config.route.target != "none":
        profile.get_route(config.route.target)
    path = resolve_path(config.screenshot_dir, directory)
    config = config.model_copy(update={"screenshot_dir": path})
    return {"config": config}
