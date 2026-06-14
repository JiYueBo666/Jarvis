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
    registry = ToolRegistry([EchoTool()])
    assert registry.get("echo") is not None
    assert len(registry.list()) == 1


@pytest.mark.asyncio
async def test_tool_executor():
    registry = ToolRegistry([EchoTool()])
    executor = ToolExecutor(registry)
    result = await executor.execute("echo", {"msg": "hello"})
    assert result == "hello"


@pytest.mark.asyncio
async def test_executor_unknown_tool():
    executor = ToolExecutor(ToolRegistry())
    result = await executor.execute("nonexistent", {})
    assert "Unknown tool" in result


def test_to_openai_schema():
    tool = EchoTool()
    schema = tool.to_openai_schema()
    assert schema == {
        "type": "function",
        "function": {
            "name": "echo",
            "description": "Echoes input back",
            "parameters": {"type": "object", "properties": {"msg": {"type": "string"}}},
        },
    }


def test_collect_returns_registered_tools():
    """collect() includes tools defined via __init_subclass__."""
    tools = {t.name for t in Tool.collect()}
    assert "echo" in tools  # EchoTool defined above


def test_readonly_default():
    """Tools default to readonly=True, dangerous=False."""
    tool = EchoTool()
    assert tool.readonly is True
    assert tool.dangerous is False
