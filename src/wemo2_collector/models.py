"""平台和界面识别的数据结构，不依赖具体 UI/OCR 库。"""

from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from wemo2.models import Platform


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
    method: str  # template、ocr 等方法名。


class TextBlock(BaseModel):
    """文字识别器输出的可见文字块；不直接等同于一条朋友圈动态。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    frame_id: str
    text: str
    bounds: Rect  # 截图局部像素坐标。
    confidence: float = Field(ge=0, le=1)


class RouteConfig(BaseModel):
    """路由动作名由 PageRegistry 校验；none 表示只截图。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    target: str = Field(default="none", min_length=1)
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)


class MatchConfig(BaseModel):
    """匹配算法参数，不包含页面或模板的业务名称。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    threshold: float = Field(default=0.9, gt=0, le=1)
