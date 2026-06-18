import os
from pathlib import Path
import uuid

# 当前文件 session.py 的目录：Agent-mini/src/content
CUR_FILE = Path(__file__).resolve()
# 项目根目录：Agent-mini
PROJECT_ROOT = CUR_FILE.parent.parent.parent


class SessionManager:
    def __init__(self):
        self.session_store_path = PROJECT_ROOT / ".jarvis/sessions"
        self.session_store_path.mkdir(parents=True, exist_ok=True)

    def get_session_path(self, session_id: str) -> Path:
        return self.session_store_path / f"{session_id}.jsonl"

    @property
    def session_id(self):
        # 用UUID生成session id
        return str(uuid.uuid4())

    def save_session(self, session_id: str):
        session_path = self.get_session_path(session_id)
        session_path.mkdir(parents=True, exist_ok=True)

        path = session_id.jsonl
        # 流式追加，写入session jsonl
        with open(session_path, "a") as f:
            f.write(session_id)

    def load_session(self, session_id: str):
        session_path = self.get_session_path(session_id)
        hitory = []

        with open(session_path, "r") as f:
            for line in f:
                hitory.append(line)

        return hitory
