"""首阶段 Collector：保存窗口截图，不把界面误认成朋友圈动态。"""

from pathlib import Path

from loguru import logger

from wemo2.interfaces import BaseCollector
from wemo2.models import Observation
from wemo2_collector.locating import MomentsEntryLocator
from wemo2_collector.models import Page, Platform, RouteConfig
from wemo2_collector.routing import WindowsPageRouter
from wemo2_collector.windows.driver import WindowsDriver
from wemo2_collector.windows.screenshot import WindowsScreenshotter


class WindowsScreenshotCollector(BaseCollector):
    def __init__(
        self,
        platform: Platform = Platform.windows,
        screenshot_dir: str | Path = "data/screenshot",
        window_title: str = "微信",
        window_process_id: int | None = None,
        route: RouteConfig | dict | None = None,
    ):
        if Platform(platform) != Platform.windows:
            raise ValueError("windows_screenshot 采集器不支持 Android")
        self.driver = WindowsDriver(window_title, window_process_id)
        self.screenshotter = WindowsScreenshotter(screenshot_dir)
        self._artifacts: list[Path] = []
        self.route = RouteConfig.model_validate(route or {})

    def collect(self) -> list[Observation]:
        self._artifacts = []
        try:
            self.driver.attach()
            if self.route.target == "moments":
                locator = MomentsEntryLocator(self.route)
                router = WindowsPageRouter(
                    self.driver, self.screenshotter, locator, self.route
                )
                router.ensure(Page.moments)
                self._artifacts = list(router.artifacts)
                return []
            surface = self.driver.prepare_capture()
            frame = self.screenshotter.capture(surface)
            self.driver.verify_capture(surface)
            self._artifacts.append(frame.image_path)
            logger.info("微信窗口截图已保存：{}", frame.image_path)
            return []  # 尚未识别动态，不生成伪造的 Observation。
        finally:
            self.driver.close()

    def artifacts(self) -> list[Path]:
        return list(self._artifacts)

    def stop(self) -> None:
        self.driver.close()
