"""动态卡片识别和媒体另存为流程；截图、输入及动作证据均经过控制器。"""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import uuid4

from loguru import logger
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from wemo2.collectors import validation
from wemo2.collectors.controller import UiController, VideoChannelSkipped
from wemo2.collectors.media_navigation import ViewerSession, image_save_strategy
from wemo2.collectors.models import (
    FeedCaptureConfig,
    LocatedTarget,
    MediaDownload,
    Rect,
    Screenshot,
)
from wemo2.collectors.recognition import (
    TextLine,
    TextRecognizer,
    file_kind,
    media_tiles,
)
from wemo2.common.files import sha256_file


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
    media_detection: Literal[
        "not_requested", "none_detected", "candidates_found", "video_channel"
    ] = "not_requested"
    media_candidates: int = Field(default=0, ge=0)


class SavedPreview(BaseModel):
    """同一次连续采集中的媒体缓存证据；位置和图像都一致才允许复用文件。"""

    source: Screenshot
    bounds: Rect
    stream_y: int
    file: SavedMedia
    workflow_complete: bool = True  # 已保存文件可能在返回列表失败后留存，不能继续复用。


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
        self.previews: list[SavedPreview] = []
        self.last_saved: SavedMedia | None = None
        self.errors: list[str] = []
        self.viewer: ViewerSession | None = None
        self.image_strategy = image_save_strategy(config.use_image_arrows)
        self.scan_only = False
        self.downloads: list[MediaDownload] | None = None
        self.on_download: Callable[[SavedMedia, int], None] | None = None
        self.on_skip: Callable[[int, str], None] | None = None

    def destination(self, stream_y: int) -> Path | None:
        """只允许下载到完整识别的朋友圈目录；视频号及未知归属的候选跳过。"""
        if self.downloads is None:
            return self.directory / "media"
        for item in self.downloads:
            if item.stream_start <= stream_y < item.stream_end:
                if item.skip_reason is None:
                    return item.directory
                logger.info("[媒体跳过] {}，流位置={}", item.skip_reason, stream_y)
                return None
        return None

    def close_viewer(self, *, alternate_bounds: Rect | None = None) -> None:
        """批次结束或切换兜底时退出查看器，成功返回后才允许复用本批媒体缓存。"""
        session = self.viewer
        if session is None:
            return
        self.controller.close_media_viewer(
            session.source,
            session.bounds,
            session.viewer,
            self.reader,
            workflow_name=self.config.media_workflow,
            alternate_bounds=alternate_bounds,
        )
        for preview in self.previews[session.preview_start :]:
            preview.workflow_complete = True
        self.viewer = None

    def _save_media(
        self,
        source: Screenshot,
        bounds: Rect,
        stream_y: int,
        *,
        keep_viewer: bool = False,
    ) -> SavedMedia:
        self.last_saved = None
        directory = self.destination(stream_y + bounds.height // 2)
        if directory is None:
            raise RuntimeError("媒体缺少完整归档身份或属于视频号，未打开保存界面")
        logger.info(
            "[媒体保存] 开始，流位置={}，保留查看器={}，保存目录={}",
            stream_y,
            keep_viewer,
            directory,
        )
        session = self.viewer
        if session is None:
            fresh = self.controller.capture()
            if fresh.surface != source.surface:
                raise RuntimeError("媒体定位后列表窗口发生变化，未点击")
            if not validation.region_unchanged(source, fresh, bounds):
                raise RuntimeError("媒体内容已变化，未使用旧坐标点击")
        box = (bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height)
        with Image.open(source.image_path) as old:
            with old.crop(box).convert("RGB") as first:
                key = f"{stream_y}:{hashlib.sha256(first.tobytes()).hexdigest()}"
        if session is None and key in self.saved:
            logger.info("[媒体保存] 复用已验证的同位置文件：{}", self.saved[key].path)
            return self.saved[key]  # 仅复用已验证重叠中的同位置媒体。
        for preview in self.previews:
            if session is not None:
                break  # 连续查看器保存需要确认当前目标，不复用列表坐标缓存。
            if not preview.workflow_complete:
                continue
            tolerance = max(1, round(2 * source.surface.scale))
            if (
                abs(preview.stream_y - stream_y) > tolerance
                or abs(preview.bounds.x - bounds.x) > tolerance
                or abs(preview.bounds.width - bounds.width) > 1
                or abs(preview.bounds.height - bounds.height) > 1
            ):
                continue
            if self.controller.locator.same_preview(
                preview.source, source, preview.bounds, bounds
            ):
                logger.info("[媒体保存] 复用已验证的重叠文件：{}", preview.file.path)
                return preview.file
        saved = None
        preview = None
        preview_start = len(self.previews) if session is None else session.preview_start

        def keep_image_open() -> bool:
            return keep_viewer and saved is not None and saved.kind == "image"

        def on_file(path: Path) -> None:
            nonlocal saved, preview
            kind = file_kind(path)
            digest = sha256_file(path)
            saved = SavedMedia(
                path=path, kind=kind, sha256=digest, size=path.stat().st_size
            )
            self.paths.append(path)
            self.last_saved = saved
            if self.on_download is not None:
                self.on_download(saved, stream_y + bounds.height // 2)
            logger.info(
                "[媒体保存] 文件校验通过，类型={}，大小={} 字节，SHA-256={}，路径={}",
                saved.kind,
                saved.size,
                saved.sha256,
                saved.path,
            )
            preview = SavedPreview(
                source=source,
                bounds=bounds,
                stream_y=stream_y,
                file=saved,
                workflow_complete=False,
            )
            self.previews.append(preview)

        try:
            self.controller.run_media_workflow(
                source,
                bounds,
                directory,
                self.reader,
                on_file,
                workflow_name=self.config.media_workflow,
                load_wait_seconds=self.config.media_load_wait_seconds,
                load_timeout_seconds=self.config.media_load_timeout_seconds,
                start_in_viewer=None if session is None else session.viewer,
                keep_viewer=keep_image_open,
            )
        except VideoChannelSkipped as error:
            logger.exception(
                "媒体已确认是视频号，记录跳过，后续回看不再打开：{}", error
            )
            if self.on_skip is not None:
                self.on_skip(stream_y + bounds.height // 2, "video_channel")
            self.viewer = None
            raise
        if saved is None:
            raise RuntimeError("媒体流程未交付验证过的文件")
        if keep_image_open():
            frame = self.controller.results[-1].after
            if frame is None:
                raise RuntimeError("保存图片后未确认查看器状态，停止切换")
            self.viewer = ViewerSession(
                source=source, bounds=bounds, viewer=frame, preview_start=preview_start
            )
        else:
            self.saved[key] = saved
            for item in self.previews[preview_start:]:
                item.workflow_complete = True
            self.viewer = None
        logger.info("[媒体保存] 保存和查看器状态均已验证：{}", saved.path)
        return saved

    def process(
        self,
        frame: Screenshot,
        region: Rect,
        anchors: list[LocatedTarget],
        stream_offset: int = 0,
    ) -> None:
        """保存当前可见卡片及昵称，必要时下载媒体；不改变列表滚动位置。"""
        if self.scan_only:
            return  # 扫描阶段只处理 FeedSegment，避免重复 OCR 半张卡片。
        self.directory.mkdir(parents=True, exist_ok=True)
        top = region.y
        boundaries = []
        for anchor in anchors:
            bottom = min(
                region.y + region.height,
                anchor.bounds.y
                + anchor.bounds.height
                + round(self.config.boundary_padding * frame.surface.scale),
            )
            boundaries.append(bottom)
        # 未闭合的长动态也必须采集可见图片，否则滚出屏幕后会丢失前几排媒体。
        if not boundaries or boundaries[-1] < region.y + region.height:
            boundaries.append(region.y + region.height)
        with Image.open(frame.image_path) as full:
            for index, bottom in enumerate(boundaries):
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
                    partial_end=bottom == region.y + region.height,
                )
                self.last_saved = None
                with full.crop(
                    (bounds.x, bounds.y, bounds.x + bounds.width, bottom)
                ) as image:
                    image.save(path)
                    self.paths.append(path)
                    try:
                        if self.config.recognize_nickname and self.downloads is None:
                            card.nickname = self.reader.nickname(
                                image,
                                frame.surface.scale,
                                search_height=image.height if index == 0 else None,
                            )
                            logger.info(
                                "[人物识别] 结果={}",
                                "未识别"
                                if card.nickname is None
                                else card.nickname.text,
                            )
                        if self.config.save_media and not self.scan_only:
                            tiles = media_tiles(image, frame.surface.scale)
                            card.media_candidates = len(tiles)
                            card.media_detection = (
                                "candidates_found" if tiles else "none_detected"
                            )
                            logger.info(
                                "[媒体定位] 当前卡片识别到 {} 个媒体候选", len(tiles)
                            )
                            targets = []
                            for tile in tiles:
                                target = Rect(
                                    x=bounds.x + tile.x,
                                    y=bounds.y + tile.y,
                                    width=tile.width,
                                    height=tile.height,
                                )
                                center = (
                                    stream_offset
                                    + target.y
                                    - region.y
                                    + target.height // 2
                                )
                                if self.destination(center) is not None:
                                    targets.append(target)
                            channel = False
                            if targets:
                                channel = self.reader.is_channel_card(
                                    image, tiles=tiles
                                )
                            if channel:
                                targets = []
                                card.media_detection = "video_channel"
                                logger.info(
                                    "[媒体跳过] 卡片来源为视频号，仅保留朋友圈截图"
                                )
                            self.image_strategy.save(
                                self, card, targets, stream_offset, region
                            )
                        card.status = "captured"
                    except Exception as error:
                        logger.exception(
                            "动态卡片识别或媒体保存失败，保存已完成结果：{}", error
                        )
                        card.status = "failed"
                        card.error = str(error)
                        self.errors.append(str(error))
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
                        logger.info(
                            "[卡片留痕] 状态={}，已保存媒体={}，截图={}，元信息={}",
                            card.status,
                            len(card.media),
                            path,
                            metadata,
                        )
                top = bottom
