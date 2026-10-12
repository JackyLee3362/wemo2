"""将拼接后的朋友圈与跨屏下载媒体汇总为账号、日期、朋友圈文件夹。"""

import hashlib
import shutil
from pathlib import Path

from loguru import logger
from PIL import Image

from wemo2.api.records import (
    ArchiveMedia,
    MomentEvidence,
    MomentRecord,
)
from wemo2.archives.identity import moment_key
from wemo2.archives.paths import resolve_moment_folder
from wemo2.collectors.media import MediaCapture, SavedMedia
from wemo2.collectors.models import (
    FeedCaptureResult,
    FeedSegment,
    MediaDownload,
    MomentDetails,
    Screenshot,
)
from wemo2.common.files import sha256_file, write_model_json
from wemo2.common.names import normalize_person_with_raw
from wemo2.common.paths import resolve_resource_path


class MomentArchive:
    """同账号按人物和文案合并资源，保留所有采集证据，不覆盖或删除既有媒体。"""

    def __init__(self, resource_dir: Path, account: str):
        self.account_dir = resource_dir / account
        self.account = account
        self.paths: list[Path] = []
        self.exported: set[Path] = set()
        self.downloads: list[MediaDownload] = []

    def export(self, result: FeedCaptureResult, media: MediaCapture) -> None:
        """关联同一列表流中的截图与媒体；失败也保留已完成的截图及已验证文件。"""
        frames = {}
        for frame in result.frames:
            frames[frame.frame_id] = frame
        for segment in result.segments:
            if segment.image_path in self.exported:
                continue
            if segment.partial_end:
                logger.info(
                    "[归档跳过] 底部未闭合，保留拼接证据：{}", segment.image_path
                )
                self.exported.add(segment.image_path)
                continue
            try:
                source = frames[segment.source_frames[0]]
                with Image.open(segment.image_path) as image:
                    if media.config.recognize_nickname:
                        details = media.reader.post_details(
                            image, source.captured_at, source.surface.scale
                        )
                    else:
                        details = MomentDetails()
                    if details.header_top is None:
                        logger.info(
                            "[归档跳过] 尚未同时确认头像和更多按钮，保留拼接证据：{}",
                            segment.image_path,
                        )
                        self.exported.add(segment.image_path)
                        continue
                    top = details.header_top or 0
                    with image.crop((0, top, image.width, image.height)) as cropped:
                        self._save(
                            segment, source, details, cropped, media, result.error
                        )
                self.exported.add(segment.image_path)
            except Exception as error:
                logger.exception(
                    "朋友圈资源归档失败，保留原始采集文件并停止归档：{}，{}",
                    segment.image_path,
                    error,
                )
                raise

    def _folder(self, key: str | None, details: MomentDetails) -> Path:
        folder, previous = resolve_moment_folder(
            self.account_dir, details.person, details.approximate_date, key
        )
        if previous is not None:
            for index, path in enumerate(self.paths):
                if path.is_relative_to(previous):
                    self.paths[index] = folder / path.relative_to(previous)
        return folder

    def _save(
        self,
        segment: FeedSegment,
        source: Screenshot,
        details: MomentDetails,
        image: Image.Image,
        media: MediaCapture,
        error: str | None,
    ) -> None:
        person, raw = normalize_person_with_raw(details.person, details.person_raw)
        details.person = person
        details.person_raw = raw
        key = moment_key(details.person, details.text, details.text_status)
        folder = self._folder(key, details)
        folder.mkdir(parents=True, exist_ok=True)
        logger.info(
            "[归档] 开始，人物={}，日期={}，目录={}",
            details.person,
            details.approximate_date,
            folder.resolve(),
        )
        metadata = folder / "post.json"
        complete = details.header_top is not None and not segment.partial_end
        if metadata.is_file():
            logger.info("[归档去重] 复用已有朋友圈，合并截图证据和媒体：{}", metadata)
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
                person_raw=details.person_raw,
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
        if details.is_video_channel:
            record.media_skip_reason = "video_channel"
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
            write_model_json(metadata, record)
            self.paths.extend([metadata, primary, screenshot])
            logger.info(
                "[归档] 已写入元信息，媒体={}，错误={}，截图完整={}，路径={}",
                len(record.media),
                len(record.errors),
                record.screenshot_complete,
                metadata,
            )
        if complete and key is not None and details.approximate_date is not None:
            self.downloads.append(
                MediaDownload(
                    stream_start=segment.stream_start + (details.header_top or 0),
                    stream_end=segment.stream_end,
                    directory=folder / "media",
                    skip_reason=record.media_skip_reason,
                )
            )

    def register_download(self, saved: SavedMedia, stream_y: int) -> None:
        """实际文件已在最终目录落盘后立即登记 JSON，失败中断也保留资源记录。"""
        for item in self.downloads:
            if item.stream_start <= stream_y < item.stream_end:
                metadata = item.directory.parent / "post.json"
                record = MomentRecord.model_validate_json(
                    metadata.read_text(encoding="utf-8")
                )
                self._copy_media(metadata.parent, record, saved)
                record.media_requested = True
                write_model_json(metadata, record)
                return
        raise RuntimeError("已下载文件没有对应的完整朋友圈归档")

    def register_skip(self, stream_y: int, reason: str) -> None:
        """误打开视频号后更新共享下载计划和 JSON，后续视口不再重复打开。"""
        for item in self.downloads:
            if item.stream_start <= stream_y < item.stream_end:
                item.skip_reason = reason
                metadata = item.directory.parent / "post.json"
                record = MomentRecord.model_validate_json(
                    metadata.read_text(encoding="utf-8")
                )
                record.media_skip_reason = reason
                write_model_json(metadata, record)
                return
        raise RuntimeError("跳过的媒体没有对应朋友圈归档")

    def _copy_media(
        self, folder: Path, record: MomentRecord, saved: SavedMedia
    ) -> None:
        digest = sha256_file(saved.path)
        if digest != saved.sha256 or saved.path.stat().st_size != saved.size:
            raise RuntimeError("已下载媒体与采集哈希或大小不一致，未标记归档成功")
        for existing in record.media:
            if existing.sha256 == digest:
                target = self._resource_path(folder, existing.path)
                if not target.is_file():
                    raise RuntimeError("元信息引用的已归档媒体缺失，未跳过去重")
                if sha256_file(target) != digest:
                    raise RuntimeError("已归档媒体损坏，未跳过去重或覆盖原文件")
                return
        if saved.path.resolve().parent == (folder / "media").resolve():
            destination = saved.path.resolve()
            folder = folder.resolve()
        else:
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
        logger.info("[归档媒体] 已校验并登记 {}：{}", saved.kind, destination)

    def _resource_path(self, folder: Path, relative: str) -> Path:
        return resolve_resource_path(folder, relative)
