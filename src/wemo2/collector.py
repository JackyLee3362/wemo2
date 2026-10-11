"""JSON 导入采集器：从文件加载历史或模拟采集记录。"""

from pathlib import Path

from pydantic import TypeAdapter

from wemo2.interfaces import BaseCollector
from wemo2.models import Observation, Platform

# 保留原模块的接口导入位置。
Collector = BaseCollector


class JsonCollector(BaseCollector):
    """从 JSON 文件校验并加载 Observation 列表，用于离线导入和流程测试。

    不连接微信，也不负责去重或保存动态。
    """

    def __init__(self, path: str | Path, platform: Platform = Platform.windows):
        self.path = Path(path)
        self.platform = Platform(platform)  # 导入场景仅记录目标平台，不访问设备。

    def collect(self) -> list[Observation]:
        # 完整校验后再返回，避免无效记录导致部分导入。
        return TypeAdapter(list[Observation]).validate_json(
            self.path.read_text(encoding="utf-8-sig")
        )
