"""按客户区坐标截取屏幕显示内容，保存 PNG 及截图元数据。"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from PIL import ImageGrab

from wemo2_collector.interfaces import BaseScreenshotter
from wemo2_collector.models import Platform, Screenshot, Surface


class WindowsScreenshotter(BaseScreenshotter):
    def __init__(self, directory: str | Path = "data/screenshot"):
        self.directory = Path(directory)

    def capture(self, surface: Surface) -> Screenshot:
        if sys.platform != "win32" or surface.platform != Platform.windows:
            raise RuntimeError("截图器仅支持 Windows 窗口")
        # HWND/GDI 可能拿不到硬件加速内容，改截已置于前台的可见客户区。
        bounds = surface.bounds
        bbox = (bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height)
        image = ImageGrab.grab(
            bbox=bbox, all_screens=True, include_layered_windows=True
        )
        try:
            if image.size != (surface.bounds.width, surface.bounds.height):
                raise RuntimeError(
                    f"截图尺寸 {image.width}x{image.height} 与客户区尺寸 "
                    f"{surface.bounds.width}x{surface.bounds.height} 不一致"
                    f"（DPI 缩放 {surface.scale}），请检查 DPI 或窗口尺寸变化"
                )
            grayscale = image.convert("L")
            try:
                histogram = grayscale.histogram()
            finally:
                grayscale.close()
            dark_pixels = sum(histogram[:8])
            if dark_pixels / (image.width * image.height) >= 0.999:
                raise RuntimeError("微信截图几乎全黑，请确认桌面未锁定且窗口可见")
            captured_at = datetime.now(UTC)
            frame_id = str(uuid4())
            filename = f"wechat_{captured_at.strftime('%Y%m%dT%H%M%S')}_{frame_id}.png"
            self.directory.mkdir(parents=True, exist_ok=True)
            image_path = (self.directory / filename).resolve()
            image.save(image_path, format="PNG")
            frame = Screenshot(
                frame_id=frame_id,
                captured_at=captured_at,
                surface=surface,
                image_path=image_path,
            )
            image_path.with_suffix(".json").write_text(
                frame.model_dump_json(indent=2), encoding="utf-8"
            )
            return frame
        finally:
            image.close()
