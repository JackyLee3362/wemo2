"""昵称 OCR 与媒体预览区域识别；不将昵称或图像位置当作微信稳定身份。"""

import logging
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from wemo2_collector.airtest_logging import AirtestLogHandler
from wemo2_collector.models import Rect


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

    def nickname(self, image: Image.Image, scale: float) -> TextLine | None:
        """识别动态顶部蓝色昵称；表情及罕见字可能有误，结果不用于身份去重。"""
        import numpy as np

        with image.crop(
            (0, 0, image.width, min(image.height, round(96 * scale)))
        ) as header:
            lines = self.read(header)
            for line in lines:
                if line.score < 0.8:
                    continue
                bounds = line.bounds
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


def media_tiles(image: Image.Image, scale: float) -> list[Rect]:
    """使用 OpenCV 连通区域找深色页面媒体块；不识别为媒体的区域不自动点击。"""
    import cv2
    import numpy as np

    pixels = np.array(image.convert("RGB"))
    background = np.median(pixels[: max(1, round(4 * scale))], axis=(0, 1))
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
