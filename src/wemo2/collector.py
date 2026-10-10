"""JSON 导入采集器：从文件加载历史或模拟采集记录。"""

from pathlib import Path

from pydantic import TypeAdapter

from wemo2.interfaces import BaseCollector
from wemo2.models import Observation

# 保留原模块的接口导入位置。
Collector = BaseCollector


class JsonCollector(BaseCollector):
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def collect(self) -> list[Observation]:
        # 完整校验后再返回，避免无效记录导致部分导入。
        return TypeAdapter(list[Observation]).validate_json(
            self.path.read_text(encoding="utf-8-sig")
        )
