from pathlib import Path
from typing import Any

from src.engine.tool import Tool, ToolParameter
from src.tools.base import ToolResult, resolve_path


class PatchFile(Tool):
    """Replace the FIRST exact match of old_text with new_text in the given file."""

    def __init__(self, workspace_root: str | None = None):
        super().__init__(
            name="patch_file",
            description="Replace the first exact occurrence of old_text with new_text in a file. "
            "This is NOT a regex substitution. Returns an error if old_text is not found.",
            is_readonly=False,
            risky=True,
        )
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="path", type="string", description="Path to the file"),
            ToolParameter(
                name="old_text",
                type="string",
                description="Exact text to find (only the first match is replaced)",
            ),
            ToolParameter(
                name="new_text",
                type="string",
                description="Replacement text",
            ),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResult:
        try:
            path = resolve_path(parameters["path"], self.workspace_root)
        except ValueError as e:
            return ToolResult(output=f"Error: {e}")
        old_text = str(parameters["old_text"])
        new_text = str(parameters.get("new_text", ""))

        if not path.is_file():
            return ToolResult(output=f"Error: file not found: {path}")

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult(output=f"Error reading file: {e}")

        idx = content.find(old_text)
        if idx == -1:
            return ToolResult(
                output=f"Error: old_text not found in {path}. "
                "The exact string does not appear anywhere in the file."
            )

        new_content = content[:idx] + new_text + content[idx + len(old_text) :]

        try:
            path.write_text(new_content, encoding="utf-8")
            return ToolResult(
                output=f"Replaced first occurrence in {path}",
                metadata={"affected_paths": [str(path)]},
            )
        except OSError as e:
            return ToolResult(output=f"Error writing file: {e}")
