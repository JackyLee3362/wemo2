"""按 HWND 获取单个窗口图像，保存 PNG 及对应截图元数据。"""

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
        # 使用 HWND 定向截图，不降级为整屏截图，避免截入无关窗口。
        image = ImageGrab.grab(window=int(surface.surface_id))
        try:
            if image.size != (surface.bounds.width, surface.bounds.height):
                raise RuntimeError(
                    f"截图尺寸 {image.width}x{image.height} 与客户区尺寸 "
                    f"{surface.bounds.width}x{surface.bounds.height} 不一致"
                    f"（DPI 缩放 {surface.scale}），请检查 DPI 或窗口尺寸变化"
                )
            if image.convert("RGB").getbbox() is None:
                raise RuntimeError("微信窗口返回了全黑截图，请确认窗口正常显示")
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
