import asyncio
import os
import subprocess
import time
from typing import Callable, Literal

from src.context.session_manager import SessionManager
from src.Agent.agent import Agent
from src.data.event import (
    AgentEnd,
    AgentEvent,
    ApprovalRequired,
    CompactionEnd,
    CompactionStart,
    RetryEnd,
    RetryStart,
)
from src.data.messages import (
    AssistantMessage,
    CompactionSummaryMessage,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
    TextContent,
    ThinkingContent,
)
from src.engine.model import ModelClient, get_context_window
from src.engine.tool import Tool
from loguru import logger as log


def _git_output(cmd: str) -> str:
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


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
        self._session_manager = session_manager
        self._session_id = session_manager.new_session_id() if session_manager else ""
        self._agent = Agent(model_client, before_tool_call=self._before_tool_call)
        self._agent._state.systemPrompt = system_prompt
        self._agent._state.tools = tools or []
        self._tool_map = {tool.name: tool for tool in (tools or [])}
        self.approval: Literal["auto", "ask", "never"] = approval

        self._ui_listeners: set = set()
        self._unsub = self._agent.subscribe(self._handle_event)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def state(self):
        return self._agent._state

    async def prompt(self, query: str):
        self._agent._state.systemPrompt = self._build_system_prompt()

        if self._agent._state.isStreaming:
            raise RuntimeError("Agent is already processing")
        msg = UserMessage(
            role="user",
            content=[TextContent(text=query)],
            timestamp=int(time.time()),
        )
        await self._run_agent([msg])

    # ── 重试 & Compaction ──────────────────────────────────────

    async def _run_agent(self, messages: list):
        """启动 agent，然后在 while 循环里检查重试/压缩直到不需要处理。"""
        await self._agent.prompt(messages)
        while await self._handle_after():
            pass

    async def _handle_after(self) -> bool:
        """Agent run 结束后检查：可重试错误 → 重试；超限 → 压缩。
        Returns True 表示做了处理需要重新检查。"""
        from src.config import settings

        msg = self._last_assistant_message()
        if not msg:
            return False

        # 1. 可重试错误（rate limit, 500 等）→ 退避重试
        if self._is_retryable_error(msg):
            await self._emit_ui(RetryStart())
            if await self._prepare_retry(msg):
                await self._agent.continue_()
                self._retry_attempt = 0
                await self._emit_ui(RetryEnd(success=True))
                return True
            await self._emit_ui(RetryEnd(success=False))
            return False

        # 2. 上下文超限 → 压缩后继续
        if settings.COMPACTION_ENABLED and self._check_compaction(msg):
            await self._emit_ui(CompactionStart())
            msgs_before = len(self._agent._state.messages)
            await self._run_auto_compaction()
            msgs_after = len(self._agent._state.messages)
            await self._emit_ui(CompactionEnd(msgs_before, msgs_after))
            await self._agent.continue_()
            return True

        return False

    @staticmethod
    def _is_retryable_error(message: AssistantMessage) -> bool:
        if message.stop_reason != "error" or not message.error_message:
            return False

        err = message.error_message.lower()

        overflow_patterns = (
            "context length",
            "maximum context",
            "too many tokens",
            "token limit",
        )
        if any(p in err for p in overflow_patterns):
            return False

        retryable_patterns = [
            "overloaded",
            "provider returned error",
            "rate limit",
            "too many requests",
            "429",
            "500",
            "502",
            "503",
            "504",
            "service unavailable",
            "server error",
            "internal error",
            "network error",
            "connection error",
            "connection refused",
            "connection lost",
            "websocket closed",
            "fetch failed",
            "socket hang up",
            "timeout",
            "terminated",
        ]
        return any(p in err for p in retryable_patterns)

    def _last_assistant_message(self) -> AssistantMessage | None:
        for msg in reversed(self._agent._state.messages):
            if isinstance(msg, AssistantMessage):
                return msg
        return None

    async def _prepare_retry(self, msg: AssistantMessage) -> bool:
        """移除最后一条错误消息，指数退避后返回 True 表示可以重试。"""
        msgs = self._agent._state.messages
        if msgs and msgs[-1] is msg:
            msgs.pop()
        else:
            for i in range(len(msgs) - 1, -1, -1):
                if (
                    isinstance(msgs[i], AssistantMessage)
                    and msgs[i].stop_reason == "error"
                ):
                    msgs.pop(i)
                    break

        self._retry_attempt = getattr(self, "_retry_attempt", 0) + 1
        if self._retry_attempt > 3:
            log.warning(f"Max retries ({3}) reached, giving up")
            return False

        delay = min(1.0 * (2 ** (self._retry_attempt - 1)), 30.0)
        log.info(f"Retry attempt {self._retry_attempt}, waiting {delay:.1f}s")
        await asyncio.sleep(delay)
        return True

    @staticmethod
    def _is_context_overflow(msg: AssistantMessage) -> bool:
        if msg.stop_reason != "error" or not msg.error_message:
            return False
        err = msg.error_message.lower()
        patterns = (
            "context length",
            "maximum context",
            "too many tokens",
            "token limit",
        )
        return any(p in err for p in patterns)

    @staticmethod
    def _should_compact(input_tokens: int, ctx_window: int) -> bool:
        from src.config import settings

        return input_tokens > ctx_window - settings.COMPACTION_RESERVE_TOKENS

    def _check_compaction(self, msg: AssistantMessage) -> bool:
        ctx_window = get_context_window(self.model_client.model)
        if self._is_context_overflow(msg):
            return True
        usage = getattr(msg, "usage", None)
        if usage:
            if usage.input_tokens > ctx_window:
                return True
            if self._should_compact(usage.input_tokens, ctx_window):
                return True
        return False

    @staticmethod
    def _select_cut_point(messages: list) -> int | None:
        last_asst_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], AssistantMessage):
                last_asst_idx = i
                break
        if last_asst_idx is None:
            return None

        tool_ids = {
            block.id
            for block in messages[last_asst_idx].content
            if isinstance(block, ToolCallContent)
        }

        last_keep_idx = last_asst_idx
        for i in range(last_asst_idx + 1, len(messages)):
            if (
                isinstance(messages[i], ToolResultMessage)
                and messages[i].tool_call_id in tool_ids
            ):
                last_keep_idx = i

        cut = last_keep_idx
        for i in range(last_keep_idx - 1, -1, -1):
            if not isinstance(messages[i], CompactionSummaryMessage):
                cut = i + 1
                break
        else:
            cut = 0

        if cut < 2:
            return None
        return cut

    async def _generate_summary(self, messages: list) -> str:
        lines = []
        for m in messages:
            role = m.role
            text_parts = []
            for block in getattr(m, "content", []) or []:
                if isinstance(block, TextContent):
                    text_parts.append(block.text)
                elif isinstance(block, ThinkingContent):
                    text_parts.append(f"[thinking]{block.thinking}[/thinking]")
                elif isinstance(block, ToolCallContent):
                    text_parts.append(f"[tool_call: {block.name}({block.arguments})]")
            if isinstance(m, ToolResultMessage):
                result_text = "".join(
                    c.text for c in m.content if isinstance(c, TextContent)
                )
                text_parts.append(f"[tool_result: {result_text[:200]}]")
            if text_parts:
                lines.append(f"{role}: {' '.join(text_parts)}")

        summary_prompt = (
            "Summarize the following conversation concisely. "
            "Preserve all facts, decisions, file paths, and code references.\n\n"
            + "\n".join(lines)
        )
        llm_messages = [
            {"role": "system", "content": "You are a conversation summarizer."},
            {"role": "user", "content": summary_prompt},
        ]

        for _ in range(2):
            try:
                text_parts = []
                async for block in self.model_client.stream_complete(
                    llm_messages, max_new_tokens=1024, tools=None
                ):
                    if isinstance(block, TextContent):
                        text_parts.append(block.text)
                return "".join(text_parts)
            except Exception as e:
                log.warning(f"Summary generation failed: {e}")
                continue
        return ""

    async def _run_auto_compaction(self):
        msgs = self._agent._state.messages
        cut = self._select_cut_point(msgs)
        if cut is None or cut < 1:
            log.warning("Compaction skipped: no safe cut point found")
            return

        to_compress = msgs[:cut]
        to_keep = msgs[cut:]

        log.info(f"Compacting {len(to_compress)} messages...")
        summary = await self._generate_summary(to_compress)
        if not summary:
            log.warning("Compaction skipped: summary generation failed")
            return

        tokens_before = sum(
            m.usage.input_tokens for m in to_compress if hasattr(m, "usage") and m.usage
        )

        summary_msg = CompactionSummaryMessage(
            summary=summary,
            tokens_before=tokens_before,
        )

        self._agent._state.messages = [summary_msg] + to_keep
        log.info(
            f"Compaction done: {len(to_compress)} -> 1 summary, {len(to_keep)} kept"
        )

    async def _emit_ui(self, event):
        for listener in self._ui_listeners:
            result = listener(event)
            if asyncio.iscoroutine(result):
                await result

    def load_session(self, session_id: str) -> bool:
        """Restore messages from a previous session. Returns False if not found."""
        if not self._session_manager:
            return False
        msgs = self._session_manager.load(session_id)
        if msgs is None:
            return False
        self._agent._state.messages = msgs
        self._session_id = session_id
        return True

    def list_sessions(self):
        """列出所有已保存的会话。"""
        if not self._session_manager:
            return []
        return self._session_manager.list_sessions()

    async def _handle_event(self, event: AgentEvent):
        if isinstance(event, AgentEnd) and self._session_manager:
            merged = self._merge_messages(self._agent._state.messages)
            self._session_manager.save(self._session_id, merged)

        for listener in self._ui_listeners:
            result = listener(event)
            if asyncio.iscoroutine(result):
                await result

    @staticmethod
    def _merge_messages(messages: list) -> list:
        """合并消息中相邻同类型的 content block（stream 碎片 → 完整块）。"""
        from src.data.messages import AssistantMessage, TextContent, ThinkingContent

        out = []
        for msg in messages:
            if not isinstance(msg, AssistantMessage):
                out.append(msg)
                continue
            merged = []
            for block in getattr(msg, "content", []) or []:
                if (
                    isinstance(block, (TextContent, ThinkingContent))
                    and merged
                    and isinstance(block, type(merged[-1]))
                ):
                    prev = merged[-1]
                    if isinstance(block, ThinkingContent):
                        prev.thinking += block.thinking
                    else:
                        prev.text += block.text
                else:
                    merged.append(block)
            msg.content = merged
            out.append(msg)
        return out

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

    def _build_system_prompt(self):
        fix_prompt = self.build_prefix()

        current_branch = _git_output("git rev-parse --abbrev-ref HEAD") or "unknown"

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
        git_scope = _git_output("git rev-parse --show-toplevel") or cwd

        main_branch_raw = _git_output("git symbolic-ref refs/remotes/origin/HEAD")
        if main_branch_raw.startswith("refs/remotes/origin/"):
            main_branch = main_branch_raw[len("refs/remotes/origin/") :]
        else:
            main_branch = "main"

        return (
            f"You are Jarvis, a powerful AI coding Agent running in {cwd}.\n"
            f"\n"
            f"# Rules\n"
            f"1. Before starting the task, make sure you have a thorough understanding of the project.\n"
            f"2. When the user rejects a tool call and you do not understand why, you can ask the user for clarification.\n"
            f"\n"
            f"# Environment\n"
            f"Git scope: {git_scope}\n"
            f"Main branch: {main_branch}\n"
        )
