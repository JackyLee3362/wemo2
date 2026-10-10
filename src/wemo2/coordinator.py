"""协调器：采集 → 去重 → 原子保存。"""

from dataclasses import dataclass
from typing import Protocol

from loguru import logger

from wemo2.collector import Collector, Observation
from wemo2.deduplicator import Decision, DedupIndex, Deduplicator


class Store(DedupIndex, Protocol):
    def transaction(self): ...
    def save(self, observation: Observation, decision: Decision) -> str: ...


@dataclass
class RunResult:
    collected: int = 0
    created: int = 0
    merged: int = 0
    unchanged: int = 0


class Coordinator:
    def __init__(self, collector: Collector, deduplicator: Deduplicator, store: Store):
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
            setattr(result, action, getattr(result, action) + 1)
        logger.info(
            "采集完成：读取 {}，新增 {}，更新 {}，未变更 {}",
            result.collected,
            result.created,
            result.merged,
            result.unchanged,
        )
        return result
