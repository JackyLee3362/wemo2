"""界面采集器专属配置；wemo2 不解释这些字段。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from wemo2_collector.models import ControllerConfig, MatchConfig, Platform, RouteConfig
from wemo2_collector.profiles import build_profiles


class WindowsCollectorConfig(BaseModel):
    """窗口、截图、主题和导航设置，仅由界面采集包维护。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: Platform = Platform.windows
    screenshot_dir: Path = Path("data/screenshot")
    window_title: str = Field(default="微信", min_length=1)
    window_process_id: int | None = Field(default=None, gt=0)
    theme: str = "dark"
    controller: ControllerConfig = Field(default_factory=ControllerConfig)
    matcher: MatchConfig = Field(default_factory=MatchConfig)
    route: RouteConfig = Field(default_factory=RouteConfig)


def load_windows_options(options: dict, directory: Path) -> dict:
    config = WindowsCollectorConfig.model_validate(options)
    if config.platform != Platform.windows:
        raise ValueError("windows_screenshot 采集器不支持 Android")
    pages, themes = build_profiles()
    themes.validate(config.theme, pages)
    if config.route.target != "none":
        pages.get_route(config.route.target)
    path = config.screenshot_dir
    if not path.is_absolute():
        path = (directory / path).resolve()
    config = config.model_copy(update={"screenshot_dir": path})
    return {"config": config}
