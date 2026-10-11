"""离线模拟采集，产出与实际采集相同的本地归档格式。"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger
from PIL import Image, ImageDraw

from wemo2.api.interfaces import BaseCollector
from wemo2.api.models import CollectResult
from wemo2.api.records import MomentRecord
from wemo2.archives.identity import moment_key
from wemo2.archives.paths import resolve_moment_folder
from wemo2.common.files import write_model_json


class PseudoCollector(BaseCollector):
    """生成固定人物和文案的示例归档，重复运行复用同一文件夹，不访问微信。"""

    def __init__(self, resource_dir: Path, account: str = "default"):
        self.resource_dir = resource_dir
        self.account = account
        self.paths: list[Path] = []

    def collect(self) -> CollectResult:
        """保存示例 JSON 和截图，用于离线验证 collect 到 save 的完整交付。"""
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        key = moment_key("示例人物", "这是一条模拟朋友圈。", "complete")
        folder, _ = resolve_moment_folder(
            self.resource_dir / self.account, "示例人物", now.date(), key
        )
        folder.mkdir(parents=True, exist_ok=True)
        metadata = folder / "post.json"
        screenshot = folder / "screenshot.png"
        if metadata.exists():
            record = MomentRecord.model_validate_json(
                metadata.read_text(encoding="utf-8")
            )
            if record.account != self.account or record.dedup_key != key:
                raise ValueError("已有示例归档的账号或身份不一致")
            record.last_seen_at = now
        else:
            record = MomentRecord(
                post_id=key,
                account=self.account,
                dedup_key=key,
                person="示例人物",
                text="这是一条模拟朋友圈。",
                text_status="complete",
                approximate_date=now.date(),
                first_seen_at=now,
                last_seen_at=now,
                screenshot_complete=True,
                media_requested=False,
            )
        if not screenshot.exists():
            with Image.new("RGB", (360, 180), (35, 70, 60)) as image:
                ImageDraw.Draw(image).text(
                    (20, 70), "wemo2 pseudo collection", fill="white"
                )
                image.save(screenshot)
        write_model_json(metadata, record)
        self.paths = [metadata, screenshot]
        logger.info("模拟采集完成，归档目录：{}", folder)
        return CollectResult(posts=1, artifacts=self.paths)

    def artifacts(self) -> list[Path]:
        """返回模拟采集实际保存的文件，不伪造远端保存回执。"""
        return list(self.paths)
