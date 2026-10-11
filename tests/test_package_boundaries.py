import ast
import unittest
from pathlib import Path


class PackageBoundaryTests(unittest.TestCase):
    """验证功能包的依赖方向，防止共用函数或数据契约重新感知 CLI 和 UI 实现。"""

    def test_lower_layers_do_not_import_application_or_concrete_components(self):
        root = Path(__file__).parents[1] / "src/wemo2"
        forbidden = {
            "api": {"core", "cli", "collectors", "stores", "archives"},
            "common": {"api", "core", "cli", "collectors", "stores", "archives"},
            "archives": {"core", "cli", "collectors", "stores"},
            "stores": {"core", "cli", "collectors", "archives"},
        }
        for package, denied in forbidden.items():
            for path in (root / package).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    modules = []
                    if isinstance(node, ast.ImportFrom) and node.module:
                        modules.append(node.module)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            modules.append(alias.name)
                    for module in modules:
                        for denied_package in denied:
                            self.assertFalse(
                                module == f"wemo2.{denied_package}"
                                or module.startswith(f"wemo2.{denied_package}."),
                                f"{path.relative_to(root)} 导入 {module}",
                            )

    def test_core_business_code_has_no_terminal_framework_dependency(self):
        root = Path(__file__).parents[1] / "src/wemo2/core"
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    self.assertFalse(
                        module.startswith(("rich", "typer", "wemo2.cli")), path.name
                    )
