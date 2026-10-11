"""界面采集器专属配置；wemo2 不解释这些字段。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wemo2_collector.models import ControllerConfig, MatchConfig, Platform, RouteConfig
from wemo2_collector.profiles import build_profile
from wemo2_collector.timezones import resolve_timezone


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
    theme: str = "dark"
    controller: ControllerConfig = Field(default_factory=ControllerConfig)
    matcher: MatchConfig = Field(default_factory=MatchConfig)
    route: RouteConfig = Field(default_factory=RouteConfig)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """校验时区名称，保留字符串以便配置序列化。"""
        resolve_timezone(value)
        return value


def load_windows_options(options: dict, directory: Path) -> dict:
    config = WindowsCollectorConfig.model_validate(options)
    if config.platform != Platform.windows:
        raise ValueError("windows_screenshot 采集器不支持 Android")
    profile = build_profile()
    profile.validate_theme(config.theme)
    if config.route.target != "none":
        profile.get_route(config.route.target)
    path = config.screenshot_dir
    if not path.is_absolute():
        path = (directory / path).resolve()
    config = config.model_copy(update={"screenshot_dir": path})
    return {"config": config}
