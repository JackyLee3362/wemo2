"""读取采集留下的文件夹，不导入任何界面驱动或截图模型。"""

import hashlib
from collections.abc import Iterator
from pathlib import Path

from loguru import logger

from wemo2.archive_paths import archive_metadata_paths
from wemo2.models import ArchivedPost
from wemo2.names import person_directory
from wemo2.records import MomentRecord


class ArchiveReader:
    """读取指定账号的 post.json，防止越界路径、缺失文件或损坏媒体被当成成功。"""

    def __init__(self, resource_dir: Path, account: str):
        self.directory = resource_dir / account
        self.account = account

    def read(self) -> Iterator[ArchivedPost]:
        """按日期、文件夹名称确定读取顺序；一条错误归档会停止本次保存。"""
        paths = archive_metadata_paths(self.directory)
        if not paths:
            raise FileNotFoundError(
                f"未找到朋友圈归档：{self.directory}，请先执行 collect"
            )
        for path in paths:
            try:
                yield self._read_post(path)
            except Exception as error:
                logger.exception("读取朋友圈归档失败，停止保存：{}，{}", path, error)
                raise

    def _read_post(self, path: Path) -> ArchivedPost:
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError("归档元信息路径超出账号目录")
        record = MomentRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if record.account != self.account or record.post_id != path.parent.name:
            raise ValueError("归档的账号或朋友圈 ID 与文件夹不一致")
        if len(
            path.relative_to(self.directory).parts
        ) == 4 and path.parent.parent.name != person_directory(record.person):
            raise ValueError("归档人物目录与元信息人名不一致")
        screenshot = self._file(path.parent, record.primary_screenshot)
        media_files = []
        for media in record.media:
            file_path = self._file(path.parent, media.path)
            if file_path.stat().st_size != media.size:
                raise ValueError(f"媒体大小不一致：{file_path}")
            with file_path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != media.sha256:
                raise ValueError(f"媒体哈希不一致：{file_path}")
            media_files.append(file_path)
        evidence_files = []
        for evidence in record.evidence:
            file_path = self._file(path.parent, evidence.screenshot)
            if file_path not in evidence_files:
                evidence_files.append(file_path)
        return ArchivedPost(
            metadata_path=path,
            record=record,
            screenshot=screenshot,
            media_files=media_files,
            evidence_files=evidence_files,
        )

    def _file(self, directory: Path, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or path.drive or not relative:
            raise ValueError("归档资源路径必须是非空相对路径")
        target = (directory / path).resolve()
        if not target.is_relative_to(directory.resolve()):
            raise ValueError("归档资源路径超出朋友圈文件夹")
        if not target.is_file() or target.stat().st_size == 0:
            raise FileNotFoundError(f"归档资源缺失或为空：{target}")
        return target
