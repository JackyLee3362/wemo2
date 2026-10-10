"""页面状态、页面标志及导航边的注册表。"""

from collections import deque

from pydantic import BaseModel, ConfigDict, Field


class PageSpec(BaseModel):
    """required 必须同时匹配；forbidden 任意匹配则排除该页。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    required: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)


class RouteSpec(BaseModel):
    """一个导航步骤；verified=False 只交付点击证据，不宣称到达页面。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    source: str
    destination: str
    target: str
    verified: bool = True
    follow_foreground: bool = False
    settle_seconds: float = Field(default=0.2, ge=0, le=10)


class PageRegistry:
    """统一维护页面和路由；路由器不包含具体微信页面分支。"""

    def __init__(self):
        self.pages: dict[str, PageSpec] = {}
        self.routes: dict[str, RouteSpec] = {}

    def register_page(self, page: PageSpec) -> None:
        if page.name in self.pages or page.name == "unknown":
            raise ValueError(f"页面名称重复或保留：{page.name}")
        self.pages[page.name] = page

    def register_route(self, route: RouteSpec) -> None:
        if route.name in self.routes:
            raise ValueError(f"路由已注册：{route.name}")
        self.get_page(route.source)
        destination = self.get_page(route.destination)
        if route.verified and not destination.required:
            raise ValueError("验证型路由的目标页必须有页面标志")
        self.routes[route.name] = route

    def get_page(self, name: str) -> PageSpec:
        if name not in self.pages:
            raise ValueError(f"未注册页面：{name}")
        return self.pages[name]

    def get_route(self, name: str) -> RouteSpec:
        if name not in self.routes:
            raise ValueError(f"未注册路由：{name}")
        return self.routes[name]

    def path(self, source: str, destination: str) -> list[RouteSpec]:
        """按注册的有向边寻找最短路径，避免循环导航。"""
        self.get_page(source)
        self.get_page(destination)
        queue = deque([(source, [])])
        visited = {source}
        while queue:
            page, steps = queue.popleft()
            if page == destination:
                return steps
            for route in self.routes.values():
                if route.source != page or route.destination in visited:
                    continue
                visited.add(route.destination)
                queue.append((route.destination, steps + [route]))
        raise ValueError(f"没有从 {source} 到 {destination} 的路由")
