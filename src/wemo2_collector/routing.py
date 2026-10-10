"""Windows 页面路由：定位指南针入口，截图验证发现菜单。"""

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
        # 页面分类只依赖截图；实时性与焦点由截图/点击流程分别校验。
        selected = self.locator.locate(frame, "discover_selected")
        if selected is not None:
            menu = self.locator.locate(frame, "discover_menu")
            if menu is not None:
                return Page.discover
            return Page.unknown
        if self.locator.locate(frame, "moments_entry") is not None:
            return Page.home
        return Page.unknown

    def ensure(self, page: Page) -> Screenshot:
        if page != Page.discover:
            raise ValueError(
                "当前参考图只能验证发现菜单；朋友圈动态列表需另行定义页面特征"
            )
        frame = self._capture()
        if self.detect(frame) == page:
            return frame
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
            result = self._capture()
            if self.detect(result) == page:
                logger.info("已通过选中图标与菜单特征验证发现页面")
                return result
            time.sleep(0.2)
        raise RuntimeError("点击后未识别到选中的指南针与发现菜单，路由超时")

    def open_moments_entry(self) -> Screenshot:
        """验证发现页后点击朋友圈菜单一次，保存待验证的结果截图。"""
        frame = self.ensure(Page.discover)
        target = self.locator.locate(frame, "moments_menu")
        if target is None:
            raise RuntimeError("朋友圈菜单未可靠识别或匹配不唯一，未执行点击")
        logger.info(
            "朋友圈菜单匹配成功，分数 {:.3f}，位置 {}", target.score, target.bounds
        )
        self.driver.click(frame, target)
        time.sleep(0.5)
        # 某些微信版本会打开独立窗口；只能跟随同进程的前台窗口。
        self.driver.follow_foreground()
        result = self._capture()
        logger.warning(
            "已执行朋友圈菜单点击并保存截图，动态列表页面仍待验证：{}",
            result.image_path,
        )
        return result

    def advance(self) -> Screenshot | None:
        raise NotImplementedError("当前只实现朋友圈入口路由，尚未实现滚动采集")
