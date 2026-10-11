"""平台和界面识别的数据结构，不依赖具体 UI/OCR 库。"""

from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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
    captured_at: AwareDatetime  # 按配置时区记录，序列化保留 UTC 偏移。
    timezone: str | None = None  # IANA 名称；旧截图元数据可能没有该字段。
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


class RouteConfig(BaseModel):
    """路由动作名由 UiProfile 校验；none 表示只截图。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    target: str = Field(default="none", min_length=1)
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)


class MatchConfig(BaseModel):
    """匹配算法参数，不包含页面或模板的业务名称。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    threshold: float = Field(default=0.9, gt=0, le=1)


Direction = Literal["up", "down", "left", "right"]


class ControllerConfig(BaseModel):
    """只读操作允许有限重试；输入动作不会自动重发。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    capture_attempts: int = Field(default=2, ge=1, le=5, strict=True)
    locate_attempts: int = Field(default=2, ge=1, le=5, strict=True)
    poll_interval: float = Field(default=0.2, gt=0, le=5)
    verification_timeout: float = Field(default=5.0, gt=0, le=60)
    max_frame_age: float = Field(default=5.0, gt=0, le=5)


class Expectation(BaseModel):
    """动作后预期：页面标志或内容区域变化；变化不等于已确认到底。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["page", "change"]
    page: str | None = None  # page 预期的注册页面名称。
    region: Rect | None = None  # change 预期的截图局部区域，空值表示整幅图。
    change_threshold: float = Field(default=0.01, gt=0, le=1)

    @model_validator(mode="after")
    def check_expectation(self):
        if self.kind == "page" and (not self.page or self.region is not None):
            raise ValueError("页面预期需要非空 page，不使用截图区域")
        if self.kind == "change" and self.page is not None:
            raise ValueError("变化预期不使用 page")
        return self


class ValidationResult(BaseModel):
    """not_met 可继续观察；unavailable 表示尚无验证规则。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["passed", "not_met", "unavailable"]
    message: str
    actual_page: str | None = None
    change_ratio: float | None = Field(default=None, ge=0, le=1)


class ActionResult(BaseModel):
    """一次输入动作的执行与验证结果；失败时也保留已执行事实和证据。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    operation: Literal["click", "swipe"]
    name: str  # 语义目标名，不保存固定屏幕点击位置。
    status: Literal["executed", "verified", "pending", "failed"] = "failed"
    attempted: bool = False  # 已调用平台动作；抛错时可能部分执行，禁止自动重发。
    executed: bool = False  # 平台调用正常返回，不等于页面已经验证。
    before: Screenshot | None = None
    after: Screenshot | None = None
    target: LocatedTarget | None = None
    direction: Direction | None = None  # swipe 的拖动方向。
    distance: int | None = Field(default=None, gt=0, strict=True)  # 物理像素距离。
    evidence: list[Path] = Field(default_factory=list)  # 包含校验失败时保存的截图。
    expectation: Expectation | None = None
    validation: ValidationResult | None = None
    error: str | None = None
