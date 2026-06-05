import datetime
import hashlib
from pathlib import Path
import subprocess

from rich import json

# 扫描白名单
DOC_NAMES = ("AGENTS.md", "README.md", "pyproject.toml", "package.json")
IGNORED_PATH_NAMES = {
    ".git",
    ".jarvis",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
}
MAX_TOOL_OUTPUT = 4000


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def clip(text, limit=MAX_TOOL_OUTPUT):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


class WorkSpaceContext:
    def __init__(
        self,
        cwd,
        repo_root,
        branch,
        default_branch,
        status,
        recent_commits,
        project_docs,
    ):
        self.cwd = cwd
        self.repo_root = repo_root
        self.branch = branch
        self.default_branch = default_branch
        self.status = status
        self.recent_commits = recent_commits
        self.project_docs = project_docs

    @classmethod
    def build(cls, cwd, repo_root_override=None):
        cwd = Path(cwd).resolve()  # 转换为绝对路径，当前工作目录

        def git(args, fallback=""):
            try:
                result = subprocess.run(
                    ["git", *args],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                return result.stdout.strip() or fallback
            except Exception:
                return fallback

        repo_root = (
            Path(repo_root_override).resolve()
            if repo_root_override is not None
            else Path(git(["rev-parse", "--show-toplevel"], str(cwd))).resolve()
        )  # git项目根目录

        # 扫描白名单文件，去重，截断读取内容
        docs = {}
        for base in (repo_root, cwd):
            for name in DOC_NAMES:
                path = base / name
                if not path.exists():
                    continue
                key = str(path.relative_to(repo_root))
                if key in docs:
                    continue
                docs[key] = clip(
                    path.read_text(encoding="utf-8", errors="replace"), 1200
                )
        return cls(
            cwd=str(cwd),
            repo_root=str(repo_root),
            branch=git(["branch", "--show-current"], "-") or "-",  # 当前分支
            default_branch=(
                lambda branch: (
                    branch[len("origin/") :] if branch.startswith("origin/") else branch
                )
            )(
                git(
                    ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                    "origin/main",
                )
                or "origin/main"
            ),  # 获取远程默认分支
            status=clip(
                git(["status", "--short"], "clean") or "clean", 1500
            ),  # 仓库变更
            recent_commits=[
                line for line in git(["log", "--oneline", "-5"]).splitlines() if line
            ],
            project_docs=docs,
        )

    def text(self):
        lines = [
            f"Workspace: {self.repo_root}",
            f"Branch: {self.branch}",
            f"Default branch: {self.default_branch}",
            f"Status:\n{self.status}",
        ]
        if self.recent_commits:
            lines.append("Recent commits:")
            for commit in self.recent_commits:
                lines.append(f"  {commit}")
        if self.project_docs:
            lines.append("Project docs:")
            for path, content in self.project_docs.items():
                lines.append(f"--- {path} ---")
                lines.append(content)
        return "\n".join(lines)

    def fingerprint(self):
        # 这个指纹用来判断仓库状态是否发生了足够大的变化，
        # 从而决定是否需要重建缓存中的 prompt prefix。
        payload = {
            "cwd": self.cwd,
            "repo_root": self.repo_root,
            "branch": self.branch,
            "default_branch": self.default_branch,
            "status": self.status,
            "recent_commits": list(self.recent_commits),
            "project_docs": dict(self.project_docs),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
