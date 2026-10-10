"""平台和界面识别的数据结构，不依赖具体 UI/OCR 库。"""

from enum import StrEnum
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Platform(StrEnum):
    """数据源所在平台，不是运行 wemo2 的主机操作系统。"""

    windows = "windows"
    android = "android"


class Page(StrEnum):
    """路由器确认的页面状态；无法确认时不能继续盲点。"""

    unknown = "unknown"
    login = "login"
    home = "home"
    discover = "discover"  # 含朋友圈入口的发现菜单，不是动态列表。
    moments = "moments"
    detail = "detail"
    modal = "modal"


class Rect(BaseModel):
    """像素矩形；屏幕坐标允许负数，宽高必须为正。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    x: int = Field(strict=True)
    y: int = Field(strict=True)
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)


class Surface(BaseModel):
    """目标窗口/设备的当前可见区域；bounds 使用平台物理像素坐标。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: Platform
    surface_id: str = Field(min_length=1)  # 窗口句柄或设备/应用标识的字符串形式。
    bounds: Rect
    scale: float = Field(
        default=1.0, gt=0
    )  # DPI/显示缩放信息，不用于重复缩放像素坐标。


class Screenshot(BaseModel):
    """截图证据及采集时的区域快照；路径指向实际图片文件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    frame_id: str = Field(min_length=1)
    captured_at: AwareDatetime
    surface: Surface
    image_path: Path


class LocatedTarget(BaseModel):
    """当前截图中的定位结果；bounds 使用截图局部像素坐标。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    frame_id: str
    name: str
    bounds: Rect
    score: float = Field(ge=0, le=1)
    method: str  # uia、template、ocr 等方法名。


class TextBlock(BaseModel):
    """OCR/UIA 的可见文字块；不直接等同于一条朋友圈动态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    frame_id: str
    text: str
    bounds: Rect  # 截图局部像素坐标。
    confidence: float = Field(ge=0, le=1)


class RouteConfig(BaseModel):
    """页面路由配置；none 只截图，discover 打开并验证发现菜单。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    target: str = Field(default="none", pattern=r"^(none|discover)$")
    entry_template: Path = Path(__file__).parent / "assets/moments_entry.png"
    selected_template: Path = Path(__file__).parent / "assets/discover_selected.png"
    menu_template: Path = Path(__file__).parent / "assets/discover_menu.png"
    reference_scale: float = Field(default=1.25, gt=0)  # 模板来自 125% DPI 截图。
    match_threshold: float = Field(default=0.9, gt=0, le=1)
    sidebar_ratio: float = Field(default=0.1, gt=0, le=0.25)
    menu_ratio: float = Field(default=0.4, gt=0, le=0.75)  # 发现菜单所在的左侧区域。
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)
