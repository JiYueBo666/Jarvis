"""Abstract LLM client interface."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from src.Agent.models import Message


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
