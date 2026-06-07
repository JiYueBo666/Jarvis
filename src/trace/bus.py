import json
from pathlib import Path

from src.workspace import now


class SessionEventBus:
    """Append-only JSONL event log for a session.

    Every turn emits events here. Downstream consumers
    (checkpointer, report builder) read from this stream.
    """

    def __init__(self, session_id: str, path: str | Path):
        self.session_id = session_id
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, payload: dict | None = None) -> dict:
        record = {
            "event": event,
            "session_id": self.session_id,
            "created_at": now(),
            **(payload or {}),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        return record
