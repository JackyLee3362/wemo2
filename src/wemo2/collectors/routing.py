"""通用页面路由：通过注册的页面标志和有向路由执行导航。"""

from wemo2.collectors.controller import UiActionError, UiController
from wemo2.collectors.models import ActionResult, Expectation, RouteConfig, Screenshot
from wemo2.collectors.profiles import RouteSpec, UiProfile


class PageRouter:
    """读取统一 profile 规划导航路径，将目标、前置页面和预期交给控制器。

    不直接操作平台，也不维护等待重试循环；未验证页面不能作为中间节点。
    """

    def __init__(
        self, controller: UiController, profile: UiProfile, config: RouteConfig
    ):
        self.controller = controller
        self.last_result: ActionResult | None = None
        self.profile = profile
        self.config = config

    def detect(self, frame: Screenshot) -> str:
        return self.controller.detect_page(frame)

    def ensure(self, page: str) -> Screenshot:
        """只返回验证过的页面；未配置标志的动态列表不能伪装成功。"""
        if not self.profile.get_page(page).required:
            raise ValueError(f"页面 {page} 尚未定义验证特征，动态列表需提供真实样本")
        return self._navigate(page, require_verified=True)

    def run(self, action: str) -> Screenshot:
        """执行注册动作，未验证型动作只保存截图并在日志明确状态。"""
        route = self.profile.get_route(action)
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
        self.profile.validate_structure()
        frame = self.controller.capture()
        current = self.detect(frame)
        if current == destination:
            return frame
        if final_route is not None and current in final_route.alternate_destinations:
            return frame
        if current == "unknown":
            raise RuntimeError("当前页面未可靠识别，未执行导航")
        if final_route is None:
            steps = self.profile.path(current, destination)
        else:
            steps = self.profile.path(current, final_route.source) + [final_route]
        # 完整校验路径再执行第一个动作，不能先点击再发现路径不可验证。
        for index, route in enumerate(steps):
            if not route.verified and (require_verified or index != len(steps) - 1):
                raise ValueError("路径包含未验证页面，不能继续导航")
        for route in steps:
            alternate_pages = route.alternate_destinations
            if final_route is None and route.destination == destination:
                alternate_pages = []  # ensure 精确页面，不能把恢复子页面当作该页面。
            result = self.controller.click(
                route.target,
                Expectation(
                    kind="page", page=route.destination, alternate_pages=alternate_pages
                ),
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
            actual = (
                None if result.validation is None else result.validation.actual_page
            )
            if actual == destination:
                return frame
            if route == final_route and actual in alternate_pages:
                return frame
            if route.verified and actual is not None and actual != route.destination:
                raise RuntimeError(f"已到恢复页面 {actual}，后续路径不适用，未继续输入")
        return frame
