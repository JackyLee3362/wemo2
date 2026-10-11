"""界面采集器专属配置；wemo2 不解释这些字段。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wemo2_collector.models import (
    ControllerConfig,
    FeedCaptureConfig,
    MatchConfig,
    Platform,
    RouteConfig,
)
from wemo2_collector.profiles import build_profile
from wemo2_collector.timezones import resolve_timezone


class WindowsCollectorConfig(BaseModel):
    """窗口、截图、主题和导航设置，仅由界面采集包维护。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: Platform = Platform.windows
    screenshot_dir: Path = Path("data/screenshot")
    resource_dir: Path = Path(
        "data/moments"
    )  # 资源根目录；下面依次为账号、日期、朋友圈。
    account: str = "default"  # 显式账号命名空间，不把朋友圈作者当成登录账号。
    timezone: str = Field(
        default="Asia/Shanghai", min_length=1, description="截图时间使用的 IANA 时区"
    )
    window_title: str = Field(default="微信", min_length=1)
    window_process_id: int | None = Field(default=None, gt=0)
    theme: str = "dark"
    controller: ControllerConfig = Field(default_factory=ControllerConfig)
    matcher: MatchConfig = Field(default_factory=MatchConfig)
    route: RouteConfig = Field(default_factory=RouteConfig)
    feed: FeedCaptureConfig = Field(default_factory=FeedCaptureConfig)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """校验时区名称，保留字符串以便配置序列化。"""
        resolve_timezone(value)
        return value

    @field_validator("account")
    @classmethod
    def validate_account(cls, value: str) -> str:
        """账号直接用于 Windows 目录名，禁止路径分隔符和保留设备名。"""
        if not value or value in (".", "..") or value.rstrip(" .") != value:
            raise ValueError("账号目录名不能为空，也不能以空格或点结尾")
        if any(character in value for character in '<>:"/\\|?*') or any(
            ord(character) < 32 for character in value
        ):
            raise ValueError("账号目录名不能包含路径分隔符或 Windows 非法字符")
        reserved = {"CON", "PRN", "AUX", "NUL"}
        for number in range(1, 10):
            reserved.add(f"COM{number}")
            reserved.add(f"LPT{number}")
        if value.split(".")[0].upper() in reserved:
            raise ValueError("账号目录名不能使用 Windows 保留设备名")
        return value


def load_windows_options(options: dict, directory: Path) -> dict:
    config = WindowsCollectorConfig.model_validate(options)
    if config.platform != Platform.windows:
        raise ValueError("windows_screenshot 采集器不支持 Android")
    profile = build_profile()
    profile.validate_theme(config.theme)
    if config.feed.enabled and config.feed.save_media:
        profile.media_workflow(config.feed.media_workflow)
    if config.route.target != "none":
        profile.get_route(config.route.target)
    path = config.screenshot_dir
    if not path.is_absolute():
        path = (directory / path).resolve()
    config = config.model_copy(update={"screenshot_dir": path})
    archive_path = config.resource_dir
    if not archive_path.is_absolute():
        archive_path = (directory / archive_path).resolve()
    config = config.model_copy(update={"resource_dir": archive_path})
    return {"config": config}
