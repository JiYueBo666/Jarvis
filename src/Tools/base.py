"""Tool system: base class, registry, executor."""

from __future__ import annotations

class Tool:
    name: str = ""
    description: str = ""
    parameters: dict = {}

    def __init__(self):
        # Subclasses set class-level defaults, nothing to init
        pass

    async def execute(self, args: dict) -> str:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def get_openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]


class ToolExecutor:
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
