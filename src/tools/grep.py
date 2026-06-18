import re
from pathlib import Path
from typing import Any

from src.engine.tool import Tool, ToolParameter
from src.tools.base import ToolResult, resolve_path

_IGNORED_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", "node_modules", ".jarvis", ".sessions"})


class GrepTool(Tool):
    def __init__(self, workspace_root: str | None = None):
        super().__init__(
            name="grep",
            description="Search file contents using a regex pattern. "
                        "Returns matching lines with line numbers.",
            is_readonly=True,
        )
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="pattern", type="string", description="Regex pattern to search for"),
            ToolParameter(
                name="include",
                type="string",
                description="File glob pattern to filter (e.g. *.py, *.{ts,tsx})",
                required=False,
            ),
            ToolParameter(
                name="path",
                type="string",
                description="Directory to search in (defaults to workspace root)",
                required=False,
            ),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResult:
        pattern = parameters["pattern"]
        include = parameters.get("include")
        try:
            search_root = resolve_path(parameters.get("path", "."), self.workspace_root)
        except ValueError as e:
            return ToolResult(output=f"Error: {e}")

        if not search_root.is_dir():
            return ToolResult(output=f"Error: directory not found: {search_root}")

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(output=f"Error: invalid regex: {e}")

        max_results = 200
        matches: list[str] = []

        try:
            for f in self._walk_files(search_root, include):
                if len(matches) >= max_results:
                    break
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                except (OSError, UnicodeDecodeError):
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        rel = f.relative_to(search_root)
                        matches.append(f"{rel}:{i}:{line}")
                        if len(matches) >= max_results:
                            break
        except (OSError, ValueError) as e:
            return ToolResult(output=f"Error: {e}")

        if not matches:
            return ToolResult(output="No matches found")

        return ToolResult(output="\n".join(matches))

    @staticmethod
    def _walk_files(root: Path, glob: str | None = None):
        """Walk directories, skipping ignored dirs, yielding matching files."""
        if glob:
            for f in root.rglob(glob):
                if f.is_file() and not any(p.name in _IGNORED_DIRS for p in f.parents):
                    yield f
        else:
            for f in root.rglob("*"):
                if f.is_file() and not any(p.name in _IGNORED_DIRS for p in f.parents):
                    yield f
