"""微信页面、路由和主题模板统一由一个 Pydantic 配置结构维护。"""

from collections import deque
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PageSpec(BaseModel):
    """页面的必需和排除标志；无必需标志的页面尚不能自动验证。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    required: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)


class RouteSpec(BaseModel):
    """单步导航数据；未验证目标只能作为最终的待验证动作。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    source: str
    destination: str
    target: str
    verified: bool = True
    follow_foreground: bool = False
    settle_seconds: float = Field(default=0.2, ge=0, le=10)


class TemplateSpec(BaseModel):
    """目标模板、参考 DPI 与截图搜索范围，不包含匹配或点击行为。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path
    reference_scale: float = Field(default=1.25, gt=0)
    region_ratio: float = Field(default=0.1, gt=0, le=1)
    rgb: bool = False  # 选中样式需同时校验颜色；普通图标默认按灰度匹配。


class UiProfile(BaseModel):
    """统一维护页面、路由及各主题目标模板，替代两套注册器。"""

    model_config = ConfigDict(extra="forbid")
    pages: dict[str, PageSpec] = Field(default_factory=dict)
    routes: dict[str, RouteSpec] = Field(default_factory=dict)
    themes: dict[str, dict[str, TemplateSpec]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_structure(self):
        """检查名称及路由端点，不允许未验证页面冒充可验证目标。"""
        for name, page in self.pages.items():
            if name != page.name or name == "unknown":
                raise ValueError(f"页面名称与键不一致或使用保留名称：{name}")
        for name, route in self.routes.items():
            if name != route.name:
                raise ValueError(f"路由名称与键不一致：{name}")
            self.get_page(route.source)
            destination = self.get_page(route.destination)
            if route.verified and not destination.required:
                raise ValueError("验证型路由的目标页必须有页面标志")
        return self

    def get_page(self, name: str) -> PageSpec:
        if name not in self.pages:
            raise ValueError(f"未定义页面：{name}")
        return self.pages[name]

    def get_route(self, name: str) -> RouteSpec:
        if name not in self.routes:
            raise ValueError(f"未定义路由：{name}")
        return self.routes[name]

    def target(self, theme: str, name: str) -> TemplateSpec:
        if theme not in self.themes:
            raise ValueError(f"未定义主题：{theme}；可选：{'、'.join(self.themes)}")
        if name not in self.themes[theme]:
            raise ValueError(f"主题 {theme} 未定义目标：{name}")
        return self.themes[theme][name]

    def validate_theme(self, theme: str) -> None:
        """启动时检查所选主题完整覆盖页面和导航目标。"""
        self.validate_structure()
        if theme not in self.themes:
            raise ValueError(f"未定义主题：{theme}")
        for page in self.pages.values():
            for target in page.required + page.forbidden:
                self.target(theme, target)
        for route in self.routes.values():
            self.target(theme, route.target)

    def path(self, source: str, destination: str) -> list[RouteSpec]:
        """查找短导航路径；使用小型有向图即可，无需状态机框架。"""
        self.validate_structure()
        self.get_page(source)
        self.get_page(destination)
        queue = deque([(source, [])])
        visited = {source}
        while queue:
            page, steps = queue.popleft()
            if page == destination:
                return steps
            for route in self.routes.values():
                if route.source == page and route.destination not in visited:
                    visited.add(route.destination)
                    queue.append((route.destination, steps + [route]))
        raise ValueError(f"没有从 {source} 到 {destination} 的路由")


def build_profile() -> UiProfile:
    """微信内置页面与主题的唯一维护位置，返回独立配置对象。"""
    assets = Path(__file__).parent / "assets"
    return UiProfile(
        pages={
            "home": PageSpec(
                name="home",
                required=["moments_entry"],
                forbidden=["discover_selected", "moments_menu_selected"],
            ),
            "discover": PageSpec(
                name="discover",
                required=["discover_selected", "discover_menu"],
                forbidden=["moments_menu_selected"],
            ),
            # 菜单选中与动态列表加载是两个独立状态。
            "moments_selected": PageSpec(
                name="moments_selected", required=["moments_menu_selected"]
            ),
            "moments": PageSpec(name="moments"),
        },
        routes={
            "discover": RouteSpec(
                name="discover",
                source="home",
                destination="discover",
                target="moments_entry",
            ),
            "moments_entry": RouteSpec(
                name="moments_entry",
                source="discover",
                destination="moments_selected",
                target="moments_menu",
                follow_foreground=True,
                settle_seconds=0.5,
            ),
        },
        themes={
            "dark": {
                "moments_entry": TemplateSpec(path=assets / "moments_entry.png"),
                "discover_selected": TemplateSpec(
                    path=assets / "discover_selected.png"
                ),
                "discover_menu": TemplateSpec(
                    path=assets / "discover_menu.png", region_ratio=0.4
                ),
                "moments_menu": TemplateSpec(
                    path=assets / "moments_menu.png", region_ratio=0.4
                ),
                "moments_menu_selected": TemplateSpec(
                    path=assets / "moments_menu_selected.png",
                    region_ratio=0.6,
                    rgb=True,
                ),
                "moments_anchor": TemplateSpec(
                    path=assets / "moments_anchor.png", region_ratio=1.0
                ),
            }
        },
    )
