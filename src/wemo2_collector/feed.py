"""滚动采集及相邻截图拼接；以菜单锚点切分图像，不解析微信源身份。"""

from pathlib import Path
from uuid import uuid4

from loguru import logger
from PIL import Image

from wemo2_collector.controller import UiActionError, UiController
from wemo2_collector.media import MediaCapture
from wemo2_collector.models import (
    FeedCaptureConfig,
    FeedCaptureResult,
    FeedSegment,
    LocatedTarget,
    Rect,
    Screenshot,
)


def overlap_shift(before: Image.Image, after: Image.Image) -> int | None:
    """通过 Airtest 确认旧视口底部仍在新视口内；匹配不唯一时拒绝拼接。"""
    import numpy as np
    from airtest.aircv.template_matching import TemplateMatching

    if before.size != after.size:
        return None
    height = before.height
    start = height * 2 // 3
    with before.crop((0, start, before.width, height)) as tail:
        search = np.array(tail.convert("RGB"))[:, :, ::-1].copy()
    if search.std() < 1:
        return None
    source = np.array(after.convert("RGB"))[:, :, ::-1].copy()
    matches = TemplateMatching(
        search, source, threshold=0.95, rgb=False
    ).find_all_results()
    if not matches or len(matches) != 1:
        return None
    shift = start - int(matches[0]["rectangle"][0][1])
    if not 0 < shift <= start:
        return None
    return shift


class FeedCapture:
    """有上限地滚动列表、拼接重叠内容、按锚点保存片段及可追溯清单。

    首条、末条可能不完整；锚点是约定的切分线，并不保证包含该动态的评论。
    """

    def __init__(
        self,
        controller: UiController,
        config: FeedCaptureConfig,
        directory: Path,
        media: MediaCapture | None = None,
    ):
        self.controller = controller
        self.config = config
        self.directory = directory / f"feed_{uuid4()}"
        self.result = FeedCaptureResult()
        self.paths: list[Path] = []
        self.pending: Image.Image | None = None
        self.pending_frames: list[str] = []
        self.partial_start = True
        self.media = media
        self.stream_height = 0

    def _image(self, frame: Screenshot, region: Rect) -> Image.Image:
        with Image.open(frame.image_path) as image:
            return image.crop(
                (region.x, region.y, region.x + region.width, region.y + region.height)
            ).convert("RGB")

    def _save(self, height: int, anchor: LocatedTarget | None) -> None:
        if self.pending is None or height <= 0:
            return
        path = self.directory / f"segment_{len(self.result.segments) + 1:04d}.png"
        with self.pending.crop((0, 0, self.pending.width, height)) as image:
            image.save(path)
        segment = FeedSegment(
            image_path=path,
            source_frames=list(self.pending_frames),
            partial_start=self.partial_start,
            partial_end=anchor is None,
            end_anchor=anchor,
        )
        self.result.segments.append(segment)
        self.paths.append(path)
        remainder = self.pending.crop(
            (0, height, self.pending.width, self.pending.height)
        )
        self.pending.close()
        self.pending = remainder
        self.partial_start = False
        self.pending_frames = self.pending_frames[-1:]

    def _append(
        self,
        frame: Screenshot,
        image: Image.Image,
        region: Rect,
        anchors: list[LocatedTarget],
        shift: int,
    ) -> None:
        old_height = 0 if self.pending is None else self.pending.height
        start = image.height - shift
        joined = Image.new("RGB", (image.width, old_height + shift))
        if self.pending is not None:
            joined.paste(self.pending, (0, 0))
            self.pending.close()
        with image.crop((0, start, image.width, image.height)) as new_content:
            joined.paste(new_content, (0, old_height))
        self.pending = joined
        self.pending_frames.append(frame.frame_id)
        consumed = 0
        padding = round(self.config.boundary_padding * frame.surface.scale)
        for anchor in anchors:
            end = anchor.bounds.y + anchor.bounds.height - region.y + padding
            if end <= start or end >= image.height:
                continue  # 边缘锚点先保留，不能把被裁断的末条当作完整动态。
            boundary = old_height + end - start
            self._save(boundary - consumed, anchor)
            consumed = boundary

    def run(self, first: Screenshot) -> FeedCaptureResult:
        """先识别锚点再输入；无锚点、无进展或拼接不可靠时停止。"""
        self.directory.mkdir(parents=True, exist_ok=False)
        frame = first
        region = self.config.region or Rect(
            x=0,
            y=0,
            width=frame.surface.bounds.width,
            height=frame.surface.bounds.height,
        )
        previous = None
        try:
            for index in range(self.config.max_screens):
                if frame.surface != first.surface:
                    raise RuntimeError("滚动采集中窗口发生变化，停止拼接")
                anchors = self.controller.locator.locate_all(
                    frame, "moments_anchor", region
                )
                self.result.frames.append(frame)
                image = self._image(frame, region)
                if previous is None:
                    shift = image.height
                else:
                    shift = overlap_shift(previous, image)
                    previous.close()
                previous = image
                if shift is None:
                    self.result.stop_reason = "overlap_unverified"
                    break
                self.stream_height += shift
                self._append(frame, image, region, anchors, shift)
                if self.media is not None:
                    self.media.process(
                        frame, region, anchors, self.stream_height - image.height
                    )
                if not anchors and index == 0:
                    self.result.stop_reason = "anchor_missing"
                    break
                if index + 1 == self.config.max_screens:
                    self.result.stop_reason = "max_screens"
                    break
                action = self.controller.scroll(
                    region,
                    self.config.wheel_steps,
                    settle_seconds=self.config.settle_seconds,
                    timeout=self.config.timeout_seconds,
                )
                if action.status != "verified":
                    if (
                        action.executed
                        and action.after is not None
                        and action.validation is not None
                        and action.validation.status == "not_met"
                        and action.error is not None
                        and action.error.startswith("动作后验证超时")
                    ):
                        self.result.stop_reason = "no_progress"
                        break
                    raise UiActionError(action)
                if action.after is None:
                    raise RuntimeError("滚动后没有截图")
                frame = action.after
        except Exception as error:
            logger.exception("朋友圈滚动采集失败，保存已采集证据：{}", error)
            self.result.stop_reason = "failed"
            self.result.error = str(error)
            raise
        finally:
            if previous is not None:
                previous.close()
            if self.pending is not None:
                self._save(self.pending.height, None)
                self.pending.close()
                self.pending = None
            path = self.directory / "manifest.json"
            path.write_text(self.result.model_dump_json(indent=2), encoding="utf-8")
            self.paths.append(path)
            logger.info(
                "朋友圈截图采集停止：{}，{} 帧，{} 段",
                self.result.stop_reason,
                len(self.result.frames),
                len(self.result.segments),
            )
        return self.result
