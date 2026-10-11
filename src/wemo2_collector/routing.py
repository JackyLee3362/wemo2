"""通用页面路由：通过注册的页面标志和有向路由执行导航。"""

from wemo2_collector.controller import UiActionError
from wemo2_collector.interfaces import BaseController, BasePageRouter
from wemo2_collector.models import ActionResult, Expectation, RouteConfig, Screenshot
from wemo2_collector.pages import PageRegistry, RouteSpec


class PageRouter(BasePageRouter):
    """读取页面注册表规划导航路径，将目标、前置页面和预期交给控制器。

    不直接操作平台，也不维护等待重试循环；未验证页面不能作为中间节点。
    """

    def __init__(
        self, controller: BaseController, pages: PageRegistry, config: RouteConfig
    ):
        self.controller = controller
        self.last_result: ActionResult | None = None
        self.pages = pages
        self.config = config

    def detect(self, frame: Screenshot) -> str:
        return self.controller.detect_page(frame)

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
        self.last_result = None
        frame = self.controller.capture()
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
            result = self.controller.click(
                route.target,
                Expectation(kind="page", page=route.destination),
                source_page=route.source,
                timeout=self.config.timeout_seconds,
                settle_seconds=route.settle_seconds,
                follow_foreground=route.follow_foreground,
            )
            self.last_result = result
            if result.status == "failed":
                raise UiActionError(result)
            if result.after is None:
                raise RuntimeError("控制器未交付动作后的截图")
            if route.verified and result.status != "verified":
                raise RuntimeError("目标页面未通过验证，不能继续导航")
            frame = result.after
        return frame

    def advance(self) -> Screenshot | None:
        raise NotImplementedError("当前尚未实现滚动采集")
