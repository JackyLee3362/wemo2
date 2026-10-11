"""从主题注册表解析语义目标，再交给通用图片匹配器。"""

from wemo2_collector.interfaces import BaseImageMatcher, BaseLocator
from wemo2_collector.models import LocatedTarget, Screenshot
from wemo2_collector.themes import ThemeRegistry


class RegistryLocator(BaseLocator):
    """根据当前主题解析语义目标的模板，委托图片匹配器返回定位结果。

    不包含具体页面分支，也不执行截图或点击。
    """

    def __init__(self, themes: ThemeRegistry, theme: str, matcher: BaseImageMatcher):
        themes.get(theme)
        self.themes = themes
        self.theme = theme
        self.matcher = matcher

    def locate(self, frame: Screenshot, name: str) -> LocatedTarget | None:
        template = self.themes.target(self.theme, name)
        return self.matcher.match(frame, name, template)
