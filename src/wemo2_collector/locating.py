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
                    search, source, threshold=self.config.threshold, rgb=False
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
