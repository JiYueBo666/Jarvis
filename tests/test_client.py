import asyncio
import pytest
from src.Client.base import LLMClient, StreamEvent
from src.Agent.models import Message


class StubClient(LLMClient):
    async def chat(self, messages: list[Message]) -> Message:
        return Message(role="assistant", content="stub response")

    async def chat_stream(self, messages, channel, tools=None):
        await channel.put(StreamEvent("delta", "stub "))
        await channel.put(StreamEvent("delta", "response"))
        await channel.put(StreamEvent("done"))

    @property
    def model_name(self) -> str:
        return "stub"


@pytest.mark.asyncio
async def test_stub_client():
    client = StubClient()
    response = await client.chat([Message(role="user", content="hi")])
    assert response.role == "assistant"
    assert response.content == "stub response"


@pytest.mark.asyncio
async def test_stub_stream():
    client = StubClient()
    channel: asyncio.Queue[StreamEvent] = asyncio.Queue()
    await client.chat_stream([Message(role="user", content="hi")], channel)
    events = []
    while True:
        e = await channel.get()
        events.append(e.type)
        if e.type == "done":
            break
    assert events == ["delta", "delta", "done"]
