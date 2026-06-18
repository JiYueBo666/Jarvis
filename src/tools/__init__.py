import importlib
import inspect
import pkgutil

from src.engine.tool import Tool


def discover_tools(workspace_root: str | None = None) -> list[Tool]:
    """自动发现 src.tools 包下所有 Tool 子类并实例化。"""
    import src.tools as tools_pkg

    tools: list[Tool] = []
    for _, module_name, _ in pkgutil.iter_modules(tools_pkg.__path__):
        full_name = f"src.tools.{module_name}"
        try:
            module = importlib.import_module(full_name)
        except Exception:
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is Tool:
                continue
            if not issubclass(obj, Tool):
                continue
            # 跳过中间抽象基类（如果某个子类本身还是 ABC）
            if inspect.isabstract(obj):
                continue
            try:
                tool = obj(workspace_root=workspace_root)
                tools.append(tool)
            except Exception:
                continue

    return tools


def build_registry(workspace_root: str | None = None) -> dict[str, Tool]:
    tools = discover_tools(workspace_root=workspace_root)
    return {t.name: t for t in tools}
