from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"
STOP_REASON_FINAL_ANSWER_RETURNED = "final_answer_returned"
STOP_REASON_STEP_LIMIT_REACHED = "step_limit_reached"


@dataclass
class TouchedFile:
    path: str
    operation: str  # "read" | "write" | "patch"
    summary: str = ""  # model's understanding of the file (populated later)


@dataclass
class TaskState:
    """Per-ask() observation log. Created at start of a turn, finalized at end."""

    run_id: str
    task_id: str
    user_request: str
    status: str = STATUS_RUNNING
    tool_steps: int = 0
    attempts: int = 0
    last_tool: str = ""
    stop_reason: str = ""
    final_answer: str = ""
    # progress tracking
    in_progress: str = ""
    current_blocker: str = ""
    completed_goals: list[str] = field(default_factory=list)
    # observability
    touched_files: list[TouchedFile] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)

    # ── lifecycle ────────────────────────────────────────────

    @classmethod
    def create(cls, user_request: str, run_id: str = ""):
        task_id = (
            "task_" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        )
        if not run_id:
            run_id = (
                "run_" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
            )
        return cls(run_id=run_id, task_id=task_id, user_request=user_request)

    # ── mutations ───────────────────────────────────────────

    def record_attempt(self):
        self.attempts += 1
        return self

    def record_tool(self, name: str):
        self.tool_steps += 1
        self.last_tool = str(name or "")
        return self

    def stop(self, stop_reason: str, status: str = STATUS_STOPPED, final_answer: str = ""):
        self.status = status
        self.stop_reason = stop_reason
        if final_answer:
            self.final_answer = final_answer
        return self

    def finish_success(self, final_answer: str):
        self.status = STATUS_COMPLETED
        self.stop_reason = STOP_REASON_FINAL_ANSWER_RETURNED
        self.final_answer = str(final_answer)
        return self

    def record_touched_file(self, path: str, operation: str):
        # deduplicate: update operation if already touched
        for f in self.touched_files:
            if f.path == path:
                f.operation = operation
                return
        self.touched_files.append(TouchedFile(path=path, operation=operation))

    def record_error(self, tool_name: str, error_code: str, message: str):
        self.errors.append({
            "tool": tool_name,
            "code": error_code,
            "message": message,
        })

    # ── serialization ───────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "user_request": self.user_request,
            "status": self.status,
            "tool_steps": self.tool_steps,
            "attempts": self.attempts,
            "last_tool": self.last_tool,
            "stop_reason": self.stop_reason,
            "final_answer": self.final_answer,
            "in_progress": self.in_progress,
            "current_blocker": self.current_blocker,
            "completed_goals": list(self.completed_goals),
            "touched_files": [
                {"path": f.path, "operation": f.operation, "summary": f.summary}
                for f in self.touched_files
            ],
            "errors": list(self.errors),
            "changed_paths": list(self.changed_paths),
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            run_id=str(data.get("run_id", "")),
            task_id=str(data.get("task_id", "")),
            user_request=str(data.get("user_request", "")),
            status=str(data.get("status", STATUS_RUNNING)),
            tool_steps=int(data.get("tool_steps", 0)),
            attempts=int(data.get("attempts", 0)),
            last_tool=str(data.get("last_tool", "")),
            stop_reason=str(data.get("stop_reason", "")),
            final_answer=str(data.get("final_answer", "")),
            in_progress=str(data.get("in_progress", "")),
            current_blocker=str(data.get("current_blocker", "")),
            completed_goals=list(data.get("completed_goals", [])),
            touched_files=[
                TouchedFile(**f) for f in data.get("touched_files", [])
            ],
            errors=list(data.get("errors", [])),
            changed_paths=list(data.get("changed_paths", [])),
        )
