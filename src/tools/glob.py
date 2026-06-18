from pathlib import Path
from typing import Any

from src.engine.tool import Tool, ToolParameter
from src.tools.base import ToolResult, resolve_path


class GlobTool(Tool):
    def __init__(self, workspace_root: str | None = None):
        super().__init__(
            name="glob",
            description="Find files matching a glob pattern (e.g. **/*.py, src/**/*.ts).",
            is_readonly=True,
        )
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="pattern", type="string", description="Glob pattern to match (e.g. **/*.py)"),
            ToolParameter(
                name="path",
                type="string",
                description="Directory to search in (defaults to workspace root)",
                required=False,
            ),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResult:
        pattern = parameters["pattern"]
        try:
            search_root = resolve_path(parameters.get("path", "."), self.workspace_root)
        except ValueError as e:
            return ToolResult(output=f"Error: {e}")

        if not search_root.is_dir():
            return ToolResult(output=f"Error: directory not found: {search_root}")

        max_results = 200
        results: list[str] = []

        for f in sorted(search_root.rglob(pattern)):
            if not f.is_file():
                continue
            rel = f.relative_to(search_root)
            results.append(str(rel))
            if len(results) >= max_results:
                break

        if not results:
            return ToolResult(output="No files found")

        return ToolResult(output="\n".join(results))
