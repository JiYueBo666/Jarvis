import asyncio
import time
from typing import Literal

from src.Agent.agent import Agent
from src.data.event import AgentEvent, ApprovalRequired
from src.data.messages import UserMessage, TextContent
from src.engine.tool import Tool


class AgentSession:
    def __init__(
        self,
        model_client,
        system_prompt: str,
        tools: list[Tool] | None = None,
        approval: Literal["auto", "ask", "never"] = "ask",
    ):
        self.model_client = model_client
        self._agent = Agent(model_client, before_tool_call=self._before_tool_call)
        self._agent._state.systemPrompt = system_prompt
        self._agent._state.tools = tools or []
        self._tool_map = {tool.name: tool for tool in (tools or [])}
        self.approval: Literal["auto", "ask", "never"] = approval

        self._ui_listeners: set = set()
        self._unsub = self._agent.subscribe(self._handle_event)

    @property
    def state(self):
        return self._agent._state

    async def prompt(self, query: str):
        if self._agent._state.isStreaming:
            raise RuntimeError("Agent is already processing")
        msg = UserMessage(
            role="user",
            content=[TextContent(text=query)],
            timestamp=int(time.time()),
        )
        await self._agent.prompt([msg])

    async def _emit_to_ui(self, event):
        for listener in self._ui_listeners:
            result = listener(event)
            if asyncio.iscoroutine(result):
                await result

    async def _handle_event(self, event: AgentEvent):
        for listener in self._ui_listeners:
            result = listener(event)
            if asyncio.iscoroutine(result):
                await result

    async def _before_tool_call(self, tool_name: str, args: dict) -> dict:
        """工具执行前审批 hook。返回 {"approved": bool, "message": str}。"""
        if self.approval == "auto":
            return {"approved": True}
        if self.approval == "never":
            return {
                "approved": False,
                "message": f"Tool '{tool_name}' denied by 'never' policy",
            }

        # ask mode
        tool = self._tool_map.get(tool_name)
        if tool and not tool.risky:
            return {"approved": True}

        future = asyncio.get_event_loop().create_future()
        await self._emit_to_ui(
            ApprovalRequired(tool_name=tool_name, args=args, _future=future)
        )
        return await future

    def dispose(self):
        self._unsub()
        self._ui_listeners.clear()

    def subscribe(self, listener):
        self._ui_listeners.add(listener)
        return lambda: self._ui_listeners.discard(listener)
