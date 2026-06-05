"""多步 agent 运行时使用的轻量工作记忆。

session history 负责保存完整事件流；这个模块只保存更小的一层工作集：
当前任务摘要、最近接触的文件、文件短摘要，以及少量跨轮笔记。
这样下一轮 prompt 还能接上上一轮，但不会被整段历史塞满。
"""

import hashlib
import json
import os
import threading
from datetime import date, datetime
import re
from pathlib import Path

from src.Environment.workSpace import clip, now

WORKING_FILE_LIMIT = 8
EPISODIC_NOTE_LIMIT = 12
FILE_SUMMARY_LIMIT = 6
MAX_MEMORY_INDEX_CHARS = 10000
MAX_ENTRYPOINT_LINES = 200
ENTRYPOINT_NAME = "MEMORY.md"
LOCK_FILE_NAME = ".consolidate-lock"
HOLDER_STALE_S = 3600


# 单次 dream 最多消化的 session 数。超出时 dream prompt 只列最近 N 个，
# 防止 75+ session ID 撑爆模型上下文导致 empty_response。
DREAM_SESSION_CAP = 30
# dream 任务需要更多输出 token（要写多个 topic 文件 + 更新索引）。
DREAM_MIN_NEW_TOKENS = 4096


DURABLE_MEMORY_INTENT_PATTERN = re.compile(
    r"(?i)\b(capture|remember|save|store|persist|note)\b"
)
DURABLE_MEMORY_INTENT_ZH_PATTERN = re.compile(
    r"(记住|保存|记录|沉淀|长期记忆|持久记忆)"
)
DURABLE_MEMORY_LIST_PREFIX_PATTERN = re.compile(r"^(?:[-*]|\d+[.)])\s+")
DURABLE_MEMORY_LINE_PATTERNS = (
    ("project-conventions", re.compile(r"(?i)^Project convention:\s*(.+)$")),
    ("key-decisions", re.compile(r"(?i)^Decision:\s*(.+)$")),
    ("dependency-facts", re.compile(r"(?i)^Dependency:\s*(.+)$")),
    ("user-preferences", re.compile(r"(?i)^Preference:\s*(.+)$")),
    ("project-conventions", re.compile(r"^项目约定：\s*(.+)$")),
    ("key-decisions", re.compile(r"^决策：\s*(.+)$")),
    ("dependency-facts", re.compile(r"^依赖：\s*(.+)$")),
    ("user-preferences", re.compile(r"^偏好：\s*(.+)$")),
)
SECRET_SHAPED_TEXT_PATTERN = re.compile(
    r"(?i)(\b(api[_ -]?key|token|secret|password)\b|sk-[A-Za-z0-9_-]{6,})"
)
DURABLE_TOPIC_DEFAULTS = {
    "project-conventions": {
        "title": "Project Conventions",
        "summary": "Stable repository conventions.",
        "tags": ["convention"],
    },
    "key-decisions": {
        "title": "Key Decisions",
        "summary": "Long-lived decisions and rationale anchors.",
        "tags": ["decision"],
    },
    "dependency-facts": {
        "title": "Dependency Facts",
        "summary": "Stable dependency and environment facts.",
        "tags": ["dependency"],
    },
    "user-preferences": {
        "title": "User Preferences",
        "summary": "Stable user preferences.",
        "tags": ["preference"],
    },
}


def ensure_memory_dir(memory_dir):
    """
    创建记忆文件相关
    """
    memory_dir = Path(memory_dir)
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "logs").mkdir(parents=True, exist_ok=True)
    (memory_dir / "topics").mkdir(parents=True, exist_ok=True)
    # Memory.md
    index_path = memory_dir / ENTRYPOINT_NAME
    if not index_path.exists():
        index_path.write_text(
            "# Durable Memory Index\n\n"
            "_Empty. `/remember` writes a daily log entry; `/dream` consolidates "
            "logs into topic files and adds entries here._\n",
            encoding="utf-8",
        )
    return memory_dir


def daily_log_path(memory_dir, today=None):
    """
    返回日常记录md路径
    """
    today = today or date.today()
    memory_dir = ensure_memory_dir(memory_dir)
    path = (
        memory_dir
        / "logs"
        / str(today.year)
        / f"{today.month:02d}"
        / f"{today.isoformat()}.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_to_daily_log(memory_dir, entry, today=None):
    """
    写入daliy log
    """
    entry = str(entry).strip()
    if not entry:
        return None
    path = daily_log_path(memory_dir, today=today)
    timestamp = datetime.now().strftime("%H:%M")
    with path.open("a", encoding="utf-8") as file:
        file.write(f"- [{timestamp}] {entry}\n")
    return path


def default_memory_maintenance_audit(auto_dream=True):
    return {
        "memory_tags_appended": [],
        "auto_dream": {
            "enabled": bool(auto_dream),
            "triggered": False,
            "skip_reason": "",
            "session_count": 0,
            "session_ids": [],
            "changed_files": [],
        },
        "errors": [],
    }


def _agent_relative_path(agent, path):
    """
    把一个任意路径 → 变成【相对于 agent 根目录的相对路径】 → 并且统一成 Linux 风格路径
    """
    try:
        return Path(path).resolve().relative_to(agent.root).as_posix()
    except ValueError:
        return str(path)


def _memory_file_snapshot(agent):
    """
    其主要功能是为指定智能体的内存目录生成一个文件快照。
    这个快照以字典的形式返回，
    记录了目录下所有有效文件的相对路径及其对应的 SHA-256 哈希值。
    """
    memory_dir = Path(agent.memory_dir)
    if not memory_dir.exists():
        return {}
    snapshot = {}
    for path in memory_dir.rglob("*"):
        if not path.is_file() or path.name == LOCK_FILE_NAME:
            continue
        relative = _agent_relative_path(agent, path)
        try:
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return snapshot


def _changed_memory_files(before, after):
    """
    |为并集
    """
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )


def _emit_memory_trace(agent, event, payload):
    task_state = getattr(agent, "current_task_state", None)
    if task_state is None:
        return None
    return agent.emit_trace(task_state, event, payload)


def _write_memory_maintenance_report(agent, task_state, audit):
    try:
        if agent.run_store.report_path(task_state).exists():
            report = agent.run_store.load_report(task_state)
        else:
            report = agent.build_report(task_state)
    except (OSError, json.JSONDecodeError):
        report = agent.build_report(task_state)
    report["memory_maintenance"] = dict(audit)
    agent.run_store.write_report(task_state, agent.redact_artifact(report))


class DurableMemoryStore:
    def __init__(self, root):
        self.root = Path(root)
        self.index_path = self.root / "MEMORY.md"
        self.topics_dir = self.root / "topics"

    def topic_slugs(self):
        return [topic["topic"] for topic in self.load_index()]

    def load_index(self):
        if not self.index_path.exists():
            return []
        lines = self.index_path.read_text(encoding="utf-8").splitlines()
        topics = []
        current = None
        for raw in lines:
            line = raw.strip()
            match = re.match(r"- \[([^\]]+)\]\([^)]+\):\s*(.+)", line)
            if match:
                current = {
                    "topic": match.group(1).strip(),
                    "title": match.group(2).strip(),
                    "summary": "",
                    "tags": [],
                }
                topics.append(current)
                continue
            if current is None:
                continue
            summary_match = re.match(r"- summary:\s*(.+)", line)
            if summary_match:
                current["summary"] = summary_match.group(1).strip()
                continue
            tags_match = re.match(r"- tags:\s*(.+)", line)
            if tags_match:
                current["tags"] = [
                    tag.strip() for tag in tags_match.group(1).split(",") if tag.strip()
                ]
        return topics

    def load_topic_notes(self, topic):
        path = self.topics_dir / f"{topic}.md"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        notes = []
        capture = False
        updated_at = ""
        tags = []
        for raw in lines:
            line = raw.strip()
            if line.startswith("- tags:"):
                tags = [
                    tag.strip()
                    for tag in line.split(":", 1)[1].split(",")
                    if tag.strip()
                ]
            elif line.startswith("- updated_at:"):
                updated_at = line.split(":", 1)[1].strip()
            elif line == "## Notes":
                capture = True
            elif capture and line.startswith("- "):
                notes.append(
                    {
                        "text": line[2:].strip(),
                        "tags": tags,
                        "source": topic,
                        "created_at": updated_at or now(),
                        "kind": "durable",
                    }
                )
        return notes

    @staticmethod
    def _subject_key(text):
        text = str(text).strip()
        patterns = (
            r"^(.+?)\s+is\s+.+$",
            r"^(.+?)\s+are\s+.+$",
            r"^(.+?)\s+uses?\s+.+$",
            r"^(.+?)\s+should\s+.+$",
            r"^(.+?)是.+$",
            r"^(.+?)使用.+$",
        )
        for pattern in patterns:
            match = re.match(pattern, text, re.I)
            if match:
                subject = " ".join(_tokenize(match.group(1)))
                return subject or None
        return None

    def retrieval_candidates(self, query, limit=3):
        query_tokens = _tokenize(query)
        ranked = []
        for topic in self.load_index():
            notes = self.load_topic_notes(topic["topic"])
            for note in notes:
                note_tags = {tag.lower() for tag in note.get("tags", [])}
                note_tokens = (
                    _tokenize(note.get("text", ""))
                    | _tokenize(topic.get("title", ""))
                    | note_tags
                )
                exact_tag_match = int(bool(query_tokens & note_tags))
                keyword_overlap = len(query_tokens & note_tokens)
                if exact_tag_match == 0 and keyword_overlap == 0:
                    continue
                recency = _parse_timestamp(note.get("created_at"))
                ranked.append(((exact_tag_match, keyword_overlap, recency), note))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [note for _, note in ranked[:limit]]

    def _write_index(self, topics):
        self.root.mkdir(parents=True, exist_ok=True)
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        lines = ["# Durable Memory Index", ""]
        for topic in topics:
            lines.append(
                f"- [{topic['topic']}](topics/{topic['topic']}.md): {topic['title']}"
            )
            lines.append(f"  - summary: {topic['summary']}")
            lines.append(f"  - tags: {', '.join(topic['tags'])}")
        self.index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _write_topic(self, topic, notes):
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        meta = DURABLE_TOPIC_DEFAULTS[topic]
        lines = [
            f"# {meta['title']}",
            "",
            f"- topic: {topic}",
            f"- summary: {meta['summary']}",
            f"- tags: {', '.join(meta['tags'])}",
            f"- updated_at: {now()}",
            "",
            "## Notes",
        ]
        for note in notes:
            lines.append(f"- {note}")
        (self.topics_dir / f"{topic}.md").write_text(
            "\n".join(lines).rstrip() + "\n", encoding="utf-8"
        )

    def promote(self, promotions):
        if not promotions:
            return [], []
        topics = {topic["topic"]: topic for topic in self.load_index()}
        topic_notes = {
            slug: [note["text"] for note in self.load_topic_notes(slug)]
            for slug in topics
        }
        results = []
        superseded = []
        for topic, note_text in promotions:
            meta = DURABLE_TOPIC_DEFAULTS[topic]
            topics.setdefault(
                topic,
                {
                    "topic": topic,
                    "title": meta["title"],
                    "summary": meta["summary"],
                    "tags": list(meta["tags"]),
                },
            )
            existing = topic_notes.setdefault(topic, [])
            if note_text in existing:
                continue
            new_subject = self._subject_key(note_text)
            replaced = False
            if new_subject:
                for index, old_text in enumerate(list(existing)):
                    if self._subject_key(old_text) == new_subject:
                        superseded.append(f"{topic}: {old_text} -> {note_text}")
                        existing[index] = note_text
                        replaced = True
                        break
            if not replaced:
                existing.append(note_text)
            results.append(f"{topic}: {note_text}")
        self._write_index([topics[slug] for slug in sorted(topics)])
        for topic, notes in topic_notes.items():
            self._write_topic(topic, notes)
        return results, superseded


def default_memory_state():
    # 用一个小而结构化的状态，而不是一大段自由文本摘要。
    return {
        "working": {
            "task_summary": "",
            "recent_files": [],
        },
        "episodic_notes": [],
        "file_summaries": {},
        "task": "",
        "files": [],
        "notes": [],
        "next_note_index": 0,
    }


def _normalize_note(note, index):
    """
    整理笔记
    """
    if isinstance(note, str):
        text = clip(note.strip(), 500)
        return {
            "text": text,
            "tags": [],
            "source": "",
            "created_at": now(),
            "note_index": index,
            "kind": "episodic",
        }

    if not isinstance(note, dict):
        text = clip(str(note).strip(), 500)
        return {
            "text": text,
            "tags": [],
            "source": "",
            "created_at": now(),
            "note_index": index,
            "kind": "episodic",
        }

    text = clip(str(note.get("text", "")).strip(), 500)
    tags = [
        str(tag).strip()
        for tag in _ensure_list(note.get("tags", []))
        if str(tag).strip()
    ]
    source = str(note.get("source", "")).strip()
    created_at = str(note.get("created_at", "")).strip() or now()
    note_index = int(note.get("note_index", index))
    kind = str(note.get("kind", "episodic")).strip() or "episodic"
    return {
        "text": text,
        "tags": _dedupe_preserve_order(tags),
        "source": source,
        "created_at": created_at,
        "note_index": note_index,
        "kind": kind,
    }


def normalize_memory_state(state, workspace_root=None):
    if state is None:
        state = default_memory_state()
    elif not isinstance(state, dict):
        raise TypeError("memory state must be a mapping")

    # 规范化层的作用，是把“磁盘里可能长得不太一样的旧状态”
    # 统一整理成当前 runtime 可直接使用的紧凑结构。
    working = state.get("working")
    if not isinstance(working, dict):
        working = {}
    working.setdefault("task_summary", "")
    working.setdefault("recent_files", [])
    # 整理 task_summary
    working["task_summary"] = clip(str(working.get("task_summary", "")).strip(), 300)
    working["recent_files"] = (
        _dedupe_preserve_order(  # 最近访问过的文件去重，保留最近的几条
            [
                canonicalize_path(path, workspace_root)
                for path in _ensure_list(working.get("recent_files", []))
                if str(path).strip()
            ]
        )[-WORKING_FILE_LIMIT:]
    )
    state["working"] = working

    if not str(working["task_summary"]).strip() and state.get("task"):
        working["task_summary"] = clip(str(state.get("task", "")).strip(), 300)
    if not working["recent_files"] and state.get("files"):
        working["recent_files"] = _dedupe_preserve_order(
            [
                canonicalize_path(path, workspace_root)
                for path in _ensure_list(state.get("files", []))
                if str(path).strip()
            ]
        )[-WORKING_FILE_LIMIT:]

    episodic_notes = state.get("episodic_notes")
    if not isinstance(episodic_notes, list):
        episodic_notes = []

    if not episodic_notes and state.get("notes"):
        episodic_notes = [
            _normalize_note(note, index)
            for index, note in enumerate(_ensure_list(state.get("notes", [])))
            if str(note).strip()
        ]
    else:
        normalized_notes = []
        for index, note in enumerate(episodic_notes):
            if isinstance(note, str) and not str(note).strip():
                continue
            normalized_notes.append(_normalize_note(note, index))
        episodic_notes = normalized_notes
    episodic_notes = episodic_notes[-EPISODIC_NOTE_LIMIT:]
    state["episodic_notes"] = episodic_notes

    file_summaries = state.get("file_summaries")
    if not isinstance(file_summaries, dict):
        file_summaries = {}
    normalized_file_summaries = {}
    for path, summary in file_summaries.items():
        path = canonicalize_path(path, workspace_root)
        if isinstance(summary, dict):
            text = clip(str(summary.get("summary", "")).strip(), 500)
            created_at = str(summary.get("created_at", "")).strip() or now()
            freshness = summary.get("freshness")
            freshness = (
                None if freshness in (None, "") else str(freshness).strip() or None
            )
        else:
            text = clip(str(summary).strip(), 500)
            created_at = now()
            freshness = None
        if not path or not text:
            continue
        normalized_file_summaries[path] = {
            "summary": text,
            "created_at": created_at,
            "freshness": freshness,
        }
    state["file_summaries"] = normalized_file_summaries

    next_note_index = state.get("next_note_index")
    if not isinstance(next_note_index, int) or next_note_index < 0:
        next_note_index = 0
    max_index = max([note["note_index"] for note in episodic_notes], default=-1)
    state["next_note_index"] = max(next_note_index, max_index + 1)

    state["task"] = working["task_summary"]
    state["files"] = list(working["recent_files"])
    state["notes"] = [note["text"] for note in episodic_notes]
    durable_root = (
        Path(workspace_root) / ".pico" / "memory"
        if workspace_root is not None
        else None
    )
    durable_store = (
        DurableMemoryStore(durable_root) if durable_root is not None else None
    )
    state["durable_topics"] = (
        durable_store.topic_slugs() if durable_store is not None else []
    )
    return state


def resolve_workspace_path(raw_path, workspace_root=None):
    """
    作用：校验路径是否在工作区内部，安全返回绝对路径，不安全返回 None
    """
    path = Path(str(raw_path))
    if workspace_root is None:
        return path

    root = Path(workspace_root).resolve()
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def canonicalize_path(raw_path, workspace_root=None):
    """
    转换成相对工作区目录的相对路径
    """
    resolved = resolve_workspace_path(raw_path, workspace_root)
    if resolved is None:
        return Path(str(raw_path)).as_posix()
    if workspace_root is None:
        return Path(str(raw_path)).as_posix()
    root = Path(workspace_root).resolve()
    return resolved.relative_to(root).as_posix()


def set_task_summary(state, summary, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    state["working"]["task_summary"] = clip(str(summary).strip(), 300)
    state["task"] = state["working"]["task_summary"]
    return state


def remember_file(state, path, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    path = canonicalize_path(path, workspace_root).strip()
    if not path:
        return state
    files = [item for item in state["working"]["recent_files"] if item != path]
    files.append(path)
    state["working"]["recent_files"] = files[-WORKING_FILE_LIMIT:]
    state["files"] = list(state["working"]["recent_files"])
    return state


def _dedupe_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _ensure_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def append_note(
    state,
    text,
    tags=(),
    source="",
    created_at=None,
    workspace_root=None,
    kind="episodic",
):
    state = normalize_memory_state(state, workspace_root)
    text = clip(str(text).strip(), 500)
    if not text:
        return state
    """
    使用 _ensure_list 确保 tags 是一个列表（兼容传入元组或单个字符串的情况）。
    遍历标签，转字符串并去空格，过滤掉空标签。
    使用 _dedupe_preserve_order 对标签进行去重，但保留标签的原始输入顺序。
    """
    normalized_tags = _dedupe_preserve_order(
        [str(tag).strip() for tag in _ensure_list(tags) if str(tag).strip()]
    )
    note = {
        "text": text,
        "tags": normalized_tags,
        "source": str(source).strip(),
        "created_at": str(created_at).strip() if created_at else now(),
        "note_index": int(state.get("next_note_index", 0)),
        "kind": str(kind).strip() or "episodic",
    }
    state["next_note_index"] = note["note_index"] + 1

    notes = [item for item in state["episodic_notes"] if item["text"] != note["text"]]
    notes.append(note)
    state["episodic_notes"] = notes[-EPISODIC_NOTE_LIMIT:]
    state["notes"] = [item["text"] for item in state["episodic_notes"]]
    return state


def file_freshness(raw_path, workspace_root=None):
    resolved = resolve_workspace_path(raw_path, workspace_root)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        return None
    return hashlib.sha256(resolved.read_bytes()).hexdigest()


def invalidate_stale_file_summaries(state, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    invalidated = []
    for path, summary in list(state["file_summaries"].items()):
        current_freshness = file_freshness(path, workspace_root)
        if summary.get("freshness") == current_freshness:
            continue
        invalidated.append(path)
        # 去除文件
        state["file_summaries"].pop(path, None)
    return state, invalidated


class LayeredMemory:
    def __init__(self, state=None, workspace_root=None):
        self.workspace_root = workspace_root
        self.state = normalize_memory_state(state, workspace_root)
        self.durable_store = (
            DurableMemoryStore(Path(workspace_root) / ".jarvis" / "memory")
            if workspace_root is not None
            else None
        )

    def to_dict(self):
        self.state = normalize_memory_state(self.state, self.workspace_root)
        return self.state

    def canonical_path(self, path):
        return canonicalize_path(path, self.workspace_root)

    def set_task_summary(self, summary):
        self.state = set_task_summary(self.state, summary, self.workspace_root)
        return self

    def remember_file(self, path):
        self.state = remember_file(self.state, path, self.workspace_root)
        return self

    def append_note(self, text, tags=(), source="", created_at=None, kind="episodic"):
        self.state = append_note(
            self.state,
            text,
            tags=tags,
            source=source,
            created_at=created_at,
            workspace_root=self.workspace_root,
            kind=kind,
        )
        return self

    def invalidate_stale_file_summaries(self):
        self.state, invalidated = invalidate_stale_file_summaries(
            self.state, self.workspace_root
        )
        return invalidated
