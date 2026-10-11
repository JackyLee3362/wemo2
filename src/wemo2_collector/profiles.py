"""微信页面、路由和主题模板统一由一个 Pydantic 配置结构维护。"""

from collections import deque
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wemo2_collector.models import Rect, Surface


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
    alternate_destinations: list[str] = Field(default_factory=list)
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


class MediaTargetSpec(BaseModel):
    """媒体流程的动态预览、相对区域或 OCR 文字目标，不保存屏幕绝对位置。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["source", "region", "ocr"]
    region: tuple[float, float, float, float] = (0, 0, 1, 1)
    texts: list[str] = Field(default_factory=list)
    threshold: float = Field(default=0.8, gt=0, le=1)

    @model_validator(mode="after")
    def validate_target(self):
        x, y, width, height = self.region
        if not (
            0 <= x < 1
            and 0 <= y < 1
            and width > 0
            and height > 0
            and x + width <= 1
            and y + height <= 1
        ):
            raise ValueError("媒体目标相对区域必须位于 [0, 1] 范围内")
        if self.kind == "ocr" and (
            not self.texts or any(not text.strip() for text in self.texts)
        ):
            raise ValueError("OCR 目标需要非空候选文字")
        if self.kind != "ocr" and self.texts:
            raise ValueError("非 OCR 目标不使用候选文字")
        return self


class MediaStepSpec(BaseModel):
    """媒体单步的动作、前置状态、后置验证和唯一后继；失败始终停止并留证。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["click", "right_click", "wait_ready", "save_file", "close_viewer"]
    target: str | None = None
    before: Literal["original", "viewer", "menu", "save_dialog"]
    expect: Literal["viewer", "menu", "save_dialog", "file", "original"]
    next: str | None = None  # None 为正常终点；没有失败输入自动重放分支。
    timeout_seconds: float = Field(default=5, gt=0, le=60)
    settle_seconds: float = Field(default=0.6, ge=0, le=10)


class MediaWorkflowSpec(BaseModel):
    """媒体流程图及运行上限，启动校验引用、状态衔接并禁止输入循环。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    entry: str
    menu_target: str
    viewer_mode: Literal["independent", "embedded", "auto"] = "auto"
    # 同时容纳横图和竖图；过窄的中央区域会截断放大后的方形预览内容。
    viewer_region: tuple[float, float, float, float] = (0.06, 0.06, 0.88, 0.88)
    viewer_threshold: float = Field(default=0.9, gt=0, le=1)
    viewer_min_scale: float = Field(default=1.5, gt=1)
    max_steps: int = Field(default=8, ge=1, le=30, strict=True)
    steps: dict[str, MediaStepSpec]

    @model_validator(mode="after")
    def validate_steps(self):
        MediaTargetSpec(kind="region", region=self.viewer_region)
        current = self.entry
        visited = set()
        previous = "original"
        save_count = 0
        return_count = 0
        while current is not None:
            if current not in self.steps:
                raise ValueError(f"未定义媒体步骤：{current}")
            if current in visited:
                raise ValueError("媒体流程不允许输入循环")
            visited.add(current)
            step = self.steps[current]
            if step.before != previous:
                raise ValueError(f"媒体步骤前后状态不衔接：{current}")
            if step.action in ("click", "right_click"):
                if not step.target:
                    raise ValueError("点击步骤必须指定目标")
                if step.expect == "file":
                    raise ValueError("点击不能作为文件验证步骤")
            elif step.action == "save_file":
                save_count += 1
                if (
                    step.before != "save_dialog"
                    or step.expect != "file"
                    or step.target is not None
                ):
                    raise ValueError("保存步骤必须从对话框验证文件，不使用点击目标")
            elif step.action == "wait_ready":
                if step.before != "viewer" or step.expect != "viewer" or step.target:
                    raise ValueError("加载等待只观察查看器，不执行输入或改变页面")
            elif (
                step.before != "viewer"
                or step.expect != "original"
                or step.target is not None
            ):
                raise ValueError("返回步骤必须从查看器返回原列表，不使用点击目标")
            else:
                return_count += 1
            previous = "viewer" if step.expect == "file" else step.expect
            current = step.next
        if visited != set(self.steps) or len(visited) > self.max_steps:
            raise ValueError("媒体流程存在不可达步骤或超出运行上限")
        if previous != "original":
            raise ValueError("媒体流程必须验证返回原列表后结束")
        if save_count != 1 or return_count != 1:
            raise ValueError("媒体流程必须恰好保存一次并返回一次")
        return self


class UiProfile(BaseModel):
    """统一维护页面、路由及各主题目标模板，替代两套注册器。"""

    model_config = ConfigDict(extra="forbid")
    pages: dict[str, PageSpec] = Field(default_factory=dict)
    routes: dict[str, RouteSpec] = Field(default_factory=dict)
    themes: dict[str, dict[str, TemplateSpec]] = Field(default_factory=dict)
    media_targets: dict[str, MediaTargetSpec] = Field(default_factory=dict)
    media_workflows: dict[str, MediaWorkflowSpec] = Field(default_factory=dict)
    feed_insets: tuple[int, int, int, int] = (375, 40, 6, 9)
    feed_static_width: int = Field(default=90, gt=0)
    layout_scale: float = Field(default=1.25, gt=0)

    @model_validator(mode="after")
    def validate_structure(self):
        """检查名称及路由端点，不允许未验证页面冒充可验证目标。"""
        if any(value < 0 for value in self.feed_insets):
            raise ValueError("列表边距不能为负数")
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
            for alternate in route.alternate_destinations:
                if not route.verified or not self.get_page(alternate).required:
                    raise ValueError("路由的恢复页面必须有可验证的页面标志")
        for workflow in self.media_workflows.values():
            workflow.validate_steps()
            if (
                self.media_target(workflow.steps[workflow.entry].target).kind
                != "source"
            ):
                raise ValueError("媒体流程入口必须使用动态预览目标")
            if self.media_target(workflow.menu_target).kind != "ocr":
                raise ValueError("媒体菜单验证必须使用 OCR 目标")
            for step in workflow.steps.values():
                if step.target is not None:
                    target = self.media_target(step.target)
                    if target.kind == "source" and (
                        step.before != "original"
                        or step.expect != "viewer"
                        or step.action != "click"
                    ):
                        raise ValueError("动态预览目标只用于打开查看器")
                    if target.kind == "ocr" and (
                        step.before != "menu"
                        or step.expect != "save_dialog"
                        or step.action != "click"
                    ):
                        raise ValueError("文字目标只用于从菜单打开保存对话框")
                    if target.kind == "region" and (
                        step.before != "viewer"
                        or step.expect != "menu"
                        or step.action != "right_click"
                    ):
                        raise ValueError("查看器区域只用于打开媒体菜单")
        return self

    def feed_bounds(self, surface: Surface) -> Rect:
        """从主题布局边距和实际 DPI 得到列表范围，窗口宽度改变时不按比例裁掉昵称。"""
        ratio = surface.scale / self.layout_scale
        left, top, right, bottom = self.feed_insets
        left, top = round(left * ratio), round(top * ratio)
        right, bottom = round(right * ratio), round(bottom * ratio)
        width = surface.bounds.width - left - right
        height = surface.bounds.height - top - bottom
        if width <= 0 or height <= 0:
            raise ValueError("窗口太小，无法按页面布局定位朋友圈列表")
        return Rect(x=left, y=top, width=width, height=height)

    def feed_static_bounds(self, region: Rect, scale: float) -> Rect:
        """头像侧栏用于滚动进展与布局稳定校验，排除正文中的视频播放区域。"""
        width = min(
            region.width,
            max(1, round(self.feed_static_width * scale / self.layout_scale)),
        )
        return Rect(x=region.x, y=region.y, width=width, height=region.height)

    def media_target(self, name: str) -> MediaTargetSpec:
        if name not in self.media_targets:
            raise ValueError(f"未定义媒体目标：{name}")
        return self.media_targets[name]

    def media_workflow(self, name: str) -> MediaWorkflowSpec:
        if name not in self.media_workflows:
            raise ValueError(f"未定义媒体流程：{name}")
        return self.media_workflows[name]

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
                # 微信可恢复上次打开的子页面，不一定停在未选中朋友圈的发现菜单。
                alternate_destinations=["moments_selected"],
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
                # 只匹配图标内部，避免通知红点和侧栏背景改变导致误判。
                "moments_entry": TemplateSpec(path=assets / "moments_entry_core.png"),
                "discover_selected": TemplateSpec(
                    path=assets / "discover_selected_core.png"
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
        media_targets={
            "media_preview": MediaTargetSpec(kind="source"),
            "viewer_content": MediaTargetSpec(
                kind="region", region=(0.34, 0.34, 0.32, 0.32)
            ),
            "save_as": MediaTargetSpec(
                kind="ocr", texts=["另存为", "保存视频", "Saveas"]
            ),
        },
        media_workflows={
            "save_media": MediaWorkflowSpec(
                entry="open_viewer",
                menu_target="save_as",
                steps={
                    "open_viewer": MediaStepSpec(
                        action="click",
                        target="media_preview",
                        before="original",
                        expect="viewer",
                        next="wait_ready",
                    ),
                    "wait_ready": MediaStepSpec(
                        action="wait_ready",
                        before="viewer",
                        expect="viewer",
                        next="open_menu",
                        timeout_seconds=30,
                    ),
                    "open_menu": MediaStepSpec(
                        action="right_click",
                        target="viewer_content",
                        before="viewer",
                        expect="menu",
                        next="choose_save_as",
                    ),
                    "choose_save_as": MediaStepSpec(
                        action="click",
                        target="save_as",
                        before="menu",
                        expect="save_dialog",
                        next="write_file",
                    ),
                    "write_file": MediaStepSpec(
                        action="save_file",
                        before="save_dialog",
                        expect="file",
                        next="return_to_feed",
                    ),
                    "return_to_feed": MediaStepSpec(
                        action="close_viewer", before="viewer", expect="original"
                    ),
                },
            ),
        },
    )
