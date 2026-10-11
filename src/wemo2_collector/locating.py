"""Airtest 图片定位的薄适配，只补充主题、DPI 和唯一目标约束。"""

from PIL import Image

from wemo2_collector.airtest_logging import configure_airtest_logging
from wemo2_collector.models import LocatedTarget, MatchConfig, Rect, Screenshot
from wemo2_collector.profiles import UiProfile


class AirtestLocator:
    """根据 profile 找模板，使用 Airtest 多目标匹配并拒绝歧义，不执行点击。"""

    def __init__(self, profile: UiProfile, theme: str, config: MatchConfig):
        profile.validate_theme(theme)
        self.profile = profile
        self.theme = theme
        self.config = config

    def locate(self, frame: Screenshot, name: str) -> LocatedTarget | None:
        # 只在实际匹配时加载 Airtest，help/version 不加载图像引擎。
        import numpy as np
        from airtest.aircv.template_matching import TemplateMatching

        configure_airtest_logging()
        spec = self.profile.target(self.theme, name)
        with Image.open(frame.image_path) as image:
            width = int(image.width * spec.region_ratio)
            with image.convert("RGB") as rgb:
                source = np.array(rgb)[:, :width, ::-1].copy()
        with Image.open(spec.path) as image:
            template = image.convert("RGB")
        try:
            with template.convert("L") as gray:
                if np.array(gray).std() < 1:
                    raise ValueError("模板缺少可匹配的图案")
            best = None
            ratio = frame.surface.scale / spec.reference_scale
            # 这里只适配参考 DPI 的三个邻近尺寸；相似度和多目标搜索交给 Airtest。
            for adjustment in (0.9, 1.0, 1.1):
                size = (
                    max(2, round(template.width * ratio * adjustment)),
                    max(2, round(template.height * ratio * adjustment)),
                )
                if size[0] > source.shape[1] or size[1] > source.shape[0]:
                    continue
                with template.resize(size, Image.Resampling.BILINEAR) as resized:
                    search = np.array(resized)[:, :, ::-1].copy()
                matches = TemplateMatching(
                    search, source, threshold=self.config.threshold, rgb=spec.rgb
                ).find_all_results()
                if not matches:
                    continue
                if len(matches) != 1:
                    return None
                match = matches[0]
                left, top = match["rectangle"][0]
                right, bottom = match["rectangle"][2]
                candidate = LocatedTarget(
                    frame_id=frame.frame_id,
                    name=name,
                    bounds=Rect(
                        x=int(left),
                        y=int(top),
                        width=int(right - left),
                        height=int(bottom - top),
                    ),
                    score=min(1.0, max(0.0, match["confidence"])),
                    method="airtest_template",
                )
                if best is not None:
                    if (
                        abs(
                            match["result"][0]
                            - (best.bounds.x + best.bounds.width // 2)
                        )
                        > best.bounds.width // 2
                        or abs(
                            match["result"][1]
                            - (best.bounds.y + best.bounds.height // 2)
                        )
                        > best.bounds.height // 2
                    ):
                        return None
                if best is None or candidate.score > best.score:
                    best = candidate
            return best
        finally:
            template.close()

    def locate_all(
        self, frame: Screenshot, name: str, region: Rect | None = None
    ) -> list[LocatedTarget]:
        """识别重复锚点；按实际 DPI 匹配一次，避免相邻尺寸重复报告同一目标。"""
        import numpy as np
        from airtest.aircv.template_matching import TemplateMatching

        configure_airtest_logging()
        spec = self.profile.target(self.theme, name)
        area = region or Rect(
            x=0,
            y=0,
            width=int(frame.surface.bounds.width * spec.region_ratio),
            height=frame.surface.bounds.height,
        )
        if (
            area.x < 0
            or area.y < 0
            or area.x + area.width > frame.surface.bounds.width
            or area.y + area.height > frame.surface.bounds.height
        ):
            raise ValueError("锚点搜索区域超出截图")
        with Image.open(frame.image_path) as image:
            with image.crop(
                (area.x, area.y, area.x + area.width, area.y + area.height)
            ).convert("RGB") as cropped:
                source = np.array(cropped)[:, :, ::-1].copy()
        with Image.open(spec.path) as image:
            ratio = frame.surface.scale / spec.reference_scale
            size = (
                max(2, round(image.width * ratio)),
                max(2, round(image.height * ratio)),
            )
            with image.convert("RGB").resize(
                size, Image.Resampling.BILINEAR
            ) as resized:
                search = np.array(resized)[:, :, ::-1].copy()
        if size[0] > area.width or size[1] > area.height:
            return []
        matcher = TemplateMatching(
            search, source, threshold=self.config.threshold, rgb=spec.rgb
        )
        matches = matcher.find_all_results() or []
        if len(matches) > matcher.MAX_RESULT_COUNT:
            raise RuntimeError("可见锚点超过 Airtest 匹配上限，请缩小列表区域")
        targets = []
        for match in matches:
            left, top = match["rectangle"][0]
            right, bottom = match["rectangle"][2]
            targets.append(
                LocatedTarget(
                    frame_id=frame.frame_id,
                    name=name,
                    bounds=Rect(
                        x=int(left) + area.x,
                        y=int(top) + area.y,
                        width=int(right - left),
                        height=int(bottom - top),
                    ),
                    score=min(1.0, max(0.0, match["confidence"])),
                    method="airtest_template",
                )
            )
        targets.sort(key=lambda target: target.bounds.y)
        return targets

    def is_enlarged_preview(
        self,
        source: Screenshot,
        current: Screenshot,
        bounds: Rect,
        region: tuple[float, float, float, float],
        threshold: float,
        min_scale: float,
        *,
        allow_independent: bool = False,
    ) -> bool:
        """用 Airtest 匹配放大的原缩略图；独立查看器须由调用者先校验窗口归属。"""
        # 仅在媒体验证时加载图像引擎，启动和帮助命令不受影响。
        import numpy as np
        from airtest.aircv.multiscale_template_matching import (
            MultiScaleTemplateMatching,
        )

        if current.surface != source.surface and not allow_independent:
            return False
        configure_airtest_logging()
        x, y, width, height = region
        size = current.surface.bounds
        box = (
            round(x * size.width),
            round(y * size.height),
            round((x + width) * size.width),
            round((y + height) * size.height),
        )
        with Image.open(source.image_path) as image:
            with image.crop(
                (bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height)
            ).convert("RGB") as preview:
                template = np.array(preview)[:, :, ::-1].copy()
        with Image.open(current.image_path) as image:
            with image.crop(box).convert("RGB") as viewer:
                screen = np.array(viewer)[:, :, ::-1].copy()
        if template.std() < 1:
            return False
        # 原像素搜索和较细步长避免树叶等密集纹理被二次缩放后相似度失真。
        match = MultiScaleTemplateMatching(
            template,
            screen,
            threshold=threshold,
            rgb=False,
            scale_step=0.005,
            scale_max=max(screen.shape[:2]),
        ).find_best_result()
        if match is None:
            return False
        left, top = match["rectangle"][0]
        right, bottom = match["rectangle"][2]
        return (
            right - left >= bounds.width * min_scale
            and bottom - top >= bounds.height * min_scale
        )

    def same_preview(
        self, before: Screenshot, after: Screenshot, old_bounds: Rect, new_bounds: Rect
    ) -> bool:
        """核对已确认重叠位置的缩略图，允许 DPI 舍入但拒绝不同图片缓存。"""
        import numpy as np
        from airtest.aircv.template_matching import TemplateMatching

        if before.surface != after.surface:
            return False
        configure_airtest_logging()
        with Image.open(before.image_path) as image:
            with image.crop(
                (
                    old_bounds.x,
                    old_bounds.y,
                    old_bounds.x + old_bounds.width,
                    old_bounds.y + old_bounds.height,
                )
            ).convert("RGB") as crop:
                template = np.array(crop)[:, :, ::-1].copy()
        size = after.surface.bounds
        with Image.open(after.image_path) as image:
            with image.crop(
                (
                    max(0, new_bounds.x - 2),
                    max(0, new_bounds.y - 2),
                    min(size.width, new_bounds.x + new_bounds.width + 2),
                    min(size.height, new_bounds.y + new_bounds.height + 2),
                )
            ).convert("RGB") as crop:
                screen = np.array(crop)[:, :, ::-1].copy()
        if (
            template.std() < 1
            or template.shape[0] > screen.shape[0]
            or template.shape[1] > screen.shape[1]
        ):
            return False
        # 已由流位置限定同一候选，只在其两像素边距内校准，不进行多目标搜索。
        match = TemplateMatching(
            template, screen, threshold=0.97, rgb=True
        ).find_best_result()
        return match is not None
