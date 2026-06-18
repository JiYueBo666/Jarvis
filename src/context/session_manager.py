"""Conversation persistence — save/load AgentMessage lists as JSON."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.data.messages import (
    AgentMessage,
    UserMessage,
    AssistantMessage,
    ToolResultMessage,
    CompactionSummaryMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    Usage,
)


def _serialize(msgs: list[AgentMessage]) -> list[dict]:
    return [_msg_to_dict(m) for m in msgs]


def _deserialize(data: list[dict]) -> list[AgentMessage]:
    return [_msg_from_dict(d) for d in data]


def _msg_to_dict(m: AgentMessage) -> dict:
    d: dict[str, Any] = {"role": m.role, "timestamp": m.timestamp}
    d["content"] = [_content_to_dict(c) for c in m.content]

    if isinstance(m, AssistantMessage) and m.usage:
        u = m.usage
        d["usage"] = {
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_tokens": u.cache_read_tokens,
            "cache_write_tokens": u.cache_write_tokens,
        }
    if isinstance(m, ToolResultMessage):
        d["tool_call_id"] = m.tool_call_id
        d["tool_name"] = m.tool_name
        d["is_error"] = m.is_error
    if isinstance(m, CompactionSummaryMessage):
        d["summary"] = m.summary
        d["tokens_before"] = m.tokens_before

    return d


def _content_to_dict(c: TextContent | ThinkingContent | ToolCallContent) -> dict:
    if isinstance(c, TextContent):
        return {"type": "text", "text": c.text}
    if isinstance(c, ThinkingContent):
        return {"type": "thinking", "thinking": c.thinking}
    if isinstance(c, ToolCallContent):
        return {"type": "tool_call", "id": c.id, "name": c.name, "arguments": c.arguments}
    raise TypeError(f"Unknown content type: {type(c)}")


def _msg_from_dict(d: dict) -> AgentMessage:
    role = d["role"]
    content = [_content_from_dict(c) for c in d.get("content", [])]
    ts = d.get("timestamp", 0)

    if role == "user":
        return UserMessage(content=content, timestamp=ts)
    if role == "assistant":
        usage = None
        if "usage" in d:
            u = d["usage"]
            usage = Usage(
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
                cache_read_tokens=u.get("cache_read_tokens", 0),
                cache_write_tokens=u.get("cache_write_tokens", 0),
            )
        return AssistantMessage(content=content, timestamp=ts, usage=usage)
    if role == "tool_result":
        return ToolResultMessage(
            content=content,
            timestamp=ts,
            tool_call_id=d.get("tool_call_id", ""),
            tool_name=d.get("tool_name", ""),
            is_error=d.get("is_error", False),
        )
    if role == "compaction_summary":
        return CompactionSummaryMessage(
            content=content,
            timestamp=ts,
            summary=d.get("summary", ""),
            tokens_before=d.get("tokens_before", 0),
        )
    raise ValueError(f"Unknown message role: {role}")


def _content_from_dict(d: dict) -> TextContent | ThinkingContent | ToolCallContent:
    t = d["type"]
    if t == "text":
        return TextContent(text=d["text"])
    if t == "thinking":
        return ThinkingContent(thinking=d["thinking"])
    if t == "tool_call":
        return ToolCallContent(id=d["id"], name=d["name"], arguments=d["arguments"])
    raise ValueError(f"Unknown content type: {t}")


@dataclass
class SessionInfo:
    session_id: str
    title: str
    message_count: int
    mtime: float


class SessionManager:
    """Saves/loads conversation messages as JSON files.

    Pure persistence — no event hooks, no agent coupling.
    """

    def __init__(self, store_dir: str | Path):
        self._store = Path(store_dir)
        self._store.mkdir(parents=True, exist_ok=True)

    def save(self, session_id: str, messages: list[AgentMessage]):
        """Persist messages for *session_id*. Overwrites on each call."""
        path = self._store / f"{session_id}.json"
        path.write_text(
            json.dumps(_serialize(messages), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, session_id: str) -> list[AgentMessage] | None:
        """Load persisted messages, or None if not found."""
        path = self._store / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _deserialize(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def list_sessions(self) -> list[SessionInfo]:
        """Return all persisted sessions sorted newest-first."""
        sessions = []
        for f in sorted(self._store.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            first = data[0] if data else {}
            first_content = ""
            for c in first.get("content", []):
                if c.get("type") == "text":
                    first_content = c.get("text", "")
                    break
            sessions.append(
                SessionInfo(
                    session_id=f.stem,
                    title=first_content[:60],
                    message_count=len(data),
                    mtime=f.stat().st_mtime,
                )
            )
        return sessions

    @staticmethod
    def new_session_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S")
