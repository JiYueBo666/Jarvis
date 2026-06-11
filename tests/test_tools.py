import pytest
from src.Tools.base import Tool, ToolRegistry, ToolExecutor


class EchoTool(Tool):
    name = "echo"
    description = "Echoes input back"
    parameters = {"type": "object", "properties": {"msg": {"type": "string"}}}

    async def execute(self, args: dict) -> str:
        return args.get("msg", "")


@pytest.mark.asyncio
async def test_tool_registry():
    registry = ToolRegistry()
    registry.register(EchoTool())
    assert registry.get("echo") is not None
    assert len(registry.list()) == 1


@pytest.mark.asyncio
async def test_tool_executor():
    registry = ToolRegistry()
    registry.register(EchoTool())
    executor = ToolExecutor(registry)
    result = await executor.execute("echo", {"msg": "hello"})
    assert result == "hello"


@pytest.mark.asyncio
async def test_executor_unknown_tool():
    executor = ToolExecutor(ToolRegistry())
    result = await executor.execute("nonexistent", {})
    assert "Unknown tool" in result
