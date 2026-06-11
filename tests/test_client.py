import pytest
from src.Client.base import LLMClient
from src.Agent.models import Message


class StubClient(LLMClient):
    async def chat(self, messages: list[Message]) -> Message:
        return Message(role="assistant", content="stub response")

    @property
    def model_name(self) -> str:
        return "stub"


@pytest.mark.asyncio
async def test_stub_client():
    client = StubClient()
    response = await client.chat([Message(role="user", content="hi")])
    assert response.role == "assistant"
    assert response.content == "stub response"
