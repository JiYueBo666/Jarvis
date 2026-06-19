from typing import Literal
from pathlib import Path
from dataclasses import dataclass
from pydantic import BaseModel
import uuid


class TaskStatus(BaseModel):
    """Task status class."""

    status: Literal["idle", "pending", "running", "completed", "failed"] = "idle"
    current_task: str | None = None
    task_id: str | None = None
    task_attempts: int = 0  # 尝试次数
    edit_files: list[str] = []
    tool_steps: int = 0
    last_tool: str | None = None
    stop_reason: str | None = None
    final_answer: str | None = None


class TaskManager:
    """Task manager for managing tasks."""

    def __init__(self, task_dir: str):
        self.task_status: TaskStatus = TaskStatus()
        self.task_id = None
        self.session_dir = (
            Path(task_dir) if task_dir else Path.cwd() / ".jarvis" / "tasks"
        )

    def record_task(self, query: str):
        """
        负责将用户的任务记录在盘
        """
        self.task_status.current_task = query.strip()
        task_id = _new_id()
        self.task_status.task_id = task_id
        self.task_status.status = "pending"
        self._save()
        return self.task_status.task_id

    def record_tool(self, tool_name: str, affected_files: list[str] | None = None):
        """记录一次工具调用。"""
        self.task_status.tool_steps += 1
        self.task_status.last_tool = tool_name
        if affected_files:
            self.task_status.edit_files.extend(affected_files)
        self.task_status.status = "running"
        self._save()

    def finish(self, status: str = "completed", **kwargs):
        """标记任务完成/失败。"""
        self.task_status.status = status
        for k, v in kwargs.items():
            if hasattr(self.task_status, k):
                setattr(self.task_status, k, v)
        self._save()

    @property
    def status(self):
        """
        获取当前状态
        """
        return self.task_status.status

    def _to_dict(self):
        dict_status = self.task_status.model_dump()  # 作为字典
        return dict_status

    def to_json(self):
        return self.task_status.model_dump_json(indent=2)

    def _save(self):
        self.session_dir.mkdir(parents=True, exist_ok=True)
        path = self.session_dir / f"{self.task_status.task_id}.json"
        path.write_text(self.to_json(), encoding="utf-8")


def _new_id() -> str:
    return uuid.uuid4().hex[:8]
