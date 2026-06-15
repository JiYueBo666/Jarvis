"""Abstract LLM client interface."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod

from src.Agent.models import Message, ToolCall


class StreamEvent:
    """Event pushed into channel during streaming / emitted to UI.

    Low-level types used by chat_stream:
      "delta"     — content token chunk (data: str)
      "tool_call" — tool call delta fragment (data: dict)
      "done"      — streaming finished (data: None)

    High-level types emitted to UI:
      "message_start"  — a new assistant message began
      "message_update" — an in-progress message changed
      "message_end"    — final message ready
    """

    def __init__(
        self,
        type: str,
        data: object = None,
        message: object = None,
        assistant_message_event: object = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        result: str | None = None,
    ):
        self.type = type
        self.data = data
        self.message = message
        self.assistant_message_event = assistant_message_event
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.result = result


class LLMClient(ABC):
    @abstractmethod
    async def chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> Message: ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[Message],
        channel: asyncio.Queue[StreamEvent],
        tools: list[dict] | None = None,
    ) -> None:
        """Push StreamEvent objects into channel. Ends with StreamEvent("done")."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str: ...


# ── 格式转换 ──────────────────────────────────────


def convert_to_llm(messages: list[Message]) -> list[dict]:
    """内部 Message → OpenAI 格式 dict 列表。"""
    llm_messages = []
    for message in messages:
        if message.role in ("user", "system"):
            llm_messages.append({"role": message.role, "content": message.content})
        elif message.role == "assistant":
            d: dict = {"role": "assistant", "content": message.content}
            if message.reasoning_content:
                d["reasoning_content"] = message.reasoning_content
            if message.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in message.tool_calls
                ]
            llm_messages.append(d)
        elif message.role == "tool":
            d: dict[str, object] = {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
            if message.tool_name:
                d["name"] = message.tool_name
            llm_messages.append(d)
        elif message.role == "contextSummary":
            llm_messages.append({
                "role": "user",
                "content": f"The summary of the context: {message.content}",
            })
    return llm_messages
