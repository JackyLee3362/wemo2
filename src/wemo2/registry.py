"""组件名称、实现类及构造参数的统一注册入口。"""

import inspect
from typing import Any

from pydantic import BaseModel, Field

from wemo2.collector import JsonCollector
from wemo2.deduplicator import Deduplicator
from wemo2.interfaces import BaseCollector, BaseDeduplicator, BaseStore
from wemo2.pseudo import PseudoCollector, PseudoDeduplicator, PseudoStore
from wemo2.store import SQLiteStore
from wemo2_collector.collector import WindowsScreenshotCollector


class ComponentSpec(BaseModel):
    """注册信息；parameters 将构造参数名映射到配置字段名。"""

    implementation: type
    parameters: dict[str, str] = Field(default_factory=dict)
    optional_parameters: dict[str, str] = Field(default_factory=dict)
    defaults: dict[str, str] = Field(default_factory=dict)
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
        parameters: dict[str, str] | None = None,
        optional_parameters: dict[str, str] | None = None,
        defaults: dict[str, str] | None = None,
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
            parameters=parameters or {},
            optional_parameters=optional_parameters or {},
            defaults=defaults or {},
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

    def validate(self, name: str, settings: dict) -> None:
        spec = self.get(name)
        for field in spec.parameters.values():
            if settings.get(field) is None:
                raise ValueError(f"{self.label} {name} 需要配置字段 {field}")

    def create(self, name: str, settings: dict) -> Any:
        """只传入注册时声明的构造参数，不把全部配置交给实现类。"""
        self.validate(name, settings)
        spec = self.get(name)
        arguments = {}
        for parameter, field in spec.parameters.items():
            arguments[parameter] = settings[field]
        for parameter, field in spec.optional_parameters.items():
            if field in settings:
                arguments[parameter] = settings[field]
        return spec.implementation(**arguments)


collectors = ComponentRegistry("采集器", BaseCollector)
deduplicators = ComponentRegistry("去重器", BaseDeduplicator)
stores = ComponentRegistry("存储器", BaseStore)

# 新增内置实现只需在这里导入并注册，不修改 CLI、配置模型或协调器。
IMPORT_COLLECTOR = "json"
collectors.register(
    "pseudo", PseudoCollector, default=True, parameters={"platform": "platform"}
)
collectors.register(
    IMPORT_COLLECTOR,
    JsonCollector,
    parameters={"path": "json_path", "platform": "platform"},
    defaults={"deduplicator": "stable", "store": "sqlite"},
)
deduplicators.register("pseudo", PseudoDeduplicator, default=True)
collectors.register(
    "windows_screenshot",
    WindowsScreenshotCollector,
    parameters={
        "platform": "platform",
        "screenshot_dir": "screenshot_dir",
        "window_title": "window_title",
    },
    optional_parameters={"window_process_id": "window_process_id", "route": "route"},
)
deduplicators.register("stable", Deduplicator)
stores.register(
    "pseudo",
    PseudoStore,
    default=True,
    notice="模拟存储：结果仅保留在内存中。",
)
stores.register("sqlite", SQLiteStore, parameters={"path": "database"})
