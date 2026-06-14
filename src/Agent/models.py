"""Internal message types for the agent loop."""

from __future__ import annotations

from pydantic import BaseModel
from typing import Literal, List


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class AgentMessage(BaseModel):
    systemPrompt: str
    messages: List[Message]
    tools: List[dict]


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


class ToolResult(BaseModel):
    tool_call_id: str
    content: str
