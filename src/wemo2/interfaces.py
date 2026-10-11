"""组件抽象接口；协调器只依赖这里的契约。"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from contextlib import AbstractContextManager
from pathlib import Path

from wemo2.models import Action, Decision, Observation, Post, SavedObservation


class BaseCollector(ABC):
    """采集器契约：输出统一的 Observation，并提供停止和证据交付接口。"""

    def artifacts(self) -> list[Path]:
        """本次采集保存的证据文件；不规定文件格式或采集技术。"""
        return []

    @abstractmethod
    def collect(self) -> Iterable[Observation]:
        """读取数据源，产生统一的采集记录。"""

    def stop(self) -> None:
        """停止采集并释放资源；后台采集实现应停止工作线程，简单采集器可不覆盖。"""
        return None


class BaseStore(ABC):
    """存储器契约：提供事务、身份查询和动态保存，管理存储资源。"""

    @abstractmethod
    def transaction(self) -> AbstractContextManager[None]:
        """包裹匹配与保存，失败时回滚当前记录的写入。"""

    @abstractmethod
    def get_observation(
        self, scope: str, observation_id: str
    ) -> SavedObservation | None:
        """按账号和采集记录 ID 查询幂等索引。"""

    @abstractmethod
    def find_post(self, scope: str, identity_key: str) -> Post | None:
        """按账号和动态身份查询已有动态。"""

    @abstractmethod
    def save(self, observation: Observation, decision: Decision) -> Action:
        """按决定保存，返回实际发生的动作；在 transaction 内调用。"""

    @abstractmethod
    def list_posts(self) -> list[Post]:
        """读取当前动态，返回与内部存储隔离的模型对象。"""

    @abstractmethod
    def close(self) -> None:
        """释放存储资源。"""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class BaseDeduplicator(ABC):
    """去重器契约：根据采集记录和已有索引返回决定，不修改存储。"""

    @abstractmethod
    def resolve(self, observation: Observation, store: BaseStore) -> Decision:
        """只查询存储并返回去重决定，不执行写入。"""
