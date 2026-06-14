"""Tool system: base class, registry, executor."""

from __future__ import annotations

from typing import ClassVar


class Tool:
    name: str = ""
    description: str = ""
    parameters: dict = {}
    readonly: bool = True
    dangerous: bool = False

    _registry: ClassVar[dict[str, type["Tool"]]] = {}

    def __init_subclass__(cls, **kwargs):
        """Auto-register subclasses that have a non-empty name."""
        super().__init_subclass__(**kwargs)
        if cls.name:
            Tool._registry[cls.name] = cls

    @classmethod
    def collect(cls) -> list[Tool]:
        """Scan all registered tools and return an instance of each."""
        return [tool_cls() for tool_cls in cls._registry.values()]

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, args: dict) -> str:
        raise NotImplementedError


class ToolRegistry:
    """Lookup layer: register, get, list tools."""

    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        if tools:
            for t in tools:
                self.register(t)

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def get_openai_tools(self) -> list[dict]:
        return [t.to_openai_schema() for t in self._tools.values()]


class ToolExecutor:
    """Execution layer: execute tools by name via registry."""

    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    async def execute(self, name: str, args: dict) -> str:
        tool = self._registry.get(name)
        if tool is None:
            return f"Unknown tool: {name}"
        try:
            return await tool.execute(args)
        except Exception as e:
            return f"Tool {name} error: {e}"
