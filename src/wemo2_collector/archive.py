"""将拼接后的朋友圈与跨屏下载媒体汇总为账号、日期、朋友圈文件夹。"""

import hashlib
import json
import shutil
import unicodedata
from pathlib import Path
from uuid import uuid4

from loguru import logger
from PIL import Image

from wemo2_collector.media import MediaCapture, SavedMedia
from wemo2_collector.models import FeedCaptureResult, FeedSegment, Screenshot
from wemo2_collector.records import (
    ArchiveMedia,
    MomentDetails,
    MomentEvidence,
    MomentRecord,
)


def moment_key(person: str | None, text: str | None, text_status: str) -> str | None:
    """按人物和完整文案生成身份键；保留空文案语义，不把未知或截断正文参与去重。"""
    if not person or not person.strip() or text is None or text_status != "complete":
        return None
    person = " ".join(unicodedata.normalize("NFC", person).split())
    text = " ".join(unicodedata.normalize("NFC", text).split())
    payload = json.dumps([person, text], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MomentArchive:
    """同账号按人物和文案合并资源，保留所有采集证据，不覆盖或删除既有媒体。"""

    def __init__(self, resource_dir: Path, account: str):
        self.account_dir = resource_dir / account
        self.account = account
        self.paths: list[Path] = []

    def export(self, result: FeedCaptureResult, media: MediaCapture) -> None:
        """关联同一列表流中的截图与媒体；失败也保留已完成的截图及已验证文件。"""
        frames = {}
        for frame in result.frames:
            frames[frame.frame_id] = frame
        for segment in result.segments:
            try:
                source = frames[segment.source_frames[0]]
                with Image.open(segment.image_path) as image:
                    if media.config.recognize_nickname:
                        details = media.reader.post_details(
                            image, source.captured_at, source.surface.scale
                        )
                    else:
                        details = MomentDetails()
                    if segment.partial_end:
                        details.text_status = (
                            "partial" if details.text is not None else "unknown"
                        )
                    top = details.header_top or 0
                    with image.crop((0, top, image.width, image.height)) as cropped:
                        self._save(
                            segment, source, details, cropped, media, result.error
                        )
            except Exception as error:
                logger.exception(
                    "朋友圈资源归档失败，保留原始采集文件并停止归档：{}，{}",
                    segment.image_path,
                    error,
                )
                raise

    def _folder(self, key: str | None, details: MomentDetails) -> Path:
        day = (
            "unknown_date"
            if details.approximate_date is None
            else details.approximate_date.isoformat()
        )
        identifier = key or f"unidentified_{uuid4()}"
        if key is not None:
            matches = list(self.account_dir.glob(f"*/{key}/post.json"))
            if len(matches) > 1:
                raise RuntimeError("同账号存在多个相同去重键的文件夹，请先检查归档目录")
            if matches:
                folder = matches[0].parent
                if folder.parent.name == "unknown_date" and day != "unknown_date":
                    destination = self.account_dir / day / identifier
                    root = self.account_dir.resolve()
                    if not folder.resolve().is_relative_to(
                        root
                    ) or not destination.resolve().is_relative_to(root):
                        raise RuntimeError("归档目录超出账号资源范围，未移动")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    folder.rename(destination)
                    for index, path in enumerate(self.paths):
                        if path.is_relative_to(folder):
                            self.paths[index] = destination / path.relative_to(folder)
                    folder = destination
                return folder
        return self.account_dir / day / identifier

    def _save(
        self,
        segment: FeedSegment,
        source: Screenshot,
        details: MomentDetails,
        image: Image.Image,
        media: MediaCapture,
        error: str | None,
    ) -> None:
        key = moment_key(details.person, details.text, details.text_status)
        folder = self._folder(key, details)
        folder.mkdir(parents=True, exist_ok=True)
        metadata = folder / "post.json"
        complete = details.header_top is not None and not segment.partial_end
        if metadata.is_file():
            record = MomentRecord.model_validate_json(
                metadata.read_text(encoding="utf-8")
            )
            if record.dedup_key != key or record.account != self.account:
                raise RuntimeError("已有元信息与去重键或账号不一致，未覆盖")
        else:
            record = MomentRecord(
                post_id=folder.name,
                account=self.account,
                dedup_key=key,
                person=details.person,
                text=details.text,
                text_status=details.text_status,
                approximate_date=details.approximate_date,
                first_seen_at=source.captured_at,
                last_seen_at=source.captured_at,
                media_requested=media.config.save_media,
            )
        record.last_seen_at = max(record.last_seen_at, source.captured_at)
        record.first_seen_at = min(record.first_seen_at, source.captured_at)
        if details.approximate_date is not None:
            if record.approximate_date is None:
                record.approximate_date = details.approximate_date
            if details.approximate_date not in record.observed_dates:
                record.observed_dates.append(details.approximate_date)
        screenshot_id = hashlib.sha256(
            image.tobytes() + str(image.size).encode()
        ).hexdigest()
        screenshot = folder / "screenshots" / f"{screenshot_id}.png"
        screenshot.parent.mkdir(exist_ok=True)
        if not screenshot.exists():
            image.save(screenshot)
        primary = self._resource_path(folder, record.primary_screenshot)
        if not primary.exists() or complete and not record.screenshot_complete:
            shutil.copy2(screenshot, primary)
            record.screenshot_complete = complete
        evidence = MomentEvidence(
            captured_at=source.captured_at,
            timezone=source.timezone or str(source.captured_at.tzinfo),
            approximate_date=details.approximate_date,
            date_raw=details.date_raw,
            screenshot=screenshot.relative_to(folder).as_posix(),
            source_frames=segment.source_frames,
            screenshot_complete=complete,
        )
        if evidence not in record.evidence:
            record.evidence.append(evidence)
        record.media_requested = record.media_requested or media.config.save_media
        if error is not None and error not in record.errors:
            record.errors.append(error)
            record.media_complete = False
        try:
            for preview in media.previews:
                center = preview.stream_y + preview.bounds.height // 2
                if segment.stream_start <= center < segment.stream_end:
                    self._copy_media(folder, record, preview.file)
                    if not preview.workflow_complete:
                        record.media_complete = False
        except Exception as failure:
            logger.exception(
                "媒体归档失败，记录错误并保留已归档的文件：{}，{}", folder, failure
            )
            record.errors.append(str(failure))
            record.media_complete = False
            raise
        finally:
            temporary = metadata.with_name(f"post_{uuid4()}.tmp")
            temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(metadata)
            self.paths.extend([metadata, primary, screenshot])

    def _copy_media(
        self, folder: Path, record: MomentRecord, saved: SavedMedia
    ) -> None:
        with saved.path.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        if digest != saved.sha256 or saved.path.stat().st_size != saved.size:
            raise RuntimeError("已下载媒体与采集哈希或大小不一致，未标记归档成功")
        for existing in record.media:
            if existing.sha256 == digest:
                target = self._resource_path(folder, existing.path)
                if not target.is_file():
                    raise RuntimeError("元信息引用的已归档媒体缺失，未跳过去重")
                with target.open("rb") as file:
                    if hashlib.file_digest(file, "sha256").hexdigest() != digest:
                        raise RuntimeError("已归档媒体损坏，未跳过去重或覆盖原文件")
                return
        destination = folder / "media" / f"{digest}{saved.path.suffix.lower()}"
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(saved.path, destination)
        record.media.append(
            ArchiveMedia(
                path=destination.relative_to(folder).as_posix(),
                kind=saved.kind,
                sha256=digest,
                size=saved.size,
            )
        )
        self.paths.append(destination)

    def _resource_path(self, folder: Path, relative: str) -> Path:
        target = folder / relative
        if not target.resolve().is_relative_to(folder.resolve()):
            raise RuntimeError("元信息中的资源路径超出朋友圈文件夹，未读取或覆盖")
        return target
