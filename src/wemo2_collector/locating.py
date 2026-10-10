"""通过左侧栏图标模板定位朋友圈入口，不使用固定点击坐标。"""

from PIL import Image

from wemo2_collector.interfaces import BaseLocator
from wemo2_collector.models import LocatedTarget, Rect, RouteConfig, Screenshot


class MomentsEntryLocator(BaseLocator):
    def __init__(self, config: RouteConfig):
        self.config = config

    def locate(self, frame: Screenshot, name: str) -> LocatedTarget | None:
        if name == "moments_entry":
            template_path = self.config.entry_template
            search_ratio = self.config.sidebar_ratio
        elif name == "discover_selected":
            template_path = self.config.selected_template
            search_ratio = self.config.sidebar_ratio
        elif name == "discover_menu":
            template_path = self.config.menu_template
            search_ratio = self.config.menu_ratio
        else:
            raise ValueError(f"未定义的定位目标：{name}")
        # 只在识别入口时加载 OpenCV，不影响 help/version 或单纯截图。
        import cv2
        import numpy as np

        with Image.open(frame.image_path) as image:
            gray = np.array(image.convert("L"))
        with Image.open(template_path) as image:
            template = np.array(image.convert("L"))
        sidebar_width = int(gray.shape[1] * search_ratio)
        sidebar = gray[:, :sidebar_width]
        candidates = []
        ratio = frame.surface.scale / self.config.reference_scale
        for adjustment in (0.9, 1.0, 1.1):
            width = max(2, round(template.shape[1] * ratio * adjustment))
            height = max(2, round(template.shape[0] * ratio * adjustment))
            if width > sidebar.shape[1] or height > sidebar.shape[0]:
                continue
            resized = cv2.resize(template, (width, height))
            if resized.std() < 1:
                raise ValueError("入口模板缺少可匹配的图案")
            scores = cv2.matchTemplate(sidebar, resized, cv2.TM_CCOEFF_NORMED)
            _, score, _, position = cv2.minMaxLoc(scores)
            if score < self.config.match_threshold:
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
            if second_score >= self.config.match_threshold:
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
