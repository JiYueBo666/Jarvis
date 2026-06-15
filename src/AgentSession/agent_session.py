"""
AgentSession — 核心全局调度器。

职责：
  - 用户输入预处理（斜杠命令、skill 展开）
  - 模型 / API key 验证
  - 上下文压缩（compaction）
  - 错误重试 + 指数退避
  - 消息持久化
  - 事件广播给 UI 订阅者
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from src.Agent.agent import CodingAgent
from src.Agent.loop import AgentLoopConfig
from src.Client.base import convert_to_llm
from src.Agent.models import Message as InternalMessage
from src.Client.base import StreamEvent
from src.Client.openai import OpenAIClient
from src.Sessions.session_manager import SessionManager
from src.Tools.base import Tool, ToolExecutor, ToolRegistry
from src.Tools.builtin import ReadFileTool, RunShellTool, SearchCodeTool, WriteFileTool

# ── 内置工具映射 ───────────────────────────────────

_BUILTIN_TOOLS: dict[str, type[Tool]] = {
    "read_file": ReadFileTool,
    "write_file": WriteFileTool,
    "run_shell": RunShellTool,
    "search_code": SearchCodeTool,
}


# ── AgentSession ──────────────────────────────────


class AgentSession:
    """顶层编排层：处理输入、调度 Agent、管理生命周期。"""

    def __init__(
        self,
        config: AgentLoopConfig,
        executor: ToolExecutor,
        session_manager: SessionManager | None = None,
        agent: CodingAgent | None = None,
    ):
        self.config = config
        self.executor = executor
        self.session_manager = session_manager or SessionManager()
        self.agent = agent or CodingAgent(config=config, executor=executor)

        # 当前激活的工具名列表
        self._tool_registry = ToolRegistry()
        self._active_tool_names: list[str] = ["read_file", "search_code"]
        self._init_tools()

        # 重试
        self._retry_attempts = 0
        self._max_retries = 3

        # UI 订阅者
        self._subscribers: list[Callable[[StreamEvent], Awaitable[None]]] = []

        # 上下文压缩
        self._compaction_threshold = 50_000  # 超过此 token 数触发压缩
        self._compacted = False

    # ── 订阅 ────────────────────────────────────────

    def subscribe(self, fn: Callable[[StreamEvent], Awaitable[None]]) -> None:
        """注册 UI 订阅者，收到消息广播。"""
        self._subscribers.append(fn)

    def unsubscribe(self, fn: Callable[[StreamEvent], Awaitable[None]]) -> None:
        if fn in self._subscribers:
            self._subscribers.remove(fn)

    async def _emit(self, event: StreamEvent) -> None:
        """广播事件给所有 UI 订阅者。"""
        for sub in self._subscribers:
            try:
                await sub(event)
            except Exception:
                pass

    # ── 工具管理 ────────────────────────────────────

    def _init_tools(self) -> None:
        """注册所有内置工具并设置初始激活工具。"""
        for cls in _BUILTIN_TOOLS.values():
            self._tool_registry.register(cls())
        self.set_active_tools_by_name(self._active_tool_names)

    def set_active_tools_by_name(self, names: list[str]) -> None:
        """启用指定工具列表，更新 system prompt。"""
        self._active_tool_names = list(names)
        tools: list[Tool] = []
        for name in names:
            cls = _BUILTIN_TOOLS.get(name)
            if cls:
                tools.append(cls())
        self.agent.state.tools = tools

    def get_active_tool_names(self) -> list[str]:
        return list(self._active_tool_names)

    def get_tool_definition(self, name: str) -> Tool | None:
        cls = _BUILTIN_TOOLS.get(name)
        return cls() if cls else None

    # ── 入口 ────────────────────────────────────────

    async def prompt(
        self,
        user_input: str,
        approval_check: Callable[[str, dict], Awaitable[bool]] | None = None,
    ) -> str | None:
        """用户输入入口。返回最终响应内容，或 None（skipped）。"""

        # 1. 检查斜杠命令
        if user_input.startswith("/"):
            return await self._handle_command(user_input)

        # 2. 验证配置
        if not self._validate_config():
            err = "API key 或模型未配置"
            await self._emit(StreamEvent("error", data=err))
            return err

        # 3. 检查是否需要压缩
        await self._check_compaction()

        # 4. 持久化用户消息
        self._persist_message("user", user_input)

        # 5. 调度 Agent
        return await self._run_agent_prompt(user_input, approval_check=approval_check)

    # ── 斜杠命令 ───────────────────────────────────

    async def _handle_command(self, cmd: str) -> str | None:
        parts = cmd.strip().split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        match command:
            case "/clear":
                self.agent.state.messages.clear()
                await self._emit(
                    StreamEvent(
                        "message_end",
                        message=InternalMessage(
                            role="assistant",
                            content="会话已清空",
                        ),
                    )
                )
                return "会话已清空"

            case "/tools":
                names = ", ".join(self._active_tool_names)
                text = f"当前工具: {names}"
                await self._emit(
                    StreamEvent(
                        "message_end",
                        message=InternalMessage(
                            role="assistant",
                            content=text,
                        ),
                    )
                )
                return text

            case "/tool_on":
                if arg:
                    self.set_active_tools_by_name([arg])
                return f"工具 [{arg}] 已启用"

            case "/retry":
                # 重用状态中的最后一条用户消息
                last_user = None
                for msg in reversed(self.agent.state.messages):
                    if msg.role == "user" and msg.content:
                        last_user = msg.content
                        break
                return (
                    await self._run_agent_prompt(last_user or "")
                    if last_user
                    else "没有可重试的消息"
                )

            case "/help":
                help_text = (
                    "/clear  清空对话历史\n"
                    "/tools  查看当前工具列表\n"
                    "/retry  重试上一次请求\n"
                    "/help   显示帮助"
                )
                await self._emit(
                    StreamEvent(
                        "message_end",
                        message=InternalMessage(
                            role="assistant",
                            content=help_text,
                        ),
                    )
                )
                return help_text

            case _:
                return None

    # ── 验证 ────────────────────────────────────────

    def _validate_config(self) -> bool:
        if not self.config.api_key:
            return False
        if not self.config.model:
            return False
        return True

    # ── 压缩 ────────────────────────────────────────

    async def _check_compaction(self) -> None:
        """估算 token 数，超过阈值时生成摘要并替换历史。"""
        total = self._estimate_tokens(self.agent.state.messages)
        if total < self._compaction_threshold:
            return

        # 保留 system prompt + 最近 N 轮 + 当前
        keep_last = 4
        if len(self.agent.state.messages) > keep_last:
            summary_parts = self.agent.state.messages[:-keep_last]
            recent = self.agent.state.messages[-keep_last:]

            summary_text = await self._generate_summary(summary_parts)
            summary_msg = InternalMessage(
                role="system",
                content=f"[上下文摘要]:\n{summary_text}",
            )
            self.agent.state.messages = [summary_msg] + recent
            self._compacted = True

            await self._emit(
                StreamEvent(
                    "message_end",
                    message=InternalMessage(
                        role="assistant",
                        content=f"📐 上下文已压缩，生成了 {len(summary_text)} 字符的摘要",
                    ),
                )
            )

    def _estimate_tokens(self, messages: list[InternalMessage]) -> int:
        """粗略估算 token 数量。"""
        total = 0
        for msg in messages:
            if msg.content:
                total += len(msg.content)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total += len(tc.name) + len(str(tc.arguments))
        return total

    async def _generate_summary(self, messages: list[InternalMessage]) -> str:
        """用 LLM 为历史消息生成摘要。"""
        texts = []
        for m in messages:
            if m.content:
                texts.append(f"[{m.role}] {m.content[:500]}")
        if not texts:
            return ""

        prompt = (
            "请总结以下对话历史的关键信息（技术决策、已解决的问题、当前状态）：\n\n"
            + "\n".join(texts[-20:])
        )

        try:
            client = self.config.client
            resp = await client.chat([InternalMessage(role="user", content=prompt)])
            return resp.content or "(摘要生成失败)"
        except Exception:
            return "(摘要生成失败)"

    # ── Agent 调度 ─────────────────────────────────

    async def _run_agent_prompt(
        self, user_input: str,
        approval_check: Callable[[str, dict], Awaitable[bool]] | None = None,
    ) -> str | None:
        """调用 Agent，带重试逻辑。"""
        self._retry_attempts = 0

        # 桥接：CodingAgent._listeners → AgentSession._subscribers
        async def _bridge(event: StreamEvent) -> None:
            await self._emit(event)
        self.agent.add_listener(_bridge)

        try:
            while self._retry_attempts < self._max_retries:
                try:
                    self.agent.state.reset_runtime()
                    msg_count = len(self.agent.state.messages)
                    result = await self.agent.prompt(user_input, approval_check=approval_check)

                    if result:
                        self._persist_message("assistant", result)

                    self._retry_attempts = 0
                    return result

                except Exception as e:
                    # 恢复消息长度（prompt() 可能已添加用户消息）
                    if len(self.agent.state.messages) > msg_count:
                        del self.agent.state.messages[msg_count:]
                    self._retry_attempts += 1
                    err = f"请求失败 (第 {self._retry_attempts} 次): {e}"
                    await self._emit(StreamEvent("error", data=err))

                    if self._retry_attempts >= self._max_retries:
                        return err

                    wait = 2 ** (self._retry_attempts - 1)
                    await asyncio.sleep(wait)
        finally:
            self.agent.remove_listener(_bridge)

    # ── 持久化 ──────────────────────────────────────

    def _persist_message(self, role: str, content: str) -> None:
        self.session_manager.append(
            {
                "role": role,
                "content": content,
                "time": datetime.now(timezone.utc).isoformat(),
                "model": self.config.model,
            }
        )

    # ── 重建 Agent ─────────────────────────────────

    @classmethod
    def create(
        cls,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
        session_dir: str | None = None,
    ) -> AgentSession:
        """便捷工厂方法。"""
        client = OpenAIClient(api_key=api_key, base_url=base_url, model=model)
        registry = ToolRegistry(Tool.collect())
        executor = ToolExecutor(registry)

        config = AgentLoopConfig(
            model=model,
            api_key=api_key,
            client=client,
            convert_to_llm=convert_to_llm,
        )

        session_mgr = SessionManager(session_dir)
        return cls(config=config, executor=executor, session_manager=session_mgr)
