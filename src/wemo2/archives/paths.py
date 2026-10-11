"""归档目录布局及旧布局升级，供实际采集与模拟采集共用。"""

from datetime import date
from pathlib import Path
from uuid import uuid4

from loguru import logger

from wemo2.api.records import MomentRecord
from wemo2.archives.identity import moment_key
from wemo2.common.files import write_model_json
from wemo2.common.names import person_directory
from wemo2.common.paths import require_within


def archive_metadata_paths(account_dir: Path) -> list[Path]:
    """同时读取日期/人物/朋友圈的新布局与日期/朋友圈的旧布局，保持顺序确定。"""
    paths = list(account_dir.glob("*/*/*/post.json"))
    paths.extend(account_dir.glob("*/*/post.json"))
    return sorted(paths)


def resolve_moment_folder(
    account_dir: Path,
    person: str | None,
    approximate_date: date | None,
    key: str | None,
) -> tuple[Path, Path | None]:
    """复用同账号已有归档，必要时升级布局及 emoji 身份键，移动前校验账号范围。"""
    day = "unknown_date" if approximate_date is None else approximate_date.isoformat()
    identifier = key or f"unidentified_{uuid4()}"
    existing = None
    record = None
    if key is not None:
        for metadata in archive_metadata_paths(account_dir):
            require_within(account_dir, metadata)
            require_within(account_dir, metadata.parent)
            candidate = MomentRecord.model_validate_json(
                metadata.read_text(encoding="utf-8")
            )
            candidate_key = moment_key(
                candidate.person, candidate.text, candidate.text_status
            )
            if candidate_key != key:
                continue
            if candidate.account != account_dir.name:
                raise ValueError("已有归档账号与目录不一致，未移动")
            if existing is not None:
                raise RuntimeError(
                    "同账号存在多个清洗后身份相同的朋友圈，请先检查归档目录"
                )
            existing = metadata.parent
            record = candidate
    if existing is not None:
        relative = existing.relative_to(account_dir)
        old_day = relative.parts[0]
        if old_day != "unknown_date":
            day = date.fromisoformat(old_day).isoformat()
    destination = account_dir / day / person_directory(person) / identifier
    require_within(account_dir, destination)
    if existing is not None and existing != destination:
        if destination.exists():
            raise FileExistsError("目标朋友圈目录已经存在，未覆盖或合并旧目录")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing.rename(destination)
            record.post_id = identifier
            record.dedup_key = key
            write_model_json(destination / "post.json", record)
            logger.info(
                "归档目录已升级，保留截图和媒体：{} → {}", existing, destination
            )
        except Exception as error:
            logger.exception(
                "升级归档目录失败，保留现有目录及资源，停止本次采集：{}，{}",
                existing,
                error,
            )
            raise
    previous = existing if existing is not None and existing != destination else None
    return destination, previous
