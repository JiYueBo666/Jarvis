from pathlib import Path
from typing import Any

from src.engine.tool import Tool, ToolParameter
from src.tools.base import ToolResult, resolve_path

_IGNORED = frozenset({".git", ".venv", "venv", "__pycache__", "node_modules", ".jarvis", ".sessions", ".pytest_cache"})


class ListDirTool(Tool):
    def __init__(self, workspace_root: str | None = None):
        super().__init__(
            name="list_dir",
            description="List files and directories in a given path. "
                        "Shows type, size, and basic metadata. Can recurse up to 3 levels.",
            is_readonly=True,
        )
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="path", type="string", description="Directory path to list"),
            ToolParameter(
                name="max_depth",
                type="integer",
                description="Max recursion depth (1 = immediate children only, default 1, max 3)",
                required=False,
            ),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResult:
        try:
            target = resolve_path(parameters["path"], self.workspace_root)
        except ValueError as e:
            return ToolResult(output=f"Error: {e}")
        max_depth = min(int(parameters.get("max_depth", 1)), 3)

        if not target.is_dir():
            return ToolResult(output=f"Error: directory not found: {target}")

        max_entries = 500
        lines: list[str] = []
        lines.append(f"📁 {target.name}/")

        try:
            self._walk(target, target, 0, max_depth, max_entries, lines)
        except (OSError, ValueError) as e:
            return ToolResult(output=f"Error: {e}")

        return ToolResult(output="\n".join(lines[:max_entries]))

    @staticmethod
    def _walk(root: Path, current: Path, depth: int, max_depth: int, max_entries: int, lines: list[str]):
        if depth > max_depth or len(lines) >= max_entries:
            return

        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return

        for i, entry in enumerate(entries):
            if entry.name.startswith("."):
                continue
            if entry.is_dir() and entry.name in _IGNORED:
                continue
            if len(lines) >= max_entries:
                return

            prefix = "├─ " if i < len(entries) - 1 else "└─ "
            indent = "│   " * depth
            name = entry.name

            if entry.is_dir():
                lines.append(f"{indent}{prefix}{name}/")
                ListDirTool._walk(root, entry, depth + 1, max_depth, max_entries, lines)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{indent}{prefix}{name} ({_fmt_size(size)})")


def _fmt_size(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    if b < 1024 ** 2:
        return f"{b / 1024:.1f}KB"
    return f"{b / 1024 ** 2:.1f}MB"
