"""协调器：采集 → 去重 → 原子保存。"""

from loguru import logger

from wemo2.interfaces import BaseCollector, BaseDeduplicator, BaseStore
from wemo2.models import RunResult


class Coordinator:
    def __init__(
        self, collector: BaseCollector, deduplicator: BaseDeduplicator, store: BaseStore
    ):
        self.collector = collector
        self.deduplicator = deduplicator
        self.store = store

    def run(self) -> RunResult:
        result = RunResult()
        for observation in self.collector.collect():
            with self.store.transaction():
                decision = self.deduplicator.resolve(observation, self.store)
                action = self.store.save(observation, decision)
            result.collected += 1
            if action == "created":
                result.created += 1
            elif action == "merged":
                result.merged += 1
            elif action == "unchanged":
                result.unchanged += 1
            else:
                raise ValueError(f"未知存储动作：{action}")
        logger.info(
            "采集完成：读取 {}，新增 {}，更新 {}，未变更 {}",
            result.collected,
            result.created,
            result.merged,
            result.unchanged,
        )
        return result
