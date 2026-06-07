import json

from src.context.compact import compact_history
from src.engine.executor import ToolExecutor
from src.workspace import WorkspaceContext


class ContextManager:
    """Owns the message list and all message format details.

    Cross-turn history is accumulated in _history.  Each turn starts with
    system + compacted history + user query.  After the turn, finish_turn()
    migrates the turn's messages into _history.
    """

    def __init__(self, executor: ToolExecutor, workspace_root: str):
        self.executor = executor
        self.workspace_root = workspace_root
        self._workspace = WorkspaceContext.build(workspace_root)
        self._history: list[dict] = []
        self._messages: list[dict] = []

    # ── lifecycle ────────────────────────────────────────────────

    def refresh(self):
        self._workspace = WorkspaceContext.build(self.workspace_root)

    def start_turn(self, query: str):
        """Begin a new turn: system prompt → compacted history → user query."""
        self.refresh()
        history_msgs = compact_history(self._history)
        self._messages = [
            {"role": "system", "content": self._system_prompt()},
            *history_msgs,
            {"role": "user", "content": query},
        ]

    def finish_turn(self):
        """Move this turn's messages (everything but system) into cross-turn history."""
        turn_msgs = self._messages[1:]  # drop system
        self._history.extend(turn_msgs)

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
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    # ── internals ────────────────────────────────────────────────

    def _system_prompt(self) -> str:
        return (
            "You are jarvis, a small local coding agent.\n"
            "Use the available tools to read, write, and modify files.\n"
            "When the task is complete, return a final answer in natural language.\n"
            "Never invent tool results.\n"
            f"\nCurrent workspace:\n{self._workspace.text()}"
        )
