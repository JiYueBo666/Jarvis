"""组合根：串联所有依赖。"""

import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.config import settings
from src.context.manager import ContextManager
from src.engine.executor import ToolExecutor
from src.engine.loop import Engine
from src.engine.model import ModelClient
from src.tools import build_registry
from src.trace.bus import SessionEventBus
from src.trace.store import RunStore

SESSIONS_DIR_NAME = ".jarvis/sessions"


class Agent:
    """组合根。初始化所有组件，在 call time 传给 Engine。"""

    def __init__(
        self,
        workspace_root: str | None = None,
        approval_policy: str = "auto",
        session_dir: str | None = None,
        prompt_caching: bool = True,
        max_new_tokens: int = 8192,
        history_budget: int = 50000,
    ):
        self.workspace_root = workspace_root or os.getcwd()
        self.approval_policy = approval_policy
        self.prompt_caching = prompt_caching
        self.max_new_tokens = max_new_tokens
        self.history_budget = history_budget
        self._resume_state: dict | None = None

        if session_dir:
            self._load_existing_session(session_dir)
        else:
            self._new_session()

        self.model_client = ModelClient(
            model=settings.SPEED_MODEL,
            base_url=settings.BASE_URL,
            api_key=settings.API_KEY,
        )
        self.tools = build_registry(workspace_root=self.workspace_root)
        self.executor = ToolExecutor(self.tools)
        self.ctx = ContextManager(
            self.executor, self.workspace_root,
            prompt_caching=prompt_caching, history_budget=history_budget,
        )

    # ── 会话管理 ────────────────────────────────────────────

    @staticmethod
    def list_sessions(workspace_root: str | None = None) -> list[dict]:
        """扫描所有会话目录，返回按时间降序排列的会话列表。"""
        root = Path(workspace_root or os.getcwd()) / SESSIONS_DIR_NAME
        if not root.exists():
            return []
        sessions = []
        for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            meta_path = d / "session.json"
            if meta_path.exists():
                import json

                sessions.append(json.loads(meta_path.read_text(encoding="utf-8")))
            else:
                sessions.append(
                    {"session_id": d.name, "created_at": "", "turn_count": 0}
                )
        return sessions

    def _new_session(self):
        self.session_id = (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        )
        session_dir = Path(self.workspace_root) / SESSIONS_DIR_NAME / self.session_id
        self.bus = SessionEventBus(
            session_id=self.session_id,
            path=str(session_dir / "events.jsonl"),
        )
        self.store = RunStore(session_dir)
        self.store.save_session_meta(
            {
                "session_id": self.session_id,
                "created_at": datetime.now().isoformat(),
                "workspace_root": self.workspace_root,
                "turn_count": 0,
                "status": "active",
            }
        )

    def _load_existing_session(self, session_dir: str):
        path = Path(session_dir)
        meta = json.loads((path / "session.json").read_text(encoding="utf-8"))
        self.session_id = meta.get("session_id", path.name)
        self.bus = SessionEventBus(
            session_id=self.session_id,
            path=str(path / "events.jsonl"),
        )
        self.store = RunStore(path)
        # 检查是否有未完成的任务
        self._resume_state = self.store.last_unfinished_task()

    # ── 执行 ────────────────────────────────────────────────

    def ask(self, query: str) -> str:
        answer = ""
        for event in self.ask_stream(query):
            if event["type"] in ("final", "step_limit", "error"):
                answer = event.get("text") or event.get("message") or "(no answer)"
        return answer

    def ask_stream(self, query: str):
        """执行一轮对话，yield 进度事件。

        副作用：
        - 写 per-turn trace.jsonl
        - 渐进更新 task_state.json
        """
        # 判断是否需要恢复
        if self._resume_state:
            self.ctx.resume_turn(self._resume_state, query)
            self._resume_state = None  # 只恢复一次
        else:
            self.ctx.start_turn(query)

        task_id = None
        for event in Engine.run_stream(
            self.model_client, self.executor, self.ctx, self.bus, query,
            max_new_tokens=self.max_new_tokens,
            approval_policy=self.approval_policy,
        ):
            # 提取 task_id
            if not task_id:
                task_id = event.get("task_id", "")
            if not task_id and "record" in event:
                task_id = event["record"].get("task_id", "")

            # 写 trace.jsonl
            if task_id and event["type"] != "record":
                self.store.append_trace(task_id, event)

            # 渐进保存 task_state.json
            if "record" in event:
                self.store.save_task_state(event["record"]["task_id"], event["record"])

            yield event
