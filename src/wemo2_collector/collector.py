"""界面采集编排；仅此组装入口感知平台和具体组件实现。"""

from pathlib import Path

from wemo2.interfaces import BaseCollector
from wemo2.models import Observation
from wemo2_collector.config import WindowsCollectorConfig
from wemo2_collector.interaction import UiInteractor
from wemo2_collector.locating import RegistryLocator
from wemo2_collector.matching import TemplateMatcher
from wemo2_collector.profiles import build_profiles
from wemo2_collector.routing import PageRouter
from wemo2_collector.windows.driver import WindowsDriver
from wemo2_collector.windows.screenshot import WindowsScreenshotter


class WindowsScreenshotCollector(BaseCollector):
    def __init__(self, config: WindowsCollectorConfig):
        self.config = config
        pages, themes = build_profiles()
        self.driver = WindowsDriver(config.window_title, config.window_process_id)
        self.screenshotter = WindowsScreenshotter(config.screenshot_dir)
        self.matcher = TemplateMatcher(config.matcher)
        self.locator = RegistryLocator(themes, config.theme, self.matcher)
        self.interaction = UiInteractor(self.driver, self.screenshotter, self.locator)
        self.router = PageRouter(self.interaction, pages, config.route)

    def collect(self) -> list[Observation]:
        self.interaction.reset()
        try:
            self.driver.attach()
            if self.config.route.target == "none":
                self.interaction.capture()
            else:
                self.router.run(self.config.route.target)
            return []  # 当前只交付界面证据，尚未识别朋友圈动态。
        finally:
            self.driver.close()

    def artifacts(self) -> list[Path]:
        return self.interaction.artifacts()

    def stop(self) -> None:
        self.driver.close()
