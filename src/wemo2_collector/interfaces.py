"""平台驱动契约；内部单实现组件直接使用函数或具体类。"""

from abc import ABC, abstractmethod
from pathlib import Path

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

    def scroll(self, frame: Screenshot, target: LocatedTarget, steps: int) -> None:
        """在目标区域向下滚动；不支持滚轮的平台需实现自己的输入方式。"""
        raise NotImplementedError("当前平台尚未实现列表滚动")

    def right_click(self, frame: Screenshot, target: LocatedTarget) -> None:
        """打开媒体上下文菜单，由平台实现输入。"""
        raise NotImplementedError("当前平台尚未实现右键菜单")

    def save_dialog(self, directory: Path) -> Path:
        """在已验证的同进程另存为对话框中保存唯一文件，返回实际路径。"""
        raise NotImplementedError("当前平台尚未实现另存为")

    def is_save_dialog(self) -> bool:
        """只读检查当前窗口是否为保存对话框，不输入文件名。"""
        return False

    def close_viewer(self, original: Surface) -> None:
        """关闭本次打开的查看器并确认回到原列表窗口。"""
        raise NotImplementedError("当前平台尚未实现返回列表")
