"""通用截图模板匹配；不理解页面，也不操作鼠标。"""

from PIL import Image

from wemo2_collector.interfaces import BaseImageMatcher
from wemo2_collector.models import LocatedTarget, MatchConfig, Rect, Screenshot
from wemo2_collector.themes import TemplateSpec


class TemplateMatcher(BaseImageMatcher):
    """在模板指定区域内执行多尺度图片匹配，返回位置和匹配分数。

    低分或多个独立目标时拒绝匹配，不理解页面含义，也不执行界面动作。
    """

    def __init__(self, config: MatchConfig):
        self.config = config

    def match(
        self, frame: Screenshot, name: str, spec: TemplateSpec
    ) -> LocatedTarget | None:
        # 只在匹配时加载 OpenCV，不影响 help/version 或单纯截图。
        import cv2
        import numpy as np

        with Image.open(frame.image_path) as image:
            gray = np.array(image.convert("L"))
        with Image.open(spec.path) as image:
            template = np.array(image.convert("L"))
        region_width = int(gray.shape[1] * spec.region_ratio)
        region = gray[:, :region_width]
        candidates = []
        ratio = frame.surface.scale / spec.reference_scale
        for adjustment in (0.9, 1.0, 1.1):
            width = max(2, round(template.shape[1] * ratio * adjustment))
            height = max(2, round(template.shape[0] * ratio * adjustment))
            if width > region.shape[1] or height > region.shape[0]:
                continue
            resized = cv2.resize(template, (width, height))
            if resized.std() < 1:
                raise ValueError("模板缺少可匹配的图案")
            scores = cv2.matchTemplate(region, resized, cv2.TM_CCOEFF_NORMED)
            _, score, _, position = cv2.minMaxLoc(scores)
            if score < self.config.threshold:
                continue
            x, y = position
            candidate = LocatedTarget(
                frame_id=frame.frame_id,
                name=name,
                bounds=Rect(x=x, y=y, width=width, height=height),
                score=min(1.0, max(0.0, score)),
                method="template",
            )
            candidates.append(candidate)
            # 屏蔽同一图标附近的匹配，只接受一个独立目标。
            scores[max(0, y - height) : y + height, max(0, x - width) : x + width] = -1
            _, second_score, _, _ = cv2.minMaxLoc(scores)
            if second_score >= self.config.threshold:
                return None
        if not candidates:
            return None
        candidates.sort(key=lambda target: target.score, reverse=True)
        best = candidates[0]
        best_x = best.bounds.x + best.bounds.width // 2
        best_y = best.bounds.y + best.bounds.height // 2
        for target in candidates[1:]:
            x = target.bounds.x + target.bounds.width // 2
            y = target.bounds.y + target.bounds.height // 2
            if (
                abs(x - best_x) > best.bounds.width
                or abs(y - best_y) > best.bounds.height
            ):
                return None
        return best
