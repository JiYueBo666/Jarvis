"""
JSONL 会话持久化：追加写入、按时间范围查询、自动归档。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionManager:
    """基于 JSONL 的会话管理器。

    每行一个 JSON 对象，包含 role、content、time 等字段。
    """

    def __init__(self, session_dir: str | Path | None = None):
        self.session_dir = Path(session_dir) if session_dir else Path.cwd() / ".sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._current_file: Path | None = None
        self._session_id: str | None = None

    # ── 会话生命周期 ─────────────────────────────────

    def start_session(self, session_id: str | None = None) -> str:
        """开始新会话，返回 session_id。"""
        if session_id is None:
            session_id = datetime.now(timezone.utc).strftime("session_%Y%m%d_%H%M%S")
        self._session_id = session_id
        self._current_file = self.session_dir / f"{session_id}.jsonl"
        return session_id

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def current_file(self) -> Path | None:
        return self._current_file

    # ── 读写 ─────────────────────────────────────────

    def append(self, record: dict[str, Any]) -> None:
        """追加一条记录到当前会话文件。"""
        if self._current_file is None:
            self.start_session()
        with open(self._current_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        """读取当前会话的全部记录。"""
        if self._current_file is None or not self._current_file.exists():
            return []
        records: list[dict[str, Any]] = []
        with open(self._current_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def read_recent(self, n: int = 50) -> list[dict[str, Any]]:
        """读取最近 n 条记录。"""
        records = self.read_all()
        return records[-n:]

    # ── 管理 ─────────────────────────────────────────

    def list_sessions(self) -> list[Path]:
        """列出所有会话文件，按修改时间排序（最新的在前）。"""
        files = sorted(
            self.session_dir.glob("*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        return files

    def delete_session(self, session_id: str) -> bool:
        """删除指定会话文件。"""
        path = self.session_dir / f"{session_id}.jsonl"
        if path.exists():
            path.unlink()
            return True
        return False
