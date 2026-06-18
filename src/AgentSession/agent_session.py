import asyncio
import os
import time
from typing import Literal

from src.context.session_manager import SessionManager
from src.Agent.agent import Agent
from src.data.event import AgentEvent, ApprovalRequired
from src.data.messages import UserMessage, TextContent
from src.engine.tool import Tool
from loguru import logger as log


class AgentSession:
    def __init__(
        self,
        model_client,
        system_prompt: str,
        tools: list[Tool] | None = None,
        approval: Literal["auto", "ask", "never"] = "ask",
        session_manager: SessionManager = None,
    ):
        self.tools = tools
        self.model_client = model_client
        self.session_manager = session_manager
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
        self._agent._state.systemPrompt = self._build_systemPrompt()
        log.info("System Prompt:\n{}", self._agent._state.systemPrompt)

        if self._agent._state.isStreaming:
            raise RuntimeError("Agent is already processing")
        msg = UserMessage(
            role="user",
            content=[TextContent(text=query)],
            timestamp=int(time.time()),
        )
        await self._agent.prompt([msg])

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
        await self._handle_event(
            ApprovalRequired(tool_name=tool_name, args=args, _future=future)
        )
        return await future

    def dispose(self):
        self._unsub()
        self._ui_listeners.clear()

    def subscribe(self, listener):
        self._ui_listeners.add(listener)
        return lambda: self._ui_listeners.discard(listener)

    def _build_systemPrompt(self):
        fix_prompt = self.build_prefix()

        # 获取当前分支
        current_branch = os.popen("git rev-parse --abbrev-ref HEAD").read().strip() or "unknown"

        descriptions = "\n".join(f"- {t.name}" for t in self.tools)

        return (
            f"{fix_prompt}\n"
            f"Current branch: {current_branch}\n\n"
            f"# Tool-use ability\n"
            f"You can use tools to finish the task:\n"
            f"{descriptions}\n"
        )

    def build_prefix(self):
        cwd = os.getcwd()
        git_scope = os.popen("git rev-parse --show-toplevel").read().strip() or cwd

        # git symbolic-ref may fail when remote HEAD is not set
        main_branch_raw = os.popen("git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null").read().strip()
        if main_branch_raw.startswith("refs/remotes/origin/"):
            main_branch = main_branch_raw[len("refs/remotes/origin/"):]
        else:
            main_branch = "main"

        return (
            f"You are Jarvis, a powerful AI coding Agent running in {cwd}.\n"
            f"\n"
            f"# Rules\n"
            f"1. Before starting the task, make sure you have a thorough understanding of the project.\n"
            f"\n"
            f"# Environment\n"
            f"Git scope: {git_scope}\n"
            f"Main branch: {main_branch}\n"
        )

    def get_tool_description(self):
        # Superseded by _build_systemPrompt — kept for backward compat
        return ""
