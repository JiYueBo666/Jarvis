"""Agent 层：状态管理 + 事件处理 + runLoop 编排。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from src.Agent.loop import (
    AgentContext,
    AgentLoopConfig,
    AssistantMessage,
    streamAssistantResponse,
)
from src.Agent.models import Message
from src.Client.base import StreamEvent
from src.Tools.base import Tool, ToolExecutor


# ── 状态 ─────────────────────────────────────────────────


@dataclass
class AgentState:
    """Agent 的完整状态。"""

    # 持久状态（跨轮次保留）
    system_prompt: str = ""
    model: str = ""
    thinking_level: str = "off"
    tools: list[Tool] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)

    # 运行时状态（一轮 run 结束后重置）
    is_streaming: bool = False
    streaming_message: AssistantMessage | None = None
    pending_tool_calls: set[str] = field(default_factory=set)
    error_message: str | None = None

    def reset_runtime(self):
        self.is_streaming = False
        self.streaming_message = None
        self.pending_tool_calls = set()
        self.error_message = None


# ── Agent ─────────────────────────────────────────────────


class CodingAgent:
    """带状态的 Agent。持有 AgentState 和运行配置。"""

    def __init__(
        self,
        config: AgentLoopConfig,
        executor: ToolExecutor,
        state: AgentState | None = None,
    ):
        self.config = config
        self.executor = executor
        self.state = state or AgentState()
        self._abort: asyncio.Event | None = None
        self._listeners: list[Callable[[StreamEvent], Awaitable[None]]] = []

    # ── 中止 ─────────────────────────────────────────

    @property
    def abort_signal(self) -> asyncio.Event:
        if self._abort is None:
            self._abort = asyncio.Event()
        return self._abort

    def abort(self):
        self.abort_signal.set()

    # ── 事件处理 ───────────────────────────────────────

    async def process_event(self, event: StreamEvent) -> None:
        match event.type:
            case "message_start":
                self.state.is_streaming = True
                self.state.streaming_message = event.message

            case "message_update":
                self.state.streaming_message = event.message

            case "message_end":
                self.state.is_streaming = False
                self.state.streaming_message = None
                # 不 append：streamAssistantResponse 已在 context.messages 中维护

            case "error":
                self.state.is_streaming = False
                self.state.error_message = str(event.data or "Unknown error")
                self.state.streaming_message = None

            case "tool_start":
                self.state.streaming_message = None
                if event.tool_call_id:
                    self.state.pending_tool_calls.add(event.tool_call_id)

            case "tool_end":
                if event.tool_call_id:
                    self.state.pending_tool_calls.discard(event.tool_call_id)

        # 通知所有外部 listener
        for listener in self._listeners:
            await listener(event)

    # ── Listener 注册 ─────────────────────────────────

    def add_listener(self, fn: Callable[[StreamEvent], Awaitable[None]]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[StreamEvent], Awaitable[None]]) -> None:
        self._listeners.remove(fn)

    # ── emit 工厂 ─────────────────────────────────────

    def make_emit(self) -> Callable[[StreamEvent], Awaitable[None]]:
        async def emit(event: StreamEvent) -> None:
            await self.process_event(event)

        return emit

    # ── 入口 ─────────────────────────────────────────

    async def prompt(
        self,
        input: str | list[Message] | dict,
        approval_check: Callable[[str, dict], Awaitable[bool]] | None = None,
    ) -> str | None:
        """用户入口：传入消息，启动 runLoop。返回最后一条 assistant 消息内容。"""
        messages = self._normalize_input(input)
        self.state.messages.extend(messages)

        context = self._create_context()
        new_messages: list[Message] = []
        emit = self.make_emit()

        await run_loop(
            context=context,
            new_messages=new_messages,
            config=self.config,
            executor=self.executor,
            signal=self._abort,
            emit=emit,
            approval_check=approval_check,
        )

        # 返回最后一条 assistant 消息内容
        for msg in reversed(self.state.messages):
            if msg.role == "assistant" and msg.content:
                return msg.content
        return None

    def _normalize_input(self, input: str | list[Message] | dict) -> list[Message]:
        if isinstance(input, str):
            return [Message(role="user", content=input)]
        elif isinstance(input, list):
            return input
        elif isinstance(input, dict):
            return [Message(role="user", content=input.get("content", ""))]
        return []

    def _create_context(self) -> AgentContext:
        return AgentContext(
            messages=self.state.messages,
            system_prompt=self.state.system_prompt,
            tools=[t.to_openai_schema() for t in self.state.tools],
        )


# ── runLoop ─────────────────────────────────────────────


async def run_loop(
    context: AgentContext,
    new_messages: list[Message],
    config: AgentLoopConfig,
    executor: ToolExecutor,
    signal: asyncio.Event | None,
    emit: Callable[[StreamEvent], Awaitable[None]],
    max_steps: int = 20,
    approval_check: Callable[[str, dict], Awaitable[bool]] | None = None,
) -> None:
    """完整 Agent 循环：处理 steering 消息 → 调 LLM → 执行工具 → 继续。"""
    current_context = context
    first_turn = True
    pending_messages: list[Message] = await _get_steering(config)
    steps = 0

    while True:
        has_more_tool_calls = True

        # 内层循环：处理 tool calls 和 steering 消息
        while (has_more_tool_calls or pending_messages) and steps < max_steps:
            if first_turn:
                first_turn = False
            else:
                await emit(StreamEvent("turn_start"))

            # 注入 steering 消息
            if pending_messages:
                for msg in pending_messages:
                    await emit(StreamEvent("message_start", message=msg))
                    await emit(StreamEvent("message_end", message=msg))
                    current_context.messages.append(msg)
                    new_messages.append(msg)
                pending_messages = []

            # 检查中止信号
            if signal and signal.is_set():
                await emit(StreamEvent("turn_end"))
                await emit(StreamEvent("agent_end", data=new_messages))
                return

            # 调 LLM
            steps += 1
            assistant_msg = await streamAssistantResponse(
                context=current_context,
                config=config,
                emit=emit,
                signal=signal,
            )
            new_messages.append(assistant_msg)

            # 执行工具
            if assistant_msg.tool_calls:
                tool_results = await _execute_tool_calls(
                    assistant_msg, executor, current_context, new_messages,
                    emit, signal, approval_check,
                )
                has_more_tool_calls = True
            else:
                tool_results = []
                has_more_tool_calls = False

            await emit(StreamEvent("turn_end"))

            # prepare_next_turn 钩子
            if config.prepare_next_turn:
                snapshot = await config.prepare_next_turn(
                    {
                        "message": assistant_msg,
                        "tool_results": tool_results,
                        "context": current_context,
                        "new_messages": new_messages,
                    }
                )
                if snapshot:
                    current_context = snapshot.get("context", current_context)

            # should_stop_after_turn 钩子
            if config.should_stop_after_turn:
                should_stop = await config.should_stop_after_turn(
                    {
                        "message": assistant_msg,
                        "tool_results": tool_results,
                        "context": current_context,
                        "new_messages": new_messages,
                    }
                )
                if should_stop:
                    await emit(StreamEvent("agent_end", data=new_messages))
                    return

            # 新一轮 steering 消息
            pending_messages = await _get_steering(config)

        # 达到最大步数
        if steps >= max_steps:
            await emit(StreamEvent("agent_end", data=f"Reached max steps ({max_steps})"))
            return

        # 外层循环：follow-up 消息
        follow_up = await _get_follow_up(config)
        if follow_up:
            pending_messages = follow_up
            continue

        break

    await emit(StreamEvent("agent_end", data=new_messages))


async def _execute_tool_calls(
    assistant_msg: Message,
    executor: ToolExecutor,
    context: AgentContext,
    new_messages: list[Message],
    emit: Callable[[StreamEvent], Awaitable[None]],
    signal: asyncio.Event | None = None,
    approval_check: Callable[[str, dict], Awaitable[bool]] | None = None,
) -> list[Message]:
    """执行 assistant 消息中的工具调用。"""
    results: list[Message] = []

    for tc in assistant_msg.tool_calls:
        if signal and signal.is_set():
            break

        # 审批检查
        if approval_check:
            approved = await approval_check(tc.name, tc.arguments)
            if not approved:
                await emit(StreamEvent("error", data=f"用户拒绝了工具调用: {tc.name}"))
                continue

        await emit(StreamEvent("tool_start", tool_name=tc.name, tool_call_id=tc.id))

        try:
            result = await executor.execute(tc.name, tc.arguments)
        except Exception as e:
            result = f"Tool error: {e}"
            await emit(
                StreamEvent("error", data=result, tool_name=tc.name, tool_call_id=tc.id)
            )

        tool_msg = Message(role="tool", content=result, tool_call_id=tc.id, tool_name=tc.name)
        context.messages.append(tool_msg)
        new_messages.append(tool_msg)
        results.append(tool_msg)

        await emit(
            StreamEvent(
                "tool_end", tool_name=tc.name, tool_call_id=tc.id, result=result
            )
        )

    return results


async def _get_steering(config: AgentLoopConfig) -> list[Message]:
    if config.get_steering_messages:
        return await config.get_steering_messages()
    return []


async def _get_follow_up(config: AgentLoopConfig) -> list[Message]:
    if config.get_follow_up_messages:
        return await config.get_follow_up_messages()
    return []
