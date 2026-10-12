"""界面采集编排；仅此组装入口感知平台和具体组件实现。"""

from pathlib import Path

from wemo2.api.interfaces import BaseCollector
from wemo2.api.models import CollectResult
from wemo2.collectors.archive import MomentArchive
from wemo2.collectors.config import WindowsCollectorConfig
from wemo2.collectors.controller import UiController
from wemo2.collectors.feed import FeedCapture
from wemo2.collectors.locating import AirtestLocator
from wemo2.collectors.media import MediaCapture
from wemo2.collectors.profiles import build_profile
from wemo2.collectors.routing import PageRouter
from wemo2.collectors.windows.driver import WindowsDriver
from wemo2.collectors.windows.screenshot import WindowsScreenshotter


class WindowsScreenshotCollector(BaseCollector):
    """组装 Windows 驱动、截图器、Airtest 定位器、控制器和页面路由器。

    编排导航、分段截图、昵称识别和媒体保存；交付本地证据并释放会话。
    去重及本地归档在采集阶段完成，远端保存由独立命令执行。
    """

    def __init__(
        self,
        config: WindowsCollectorConfig,
        resource_dir: Path = Path("data/moments"),
        account: str = "default",
    ):
        self.config = config
        self.resource_dir = resource_dir
        self.account = account
        self.profile = build_profile()
        self.driver = WindowsDriver(
            config.window_title,
            config.window_process_id,
            save_options=config.save_dialog,
        )
        self.screenshotter = WindowsScreenshotter(
            config.screenshot_dir, config.timezone
        )
        self.locator = AirtestLocator(self.profile, config.theme, config.matcher)
        self.controller = UiController(
            self.driver,
            self.screenshotter,
            self.locator,
            self.profile,
            config.controller,
            config.screenshot_dir,
        )
        self.router = PageRouter(self.controller, self.profile, config.route)
        self.feed: FeedCapture | None = None
        self.media: MediaCapture | None = None
        self.archive: MomentArchive | None = None

    def collect(self) -> CollectResult:
        """导航、采集并合并本地资源，返回实际归档文件和停止原因。"""
        self.controller.reset()
        self.feed = None
        self.media = None
        self.archive = None
        try:
            self.driver.attach()
            if self.config.route.target == "none":
                frame = self.controller.capture()
            else:
                frame = self.router.run(self.config.route.target)
            if self.config.feed.enabled:
                if self.config.feed.recognize_nickname or self.config.feed.save_media:
                    self.media = MediaCapture(
                        self.controller, self.config.feed, self.config.screenshot_dir
                    )
                    self.archive = MomentArchive(self.resource_dir, self.account)
                self.feed = FeedCapture(
                    self.controller,
                    self.config.feed,
                    self.config.screenshot_dir,
                    self.media,
                    None if self.archive is None else self.archive.export,
                )
                try:
                    self.feed.run(frame)
                finally:
                    if (
                        self.media is not None
                        and self.archive is not None
                        and self.feed.result.segments
                    ):
                        self.archive.export(self.feed.result, self.media)
            posts = set()
            if self.archive is not None:
                for path in self.archive.paths:
                    if path.name == "post.json":
                        posts.add(path)
            reason = "completed" if self.feed is None else self.feed.result.stop_reason
            return CollectResult(
                posts=len(posts), artifacts=self.artifacts(), stop_reason=reason
            )
        finally:
            self.driver.close()

    def artifacts(self) -> list[Path]:
        paths = self.controller.artifacts()
        if self.feed is not None:
            paths.extend(self.feed.paths)
        if self.media is not None:
            paths.extend(self.media.paths)
        if self.archive is not None:
            paths.extend(self.archive.paths)
        return paths

    def stop(self) -> None:
        self.driver.close()
