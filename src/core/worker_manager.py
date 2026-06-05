from dataclasses import dataclass
import json
import queue
import threading


@dataclass
class WorkerTask:
    id: str
    description: str
    subagent_type: str
    write_scope: tuple[str, ...]
    runtime: object
    thread: threading.Thread | None = None
    stop_requested: bool = False
    state: dict = field(default_factory=dict)


class WorkerManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self.runtime.session.setdefault("workers", {"next_id": 1, "items": []})
        self._tasks = {}
        self._lock = threading.Lock()
        self._notifications = queue.Queue()


def dumps_payload(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
