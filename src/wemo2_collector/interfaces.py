"""界面采集的能力接口；具体 Windows/Android 实现留待后续接入。"""

from abc import ABC, abstractmethod
from pathlib import Path

from wemo2.models import Observation
from wemo2_collector.models import (
    ActionResult,
    Direction,
    Expectation,
    LocatedTarget,
    Screenshot,
    Surface,
    TextBlock,
    ValidationResult,
)
from wemo2_collector.themes import TemplateSpec


class BaseDriver(ABC):
    """唯一执行平台操作的组件，负责窗口/设备会话和输入动作。"""

    @abstractmethod
    def attach(self) -> Surface:
        """连接目标微信实例，返回当前区域，不启动新微信进程。"""

    @abstractmethod
    def current_surface(self) -> Surface:
        """重新读取位置与大小，供动作前校验截图是否已经失效。"""

    @abstractmethod
    def prepare_capture(self) -> Surface:
        """确认目标可截图，返回物理像素区域。"""

    @abstractmethod
    def verify_capture(self, surface: Surface) -> None:
        """校验窗口、坐标及焦点未变化。"""

    @abstractmethod
    def follow_foreground(self) -> None:
        """跟随本次会话允许的前台窗口。"""

    @abstractmethod
    def click(self, frame: Screenshot, target: LocatedTarget) -> None:
        """校验帧、目标窗口和坐标范围，再转换为平台坐标执行点击。"""

    @abstractmethod
    def swipe(
        self,
        frame: Screenshot,
        target: LocatedTarget,
        direction: Direction,
        distance: int,
    ) -> None:
        """在定位的区域内滑动，距离为物理像素，端点必须位于区域内。"""

    @abstractmethod
    def close(self) -> None:
        """释放采集会话，不关闭用户的微信应用。"""


class BaseScreenshotter(ABC):
    """只获取目标可见区域的图片，不负责路由或解析。"""

    @abstractmethod
    def capture(self, surface: Surface) -> Screenshot:
        """返回截图文件与对应区域快照，无法可靠截图时明确报错。"""


class BaseLocator(ABC):
    """按目标名称定位，模板、控件和 OCR 实现可组合使用。"""

    @abstractmethod
    def locate(self, frame: Screenshot, name: str) -> LocatedTarget | None:
        """找不到可靠且唯一的目标时返回 None，不直接点击。"""


class BaseTextRecognizer(ABC):
    """将截图或控件信息转成带坐标和置信度的文字块。"""

    @abstractmethod
    def recognize(self, frame: Screenshot) -> list[TextBlock]:
        """保留坐标与置信度，不猜测作者身份或发布时间。"""


class BasePageRouter(ABC):
    """识别页面并执行有后置验证的路由，不承担数据去重。"""

    @abstractmethod
    def detect(self, frame: Screenshot) -> str:
        """通过页面标志判断当前状态。"""

    @abstractmethod
    def ensure(self, page: str) -> Screenshot:
        """到达目标页并返回验证成功的最新截图，未知页面或超时则报错。"""

    @abstractmethod
    def run(self, action: str) -> Screenshot:
        """执行已注册的导航动作，未验证结果只能作为证据交付。"""

    @abstractmethod
    def advance(self) -> Screenshot | None:
        """取得下一屏；确认到底返回 None，无进展但无法确认到底则报错。"""


class BaseMomentReader(ABC):
    """解析可见动态卡片，统一输出 wemo2 的 Observation。"""

    @abstractmethod
    def read(self, frame: Screenshot) -> list[Observation]:
        """分割卡片并提取字段，附带原始截图证据及字段完整度。"""


class BaseImageMatcher(ABC):
    """纯图片匹配能力，不执行点击。"""

    @abstractmethod
    def match(
        self, frame: Screenshot, name: str, spec: TemplateSpec
    ) -> LocatedTarget | None:
        """返回可靠且唯一的匹配，低分或歧义返回 None。"""


class BaseValidator(ABC):
    """独立校验器，不执行截图、鼠标或键盘动作。"""

    @abstractmethod
    def validate_frame(self, frame: Screenshot) -> None:
        """校验时效和实际图片，异常时拒绝后续操作。"""

    @abstractmethod
    def validate_target(self, frame: Screenshot, target: LocatedTarget) -> None:
        """校验帧归属、时效与目标范围。"""

    @abstractmethod
    def detect_page(self, frame: Screenshot) -> str:
        """根据注册页面标志识别状态。"""

    @abstractmethod
    def evaluate(
        self, before: Screenshot, after: Screenshot, expectation: Expectation
    ) -> ValidationResult:
        """校验页面预期或区域进展，不把无法验证当作成功。"""


class BaseController(ABC):
    """规范截图、定位、输入及观察验证的控制器；输入动作最多执行一次。"""

    @abstractmethod
    def reset(self) -> None:
        """开始新一轮证据收集，不删除历史文件。"""

    @abstractmethod
    def capture(self, *, follow_foreground: bool = False) -> Screenshot:
        """有限重试截图与校验，失败明确报错。"""

    @abstractmethod
    def locate(
        self, name: str, frame: Screenshot | None = None
    ) -> LocatedTarget | None:
        """无帧时自动截图，有帧时检查有效性；不执行输入动作。"""

    @abstractmethod
    def detect_page(self, frame: Screenshot) -> str:
        """委托校验器识别页面。"""

    @abstractmethod
    def click(
        self,
        name: str,
        expectation: Expectation | None = None,
        *,
        source_page: str | None = None,
        timeout: float | None = None,
        settle_seconds: float = 0.2,
        follow_foreground: bool = False,
    ) -> ActionResult:
        """获取新截图、定位、校验、点击一次，再等待并验证预期。"""

    @abstractmethod
    def swipe(
        self,
        name: str,
        direction: Direction,
        distance: int,
        expectation: Expectation | None = None,
        *,
        timeout: float | None = None,
        settle_seconds: float = 0.2,
    ) -> ActionResult:
        """定位操作区域、校验、滑动一次，再等待并验证预期。"""

    @abstractmethod
    def artifacts(self) -> list[Path]:
        """返回截图与动作证据文件。"""
