"""滚动采集及相邻截图拼接；以菜单锚点切分图像，不解析微信源身份。"""

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from loguru import logger
from PIL import Image

from wemo2.collectors.controller import UiActionError, UiController
from wemo2.collectors.media import MediaCapture
from wemo2.collectors.models import (
    FeedCaptureConfig,
    FeedCaptureResult,
    FeedSegment,
    LocatedTarget,
    Rect,
    Screenshot,
)


def overlap_shift(
    before: Image.Image, after: Image.Image, static_width: int | None = None
) -> int | None:
    """通过 Airtest 确认旧视口底部仍在新视口内；匹配不唯一时拒绝拼接。"""
    import numpy as np
    from airtest.aircv.template_matching import TemplateMatching

    if before.size != after.size:
        return None
    height = before.height
    width = before.width if static_width is None else min(before.width, static_width)
    with after.crop((0, 0, width, height)) as area:
        source = np.array(area.convert("RGB"))[:, :, ::-1].copy()
    shift = None
    best_score = 0.0
    # 底部新露出的图片可能仍在加载；中部提供独立证据，两个命中必须一致。
    for start in (height * 2 // 3, height // 3):
        end = min(height, start + height - height * 2 // 3)
        with before.crop((0, start, width, end)) as strip:
            search = np.array(strip.convert("RGB"))[:, :, ::-1].copy()
        if search.std() < 1:
            continue
        matches = TemplateMatching(
            search, source, threshold=0.95, rgb=False
        ).find_all_results()
        if not matches:
            continue
        if len(matches) != 1:
            return None
        candidate = start - int(matches[0]["rectangle"][0][1])
        if not 0 < candidate <= height * 2 // 3:
            return None
        # 125% DPI 的子像素滚动可能让不同条带的整数定位相差 1 像素。
        if shift is not None and abs(shift - candidate) > 1:
            return None
        score = matches[0]["confidence"]
        if shift is None or score > best_score:
            shift = candidate
            best_score = score
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
        on_progress: Callable[[FeedCaptureResult, MediaCapture], None] | None = None,
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
        self.on_progress = on_progress
        self.stream_height = 0
        self.segment_offset = 0

    def _image(self, frame: Screenshot, region: Rect) -> Image.Image:
        with Image.open(frame.image_path) as image:
            return image.crop(
                (region.x, region.y, region.x + region.width, region.y + region.height)
            ).convert("RGB")

    def _save(
        self, height: int, anchor: LocatedTarget | None, recognize: bool = True
    ) -> None:
        if self.pending is None or height <= 0:
            return
        path = self.directory / f"segment_{len(self.result.segments) + 1:04d}.png"
        with self.pending.crop((0, 0, self.pending.width, height)) as image:
            image.save(path)
            nickname = None
            if recognize and self.media is not None and self.config.recognize_nickname:
                nickname = self.media.reader.nickname(
                    image,
                    self.result.frames[-1].surface.scale,
                    search_height=image.height if self.partial_start else None,
                )
        segment = FeedSegment(
            image_path=path,
            source_frames=list(self.pending_frames),
            stream_start=self.segment_offset,
            stream_end=self.segment_offset + height,
            partial_start=self.partial_start,
            partial_end=anchor is None,
            end_anchor=anchor,
            nickname=None if nickname is None else nickname.text,
            nickname_score=None if nickname is None else nickname.score,
        )
        self.result.segments.append(segment)
        logger.info(
            "[朋友圈截图] 已保存片段，顶部缺失={}，底部缺失={}，路径={}",
            segment.partial_start,
            segment.partial_end,
            path,
        )
        self.segment_offset += height
        self.paths.append(path)
        remainder = self.pending.crop(
            (0, height, self.pending.width, self.pending.height)
        )
        self.pending.close()
        self.pending = remainder
        self.partial_start = False
        # 保留来源帧的完整上界，避免跨屏余段丢失早先帧的证据引用。

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
        """识别锚点并有上限地滚动；已验证封面可继续寻找首条动态。"""
        self.directory.mkdir(parents=True, exist_ok=False)
        frame = first
        region = self.config.region or self.controller.profile.feed_bounds(
            frame.surface
        )
        previous = None
        static_region = self.controller.profile.feed_static_bounds(
            region, first.surface.scale
        )
        logger.info(
            "[滚动采集] 开始，最多 {} 屏，每次滚轮 {} 步，证据目录={}",
            self.config.max_screens,
            self.config.wheel_steps,
            self.directory,
        )
        try:
            for index in range(self.config.max_screens):
                if frame.surface != first.surface:
                    raise RuntimeError("滚动采集中窗口发生变化，停止拼接")
                anchors = self.controller.locator.locate_all(
                    frame, "moments_anchor", region
                )
                logger.info(
                    "[滚动采集] 第 {}/{} 屏，识别到 {} 个朋友圈锚点",
                    index + 1,
                    self.config.max_screens,
                    len(anchors),
                )
                self.result.frames.append(frame)
                image = self._image(frame, region)
                if previous is None:
                    shift = image.height
                else:
                    shift = overlap_shift(previous, image, static_region.width)
                    previous.close()
                previous = image
                if shift is None:
                    logger.warning(
                        "[拼接] 无法确认与上一屏的重叠，停止继续滚动并保留证据"
                    )
                    self.result.stop_reason = "overlap_unverified"
                    break
                logger.info("[拼接] 第 {} 屏新增 {} 像素", index + 1, shift)
                self.stream_height += shift
                self._append(frame, image, region, anchors, shift)
                if (
                    not anchors
                    and index == 0
                    and self.controller.detect_page(frame) != "moments_selected"
                ):
                    self.result.stop_reason = "anchor_missing"
                    break
                try:
                    if self.media is not None:
                        self.media.process(
                            frame, region, anchors, self.stream_height - image.height
                        )
                finally:
                    if self.media is not None and self.on_progress is not None:
                        self.on_progress(self.result, self.media)
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
                    if action.after is not None:
                        self.result.frames.append(action.after)
                    if (
                        action.executed
                        and action.after is not None
                        and action.validation is not None
                        and action.validation.status == "not_met"
                        and action.validation.change_ratio is not None
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
                self._save(self.pending.height, None, recognize=False)
                self.pending.close()
                self.pending = None
            path = self.directory / "manifest.json"
            path.write_text(self.result.model_dump_json(indent=2), encoding="utf-8")
            self.paths.append(path)
            logger.info(
                "[滚动采集] 停止：{}，{} 帧，{} 段，清单={}",
                self.result.stop_reason,
                len(self.result.frames),
                len(self.result.segments),
                path,
            )
        return self.result

    def download_media(self) -> None:
        """从扫描终点逐屏回看，依靠相邻截图重叠定位流坐标，再下载到最终目录。"""
        if self.media is None or not self.media.downloads:
            self.result.media_stop_reason = "no_complete_posts"
            logger.warning(
                "[媒体下载] 没有已确认日期、昵称和完整文案的朋友圈，保留截图证据"
            )
            self._write_manifest()
            return
        if self.result.stop_reason not in ("max_screens", "no_progress"):
            self.result.media_stop_reason = "scan_unverified"
            self._write_manifest()
            return
        original = self.result.frames[-1]
        region = self.config.region or self.controller.profile.feed_bounds(
            original.surface
        )
        static = self.controller.profile.feed_static_bounds(
            region, original.surface.scale
        )
        frame = self.controller.capture()
        from wemo2.collectors.validation import region_unchanged

        if frame.surface != original.surface or not region_unchanged(
            original, frame, static
        ):
            raise RuntimeError("下载开始前列表位置已变化，未使用旧的朋友圈坐标")
        offset = self.stream_height - region.height
        limit = self.config.max_screens * self.config.wheel_steps + 2
        logger.info(
            "[媒体下载] 开始回看，归档范围={}，最多 {} 屏",
            len(self.media.downloads),
            limit,
        )
        try:
            for _ in range(limit):
                anchors = self.controller.locator.locate_all(
                    frame, "moments_anchor", region
                )
                self.media.process(frame, region, anchors, offset)
                if offset <= 0:
                    self.result.media_stop_reason = "completed_visible_range"
                    return
                # 一步回滚保留足够重叠；这是移动到上一段，不是重发失败的输入。
                action = self.controller.scroll(
                    region,
                    -1,
                    settle_seconds=self.config.settle_seconds,
                    timeout=self.config.timeout_seconds,
                )
                if action.status != "verified" or action.after is None:
                    raise UiActionError(action)
                after = action.after
                if after.surface != frame.surface:
                    raise RuntimeError("媒体回看时窗口发生变化，停止下载")
                with (
                    self._image(after, region) as earlier,
                    self._image(frame, region) as later,
                ):
                    shift = overlap_shift(earlier, later, static.width)
                if shift is None:
                    raise RuntimeError(
                        "媒体回看无法验证相邻截图重叠，停止以防下载到错误目录"
                    )
                offset -= shift
                logger.info("[媒体回看] 向上移动 {} 像素，当前流位置={}", shift, offset)
                frame = after
            raise RuntimeError("媒体回看达到屏数上限，未完成已扫描范围")
        except Exception as error:
            logger.exception("媒体回看下载失败，保留已保存图片和元数据：{}", error)
            self.result.media_stop_reason = "failed"
            self.result.error = str(error)
            raise
        finally:
            self._write_manifest()

    def _write_manifest(self) -> None:
        path = self.directory / "manifest.json"
        path.write_text(self.result.model_dump_json(indent=2), encoding="utf-8")
