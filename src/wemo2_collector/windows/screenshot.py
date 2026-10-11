"""按客户区坐标截取屏幕显示内容，保存 PNG 及截图元数据。"""

import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PIL import Image

from wemo2_collector.models import Platform, Screenshot, Surface
from wemo2_collector.timezones import resolve_timezone
from wemo2_collector.validation import validate_image
from wemo2_collector.windows.backend import create_device


class WindowsScreenshotter:
    """通过 Airtest 截取虚拟桌面并裁出客户区，保存 PNG 和对应截图元数据。

    校验尺寸和近乎全黑的结果，不把完整桌面写入磁盘，也不负责页面判断。
    """

    def __init__(
        self, directory: str | Path = "data/screenshot", timezone: str = "Asia/Shanghai"
    ):
        self.directory = Path(directory)
        self.timezone = resolve_timezone(timezone)

    def capture(self, surface: Surface) -> Screenshot:
        if sys.platform != "win32" or surface.platform != Platform.windows:
            raise RuntimeError("截图器仅支持 Windows 窗口")
        image = self._capture_image(surface)
        try:
            validate_image(image, (surface.bounds.width, surface.bounds.height))
            captured_at = datetime.now(self.timezone)
            frame_id = str(uuid4())
            filename = (
                f"wechat_{captured_at.strftime('%Y%m%dT%H%M%S%z')}_{frame_id}.png"
            )
            self.directory.mkdir(parents=True, exist_ok=True)
            image_path = (self.directory / filename).resolve()
            image.save(image_path, format="PNG")
            frame = Screenshot(
                frame_id=frame_id,
                captured_at=captured_at,
                timezone=self.timezone.key,
                surface=surface,
                image_path=image_path,
            )
            image_path.with_suffix(".json").write_text(
                frame.model_dump_json(indent=2), encoding="utf-8"
            )
            return frame
        finally:
            image.close()

    def _capture_image(self, surface: Surface) -> Image.Image:
        """Airtest 抓取虚拟桌面后裁出客户区，只将客户区写入磁盘。"""
        # 使用未绑定窗口的设备，避免 Airtest 的窗口裁切混用多屏坐标。
        device = create_device()
        try:
            monitor = device.screen.monitors[0]
            pixels = device.snapshot()
            if pixels is None or pixels.ndim != 3 or pixels.shape[2] != 3:
                raise RuntimeError("Airtest 未返回有效的彩色截图")
            if pixels.shape[:2] != (monitor["height"], monitor["width"]):
                raise RuntimeError("Airtest 截图与虚拟桌面尺寸不一致，请检查 DPI")
            bounds = surface.bounds
            x = bounds.x - monitor["left"]
            y = bounds.y - monitor["top"]
            if (
                x < 0
                or y < 0
                or x + bounds.width > pixels.shape[1]
                or y + bounds.height > pixels.shape[0]
            ):
                raise RuntimeError("微信客户区超出 Airtest 截图范围")
            client = pixels[y : y + bounds.height, x : x + bounds.width]
            # Airtest 的 ndarray 为 BGR；Pillow 保存时需要 RGB。
            return Image.fromarray(client[:, :, ::-1].copy())
        finally:
            device.screen.close()
