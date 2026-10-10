"""根据注册名称组装组件；不维护具体实现的分支。"""

from pathlib import Path

from wemo2.coordinator import Coordinator
from wemo2.models import StopPolicy
from wemo2.registry import collectors, deduplicators, stores


def build_coordinator(
    collector: str | None = None,
    deduplicator: str | None = None,
    store: str | None = None,
    *,
    collector_options: dict | None = None,
    config_directory: str | Path | None = None,
    database: str | Path = "data/wemo2.sqlite3",
    stop_policy: StopPolicy | None = None,
) -> Coordinator:
    if collector is None:
        collector = collectors.default_name()
    if deduplicator is None:
        deduplicator = deduplicators.default_name()
    if store is None:
        store = stores.default_name()
    settings = {
        "collector_options": collector_options or {},
        "config_directory": Path(config_directory or Path.cwd()),
        "database": database,
    }
    # 在创建任何实例前校验组合，避免无效配置提前打开存储资源。
    collectors.validate(collector, settings)
    deduplicators.validate(deduplicator, settings)
    stores.validate(store, settings)
    source = collectors.create(collector, settings)
    strategy = deduplicators.create(deduplicator, settings)
    target = stores.create(store, settings)
    return Coordinator(source, strategy, target, stop_policy)
