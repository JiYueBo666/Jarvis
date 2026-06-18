import asyncio
from dataclasses import dataclass, field
from typing import Any, List

# Agent生命周期


@dataclass
class AgentStart:
    """Agent 开始一次运行。"""


@dataclass
class AgentEnd:
    """Agent 运行结束，附带本轮新增的消息。"""

    messages: list


# ── Turn 生命周期 ──


@dataclass
class TurnStart:
    """一轮开始（一次调 LLM + N 次工具调用）。"""


@dataclass
class TurnEnd:
    """一轮结束。"""

    message: Any
    tool_results: list


# ── 消息生命周期 ──


@dataclass
class MessageStart:
    """新消息开始。"""

    message: Any


@dataclass
class MessageUpdate:
    """流式更新 assistant 消息（text_delta 等）。"""

    message: Any


@dataclass
class MessageEnd:
    """消息完成。"""

    message: Any


# ── 工具执行生命周期 ──


@dataclass
class ToolExecutionStart:
    """工具开始执行。"""

    tool_call_id: str
    tool_name: str
    args: dict


@dataclass
class ToolExecutionEnd:
    """工具执行结束。"""

    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool = False


@dataclass
class ApprovalRequired:
    """需要用户审批才能执行的危险工具。"""

    tool_name: str
    args: dict
    _future: asyncio.Future = field(repr=False)

    def approve(self):
        if not self._future.done():
            loop = self._future.get_loop()
            loop.call_soon_threadsafe(self._future.set_result, {"approved": True})

    def deny(self, message: str = "Tool execution denied"):
        if not self._future.done():
            loop = self._future.get_loop()
            loop.call_soon_threadsafe(
                self._future.set_result, {"approved": False, "message": message}
            )


# ── 上下文压缩 ──


@dataclass
class CompactionStart:
    """上下文压缩开始。"""


@dataclass
class CompactionEnd:
    """上下文压缩完成。"""

    messages_before: int
    messages_after: int


# ── 自动重试 ──


@dataclass
class RetryStart:
    """自动重试开始（rate limit / server error 等）。"""


@dataclass
class RetryEnd:
    """自动重试完成。"""

    success: bool


# ── Union 类型 ──

AgentEvent = (
    AgentStart
    | AgentEnd
    | TurnStart
    | TurnEnd
    | MessageStart
    | MessageUpdate
    | MessageEnd
    | ToolExecutionStart
    | ToolExecutionEnd
    | ApprovalRequired
    | CompactionStart
    | CompactionEnd
    | RetryStart
    | RetryEnd
)
