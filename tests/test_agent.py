import asyncio
import pytest
from src.Agent.models import Message, ToolCall
from src.Agent.loop import AgentLoop
from src.Client.base import LLMClient, StreamEvent
from src.Tools.base import Tool, ToolRegistry, ToolExecutor


class ThinkClient(LLMClient):
    """Simulates an LLM that can think, act, and answer."""
    def __init__(self, steps: list[Message]):
        self.steps = steps
        self.idx = 0

    @property
    def model_name(self) -> str:
        return "test"

    async def chat(self, messages: list[Message], tools=None) -> Message:
        step = self.steps[self.idx % len(self.steps)]
        self.idx += 1
        return step

    async def chat_stream(self, messages, channel, tools=None):
        step = self.steps[self.idx % len(self.steps)]
        self.idx += 1
        if step.content:
            await channel.put(StreamEvent("delta", step.content))
        if step.tool_calls:
            for tc in step.tool_calls:
                await channel.put(StreamEvent("tool_call", tc))
        await channel.put(StreamEvent("done"))


class StubTool(Tool):
    name = "ping"
    description = "ping tool"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict) -> str:
        return "pong"


@pytest.mark.asyncio
async def test_agent_returns_final_answer():
    """Agent returns a final answer when LLM doesn't call tools."""
    client = ThinkClient([
        Message(role="assistant", content="The answer is 42"),
    ])
    loop = AgentLoop(client=client, executor=ToolExecutor(ToolRegistry()), max_steps=5)
    result = await loop.run([Message(role="user", content="what is the answer?")])
    assert result == "The answer is 42"


@pytest.mark.asyncio
async def test_agent_calls_tool():
    """Agent executes a tool and uses the result."""
    client = ThinkClient([
        Message(
            role="assistant", content="Let me check",
            tool_calls=[ToolCall(id="call_1", name="ping", arguments={})],
        ),
        Message(role="assistant", content="pong received"),
    ])
    registry = ToolRegistry()
    registry.register(StubTool())
    loop = AgentLoop(client=client, executor=ToolExecutor(registry), max_steps=5)
    result = await loop.run([Message(role="user", content="ping?")])
    assert result == "pong received"


@pytest.mark.asyncio
async def test_agent_max_steps():
    """Agent stops when max steps reached."""
    client = ThinkClient([
        Message(
            role="assistant", content="still thinking",
            tool_calls=[ToolCall(id="call_1", name="ping", arguments={})],
        ),
    ])
    registry = ToolRegistry()
    registry.register(StubTool())
    loop = AgentLoop(client=client, executor=ToolExecutor(registry), max_steps=2)
    result = await loop.run([Message(role="user", content="do it")])
    assert "max steps" in result.lower()
