"""微信页面与主题的唯一登记处；新增页面/皮肤只修改注册数据。"""

from pathlib import Path

from wemo2_collector.pages import PageRegistry, PageSpec, RouteSpec
from wemo2_collector.themes import TemplateSpec, ThemeRegistry, ThemeSpec


def build_profiles() -> tuple[PageRegistry, ThemeRegistry]:
    pages = PageRegistry()
    pages.register_page(
        PageSpec(
            name="home", required=["moments_entry"], forbidden=["discover_selected"]
        )
    )
    pages.register_page(
        PageSpec(name="discover", required=["discover_selected", "discover_menu"])
    )
    # 动态列表还没有确认过的截图样本，不设置虚假的页面标志。
    pages.register_page(PageSpec(name="moments"))
    pages.register_route(
        RouteSpec(
            name="discover",
            source="home",
            destination="discover",
            target="moments_entry",
        )
    )
    pages.register_route(
        RouteSpec(
            name="moments_entry",
            source="discover",
            destination="moments",
            target="moments_menu",
            verified=False,
            follow_foreground=True,
            settle_seconds=0.5,
        )
    )
    assets = Path(__file__).parent / "assets"
    themes = ThemeRegistry()
    themes.register(
        ThemeSpec(
            name="dark",
            targets={
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
            },
        )
    )
    return pages, themes
