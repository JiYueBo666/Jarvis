from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolResult:
    output: str
    metadata: dict = field(default_factory=dict)


def resolve_path(path: str, workspace_root: Path | None = None) -> Path:
    """基于工作区根目录解析潜在的相对路径。"""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p.resolve()
    base = workspace_root or Path.cwd()
    return (base / p).resolve()
