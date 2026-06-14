"""Tool system: base class, registry, executor."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel


class ToolParameter(BaseModel):
    """Structured definition of a single tool parameter."""

    name: str
    type: str
    description: str = ""
    required: bool = True
    default: Any = None


class Tool:
    name: str = ""
    description: str = ""
    parameters: list[ToolParameter] = []
    readonly: bool = True
    dangerous: bool = False

    _registry: ClassVar[dict[str, type["Tool"]]] = {}

    """
    构造子类时候自动识别到该函数并调用
    """

    def __init_subclass__(cls, **kwargs):
        """Auto-register subclasses that have a non-empty name."""
        super().__init_subclass__(**kwargs)
        if cls.name:
            Tool._registry[cls.name] = cls

    @classmethod
    def collect(cls) -> list[Tool]:
        """
        返回类
        """
        """Scan all registered tools and return an instance of each."""
        return [tool_cls() for tool_cls in cls._registry.values()]

    def to_openai_schema(self) -> dict:
        properties = {}
        required = []
        for p in self.parameters:
            prop: dict[str, Any] = {"type": p.type}
            if p.description:
                prop["description"] = p.description
            if p.default is not None:
                prop["default"] = p.default
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
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
