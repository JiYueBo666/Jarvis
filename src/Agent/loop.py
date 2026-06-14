"""ReAct agent loop."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass

from src.Agent.models import Message, ToolCall
from src.Client.base import LLMClient, StreamEvent
from src.Tools.base import ToolExecutor

# ── 类型定义 ──────────────────────────────────────────────


@dataclass
class AgentContext:
    """Agent 持有的上下文状态。"""

    messages: list[Message]
    system_prompt: str
    tools: list[dict] | None = None


@dataclass
class AgentLoopConfig:
    """runLoop 配置：模型、转换函数、生命周期钩子等。"""

    model: str
    api_key: str
    client: LLMClient
    convert_to_llm: Callable[[list[Message]], list[dict]]
    transform_context: Callable[[list[Message]], Awaitable[list[Message]]] | None = None
    get_api_key: Callable[[str], str] | None = None

    # 生命周期钩子
    prepare_next_turn: Callable[[dict], Awaitable[dict | None]] | None = None
    should_stop_after_turn: Callable[[dict], Awaitable[bool]] | None = None
    get_steering_messages: Callable[[], Awaitable[list[Message]]] | None = None
    get_follow_up_messages: Callable[[], Awaitable[list[Message]]] | None = None


AssistantMessage = Message


# ── stream_simple ─────────────────────────────────────────


class LLMResponse:
    """异步迭代器，包装 client.chat_stream 为带 partial 的事件流。

    每次 yield 的 event.partial 是累加到当前时刻的完整 Message。
    迭代结束后可调用 .result() 获取最终 Message。
    """

    def __init__(
        self,
        client: LLMClient,
        messages: list[Message],
        tools: list[dict] | None,
    ):
        self._client = client
        self._messages = messages
        self._tools = tools
        self._final: Message | None = None
        self._iterator = self._generate()

    def __aiter__(self):
        return self._iterator

    async def result(self) -> Message:
        if self._final is None:
            async for _ in self:
                pass
        assert self._final is not None
        return self._final

    async def _generate(self):
        channel: asyncio.Queue[StreamEvent] = asyncio.Queue()
        producer = asyncio.create_task(
            self._client.chat_stream(self._messages, channel, self._tools)
        )

        content = ""
        tool_call_fragments: dict[int, dict[str, str]] = {}
        started = False
        error: str | None = None

        try:
            while True:
                event = await channel.get()
                match event.type:
                    case "delta":
                        content += event.data
                    case "tool_call":
                        data = event.data
                        idx: int = data["index"]
                        if idx not in tool_call_fragments:
                            tool_call_fragments[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        frag = tool_call_fragments[idx]
                        if data.get("id"):
                            frag["id"] = data["id"]
                        if data.get("name"):
                            frag["name"] += data["name"]
                        if data.get("arguments"):
                            frag["arguments"] += data["arguments"]
                    case "done":
                        await producer
                        break

                # 组装当前 partial
                partial = _assemble(content, tool_call_fragments)

                if not started:
                    started = True
                    yield _StreamEventWithPartial("start", partial=partial)
                else:
                    yield _StreamEventWithPartial("text_delta", partial=partial)
        except Exception as e:
            error = str(e)
            yield _StreamEventWithPartial(
                "error", partial=_assemble(content, tool_call_fragments)
            )

        if error:
            self._final = _assemble(content, tool_call_fragments, error=error)
        else:
            self._final = _assemble(content, tool_call_fragments)
        yield _StreamEventWithPartial("done", partial=self._final)


def _assemble(
    content: str,
    fragments: dict[int, dict[str, str]],
    error: str | None = None,
) -> Message:
    """从累积内容 + 工具调用碎片拼装 Message。"""
    tool_calls: list[ToolCall] = []
    for idx in sorted(fragments):
        frag = fragments[idx]
        try:
            args = json.loads(frag["arguments"])
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(ToolCall(id=frag["id"], name=frag["name"], arguments=args))
    content_text = content or None
    if error:
        content_text = (content or "") + f"\n\n[Error: {error}]"
    return Message(
        role="assistant",
        content=content_text,
        tool_calls=tool_calls or None,
    )


# ── 流式事件包装 ────────────────────────────────────────────


class _StreamEventWithPartial:
    """stream_simple 产出的事件，携带累积的 partial Message。"""

    def __init__(self, type: str, *, partial: Message):
        self.type = type
        self.partial = partial


# ── streamAssistantResponse ──────────────────────────────


async def streamAssistantResponse(
    context: AgentContext,
    config: AgentLoopConfig,
    emit: Callable[[StreamEvent], Awaitable[None]],
    signal: asyncio.Event | None = None,
    stream_fn: Callable[..., Awaitable[LLMResponse]] | None = None,
) -> AssistantMessage:
    """消费 LLM 流，emit 事件给 UI，返回完整消息给 runLoop。

    如果 signal 被设置，提前中止并返回积累的内容。
    """

    # 1. 转换上下文
    messages = context.messages
    if config.transform_context:
        messages = await config.transform_context(messages)

    # 2. 调 LLM，拿到事件流
    stream_fn = stream_fn or stream_simple
    response: LLMResponse = await stream_fn(config, context)

    # 3. 消费流
    partial: AssistantMessage | None = None
    added_partial = False

    async for event in response:
        match event.type:
            case "start":
                partial = event.partial
                context.messages.append(partial)
                added_partial = True
                await emit(StreamEvent("message_start", message=deepcopy(partial)))

            case "text_delta" | "text_end":
                if partial:
                    partial = event.partial
                    context.messages[-1] = partial
                    await emit(
                        StreamEvent(
                            "message_update",
                            message=deepcopy(partial),
                            assistant_message_event=event.type,
                        )
                    )

            case "done":
                final = await response.result()
                if added_partial:
                    context.messages[-1] = final
                else:
                    context.messages.append(final)

                if not added_partial:
                    await emit(StreamEvent("message_start", message=deepcopy(final)))
                await emit(StreamEvent("message_end", message=final))
                return final

            case "error":
                final = await response.result()
                await emit(
                    StreamEvent("error", data=final.content or "LLM stream error")
                )
                return final

    # 兜底
    final = await response.result()
    if added_partial:
        context.messages[-1] = final
    else:
        context.messages.append(final)
        await emit(StreamEvent("message_start", message=deepcopy(final)))
    await emit(StreamEvent("message_end", message=final))
    return final


# ── stream_simple 工厂 ────────────────────────────────────


async def stream_simple(
    config: AgentLoopConfig,
    context: AgentContext,
) -> LLMResponse:
    """默认 stream_fn：基于 LLMClient 创建 LLMResponse。"""
    return LLMResponse(
        client=config.client,
        messages=context.messages,
        tools=context.tools,
    )


# ── AgentLoop（保持不动） ─────────────────────────────────


class AgentLoop:
    """ReAct loop: think → act → observe → repeat until final answer."""

    def __init__(
        self,
        client: LLMClient,
        executor: ToolExecutor,
        max_steps: int = 20,
    ):
        self._client = client
        self._executor = executor
        self._max_steps = max_steps

    async def run(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> str:
        history = list(messages)

        for step in range(self._max_steps):
            response = await self._client.chat(history, tools=tools)
            history.append(response)

            if not response.tool_calls:
                return response.content or "(empty response)"

            for tc in response.tool_calls:
                result = await self._executor.execute(tc.name, tc.arguments)
                history.append(Message(role="tool", content=result, tool_call_id=tc.id))

        return f"Reached max steps ({self._max_steps}) without final answer"
