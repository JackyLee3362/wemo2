"""通用页面路由：通过注册的页面标志和有向路由执行导航。"""

import time

from loguru import logger

from wemo2_collector.interfaces import BaseInteractor, BasePageRouter
from wemo2_collector.models import RouteConfig, Screenshot
from wemo2_collector.pages import PageRegistry, RouteSpec


class PageRouter(BasePageRouter):
    def __init__(
        self, interaction: BaseInteractor, pages: PageRegistry, config: RouteConfig
    ):
        self.interaction = interaction
        self.pages = pages
        self.config = config

    def detect(self, frame: Screenshot) -> str:
        matches = []
        cache = {}
        for page in self.pages.pages.values():
            if not page.required:
                continue
            valid = True
            for name in page.required + page.forbidden:
                if name not in cache:
                    cache[name] = self.interaction.locate(frame, name) is not None
                if name in page.required and not cache[name]:
                    valid = False
                if name in page.forbidden and cache[name]:
                    valid = False
            if valid:
                matches.append(page.name)
        if len(matches) != 1:
            return "unknown"
        return matches[0]

    def ensure(self, page: str) -> Screenshot:
        """只返回验证过的页面；未配置标志的动态列表不能伪装成功。"""
        if not self.pages.get_page(page).required:
            raise ValueError(f"页面 {page} 尚未定义验证特征，动态列表需提供真实样本")
        return self._navigate(page, require_verified=True)

    def run(self, action: str) -> Screenshot:
        """执行注册动作，未验证型动作只保存截图并在日志明确状态。"""
        route = self.pages.get_route(action)
        return self._navigate(
            route.destination, require_verified=route.verified, final_route=route
        )

    def _navigate(
        self,
        destination: str,
        *,
        require_verified: bool,
        final_route: RouteSpec | None = None,
    ) -> Screenshot:
        frame = self.interaction.capture()
        current = self.detect(frame)
        if current == destination:
            return frame
        if current == "unknown":
            raise RuntimeError("当前页面未可靠识别，未执行导航")
        if final_route is None:
            steps = self.pages.path(current, destination)
        else:
            steps = self.pages.path(current, final_route.source) + [final_route]
        # 完整校验路径再执行第一个动作，不能先点击再发现路径不可验证。
        for index, route in enumerate(steps):
            if not route.verified and (require_verified or index != len(steps) - 1):
                raise ValueError("路径包含未验证页面，不能继续导航")
        for route in steps:
            self.interaction.click(frame, route.target)
            time.sleep(route.settle_seconds)
            if not route.verified:
                frame = self.interaction.capture(
                    follow_foreground=route.follow_foreground
                )
                logger.warning(
                    "已执行动作 {}，页面 {} 待验证：{}",
                    route.name,
                    route.destination,
                    frame.image_path,
                )
                return frame
            deadline = time.monotonic() + self.config.timeout_seconds
            while time.monotonic() < deadline:
                frame = self.interaction.capture(
                    follow_foreground=route.follow_foreground
                )
                if self.detect(frame) == route.destination:
                    logger.info("页面 {} 已验证", route.destination)
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError(f"导航至 {route.destination} 超时；未重复点击")
        return frame

    def advance(self) -> Screenshot | None:
        raise NotImplementedError("当前尚未实现滚动采集")
