"""界面采集的能力接口；具体 Windows/Android 实现留待后续接入。"""

from abc import ABC, abstractmethod

from wemo2.models import Observation
from wemo2_collector.models import LocatedTarget, Page, Screenshot, Surface, TextBlock


class BaseDriver(ABC):
    """唯一执行平台操作的组件，负责窗口/设备会话和输入动作。"""

    @abstractmethod
    def attach(self) -> Surface:
        """连接目标微信实例，返回当前区域，不启动新微信进程。"""

    @abstractmethod
    def current_surface(self) -> Surface:
        """重新读取位置与大小，供动作前校验截图是否已经失效。"""

    @abstractmethod
    def click(self, frame: Screenshot, target: LocatedTarget) -> None:
        """校验帧、目标窗口和坐标范围，再转换为平台坐标执行点击。"""

    @abstractmethod
    def scroll(self, surface: Surface, distance: int) -> None:
        """在已验证的内容区域内滚动；距离以平台适配器约定的像素表示。"""

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
    def detect(self, frame: Screenshot) -> Page:
        """通过页面标志判断当前状态。"""

    @abstractmethod
    def ensure(self, page: Page) -> Screenshot:
        """到达目标页并返回验证成功的最新截图，未知页面或超时则报错。"""

    @abstractmethod
    def advance(self) -> Screenshot | None:
        """取得下一屏；确认到底返回 None，无进展但无法确认到底则报错。"""


class BaseMomentReader(ABC):
    """解析可见动态卡片，统一输出 wemo2 的 Observation。"""

    @abstractmethod
    def read(self, frame: Screenshot) -> list[Observation]:
        """分割卡片并提取字段，附带原始截图证据及字段完整度。"""
