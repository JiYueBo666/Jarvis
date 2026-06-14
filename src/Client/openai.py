"""OpenAI-compatible LLM client adapter."""

from __future__ import annotations

import asyncio
import json

from openai import AsyncOpenAI
from src.Client.base import LLMClient, StreamEvent
from src.Agent.models import Message, ToolCall


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str, base_url: str, model: str, max_retries: int = 3):
        self._client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, max_retries=max_retries
        )
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> Message:
        raw = await self._client.chat.completions.create(
            model=self._model,
            messages=[m.model_dump(exclude_none=True) for m in messages],
            tools=tools,
        )
        choice = raw.choices[0]
        msg = choice.message

        if msg.tool_calls:
            return Message(
                role="assistant",
                content=msg.content or "",
                tool_calls=[
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                    for tc in msg.tool_calls
                ],
            )
        return Message(role="assistant", content=msg.content or "")

    async def chat_stream(
        self,
        messages: list[Message],
        channel: asyncio.Queue[StreamEvent],
        tools: list[dict] | None = None,
    ) -> None:
        """Push StreamEvent events into channel. Ends with StreamEvent("done")."""
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=[m.model_dump(exclude_none=True) for m in messages],
            tools=tools,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            if delta.content:
                await channel.put(StreamEvent("delta", delta.content))

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx is None or tc_delta.function is None:
                        continue
                    await channel.put(
                        StreamEvent(
                            "tool_call",
                            data={
                                "index": idx,
                                "id": tc_delta.id,
                                "name": tc_delta.function.name,
                                "arguments": tc_delta.function.arguments,
                            },
                        )
                    )

        await channel.put(StreamEvent("done"))
