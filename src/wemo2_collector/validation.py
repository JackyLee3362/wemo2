"""无状态校验函数：截图、目标、页面和区域进展，不执行界面动作。"""

from datetime import UTC, datetime

from PIL import Image, ImageChops

from wemo2_collector.locating import AirtestLocator
from wemo2_collector.models import (
    Expectation,
    LocatedTarget,
    Rect,
    Screenshot,
    ValidationResult,
)
from wemo2_collector.profiles import UiProfile


def validate_frame(frame: Screenshot, max_age: float = 5.0) -> None:
    """校验截图时效及实际图片，拒绝使用旧帧执行输入。"""
    age = (datetime.now(UTC) - frame.captured_at).total_seconds()
    if age < 0 or age > max_age:
        raise RuntimeError("截图已经过期，请重新截图定位")
    with Image.open(frame.image_path) as image:
        validate_image(image, (frame.surface.bounds.width, frame.surface.bounds.height))


def validate_image(image: Image.Image, expected_size: tuple[int, int]) -> None:
    """共用图片尺寸和黑屏检查，截图保存及输入前校验使用同一规则。"""
    if image.size != expected_size:
        raise RuntimeError(
            f"截图实际尺寸 {image.size} 与区域尺寸 {expected_size} 不一致"
        )
    with image.convert("L") as gray:
        histogram = gray.histogram()
    if sum(histogram[:8]) / (image.width * image.height) >= 0.999:
        raise RuntimeError("截图几乎全黑，不能执行界面动作")


def validate_target(
    frame: Screenshot, target: LocatedTarget, max_age: float = 5.0
) -> None:
    """校验目标帧归属和坐标范围，供控制器及驱动共同调用。"""
    validate_frame(frame, max_age)
    if target.frame_id != frame.frame_id:
        raise RuntimeError("定位结果不属于当前截图，拒绝动作")
    _check_region(frame, target.bounds)


def _check_region(frame: Screenshot, region: Rect) -> None:
    if (
        region.x < 0
        or region.y < 0
        or region.x + region.width > frame.surface.bounds.width
        or region.y + region.height > frame.surface.bounds.height
    ):
        raise RuntimeError("目标或验证区域超出截图范围")


def region_unchanged(before: Screenshot, after: Screenshot, region: Rect) -> bool:
    """严格比较指定区域像素，用于动画稳定及旧媒体坐标的内容连续性校验。"""
    if before.surface != after.surface:
        return False
    _check_region(before, region)
    box = (region.x, region.y, region.x + region.width, region.y + region.height)
    with Image.open(before.image_path) as old, Image.open(after.image_path) as current:
        with (
            old.crop(box).convert("RGB") as first,
            current.crop(box).convert("RGB") as second,
        ):
            with ImageChops.difference(first, second) as difference:
                return difference.getbbox() is None


def detect_page(frame: Screenshot, profile: UiProfile, locator: AirtestLocator) -> str:
    """匹配注册页面标志，多页面命中或不确定时返回 unknown。"""
    matches = []
    cache = {}
    for page in profile.pages.values():
        if not page.required:
            continue
        valid = True
        for name in page.required + page.forbidden:
            if name not in cache:
                cache[name] = locator.locate(frame, name) is not None
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
    before: Screenshot,
    after: Screenshot,
    expectation: Expectation,
    profile: UiProfile,
    locator: AirtestLocator,
) -> ValidationResult:
    """判断页面或区域变化；无法校验的目标保持待验证。"""
    if expectation.kind == "page":
        if expectation.page is None:
            raise ValueError("页面预期必须指定 page")
        page = profile.get_page(expectation.page)
        if not page.required:
            return ValidationResult(
                status="unavailable", message=f"页面 {page.name} 尚无验证特征"
            )
        actual = detect_page(after, profile, locator)
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
    _check_region(before, region)
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
                with ImageChops.difference(first_region, second_region) as difference:
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
