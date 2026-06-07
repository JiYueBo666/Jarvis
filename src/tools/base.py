from pathlib import Path

from src.result import ToolResult  # noqa: F401 — re-export


def resolve_path(path: str, workspace_root: Path | None = None) -> Path:
    """基于工作区根目录解析潜在的相对路径。"""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p.resolve()
    base = workspace_root or Path.cwd()
    return (base / p).resolve()
