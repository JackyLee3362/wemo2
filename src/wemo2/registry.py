"""组件名称、实现类及构造参数的统一注册入口。"""

import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from wemo2.collectors.collector import WindowsScreenshotCollector
from wemo2.collectors.config import load_windows_options
from wemo2.interfaces import BaseCollector, BaseStore
from wemo2.pseudo import PseudoCollector, PseudoStore


class ComponentSpec(BaseModel):
    """注册信息；parameters 将构造参数名映射到配置字段名。"""

    prepare_options: Callable[[dict, Path], dict] | None = None
    implementation: type
    parameters: dict[str, str] = Field(default_factory=dict)
    optional_parameters: dict[str, str] = Field(default_factory=dict)
    notice: str | None = None


class ComponentRegistry:
    """按名称查找实现，并检查实现是否遵守对应的抽象接口。"""

    def __init__(self, label: str, base_class: type):
        self.label = label
        self.base_class = base_class
        self._components: dict[str, ComponentSpec] = {}
        self._default_name: str | None = None

    def register(
        self,
        name: str,
        implementation: type,
        *,
        prepare_options: Callable[[dict, Path], dict] | None = None,
        parameters: dict[str, str] | None = None,
        optional_parameters: dict[str, str] | None = None,
        default: bool = False,
        notice: str | None = None,
    ) -> None:
        """注册实现；同类组件名称不允许重复，不静默覆盖。"""
        if not name or name.strip() != name:
            raise ValueError("组件名称不能为空或包含首尾空白")
        if name in self._components:
            raise ValueError(f"{self.label}已经注册：{name}")
        if not isinstance(implementation, type) or not issubclass(
            implementation, self.base_class
        ):
            raise TypeError(f"{self.label}必须继承 {self.base_class.__name__}")
        if inspect.isabstract(implementation):
            raise TypeError(f"{self.label}必须实现全部抽象方法")
        if default and self._default_name is not None:
            raise ValueError(f"{self.label}已经设置默认实现")
        spec = ComponentSpec(
            implementation=implementation,
            prepare_options=prepare_options,
            parameters=parameters or {},
            optional_parameters=optional_parameters or {},
            notice=notice,
        )
        self._components[name] = spec
        if default:
            self._default_name = name

    def names(self) -> list[str]:
        return list(self._components)

    def default_name(self) -> str:
        if self._default_name is None:
            raise ValueError(f"{self.label}尚未设置默认实现")
        return self._default_name

    def get(self, name: str) -> ComponentSpec:
        if name not in self._components:
            choices = "、".join(self.names())
            raise ValueError(f"未知{self.label}：{name}；可选：{choices}")
        return self._components[name]

    def _arguments(self, name: str, settings: dict) -> dict:
        """按注册信息解释参数；专属选项只交给实现方加载函数。"""
        spec = self.get(name)
        arguments = {}
        for parameter, field in spec.parameters.items():
            if settings.get(field) is None:
                raise ValueError(f"{self.label} {name} 需要配置字段 {field}")
            arguments[parameter] = settings[field]
        for parameter, field in spec.optional_parameters.items():
            if field in settings:
                arguments[parameter] = settings[field]
        if spec.prepare_options is not None:
            arguments.update(
                spec.prepare_options(
                    settings.get("collector_options", {}),
                    Path(settings.get("config_directory", Path.cwd())),
                )
            )
        return arguments

    def validate(self, name: str, settings: dict) -> None:
        self._arguments(name, settings)

    def create(self, name: str, settings: dict) -> Any:
        """只传入声明或加载函数返回的参数，不把应用全部配置交给实现。"""
        arguments = self._arguments(name, settings)
        return self.get(name).implementation(**arguments)


collectors = ComponentRegistry("采集器", BaseCollector)
stores = ComponentRegistry("存储器", BaseStore)
MEMOS_STORE = "memos"

# 去重只存在于采集器归档阶段；Memos 暂以模拟存储占位，不发起网络请求。
collectors.register(
    "windows_screenshot",
    WindowsScreenshotCollector,
    default=True,
    prepare_options=load_windows_options,
    parameters={"resource_dir": "resource_dir", "account": "account"},
)
collectors.register(
    "pseudo",
    PseudoCollector,
    parameters={"resource_dir": "resource_dir", "account": "account"},
)
stores.register(
    "pseudo",
    PseudoStore,
    default=True,
    notice="模拟保存：仅在内存中处理归档，未写入远端。",
)
stores.register(
    MEMOS_STORE,
    PseudoStore,
    notice="Memos 0.31 尚未接入：本次使用模拟存储，未上传任何数据。",
)
