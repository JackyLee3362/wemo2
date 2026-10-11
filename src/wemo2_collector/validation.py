"""截图、定位、页面与进展的独立校验器，不执行界面动作。"""

from datetime import UTC, datetime

from PIL import Image, ImageChops

from wemo2_collector.interfaces import BaseLocator, BaseValidator
from wemo2_collector.models import (
    ControllerConfig,
    Expectation,
    LocatedTarget,
    Rect,
    Screenshot,
    ValidationResult,
)
from wemo2_collector.pages import PageRegistry


class UiValidator(BaseValidator):
    """独立校验截图时效、图片尺寸、目标归属与范围，并判断页面或区域变化。

    只输出校验结论，不执行平台动作；像素变化不等于已确认朋友圈翻页或到底。
    """

    def __init__(
        self, pages: PageRegistry, locator: BaseLocator, config: ControllerConfig
    ):
        self.pages = pages
        self.locator = locator
        self.config = config

    def validate_frame(self, frame: Screenshot) -> None:
        age = (datetime.now(UTC) - frame.captured_at).total_seconds()
        if age < 0 or age > self.config.max_frame_age:
            raise RuntimeError("截图已经过期，请重新截图定位")
        with Image.open(frame.image_path) as image:
            if image.size != (frame.surface.bounds.width, frame.surface.bounds.height):
                raise RuntimeError("截图实际尺寸与区域快照不一致")
            with image.convert("L") as gray:
                histogram = gray.histogram()
            if sum(histogram[:8]) / (image.width * image.height) >= 0.999:
                raise RuntimeError("截图几乎全黑，不能执行界面动作")

    def validate_target(self, frame: Screenshot, target: LocatedTarget) -> None:
        self.validate_frame(frame)
        if target.frame_id != frame.frame_id:
            raise RuntimeError("定位结果不属于当前截图，拒绝动作")
        self._check_region(frame, target.bounds)

    def _check_region(self, frame: Screenshot, region: Rect) -> None:
        if (
            region.x < 0
            or region.y < 0
            or region.x + region.width > frame.surface.bounds.width
            or region.y + region.height > frame.surface.bounds.height
        ):
            raise RuntimeError("目标或验证区域超出截图范围")

    def detect_page(self, frame: Screenshot) -> str:
        matches = []
        cache = {}
        for page in self.pages.pages.values():
            if not page.required:
                continue
            valid = True
            for name in page.required + page.forbidden:
                if name not in cache:
                    cache[name] = self.locator.locate(frame, name) is not None
                if name in page.required and not cache[name]:
                    valid = False
                if name in page.forbidden and cache[name]:
                    valid = False
            if valid:
                matches.append(page.name)
        if len(matches) != 1:
            return "unknown"
        return matches[0]

    def evaluate(
        self, before: Screenshot, after: Screenshot, expectation: Expectation
    ) -> ValidationResult:
        if expectation.kind == "page":
            if expectation.page is None:
                raise ValueError("页面预期必须指定 page")
            page = self.pages.get_page(expectation.page)
            if not page.required:
                return ValidationResult(
                    status="unavailable", message=f"页面 {page.name} 尚无验证特征"
                )
            actual = self.detect_page(after)
            if actual == page.name:
                return ValidationResult(
                    status="passed", message=f"页面 {actual} 已验证", actual_page=actual
                )
            return ValidationResult(
                status="not_met",
                message=f"预期 {page.name}，当前 {actual}",
                actual_page=actual,
            )
        if before.surface != after.surface:
            raise RuntimeError("窗口或区域已变化，不能判断滑动进展")
        region = expectation.region or Rect(
            x=0,
            y=0,
            width=before.surface.bounds.width,
            height=before.surface.bounds.height,
        )
        self._check_region(before, region)
        bbox = (region.x, region.y, region.x + region.width, region.y + region.height)
        with (
            Image.open(before.image_path) as original,
            Image.open(after.image_path) as current,
        ):
            with original.convert("RGB") as first, current.convert("RGB") as second:
                with (
                    first.crop(bbox) as first_region,
                    second.crop(bbox) as second_region,
                ):
                    with ImageChops.difference(
                        first_region, second_region
                    ) as difference:
                        with difference.convert("L") as gray:
                            histogram = gray.histogram()
        changed = sum(histogram[9:]) / (region.width * region.height)
        if changed >= expectation.change_threshold:
            return ValidationResult(
                status="passed",
                message="指定区域发生变化；不代表已确认到底",
                change_ratio=changed,
            )
        return ValidationResult(
            status="not_met", message="指定区域未观察到足够变化", change_ratio=changed
        )
