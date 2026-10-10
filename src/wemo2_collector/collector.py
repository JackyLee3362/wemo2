"""首阶段 Collector：保存窗口截图，不把界面误认成朋友圈动态。"""

from pathlib import Path

from loguru import logger

from wemo2.interfaces import BaseCollector
from wemo2.models import Observation
from wemo2_collector.models import Platform
from wemo2_collector.windows.driver import WindowsDriver
from wemo2_collector.windows.screenshot import WindowsScreenshotter


class WindowsScreenshotCollector(BaseCollector):
    def __init__(
        self,
        platform: Platform = Platform.windows,
        screenshot_dir: str | Path = "data/screenshot",
        window_title: str = "微信",
        window_process_id: int | None = None,
    ):
        if Platform(platform) != Platform.windows:
            raise ValueError("windows_screenshot 采集器不支持 Android")
        self.driver = WindowsDriver(window_title, window_process_id)
        self.screenshotter = WindowsScreenshotter(screenshot_dir)
        self._artifacts: list[Path] = []

    def collect(self) -> list[Observation]:
        self._artifacts = []
        try:
            surface = self.driver.attach()
            frame = self.screenshotter.capture(surface)
            if self.driver.current_surface() != surface:
                raise RuntimeError(
                    "截图时微信窗口发生变化，本次截图不能作为有效证据，请重试"
                )
            self._artifacts.append(frame.image_path)
            logger.info("微信窗口截图已保存：{}", frame.image_path)
            return []  # 尚未识别动态，不生成伪造的 Observation。
        finally:
            self.driver.close()

    def artifacts(self) -> list[Path]:
        return list(self._artifacts)

    def stop(self) -> None:
        self.driver.close()
