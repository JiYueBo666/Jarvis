from pathlib import Path
from typing import Any

from src.engine.tool import Tool, ToolParameter
from src.tools.base import ToolResult, resolve_path


class WriteFile(Tool):
    def __init__(self, workspace_root: str | None = None):
        super().__init__(
            name="write_file",
            description="Create a new file or overwrite an existing one with the given content.",
            is_readonly=False,
            risky=True,
        )
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="path", type="string", description="Path to the file"),
            ToolParameter(
                name="content", type="string", description="Full file content to write"
            ),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResult:
        try:
            path = resolve_path(parameters["path"], self.workspace_root)
        except ValueError as e:
            return ToolResult(output=f"Error: {e}")
        content = str(parameters.get("content", ""))

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(
                output=f"Written {len(content)} chars to {path}",
                metadata={"affected_paths": [str(path)]},
            )
        except OSError as e:
            return ToolResult(output=f"Error writing file: {e}")
