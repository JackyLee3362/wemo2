"""组装组件，选择逻辑与协调器执行逻辑分离。"""

from pathlib import Path

from wemo2.collector import JsonCollector
from wemo2.coordinator import Coordinator
from wemo2.deduplicator import Deduplicator
from wemo2.interfaces import BaseCollector, BaseStore
from wemo2.pseudo import PseudoCollector, PseudoDeduplicator, PseudoStore
from wemo2.store import SQLiteStore


def build_coordinator(
    collector: str = "pseudo",
    deduplicator: str = "pseudo",
    store: str = "pseudo",
    *,
    json_path: str | Path | None = None,
    database: str | Path = "data/wemo2.sqlite3",
) -> Coordinator:
    source: BaseCollector
    if collector == "pseudo":
        source = PseudoCollector()
    elif collector == "json":
        if json_path is None:
            raise ValueError("JSON 采集器需要文件路径")
        source = JsonCollector(json_path)
    else:
        raise ValueError(f"未知采集器：{collector}")

    if deduplicator == "pseudo":
        strategy = PseudoDeduplicator()
    elif deduplicator == "stable":
        strategy = Deduplicator()
    else:
        raise ValueError(f"未知去重器：{deduplicator}")

    target: BaseStore
    if store == "pseudo":
        target = PseudoStore()
    elif store == "sqlite":
        target = SQLiteStore(database)
    else:
        raise ValueError(f"未知存储器：{store}")
    return Coordinator(source, strategy, target)
