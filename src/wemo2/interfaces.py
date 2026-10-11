"""采集和保存的抽象接口；本地归档是两阶段之间的交付契约。"""

from abc import ABC, abstractmethod
from pathlib import Path

from wemo2.models import ArchivedPost, CollectResult


class BaseCollector(ABC):
    """采集数据源并生成已去重的本地归档，不负责向远端存储上传。"""

    @abstractmethod
    def collect(self) -> CollectResult:
        """执行采集并归档，返回本次交付的文件和数量。"""

    def artifacts(self) -> list[Path]:
        """取得已经落盘的证据；失败时也可用于排查。"""
        return []

    def stop(self) -> None:
        """释放本次采集会话，不关闭用户的数据源应用。"""
        return None


class BaseStore(ABC):
    """接收已校验的归档及资源文件，不再次参与朋友圈身份去重。"""

    @abstractmethod
    def save(self, post: ArchivedPost) -> None:
        """保存一条元信息、截图及媒体，失败必须向调用者报告。"""

    def close(self) -> None:
        """释放存储资源，简单的内存实现无需额外处理。"""
        return None
