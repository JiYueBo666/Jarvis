import pytest
from src.Tools.base import Tool, ToolParameter, ToolRegistry, ToolExecutor


class EchoTool(Tool):
    name = "echo"
    description = "Echoes input back"
    parameters = [
        ToolParameter(name="msg", type="string", description="Message to echo"),
    ]

    async def execute(self, args: dict) -> str:
        return args.get("msg", "")


class CalcTool(Tool):
    """Tool with optional parameters for testing."""
    name = "calc"
    description = "Add numbers"
    parameters = [
        ToolParameter(name="a", type="integer", description="First number"),
        ToolParameter(name="b", type="integer", description="Second number"),
        ToolParameter(name="round", type="boolean", description="Round result", required=False, default=False),
    ]

    async def execute(self, args: dict) -> str:
        return str(args["a"] + args["b"])


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


class TestToolParameter:
    def test_basic(self):
        p = ToolParameter(name="x", type="string", description="A value")
        assert p.name == "x"
        assert p.type == "string"
        assert p.required is True
        assert p.default is None

    def test_optional_with_default(self):
        p = ToolParameter(name="x", type="integer", required=False, default=42)
        assert p.required is False
        assert p.default == 42


class TestToOpenAISchema:
    def test_required_only(self):
        schema = EchoTool().to_openai_schema()
        assert schema == {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echoes input back",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "msg": {"type": "string", "description": "Message to echo"},
                    },
                    "required": ["msg"],
                },
            },
        }

    def test_with_optional_and_default(self):
        schema = CalcTool().to_openai_schema()
        params = schema["function"]["parameters"]
        assert params["required"] == ["a", "b"]  # round is optional
        assert params["properties"]["round"]["default"] is False

    def test_optional_not_in_required(self):
        schema = CalcTool().to_openai_schema()
        assert "round" not in schema["function"]["parameters"]["required"]
        assert "a" in schema["function"]["parameters"]["required"]
        assert "b" in schema["function"]["parameters"]["required"]


def test_collect_returns_registered_tools():
    """collect() includes tools defined via __init_subclass__."""
    tools = {t.name for t in Tool.collect()}
    assert "echo" in tools


def test_readonly_default():
    tool = EchoTool()
    assert tool.readonly is True
    assert tool.dangerous is False
