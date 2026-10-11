"""采集与保存的应用流程，只依赖通用契约，不展示终端或执行 UI 操作。"""

from loguru import logger

from wemo2.api.models import CollectResult, SaveResult
from wemo2.archives.reader import ArchiveReader
from wemo2.core.config import AppConfig
from wemo2.core.registry import MEMOS_STORE, collectors, stores


def run_collect(config: AppConfig) -> CollectResult:
    """只实例化采集器，由采集器完成本地归档和去重，不创建存储器。"""
    collector = collectors.create(config.collector, config.model_dump())
    logger.info("[采集] 已创建采集器：{}", config.collector)
    try:
        return collector.collect()
    finally:
        collector.stop()


def run_save(config: AppConfig, use_memos: bool = False) -> SaveResult:
    """读取配置账号的归档，交给存储器；不实例化或操作微信采集器。"""
    name = MEMOS_STORE if use_memos else config.store
    store = stores.create(name, config.model_dump())
    logger.info("[存储] 已创建存储器：{}", name)
    result = SaveResult(store=name)
    try:
        reader = ArchiveReader(config.resource_dir, config.account)
        for post in reader.read():
            logger.info("[存储] 开始提交归档：{}", post.metadata_path)
            store.save(post)
            result.posts += 1
            result.media += len(post.media_files)
            logger.info(
                "[存储] 本条提交完成，累计 {} 条朋友圈，{} 个媒体",
                result.posts,
                result.media,
            )
        return result
    finally:
        store.close()
