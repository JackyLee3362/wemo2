"""界面交互组件：组合截图、语义定位和平台输入。"""

from pathlib import Path

from loguru import logger

from wemo2_collector.interfaces import (
    BaseDriver,
    BaseInteractor,
    BaseLocator,
    BaseScreenshotter,
)
from wemo2_collector.models import LocatedTarget, Screenshot


class UiInteractor(BaseInteractor):
    def __init__(
        self, driver: BaseDriver, screenshotter: BaseScreenshotter, locator: BaseLocator
    ):
        self.driver = driver
        self.screenshotter = screenshotter
        self.locator = locator
        self._artifacts: list[Path] = []

    def reset(self) -> None:
        """开始新一轮证据收集，不删除已经保存的文件。"""
        self._artifacts = []

    def capture(self, *, follow_foreground: bool = False) -> Screenshot:
        if follow_foreground:
            self.driver.follow_foreground()
        surface = self.driver.prepare_capture()
        frame = self.screenshotter.capture(surface)
        self.driver.verify_capture(surface)
        self._artifacts.append(frame.image_path)
        return frame

    def locate(self, frame: Screenshot, name: str) -> LocatedTarget | None:
        return self.locator.locate(frame, name)

    def click(self, frame: Screenshot, name: str) -> LocatedTarget:
        target = self.locate(frame, name)
        if target is None:
            raise RuntimeError(f"目标 {name} 未可靠识别或匹配不唯一，未执行点击")
        logger.info(
            "目标 {} 匹配成功，分数 {:.3f}，位置 {}", name, target.score, target.bounds
        )
        self.driver.click(frame, target)
        return target

    def artifacts(self) -> list[Path]:
        return list(self._artifacts)
