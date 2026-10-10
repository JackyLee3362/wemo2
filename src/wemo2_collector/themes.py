"""主题与语义目标的模板注册表，不执行截图或点击。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from wemo2_collector.pages import PageRegistry


class TemplateSpec(BaseModel):
    """一个语义目标的模板及截图搜索区域；比例相对整个截图宽度。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: Path
    reference_scale: float = Field(default=1.25, gt=0)
    region_ratio: float = Field(default=0.1, gt=0, le=1)


class ThemeSpec(BaseModel):
    """同一布局主题的一组模板；页面与路由只引用语义目标名。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    targets: dict[str, TemplateSpec]


class ThemeRegistry:
    def __init__(self):
        self.themes: dict[str, ThemeSpec] = {}

    def register(self, theme: ThemeSpec) -> None:
        if theme.name in self.themes:
            raise ValueError(f"主题已注册：{theme.name}")
        self.themes[theme.name] = theme

    def get(self, name: str) -> ThemeSpec:
        if name not in self.themes:
            raise ValueError(f"未注册主题：{name}；可选：{'、'.join(self.themes)}")
        return self.themes[name]

    def target(self, theme: str, name: str) -> TemplateSpec:
        profile = self.get(theme)
        if name not in profile.targets:
            raise ValueError(f"主题 {theme} 未定义目标：{name}")
        return profile.targets[name]

    def validate(self, theme: str, pages: PageRegistry) -> None:
        """启动时检查主题是否覆盖所有已注册页面和路由目标。"""
        for page in pages.pages.values():
            for target in page.required + page.forbidden:
                self.target(theme, target)
        for route in pages.routes.values():
            self.target(theme, route.target)
