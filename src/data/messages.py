from dataclasses import dataclass, field
import time
from typing import Literal


# content-block类型
@dataclass
class TextContent:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ToolCallContent:
    id: str
    name: str
    arguments: dict
    type: Literal["tool_call"] = "tool_call"


@dataclass
class ThinkingContent:
    thinking: str
    type: Literal["thinking"] = "thinking"
    redacted: bool = False


# ----------------------基础消息类型
@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens


@dataclass
class Message:
    role: Literal["user", "assistant", "system", "tool_result"]
    content: list[TextContent | ToolCallContent] = field(default_factory=list)
    timestamp: int = field(default_factory=lambda: int(time.time()))


@dataclass
class UserMessage(Message):
    role: Literal["user"] = "user"


@dataclass
class AssistantMessage(Message):
    role: Literal["assistant"] = "assistant"
    content: list[TextContent | ThinkingContent | ToolCallContent] = field(
        default_factory=list
    )
    api: str = ""
    provider: str = ""
    model: str = ""
    response_model: str | None = None
    response_id: str | None = None
    usage: Usage | None = None
    stop_reason: str = "stop"
    error_message: str | None = None


@dataclass
class ToolResultMessage(Message):
    role: Literal["tool_result"] = "tool_result"
    tool_call_id: str = ""
    tool_name: str = ""
    is_error: bool = False


@dataclass
class CompactionSummaryMessage(Message):
    role: Literal["compaction_summary"] = "compaction_summary"
    summary: str = ""
    tokens_before: int = 0


from typing import Union

AgentMessage = Union[
    UserMessage, AssistantMessage, ToolResultMessage, CompactionSummaryMessage
]
