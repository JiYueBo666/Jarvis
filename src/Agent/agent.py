from src.data.event import (
    AgentEnd,
    MessageEnd,
    MessageStart,
    MessageUpdate,
    ToolExecutionEnd,
    ToolExecutionStart,
    TurnEnd,
)
from src.data.messages import AgentMessage, AssistantMessage
from src.engine.model import ModelClient
from src.context.convert_to_llm import convert_to_llm
from dataclasses import dataclass, field
from typing import Callable, Optional
from src.engine.loop import Engine


@dataclass
class AgentState:
    systemPrompt: str = ""
    model: str = ""
    tools: list = field(default_factory=list)
    messages: list[AgentMessage] = field(default_factory=list)
    isStreaming: bool = False
    streamingMessage: Optional[AssistantMessage] = None
    pendingToolCalls: set[str] = field(default_factory=set)
    errorMessage: str = ""


class Agent:
    def __init__(self, model_client: ModelClient, before_tool_call=None):
        self._state = AgentState()
        self._engine = Engine(model_client=model_client, convert_to_llm=convert_to_llm)
        self._listeners: set = set()
        self._before_tool_call = before_tool_call

    async def prompt(self, messages: list[AgentMessage]):
        self._state.isStreaming = True
        self._state.messages.extend(messages)

        await self._engine.run_stream(
            messages=self._state.messages,
            tools=self._state.tools,
            system_prompt=self._state.systemPrompt,
            emit=self._process_event,
            before_tool_call=self._before_tool_call,
        )

    async def continue_(self):
        """从当前 state 继续，不添加新消息。用于重试/压缩后继续。"""
        self._state.isStreaming = True
        await self._engine.run_stream(
            messages=self._state.messages,
            tools=self._state.tools,
            system_prompt=self._state.systemPrompt,
            emit=self._process_event,
            before_tool_call=self._before_tool_call,
        )

    async def _emit(self, event):
        for listener in self._listeners:
            await listener(event)

    async def _process_event(self, event):
        match event:
            case MessageStart():
                self._state.streamingMessage = event.message

            case MessageUpdate():
                if self._state.streamingMessage is None:
                    self._state.streamingMessage = event.message
                else:
                    self._state.streamingMessage.content.extend(event.message.content)

            case MessageEnd():
                self._state.streamingMessage = None
                self._state.messages.append(event.message)

            case ToolExecutionStart():
                self._state.pendingToolCalls.add(event.tool_call_id)

            case ToolExecutionEnd():
                self._state.pendingToolCalls.discard(event.tool_call_id)

            case TurnEnd():
                if (
                    hasattr(event.message, "error_message")
                    and event.message.error_message
                ):
                    self._state.errorMessage = event.message.error_message

            case AgentEnd():
                self._state.isStreaming = False
                self._state.streamingMessage = None

        # 通知各个部件
        await self._emit(event)

    def subscribe(self, listener):
        """注册事件监听器。返回取消订阅的函数。"""
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)
