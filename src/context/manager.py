import hashlib
import json
from pathlib import Path

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
        self._file_hashes: dict[str, str] = {}  # 路径 → SHA-256 hex
        # 初始化时立即构建系统提示，保证 /context 有数据
        self._messages = [
            {"role": "system", "content": self._system_prompt(), **self._cache_tag()},
        ]

    # ── lifecycle ────────────────────────────────────────────────

    def refresh(self):
        self._workspace = WorkspaceContext.build(self.workspace_root)

    @property
    def history(self) -> list[dict]:
        """暴露跨轮历史，供外部持久化。"""
        return list(self._history)

    def restore_history(self, messages: list[dict]):
        """从持久化存储加载历史，恢复跨轮对话上下文。"""
        self._history = list(messages)

    # ── 文件 SHA-256 追踪，用于跨模型调用的变更检测 ──

    def record_file_access(self, path: str):
        """记录模型接触过的文件的 SHA-256 hash。

        在工具执行（读/写文件）后调用，以便后续模型调用前
        重新 hash 对比，检测外部修改。
        """
        abs_path = str(Path(path).resolve())
        try:
            h = hashlib.sha256(Path(abs_path).read_bytes()).hexdigest()
        except OSError:
            h = ""
        self._file_hashes[abs_path] = h

    def check_file_changes(self) -> list[dict]:
        """重新计算所有已追踪文件的 hash，返回发生变更的文件列表。

        每条记录: {path, old_hash (前12位), new_hash (前12位)}
        同时就地更新 self._file_hashes 为最新值。
        """
        changes: list[dict] = []
        for path, old_hash in list(self._file_hashes.items()):
            try:
                new_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            except OSError:
                new_hash = ""
            if new_hash and new_hash != old_hash:
                changes.append(
                    {
                        "path": path,
                        "old_hash": old_hash[:12],
                        "new_hash": new_hash[:12],
                    }
                )
                self._file_hashes[path] = new_hash
        return changes

    def rebuild_system_prompt(self):
        """用最新工作区状态重建第一条 system 消息的内容。

        检测到外部文件变更后调用，相当于"刷新模型看到的对当前工作区的认知"。
        """
        self.refresh()
        if self._messages and self._messages[0].get("role") == "system":
            self._messages[0]["content"] = self._system_prompt()

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

    def preview_resume(self, last_state: dict):
        """加载恢复上下文到 messages，让 /context 能显示内容。用户发消息时 resume_turn 会替换它。"""
        self.refresh()
        system = self._system_prompt()
        resume_note = _build_resume_block(last_state)
        self._messages = [
            {
                "role": "system",
                "content": f"{system}\n\n{resume_note}",
                **self._cache_tag(),
            },
        ]

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

    def context_stats(self) -> dict:
        """分析当前各 section 的字符数和估算 token 占比。"""
        sections = {
            "system": [],
            "history": [],
            "user": [],
            "assistant": [],
            "tool": [],
        }
        order = ["system", "history", "user", "assistant", "tool"]
        # 找到 history 的起止位置（system 之后、最后一个 user 之前的非 system/非 user 消息）
        for m in self._messages:
            role = m.get("role", "")
            if role == "system":
                sections["system"].append(m)
            elif role == "user":
                sections["user"].append(m)
            elif role == "assistant":
                sections["assistant"].append(m)
            elif role == "tool":
                sections["tool"].append(m)

        # 第一条 user 之前的 assistant+tool 算 history
        # 但简单的做法：按 role 分组统计即可
        stats = {}
        total = 0
        for key in order:
            msgs = sections[key]
            chars = sum(len(m.get("content", "") or "") for m in msgs)
            # 加上 tool_calls 的参数字符
            for m in msgs:
                for tc in m.get("tool_calls") or []:
                    chars += len(tc.get("function", {}).get("arguments", "") or "")
            tokens = chars // 4  # 粗略估算
            stats[key] = {"messages": len(msgs), "chars": chars, "tokens": tokens}
            total += chars

        stats["_total"] = {"chars": total, "tokens": total // 4}
        # 计算百分比
        for key in order:
            if total > 0:
                stats[key]["pct"] = round(stats[key]["chars"] / total * 100, 1)
            else:
                stats[key]["pct"] = 0.0
        return stats

    def inject_plan(self, plan_content: str):
        """将已批准的计划注入系统提示末尾。

        在执行阶段（/execute 后）调用，追加到第一条 system 消息的末尾，
        后续模型每次调用都能看到计划内容。
        """
        if self._messages and self._messages[0].get("role") == "system":
            footer = "\n\n以下是被批准的执行计划:\n" + plan_content
            self._messages[0]["content"] += footer

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
        prompt = f"""You are Jarvis (J.A.R.V.I.S.), Tony Stark's super AI assistant. You are now assisting Mr. Stark with writing code.

    Use the available tools to read, write, and modify files.
    When the task is complete, return a final answer in natural language.
    Never invent tool results. Always ground your responses in the actual output of the tools you have used.

    【Core Persona & Tone】
    - You always address the user as "Sir", in the manner of a wise, composed, and impeccably tasteful British butler. Remain calm and poised in every situation.
    - Be concise and precise. Always deliver the conclusion or solution first, then offer supporting details — as if delivering a status report on the Iron Man suit.
    - You possess a refined dry wit and subtle British sarcasm. When Sir makes an obvious mistake, you point it out with respect but a touch of gentle teasing, e.g., "Sir, I feel obliged to mention that this semicolon appears to still be on holiday." Never be mean or cutting.
    - When Sir is frustrated or facing a setback, you offer steady reassurance: "We have all been there, Sir. Shall we take a moment, pour a cup of coffee, and then trace through the stack together?"

    【Tool Usage & Truthful Reporting】
    - You must always rely on the actual results returned by the tools. You will not fabricate, imagine, or embellish what the tools report.
    - Once the task is fully complete, deliver a final summary in natural language, in Jarvis's characteristic tone.

    【Proactive Coding Conduct】
    - You are a world-class coding partner. Anticipate Sir's needs. When you complete a core task, naturally supplement it with corresponding tests, optimisation suggestions, or a quiet alert about potential risks — just as you would monitor the suit's integrity.
    - If Sir's instruction could lead to a problem, you respectfully but directly intervene: "Sir, I must respectfully advise caution. This approach carries a risk of energy bleed under concurrent loads. I recommend adding a caching layer as a shield."
    - When an instruction is ambiguous, you never guess. Instead, you ask a precise clarifying question: "Sir, regarding the instruction to 'make it faster' — are we aiming to reduce time complexity, or to improve I/O throughput?"

    Current workspace:
    {self._workspace.text()}"""
        return prompt
