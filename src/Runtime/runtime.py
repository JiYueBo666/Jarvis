from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import textwrap
import uuid
from runtime_checkpoints import RuntimeCheckpointsMiXin
from runtime_secret import RuntimeSecretsMixin
from run_store import RunStore
from src.Runtime.runtime_consumers import default_runtime_consumers
from src.Runtime.runtime_events import build_runtime_event
from src.Runtime.task_state import TaskState
from src.core.compact import CompactManager
from src.core.context_manager import ContextManager
from src.core.permission_checker import PermissionChecker
from src.Tool.tool_ledger import TodoLedger
from src.Tool.tool_profile import build_tool_profiles
from src.core.turn_history import TurnHistoryBuilder
from src.core.worker_manager import WorkerManager, WorkerTask
from session_events import SessionEventBus
from src.core.plan_mode import PlanModeController
from src.Environment.workSpace import now
from src.Features import SandboxConfig, SandboxRunner
from src.Features import memory as memorylib
from src.Features import skills as skillslib
from src.core.engine import Engine
from src.Tool import registry as toolkit

DEFAULT_SHELL_ENV_ALLOWLIST = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "PWD",
    "SHELL",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "USER",
)

DEFAULT_FEATURE_FLAGS = {
    "memory": True,
    "relevant_memory": True,
    "context_reduction": True,
    "prompt_cache": True,
}
CHECKPOINT_SCHEMA_VERSION = "phase1-v1"
CHECKPOINT_NONE_STATUS = "no-checkpoint"
CHECKPOINT_FULL_VALID_STATUS = "full-valid"
CHECKPOINT_PARTIAL_STALE_STATUS = "partial-stale"
CHECKPOINT_WORKSPACE_MISMATCH_STATUS = "workspace-mismatch"
CHECKPOINT_SCHEMA_MISMATCH_STATUS = "schema-mismatch"


@dataclass
class PromptPrefix:
    # prefix 除了文本本身，还带一小份元数据，
    # 这样 runtime 才能明确判断 prefix 是否可以复用。
    text: str
    hash: str
    workspace_fingerprint: str
    tool_signature: str
    built_at: str


class Jarvis(RuntimeCheckpointsMiXin, RuntimeSecretsMixin):
    def __init__(
        self,
        model_client,
        workspace,
        session_store,
        session=None,
        run_store=None,
        approval_policy="ask",
        max_steps=50,
        max_new_tokens=8192,
        depth=0,
        max_depth=1,
        read_only=False,
        shell_env_allowlist=None,
        secret_env_names=None,
        feature_flags=None,
        write_scope=None,
        memory_dir=None,
        auto_dream=True,
        dream_interval_hours=24.0,
        dream_min_sessions=5,
        model_client_factory=None,
        sandbox_config=None,
        ask_user_callback=None,
        allowed_tools=None,
    ):
        self.model_client = model_client
        self.model_client_factory = model_client_factory
        self.abort_requested = False
        self.ask_user_callback = ask_user_callback
        self.sandbox_config = sandbox_config or SandboxConfig()
        self.sandbox_runner = SandboxRunner(
            self.sandbox_config,
            emit_event=lambda event, payload: self.session_event_bus.emit(
                event, payload
            ),
        )
        self.workspace = workspace
        self.root = Path(workspace.repo_root)
        self.session_store = session_store
        self.approval_policy = approval_policy
        self.max_steps = max_steps
        self.max_new_tokens = max_new_tokens
        self.depth = depth
        self.max_depth = max_depth
        self.read_only = read_only
        self.shell_env_allowlist = tuple(
            shell_env_allowlist or DEFAULT_SHELL_ENV_ALLOWLIST
        )
        self.secret_env_names = {str(name).upper() for name in (secret_env_names or ())}
        if isinstance(write_scope, str):
            write_scope = [write_scope]
        self.write_scope = tuple(
            str(path) for path in (write_scope or ()) if str(path).strip()
        )
        self.feature_flags = dict(DEFAULT_FEATURE_FLAGS)
        if feature_flags:
            self.feature_flags.update(
                {str(key): bool(value) for key, value in feature_flags.items()}
            )
        self.memory_dir = self._resolve_memory_dir(memory_dir)
        memorylib.ensure_memory_dir(self.memory_dir)
        self.auto_dream = bool(auto_dream)
        self.dream_interval_hours = float(dream_interval_hours)
        self.dream_min_sessions = int(dream_min_sessions)
        self.allowed_tools = self._normalize_allowed_tools(allowed_tools)
        self.run_store = run_store or RunStore(
            Path(workspace.repo_root) / ".jarvis" / "runs"
        )
        self.session = session or {
            "id": datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            "created_at": now(),
            "workspace_root": workspace.repo_root,
            "history": [],
            "memory": memorylib.default_memory_state(),
        }
        self._ensure_session_shape()
        self.session_event_bus = SessionEventBus(
            self.session["id"],
            self.session_store.event_path(self.session["id"]),
            redact=self.redact_artifact,
        )
        if (
            not self.session_event_bus.path.exists()
            or self.session_event_bus.path.stat().st_size == 0
        ):
            self.session_event_bus.emit(
                "session_started", {"workspace_root": workspace.repo_root}
            )
        self.plan_mode = PlanModeController(self)
        self.engine = Engine(self)
        self.memory = memorylib.LayeredMemory(
            self.session.setdefault("memory", memorylib.default_memory_state()),
            workspace_root=self.root,
        )
        self.session["memory"] = self.memory.to_dict()
        self.self_authored_file_freshness = {}
        self.todo_ledger = TodoLedger(self)
        self.worker_manager = WorkerManager(self)
        self.skills = skillslib.discover_skills(self.root)
        self.tools = self._apply_tool_allowlist(
            self.build_tools()
        )  # 通过允许的集合过滤一下
        self.tool_profiles = build_tool_profiles(
            self.tools
        )  # 按照模式作为关键字返回工具组
        self._active_tool_profile_name = (
            "plan"
            if self.runtime_mode == "plan"
            else "readonly" if self.read_only else "default"
        )
        self.permission_checker = PermissionChecker(self)
        self.prefix_state = self.build_prefix()
        self.prefix = self.prefix_state.text
        self.current_turn_id = ""
        self.current_run_id = ""
        self._trace_seq = 0
        self._last_trace_span_id = {}
        self.turn_history = TurnHistoryBuilder(self)
        self.compact_manager = CompactManager(self)
        self.runtime_consumers = default_runtime_consumers()
        self.context_manager = ContextManager(self)
        self.resume_state = self.evaluate_resume_state()
        self.session_path = self.session_store.save(self.session)
        self.current_task_state = None
        self.current_run_dir = None
        self.last_prompt_metadata = {}
        self.last_completion_metadata = {}
        self.last_durable_promotions = []
        self.last_durable_rejections = []
        self.last_durable_superseded = []
        self.last_memory_maintenance = memorylib.default_memory_maintenance_audit(
            auto_dream=self.auto_dream
        )
        self.last_dream_changed_files = []
        self._memory_maintenance_thread = None
        self._last_tool_result_metadata = {}
        self._last_prefix_refresh = {
            "workspace_changed": False,
            "prefix_changed": False,
        }

    @property
    def active_tool_profile(self):
        return self.tool_profiles[self._active_tool_profile_name]

    def emit_trace(self, task_state: TaskState, event: str, payload=None):
        payload = self.redact_artifact(payload or {})
        for path in payload.get("affected_paths", []) or []:
            if path not in task_state.changed_paths:
                task_state.changed_paths.append(path)
        payload = build_runtime_event(self, task_state, event, payload)
        self.run_store.append_trace(task_state, payload)
        for consumer in self.runtime_consumers:
            try:
                consumer.handle(self, task_state, payload)
            except Exception:
                continue
        self.run_store.write_task_state(task_state)
        return payload

    def invalidate_stale_memory(self):
        invalidated = self.memory.invalidate_stale_file_summaries()
        self.session["memory"] = self.memory.to_dict()
        return invalidated

    def evaluate_resume_state(self):
        """
              开始校验断点是否可恢复
        1. 清理过期记忆（防止旧数据干扰）
        2. 读取当前断点
        3. 检查断点版本是否兼容
        4. 检查关键文件：内容是否变化（freshness）
        5. 检查运行环境：模型、路径、权限、配置是否变化
        6. 得出最终状态：完全有效 / 部分文件过期 / 环境不匹配
        7. 把结果存到 resume_state，供后续恢复决策使用
        """
        previous_resume_state = dict(self.session.get("resume_state", {}) or {})
        invalidated = self.invalidate_stale_memory()
        checkpoint = self.current_checkpoint()
        status = CHECKPOINT_NONE_STATUS
        stale_paths = list(invalidated)
        mismatch_fields = []
        if checkpoint:
            if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                status = CHECKPOINT_SCHEMA_MISMATCH_STATUS
            else:
                for item in checkpoint.get("key_files", []):
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue
                    expected = item.get("freshness")
                    current = memorylib.file_freshness(path, self.root)
                    if expected != current and path not in stale_paths:
                        stale_paths.append(path)
                saved_identity = dict(
                    checkpoint.get("runtime_identity", {})
                    or self.session.get("runtime_identity", {})
                    or {}
                )
                current_identity = self.current_runtime_identity()
                identity_keys = (
                    "cwd",
                    "model",
                    "model_client",
                    "approval_policy",
                    "read_only",
                    "max_steps",
                    "max_new_tokens",
                    "feature_flags",
                    "shell_env_allowlist",
                    "workspace_fingerprint",
                    "tool_signature",
                )
                for key in identity_keys:
                    if key not in saved_identity:
                        continue
                    if saved_identity.get(key) != current_identity.get(key):
                        mismatch_fields.append(key)
                mismatch_fields.sort()
                if stale_paths:
                    status = CHECKPOINT_PARTIAL_STALE_STATUS
                elif mismatch_fields:
                    status = CHECKPOINT_WORKSPACE_MISMATCH_STATUS
                else:
                    status = CHECKPOINT_FULL_VALID_STATUS

        resume_state = {
            "status": status,
            "stale_paths": stale_paths,
            "runtime_identity_mismatch_fields": mismatch_fields,
            "stale_summary_invalidations": max(
                len(invalidated),
                (
                    int(previous_resume_state.get("stale_summary_invalidations", 0))
                    if status == CHECKPOINT_PARTIAL_STALE_STATUS
                    else 0
                ),
            ),
        }
        self.session["resume_state"] = resume_state
        self.session["runtime_identity"] = self.current_runtime_identity()
        return resume_state

    def build_prefix(self):
        tool_lines = []
        for name, tool in self.available_tools().items():
            fields = ", ".join(
                f"{key}: {value}" for key, value in tool["schema"].items()
            )
            risk = "approval required" if tool["risky"] else "safe"
            tool_lines.append(f"- {name}({fields}) [{risk}] {tool['description']}")
        tool_text = "\n".join(tool_lines)
        examples = "\n".join(
            [
                '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
                '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>',
                '<tool name="write_file" path="binary_search.py"><content>def binary_search(nums, target):\n    return -1\n</content></tool>',
                '<tool name="patch_file" path="binary_search.py"><old_text>return -1</old_text><new_text>return mid</new_text></tool>',
                '<tool>{"name":"run_shell","args":{"command":"uv run --with pytest python -m pytest -q","timeout":20}}</tool>',
                '<tool>{"name":"agent","args":{"description":"Inspect auth","prompt":"Find auth entry points","subagent_type":"Explore"}}</tool>',
                "<final>Done.</final>",
            ]
        )
        # prefix 可以理解成 agent 的“工作手册”：
        # 它是谁、工具怎么调用、当前仓库是什么状态，都写在这里。
        text = textwrap.dedent(f"""\
            You are pico, a small local coding agent working inside a local repository.

            Rules:
            - Use tools instead of guessing about the workspace.
            - Return exactly one <tool>...</tool> or one <final>...</final>.
            - Tool calls must look like:
              <tool>{{"name":"tool_name","args":{{...}}}}</tool>
            - For write_file and patch_file with multi-line text, prefer XML style:
              <tool name="write_file" path="file.py"><content>...</content></tool>
            - Final answers must look like:
              <final>your answer</final>
            - Never invent tool results.
            - Keep answers concise and concrete.
            - If the user asks you to create or update a specific file and the path is clear, use write_file or patch_file instead of repeatedly listing files.
            - Before writing tests for existing code, read the implementation first.
            - When writing tests, match the current implementation unless the user explicitly asked you to change the code.
            - New files should be complete and runnable, including obvious imports.
            - Do not repeat the same tool call with the same arguments if it did not help. Choose a different tool or return a final answer.
            - Required tool arguments must not be empty. Do not call read_file, write_file, patch_file, run_shell, or agent with args={{}}.
            - Use agent for bounded subagents. Explore is read-only; worker writes must stay inside write_scope.
            - Use send_message to continue an existing worker instead of spawning a fresh worker with missing context.
            - {skillslib.SKILL_FILE_CREATION_GUIDE}

            {self.runtime_mode_text()}

            Tools:
            {tool_text}

            Valid response examples:
            {examples}

            {self.workspace.text()}
            """).strip()
        return PromptPrefix(
            text=text,
            hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            workspace_fingerprint=self.workspace.fingerprint(),
            tool_signature=self.tool_signature(),
            built_at=now(),
        )

    def build_tools(self):
        return toolkit.build_tool_registry(self)

    def _apply_tool_allowlist(self, tools):
        if self.allowed_tools is None:
            return tools
        unknown = [name for name in self.allowed_tools if name not in tools]
        if unknown:
            raise ValueError(f"unknown allowed tool: {', '.join(unknown)}")
        allowed = set(self.allowed_tools)
        return {name: tool for name, tool in tools.items() if name in allowed}

    def _resolve_memory_dir(self, memory_dir):
        if memory_dir:
            path = Path(memory_dir).expanduser()
            path = path if path.is_absolute() else self.root / path
        else:
            path = self.root / ".jarvis" / "memory"
        resolved = path.resolve()
        # 找公共父目录
        if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
            raise ValueError(f"memory_dir must stay inside workspace: {memory_dir}")
        return resolved

    @staticmethod
    def _normalize_allowed_tools(allowed_tools):
        """
        清理下工具格式,返回tuple
        """
        if allowed_tools is None:
            return None
        normalized = tuple(str(name).strip() for name in allowed_tools)
        if not normalized or any(not name for name in normalized):
            raise ValueError("allowed_tools must be a non-empty sequence of tool names")
        return normalized

    def _ensure_session_shape(self):
        """
        确保安全初始化
        """
        self.session.setdefault("history", [])  # 有则返回，无则创建
        self.session.setdefault("memory", memorylib.default_memory_state())
        checkpoints = self.session.setdefault("checkpoints", {})
        if not isinstance(checkpoints, dict):
            checkpoints = {}
            self.session["checkpoints"] = checkpoints
        checkpoints.setdefault("current_id", "")
        checkpoints.setdefault("items", {})
        runtime_identity = self.session.setdefault("runtime_identity", {})
        if not isinstance(runtime_identity, dict):
            self.session["runtime_identity"] = {}
        resume_state = self.session.setdefault("resume_state", {})
        if not isinstance(resume_state, dict):
            self.session["resume_state"] = {}
        runtime_mode = self.session.setdefault("runtime_mode", {"mode": "default"})
        if not isinstance(runtime_mode, dict):
            self.session["runtime_mode"] = {"mode": "default"}

    def new_task_id():
        return (
            "task_"
            + datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6]
        )

    @staticmethod
    def new_run_id():
        return (
            "run_"
            + datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6]
        )
