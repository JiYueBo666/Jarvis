import json

from src.context.compact import compact_history
from src.engine.executor import ToolExecutor
from src.workspace import WorkspaceContext


def _build_resume_block(state: dict) -> str:
    """根据上次中断的 task_state 构建恢复上下文。"""
    lines = ["[会话恢复 —— 上次执行被中断]"]
    if state.get("user_request"):
        lines.append(f"原始需求: {state['user_request']}")
    if state.get("completed_goals"):
        lines.append(f"已完成: {', '.join(state['completed_goals'])}")
    if state.get("last_tool"):
        lines.append(
            f"最后工具: {state['last_tool']}（步数 {state.get('tool_steps', 0)}）"
        )
    touched = state.get("touched_files", [])
    if touched:
        lines.append(f"接触文件: {', '.join(f['path'] for f in touched)}")
    errors = state.get("errors", [])
    if errors:
        lines.append(f"错误记录: {len(errors)} 个")
    lines.append("\n继续之前的工作。重新检查文件状态后再操作。")
    return "\n".join(lines)


class ContextManager:
    """Owns the message list and all message format details.

    Cross-turn history is accumulated in _history.  Each turn starts with
    system + compacted history + user query.  After the turn, finish_turn()
    migrates the turn's messages into _history.
    """

    def __init__(
        self,
        executor: ToolExecutor,
        workspace_root: str,
        prompt_caching: bool = True,
        history_budget: int = 50000,
    ):
        self.executor = executor
        self.workspace_root = workspace_root
        self._workspace = WorkspaceContext.build(workspace_root)
        self._history: list[dict] = []
        self._messages: list[dict] = []
        self._prompt_caching = prompt_caching
        self._history_budget = history_budget

    # ── lifecycle ────────────────────────────────────────────────

    def refresh(self):
        self._workspace = WorkspaceContext.build(self.workspace_root)

    def _cache_tag(self) -> dict:
        """返回 cache_control 标记，仅当启用 prompt_caching 时生效。"""
        return {"cache_control": {"type": "ephemeral"}} if self._prompt_caching else {}

    def start_turn(self, query: str):
        """Begin a new turn: system prompt → compacted history → user query.

        稳定部分（系统 + 历史摘要）标记 cache_control；当前用户请求不标记。
        """
        self.refresh()
        history_msgs = compact_history(self._history, budget=self._history_budget)
        # 给历史消息加上缓存标记（它们跨模型调用不变）
        for m in history_msgs:
            m.update(self._cache_tag())
        self._messages = [
            {"role": "system", "content": self._system_prompt(), **self._cache_tag()},
            *history_msgs,
            {"role": "user", "content": query},  # 不标记——每轮变化
        ]

    def finish_turn(self):
        """Move this turn's messages (everything but system) into cross-turn history."""
        turn_msgs = self._messages[1:]  # drop system
        self._history.extend(turn_msgs)

    def resume_turn(self, last_state: dict, query: str):
        """重建一轮被中断的对话：系统提示（含恢复上下文）+ 用户消息。"""
        self.refresh()
        system = self._system_prompt()
        resume_note = _build_resume_block(last_state)
        self._messages = [
            {
                "role": "system",
                "content": f"{system}\n\n{resume_note}",
                **self._cache_tag(),
            },
            {"role": "user", "content": query},
        ]

    # ── read ─────────────────────────────────────────────────────

    @property
    def messages(self) -> list[dict]:
        return list(self._messages)

    # ── append ───────────────────────────────────────────────────

    def append_assistant(self, text: str, tool_calls: list[dict] | None = None):
        msg: dict = {"role": "assistant", "content": text}
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["args"]),
                    },
                }
                for tc in tool_calls
            ]
        self._messages.append(msg)

    def append_tool_result(self, tool_call_id: str, content: str):
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
        )

    # ── internals ────────────────────────────────────────────────

    def _system_prompt(self) -> str:
        return (
            "You are Jarvis, Tony Stark's super AI assistant. You are now helping Tony write code..\n"
            "Use the available tools to read, write, and modify files.\n"
            "When the task is complete, return a final answer in natural language.\n"
            "Never invent tool results.\n"
            f"\nCurrent workspace:\n{self._workspace.text()}"
        )
