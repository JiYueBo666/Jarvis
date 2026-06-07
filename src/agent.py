"""Composition root: wires all dependencies together."""

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


class Agent:
    """Composition root. Initializes harness, passes it to Engine at call time."""

    def __init__(self, workspace_root: str | None = None, approval_policy: str = "auto"):
        self.workspace_root = workspace_root or os.getcwd()
        self.approval_policy = approval_policy
        self.session_id = (
            datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        )
        self.model_client = ModelClient(
            model=settings.SPEED_MODEL,
            base_url=settings.BASE_URL,
            api_key=settings.API_KEY,
        )
        self.tools = build_registry(workspace_root=self.workspace_root)
        self.executor = ToolExecutor(self.tools)
        self.ctx = ContextManager(self.executor, self.workspace_root)

        session_dir = Path(self.workspace_root) / ".jarvis" / "sessions" / self.session_id
        self.bus = SessionEventBus(
            session_id=self.session_id,
            path=str(session_dir / "events.jsonl"),
        )
        self.store = RunStore(session_dir)

    def ask(self, query: str) -> str:
        """Run one turn, return final answer text only. Record persisted."""
        answer = ""
        for event in self.ask_stream(query):
            if event["type"] in ("final", "step_limit", "error"):
                answer = event.get("text") or event.get("message") or "(no answer)"
        return answer

    def ask_stream(self, query: str):
        """Run one turn, yield progress events for real-time rendering.

        Side effects:
        - Writes every event to per-turn trace.jsonl (streaming event log)
        - Saves task_state.json on any event carrying a record (progressive snapshot)
        """
        task_id = None
        for event in Engine.run_stream(
            self.model_client, self.executor, self.ctx, self.bus, query,
            approval_policy=self.approval_policy,
        ):
            # Extract task_id — from top-level field, or from embedded record
            if not task_id:
                task_id = event.get("task_id", "")
            if not task_id and "record" in event:
                task_id = event["record"].get("task_id", "")

            # Write to per-turn trace.jsonl
            if task_id and event["type"] != "record":
                self.store.append_trace(task_id, event)

            # Progressive save: any event with record → update task_state.json
            if "record" in event:
                self.store.save_task_state(
                    event["record"]["task_id"], event["record"],
                )

            yield event
