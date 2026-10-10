"""内置离线采集器的配置加载；界面采集配置由对应插件提供。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from wemo2.models import Platform


class BasicCollectorConfig(BaseModel):
    """模拟采集器只记录数据源平台，不连接设备。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    platform: Platform = Platform.windows


class JsonCollectorConfig(BasicCollectorConfig):
    """离线输入文件及其来源平台。"""

    path: Path


def load_pseudo_options(options: dict, directory: Path) -> dict:
    config = BasicCollectorConfig.model_validate(options)
    return {"platform": config.platform}


def load_json_options(options: dict, directory: Path) -> dict:
    config = JsonCollectorConfig.model_validate(options)
    path = config.path
    if not path.is_absolute():
        path = (directory / path).resolve()
    return {"path": path, "platform": config.platform}
