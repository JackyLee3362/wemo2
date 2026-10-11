"""动态卡片识别和媒体另存为流程；截图、输入及动作证据均经过控制器。"""

import hashlib
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops
from pydantic import BaseModel, ConfigDict, Field

from wemo2_collector.controller import UiActionError, UiController
from wemo2_collector.models import FeedCaptureConfig, LocatedTarget, Rect, Screenshot
from wemo2_collector.recognition import TextLine, TextRecognizer, file_kind, media_tiles


class SavedMedia(BaseModel):
    """媒体查看器实际另存为的文件；原始像素质量需与微信查看器核对。"""

    model_config = ConfigDict(extra="forbid")
    path: Path
    kind: str
    sha256: str
    size: int = Field(gt=0)


class CardCapture(BaseModel):
    """可见动态片段、OCR 昵称与已保存媒体的关联；首尾片段可能不完整。"""

    model_config = ConfigDict(extra="forbid")
    source: Screenshot
    bounds: Rect
    screenshot: Path
    nickname: TextLine | None = None
    media: list[SavedMedia] = Field(default_factory=list)
    partial_start: bool = True
    partial_end: bool = False
    status: str = "processing"
    error: str | None = None


class MediaCapture:
    """识别可见卡片，按点击、右键、另存为、文件验证、返回列表的顺序执行。

    只点击经过内容核对的媒体候选；输入或返回异常即停止，不自动重放。
    """

    def __init__(
        self, controller: UiController, config: FeedCaptureConfig, directory: Path
    ):
        self.controller = controller
        self.config = config
        self.directory = directory / f"cards_{uuid4()}"
        self.reader = TextRecognizer()
        self.paths: list[Path] = []
        self.saved: dict[str, SavedMedia] = {}

    def _check(self, action):
        if action.status == "failed":
            raise UiActionError(action)
        if action.after is None:
            raise RuntimeError("媒体动作没有交付截图")
        return action.after

    def _save_media(
        self, source: Screenshot, bounds: Rect, stream_y: int
    ) -> SavedMedia:
        fresh = self.controller.capture()
        if fresh.surface != source.surface:
            raise RuntimeError("媒体定位后列表窗口发生变化，未点击")
        box = (bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height)
        with Image.open(source.image_path) as old, Image.open(fresh.image_path) as new:
            with (
                old.crop(box).convert("RGB") as first,
                new.crop(box).convert("RGB") as second,
            ):
                with ImageChops.difference(first, second) as difference:
                    if difference.getbbox() is not None:
                        raise RuntimeError("媒体内容已变化，未使用旧坐标点击")
                key = f"{stream_y}:{hashlib.sha256(first.tobytes()).hexdigest()}"
        if key in self.saved:
            return self.saved[
                key
            ]  # 仅复用本次运行中相同预览的下载，不冒充动态身份去重。
        viewer = self._check(
            self.controller.click_region(
                "media_preview", bounds, follow_foreground=True
            )
        )
        if viewer.surface.surface_id == source.surface.surface_id:
            raise RuntimeError("媒体未打开独立查看器，停止后续输入")
        area = Rect(
            x=viewer.surface.bounds.width // 3,
            y=viewer.surface.bounds.height // 3,
            width=max(1, viewer.surface.bounds.width // 3),
            height=max(1, viewer.surface.bounds.height // 3),
        )
        menu = self._check(
            self.controller.click_region("media_context", area, right=True)
        )
        with Image.open(menu.image_path) as image:
            lines = self.reader.read(image)
        matches = []
        for line in lines:
            if line.score >= 0.8 and line.text.replace(" ", "").rstrip(".。…") in (
                "另存为",
                "保存视频",
                "Saveas",
            ):
                matches.append(line)
        if len(matches) != 1:
            raise RuntimeError("未唯一识别到另存为菜单，停止输入并保留截图")
        self._check(
            self.controller.click_region(
                "save_as", matches[0].bounds, follow_foreground=True
            )
        )
        path = self.controller.platform_step(
            "save_file",
            lambda: self.controller.driver.save_dialog(self.directory / "media"),
        )
        kind = file_kind(path)
        saved = SavedMedia(
            path=path,
            kind=kind,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size=path.stat().st_size,
        )
        self.paths.append(path)
        self.controller.platform_step(
            "close_viewer", lambda: self.controller.driver.close_viewer(source.surface)
        )
        self.saved[key] = saved
        return saved

    def process(
        self,
        frame: Screenshot,
        region: Rect,
        anchors: list[LocatedTarget],
        stream_offset: int = 0,
    ) -> None:
        """保存当前可见卡片及昵称，必要时下载媒体；不改变列表滚动位置。"""
        from loguru import logger

        self.directory.mkdir(parents=True, exist_ok=True)
        top = region.y
        with Image.open(frame.image_path) as full:
            for index, anchor in enumerate(anchors):
                bottom = min(
                    region.y + region.height,
                    anchor.bounds.y
                    + anchor.bounds.height
                    + round(self.config.boundary_padding * frame.surface.scale),
                )
                if bottom <= top:
                    continue
                bounds = Rect(
                    x=region.x, y=top, width=region.width, height=bottom - top
                )
                path = self.directory / f"card_{uuid4()}.png"
                card = CardCapture(
                    source=frame,
                    bounds=bounds,
                    screenshot=path,
                    partial_start=index == 0,
                )
                with full.crop(
                    (bounds.x, bounds.y, bounds.x + bounds.width, bottom)
                ) as image:
                    image.save(path)
                    self.paths.append(path)
                    try:
                        if self.config.recognize_nickname:
                            card.nickname = self.reader.nickname(
                                image, frame.surface.scale
                            )
                        if self.config.save_media:
                            for tile in media_tiles(image, frame.surface.scale):
                                target = Rect(
                                    x=bounds.x + tile.x,
                                    y=bounds.y + tile.y,
                                    width=tile.width,
                                    height=tile.height,
                                )
                                card.media.append(
                                    self._save_media(
                                        frame,
                                        target,
                                        stream_offset + target.y - region.y,
                                    )
                                )
                        card.status = "captured"
                    except Exception as error:
                        logger.exception(
                            "动态卡片识别或媒体保存失败，保存已完成结果：{}", error
                        )
                        card.status = "failed"
                        card.error = str(error)
                        raise
                    finally:
                        metadata = path.with_suffix(".json")
                        metadata.write_text(
                            card.model_dump_json(indent=2), encoding="utf-8"
                        )
                        self.paths.append(metadata)
                top = bottom
