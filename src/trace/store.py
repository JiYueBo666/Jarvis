import json
import tempfile
from pathlib import Path


class RunStore:
    """Persists per-turn records (TaskState) to disk."""

    def __init__(self, session_dir: str | Path):
        self.session_dir = Path(session_dir)
        self.turns_dir = self.session_dir / "turns"
        self.turns_dir.mkdir(parents=True, exist_ok=True)

    def save_task_state(self, task_id: str, data: dict) -> Path:
        turn_dir = self.turns_dir / task_id
        turn_dir.mkdir(parents=True, exist_ok=True)
        path = turn_dir / "task_state.json"
        self._write_json_atomic(path, data)
        return path

    def load_task_state(self, task_id: str) -> dict | None:
        path = self.turns_dir / task_id / "task_state.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_turns(self) -> list[str]:
        if not self.turns_dir.exists():
            return []
        return sorted(d.name for d in self.turns_dir.iterdir() if d.is_dir())

    def append_trace(self, task_id: str, event: dict) -> Path:
        """Append one event to the turn's trace.jsonl (streaming event log)."""
        turn_dir = self.turns_dir / task_id
        turn_dir.mkdir(parents=True, exist_ok=True)
        path = turn_dir / "trace.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
        return path

    # ── session 元数据 ──────────────────────────────────────

    def save_session_meta(self, data: dict) -> Path:
        path = self.session_dir / "session.json"
        self._write_json_atomic(path, data)
        return path

    def load_session_meta(self) -> dict | None:
        path = self.session_dir / "session.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def last_unfinished_task(self) -> dict | None:
        """返回最后一个状态为 running 的 task_state，用于恢复。"""
        for task_id in reversed(self.list_turns()):
            state = self.load_task_state(task_id)
            if state and state.get("status") == "running":
                return state
        return None

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False,
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp",
        ) as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            temp_name = handle.name
        Path(temp_name).replace(path)
