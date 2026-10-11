"""平台驱动契约；内部单实现组件直接使用函数或具体类。"""

from abc import ABC, abstractmethod

from wemo2_collector.models import Direction, LocatedTarget, Screenshot, Surface


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
