"""模拟采集与存储：产出真实本地归档，远端保存仅在内存中演示。"""

from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger
from PIL import Image, ImageDraw

from wemo2.archive_paths import resolve_moment_folder
from wemo2.interfaces import BaseCollector, BaseStore
from wemo2.models import ArchivedPost, CollectResult
from wemo2.records import MomentRecord, moment_key


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
        temporary = metadata.with_name(f"post_{uuid4()}.tmp")
        temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(metadata)
        self.paths = [metadata, screenshot]
        logger.info("模拟采集完成，归档目录：{}", folder)
        return CollectResult(posts=1, artifacts=self.paths)

    def artifacts(self) -> list[Path]:
        """返回模拟采集实际保存的文件，不伪造远端保存回执。"""
        return list(self.paths)


class PseudoStore(BaseStore):
    """在内存中接收归档与资源引用，演示未来 Memos 0.31 保存接口，不上传数据。"""

    def __init__(self):
        self.posts: list[ArchivedPost] = []

    def save(self, post: ArchivedPost) -> None:
        """接收一条已校验的朋友圈及其实际文件，不再做人物文案去重。"""
        self.posts.append(post.model_copy(deep=True))
        logger.info(
            "模拟保存朋友圈：账号={}，人物={}，日期={}，媒体={}，源目录={}",
            post.record.account,
            post.record.person,
            post.record.approximate_date,
            len(post.media_files),
            post.metadata_path.parent,
        )
