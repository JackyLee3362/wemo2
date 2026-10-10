"""Windows 朋友圈路由：定位入口、一次点击、验证目标窗口。"""

import time
from pathlib import Path

from loguru import logger

from wemo2_collector.interfaces import BaseLocator, BasePageRouter, BaseScreenshotter
from wemo2_collector.models import Page, RouteConfig, Screenshot
from wemo2_collector.windows.driver import WindowsDriver


class WindowsPageRouter(BasePageRouter):
    def __init__(
        self,
        driver: WindowsDriver,
        screenshotter: BaseScreenshotter,
        locator: BaseLocator,
        config: RouteConfig,
    ):
        self.driver = driver
        self.screenshotter = screenshotter
        self.locator = locator
        self.config = config
        self.artifacts: list[Path] = []

    def _capture(self) -> Screenshot:
        surface = self.driver.prepare_capture()
        frame = self.screenshotter.capture(surface)
        self.driver.verify_capture(surface)
        self.artifacts.append(frame.image_path)
        return frame

    def detect(self, frame: Screenshot) -> Page:
        if frame.surface != self.driver.current_surface():
            return Page.unknown
        if self.driver.window.Name == self.config.window_title:
            return Page.moments
        if self.locator.locate(frame, "moments_entry") is not None:
            return Page.home
        return Page.unknown

    def ensure(self, page: Page) -> Screenshot:
        if page != Page.moments:
            raise ValueError("首版路由只支持朋友圈")
        # 已打开目标窗口时直接切换，不重复点击入口。
        if self.driver.switch_to_window(self.config.window_title):
            frame = self._capture()
            if self.detect(frame) == page:
                return frame
        frame = self._capture()
        target = self.locator.locate(frame, "moments_entry")
        if target is None:
            raise RuntimeError(
                "朋友圈入口未可靠识别或匹配不唯一，请检查模板、主题和 DPI"
            )
        logger.info(
            "朋友圈入口匹配成功，分数 {:.3f}，位置 {}", target.score, target.bounds
        )
        self.driver.click(frame, target)
        deadline = time.monotonic() + self.config.timeout_seconds
        while time.monotonic() < deadline:
            if self.driver.switch_to_window(self.config.window_title):
                result = self._capture()
                if self.detect(result) == page:
                    logger.info("已验证朋友圈窗口")
                    return result
            time.sleep(0.2)
        raise RuntimeError(
            f"点击后未找到同一微信进程的“{self.config.window_title}”窗口，路由超时"
        )

    def advance(self) -> Screenshot | None:
        raise NotImplementedError("当前只实现朋友圈入口路由，尚未实现滚动采集")
