"""文件哈希与原子 JSON 写入，供采集、归档升级和存储读取共用。"""

import hashlib
from pathlib import Path
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel


def sha256_file(path: Path) -> str:
    """流式计算真实文件字节的 SHA-256，不将整段视频载入内存。"""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_model_json(path: Path, model: BaseModel) -> None:
    """先写同目录临时文件再替换目标，失败保留旧 JSON 并清理本次临时文件。"""
    temporary = path.with_name(f"{path.stem}_{uuid4()}.tmp")
    try:
        temporary.write_text(model.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
    except Exception as error:
        logger.exception(
            "写入 JSON 失败，保留原文件并清理临时文件：{}，{}", path, error
        )
        try:
            temporary.unlink(missing_ok=True)
        except OSError as cleanup_error:
            logger.exception(
                "清理 JSON 临时文件失败，保留原写入异常：{}，{}",
                temporary,
                cleanup_error,
            )
        raise
