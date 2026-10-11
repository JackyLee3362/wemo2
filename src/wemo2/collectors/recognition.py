"""昵称 OCR 与媒体预览区域识别；不将昵称或图像位置当作微信稳定身份。"""

import logging
import re
from datetime import datetime
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from wemo2.collectors.airtest_logging import AirtestLogHandler
from wemo2.collectors.dates import approximate_day
from wemo2.collectors.models import MomentDetails, Rect


class TextLine(BaseModel):
    """OCR 行文本、置信度和图像内范围；原始文本保留供人工核对。"""

    model_config = ConfigDict(extra="forbid")
    text: str
    score: float = Field(ge=0, le=1)
    bounds: Rect


class TextRecognizer:
    """延迟加载 RapidOCR，提供昵称和另存为菜单文字识别。"""

    def __init__(self):
        self.engine = None

    def read(self, image: Image.Image) -> list[TextLine]:
        import numpy as np

        if self.engine is None:
            from rapidocr import RapidOCR

            native_log = logging.getLogger("RapidOCR")
            native_log.handlers = [AirtestLogHandler()]
            native_log.propagate = False
            self.engine = RapidOCR()
        result = self.engine(image)
        if result.txts is None:
            return []
        lines = []
        for box, text, score in zip(
            result.boxes, result.txts, result.scores, strict=True
        ):
            left = max(0, int(np.min(box[:, 0])))
            top = max(0, int(np.min(box[:, 1])))
            right = min(image.width, int(np.max(box[:, 0])) + 1)
            bottom = min(image.height, int(np.max(box[:, 1])) + 1)
            if right > left and bottom > top:
                lines.append(
                    TextLine(
                        text=text,
                        score=min(1, max(0, float(score))),
                        bounds=Rect(
                            x=left, y=top, width=right - left, height=bottom - top
                        ),
                    )
                )
        lines.sort(key=lambda line: (line.bounds.y, line.bounds.x))
        return lines

    def nickname(
        self, image: Image.Image, scale: float, *, search_height: int | None = None
    ) -> TextLine | None:
        """识别动态顶部蓝色昵称；表情及罕见字可能有误，结果不用于身份去重。"""
        import numpy as np

        with image.crop(
            (0, 0, image.width, min(image.height, search_height or round(96 * scale)))
        ) as header:
            lines = self.read(header)
            for line in lines:
                if line.score < 0.8:
                    continue
                bounds = line.bounds
                # 邻近头像提供布局约束，避免跨屏片段中的蓝色海报文字冒充昵称。
                avatar_left = bounds.x - round(50 * scale)
                avatar_top = max(0, bounds.y - round(5 * scale))
                avatar_size = round(40 * scale)
                if avatar_left < 0 or avatar_top + avatar_size > header.height:
                    continue
                with header.crop(
                    (
                        avatar_left,
                        avatar_top,
                        avatar_left + avatar_size,
                        avatar_top + avatar_size,
                    )
                ).convert("L") as avatar:
                    portrait = np.array(avatar)
                    if (
                        portrait.std() < 15
                        or ((portrait >= 20) & (portrait <= 40)).mean() > 0.6
                    ):
                        continue
                with header.crop(
                    (
                        bounds.x,
                        bounds.y,
                        bounds.x + bounds.width,
                        bounds.y + bounds.height,
                    )
                ).convert("RGB") as crop:
                    pixels = np.array(crop).astype(int)
                blue = (pixels[:, :, 2] - pixels[:, :, 0] > 10) & (pixels[:, :, 2] > 90)
                if blue.mean() > 0.08:
                    return line
        return None

    def post_details(
        self, image: Image.Image, captured_at: datetime, scale: float
    ) -> MomentDetails:
        """识别人名、头像下方文案和发布时间，排除图片内文字及时间后的评论。"""
        lines = self.read(image)
        name = self.nickname(image, scale, search_height=image.height)
        details = MomentDetails()
        tiles = media_tiles(image, scale)
        footer = None
        for line in lines:
            compact = "".join(line.text.split())
            if line.score < 0.8 or name is not None and line.bounds.y <= name.bounds.y:
                continue
            if not self._gray_footer(image, line):
                continue  # 文案中的“今天”“昨天”不是发布时间。
            in_media = False
            for tile in tiles:
                if (
                    tile.x <= line.bounds.x < tile.x + tile.width
                    and tile.y <= line.bounds.y < tile.y + tile.height
                ):
                    in_media = True
                    break
            if in_media:
                continue
            if re.fullmatch(
                r"(?:刚刚|今天|昨天|前天|\d+(?:分钟|小时|天)前|(?:\d{4}年)?\d{1,2}月\d{1,2}日?(?:周[一二三四五六日天])?|(?:\d{4}[-/])?\d{1,2}[-/]\d{1,2})",
                compact,
            ):
                footer = line
                details.date_raw = line.text
                details.approximate_date = approximate_day(line.text, captured_at)
                break
        if name is None:
            return details
        details.person = name.text
        details.header_top = max(0, name.bounds.y - round(5 * scale))
        # 表情可能把蓝色昵称分成多块；合并同一行邻近的蓝色文字。
        fragments = []
        for line in lines:
            if line.bounds.x >= name.bounds.x and abs(
                line.bounds.y - name.bounds.y
            ) <= round(7 * scale):
                if line.score >= 0.8 and self._blue_text(image, line):
                    fragments.append(line)
        fragments.sort(key=lambda line: line.bounds.x)
        right = name.bounds.x
        words = []
        for line in fragments:
            if line.bounds.x - right > round(40 * scale):
                break
            words.append(line.text)
            right = line.bounds.x + line.bounds.width
        if words:
            details.person = "".join(words)
        bottom = image.height if footer is None else footer.bounds.y
        for tile in tiles:
            if tile.y > name.bounds.y + name.bounds.height:
                bottom = min(bottom, tile.y)
        caption = []
        incomplete = footer is None
        for line in lines:
            if (
                line.bounds.y < name.bounds.y + name.bounds.height - round(3 * scale)
                or line.bounds.y >= bottom
            ):
                continue
            if line.bounds.x < name.bounds.x - round(8 * scale):
                continue
            if line.text.strip() in ("全文", "收起"):
                incomplete = True
                continue
            if self._blue_text(image, line):
                continue  # 地点、链接及其他蓝色交互项不是正文。
            caption.append(line)
            if line.score < 0.8:
                incomplete = True
        caption.sort(key=lambda line: (line.bounds.y, line.bounds.x))
        details.text = "\n".join(line.text for line in caption)
        details.text_status = "partial" if incomplete else "complete"
        return details

    def _blue_text(self, image: Image.Image, line: TextLine) -> bool:
        import numpy as np

        bounds = line.bounds
        with image.crop(
            (bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height)
        ).convert("RGB") as crop:
            pixels = np.array(crop).astype(int)
        blue = (pixels[:, :, 2] - pixels[:, :, 0] > 10) & (pixels[:, :, 2] > 90)
        return blue.mean() > 0.08

    def _gray_footer(self, image: Image.Image, line: TextLine) -> bool:
        import numpy as np

        bounds = line.bounds
        with image.crop(
            (bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height)
        ).convert("RGB") as crop:
            pixels = np.array(crop)
        return (pixels.max(axis=2) > 190).mean() < 0.03


def media_tiles(image: Image.Image, scale: float) -> list[Rect]:
    """使用 OpenCV 连通区域找深色页面媒体块；不识别为媒体的区域不自动点击。"""
    import cv2
    import numpy as np

    pixels = np.array(image.convert("RGB"))
    # 首屏顶部可能是封面，不能把封面颜色当作背景；寻找整幅卡片的主色桶。
    quantized = pixels.astype(int) // 16
    buckets = quantized[:, :, 0] * 256 + quantized[:, :, 1] * 16 + quantized[:, :, 2]
    dominant = np.bincount(buckets.ravel(), minlength=4096).argmax()
    background = np.median(pixels[buckets == dominant], axis=0)
    mask = (np.max(np.abs(pixels.astype(float) - background), axis=2) > 20).astype(
        "uint8"
    ) * 255
    kernel = np.ones((3, 3), dtype="uint8")
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    minimum = round(56 * scale)
    targets = []
    for x, y, width, height, area in stats[1:]:
        if (
            width < minimum
            or height < minimum
            or y < round(24 * scale)
            or width > image.width * 0.9
            or not 0.5 <= width / height <= 2.5
            or area < width * height * 0.5
        ):
            continue
        targets.append(Rect(x=int(x), y=int(y), width=int(width), height=int(height)))
    targets.sort(key=lambda target: (target.y, target.x))
    return targets


def file_kind(path: Path) -> str:
    """检查实际字节；文件扩展名不能证明保存的是图片或视频。"""
    from loguru import logger

    with path.open("rb") as file:
        header = file.read(32)
    if (
        header[4:8] == b"ftyp"
        or header[:4] == b"\x1aE\xdf\xa3"
        or (header[:4] == b"RIFF" and header[8:12] == b"AVI ")
    ):
        return "video"
    try:
        with Image.open(path) as image:
            image.verify()
        return "image"
    except (OSError, ValueError) as error:
        logger.exception("另存为文件不是可验证的图片或视频：{}，{}", path, error)
        raise RuntimeError("另存为文件无法识别为图片或视频，未标记成功") from error
