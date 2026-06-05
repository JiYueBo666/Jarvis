"""
运行跟踪是面向单任务、用于诊断的；会话事件总线则是支撑交互式会话本身、具备持久化能力的粗粒度时间线。
"""

import json
from pathlib import Path

from src.Environment.workSpace import now


class SessionEventBus:
    def __init__(self, session_id, path, redact=None):
        self.session_id = str(session_id)
        self.path = Path(path)
        self.redact = redact or (lambda value: value)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, payload: dict = None):
        record = dict(payload or {})
        record["event"] = str(event)
        record["session_id"] = self.session_id
        record["created_at"] = now()
        record = self.redact(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, sort_keys=True) + "\n")
        return record
