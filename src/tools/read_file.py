from pathlib import Path
from typing import Any

from src.engine.tool import Tool, ToolParameter
from src.tools.base import ToolResult, resolve_path


class ReadFile(Tool):
    def __init__(self, workspace_root: str | None = None):
        super().__init__(
            name="read_file",
            description="Read a file from disk, optionally limiting to a line range.",
            is_readonly=True,
        )
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="path", type="string", description="Path to the file"),
            ToolParameter(
                name="start",
                type="integer",
                description="First line number to read (1-based, optional)",
                required=False,
            ),
            ToolParameter(
                name="end",
                type="integer",
                description="Last line number to read (1-based, inclusive, optional)",
                required=False,
            ),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResult:
        path = resolve_path(parameters["path"], self.workspace_root)
        if not path.is_file():
            return ToolResult(output=f"Error: file not found: {path}")

        start = parameters.get("start")
        end = parameters.get("end")

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}")

        if start is not None:
            start = max(1, int(start))
            lines = lines[start - 1 :]
        if end is not None:
            end = min(len(lines) + (start or 1) - 1, int(end))
            lines = lines[: end - (start or 1) + 1]

        if not lines:
            return ToolResult(output="(empty)")

        line_offset = start or 1
        numbered = [f"{line_offset + i:6d} | {line}" for i, line in enumerate(lines)]
        return ToolResult(
            output="".join(numbered),
            metadata={"affected_paths": [str(path)]},
        )
