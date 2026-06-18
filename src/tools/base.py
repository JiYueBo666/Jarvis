from pathlib import Path

from src.result import ToolResult  # noqa: F401 — re-export


def resolve_path(path: str, workspace_root: Path | None = None, restrict: bool = True) -> Path:
    """基于工作区根目录解析潜在的相对路径。

    restrict=True 时，解析后的路径必须在 workspace_root 内，
    否则抛出 ValueError 防止路径遍历攻击。
    """
    p = Path(path).expanduser()
    if p.is_absolute():
        resolved = p.resolve()
    else:
        base = workspace_root or Path.cwd()
        resolved = (base / p).resolve()

    if restrict and workspace_root:
        workspace = workspace_root.resolve()
        if not resolved.is_relative_to(workspace):
            raise ValueError(f"路径在工作区外: {resolved}")

    return resolved
