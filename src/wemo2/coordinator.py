"""协调器：采集 → 去重 → 原子保存。"""

from collections.abc import Generator

from loguru import logger

from wemo2.interfaces import BaseCollector, BaseDeduplicator, BaseStore
from wemo2.models import RunResult, StopPolicy


class Coordinator:
    def __init__(
        self,
        collector: BaseCollector,
        deduplicator: BaseDeduplicator,
        store: BaseStore,
        stop_policy: StopPolicy | None = None,
    ):
        self.collector = collector
        self.deduplicator = deduplicator
        self.store = store
        self.stop_policy = stop_policy or StopPolicy()

    def run(self) -> RunResult:
        result = RunResult()
        records = None
        try:
            records = iter(self.collector.collect())
            for observation in records:
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
                # 当前记录成功提交后才决定停止，匹配到的动态修订也不会丢失。
                if decision.is_duplicate:
                    result.duplicates += 1
                    if self.stop_policy.should_stop(result.duplicates):
                        result.stop_reason = "duplicate_limit"
                        break
        finally:
            try:
                if isinstance(records, Generator):
                    records.close()
            finally:
                self.collector.stop()
        logger.info(
            "采集完成：读取 {}，新增 {}，更新 {}，未变更 {}",
            result.collected,
            result.created,
            result.merged,
            result.unchanged,
        )
        logger.info("重复 {} 次，结束原因：{}", result.duplicates, result.stop_reason)
        return result
