"""动态卡片识别和媒体另存为流程；截图、输入及动作证据均经过控制器。"""

import hashlib
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PIL import Image, ImageChops
from pydantic import BaseModel, ConfigDict, Field

from wemo2_collector.controller import UiController
from wemo2_collector.models import FeedCaptureConfig, LocatedTarget, Rect, Screenshot
from wemo2_collector.recognition import TextLine, TextRecognizer, file_kind, media_tiles


class SavedMedia(BaseModel):
    """媒体查看器实际另存为的文件；原始像素质量需与微信查看器核对。"""

    model_config = ConfigDict(extra="forbid")
    path: Path
    kind: Literal["image", "video"]
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
    media_detection: Literal["not_requested", "none_detected", "candidates_found"] = (
        "not_requested"
    )
    media_candidates: int = Field(default=0, ge=0)


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
        self.last_saved: SavedMedia | None = None

    def _save_media(
        self, source: Screenshot, bounds: Rect, stream_y: int
    ) -> SavedMedia:
        self.last_saved = None
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
            return self.saved[key]  # 仅复用已验证重叠中的同位置媒体。
        saved = None

        def on_file(path: Path) -> None:
            nonlocal saved
            kind = file_kind(path)
            with path.open("rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
            saved = SavedMedia(
                path=path, kind=kind, sha256=digest, size=path.stat().st_size
            )
            self.paths.append(path)
            self.last_saved = saved

        self.controller.run_media_workflow(
            source,
            bounds,
            self.directory / "media",
            self.reader,
            on_file,
            workflow_name=self.config.media_workflow,
        )
        if saved is None:
            raise RuntimeError("媒体流程未交付验证过的文件")
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
                self.last_saved = None
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
                            tiles = media_tiles(image, frame.surface.scale)
                            card.media_candidates = len(tiles)
                            card.media_detection = (
                                "candidates_found" if tiles else "none_detected"
                            )
                            for tile in tiles:
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
                        if (
                            self.last_saved is not None
                            and self.last_saved not in card.media
                        ):
                            card.media.append(self.last_saved)
                        raise
                    finally:
                        metadata = path.with_suffix(".json")
                        metadata.write_text(
                            card.model_dump_json(indent=2), encoding="utf-8"
                        )
                        self.paths.append(metadata)
                top = bottom
