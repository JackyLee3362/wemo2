"""界面采集编排；仅此组装入口感知平台和具体组件实现。"""

from pathlib import Path

from wemo2.interfaces import BaseCollector
from wemo2.models import Observation
from wemo2_collector.config import WindowsCollectorConfig
from wemo2_collector.controller import UiController
from wemo2_collector.locating import AirtestLocator
from wemo2_collector.profiles import build_profile
from wemo2_collector.routing import PageRouter
from wemo2_collector.windows.driver import WindowsDriver
from wemo2_collector.windows.screenshot import WindowsScreenshotter


class WindowsScreenshotCollector(BaseCollector):
    """组装 Windows 驱动、截图器、Airtest 定位器、控制器和页面路由器。

    编排一次截图或导航，交付界面证据并释放会话；当前尚不解析朋友圈动态。
    """

    def __init__(self, config: WindowsCollectorConfig):
        self.config = config
        self.profile = build_profile()
        self.driver = WindowsDriver(config.window_title, config.window_process_id)
        self.screenshotter = WindowsScreenshotter(
            config.screenshot_dir, config.timezone
        )
        self.locator = AirtestLocator(self.profile, config.theme, config.matcher)
        self.controller = UiController(
            self.driver,
            self.screenshotter,
            self.locator,
            self.profile,
            config.controller,
            config.screenshot_dir,
        )
        self.router = PageRouter(self.controller, self.profile, config.route)

    def collect(self) -> list[Observation]:
        self.controller.reset()
        try:
            self.driver.attach()
            if self.config.route.target == "none":
                self.controller.capture()
            else:
                self.router.run(self.config.route.target)
            return []  # 当前只交付界面证据，尚未识别朋友圈动态。
        finally:
            self.driver.close()

    def artifacts(self) -> list[Path]:
        return self.controller.artifacts()

    def stop(self) -> None:
        self.driver.close()
