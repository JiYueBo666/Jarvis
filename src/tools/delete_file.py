from pathlib import Path
from typing import Any

from src.engine.tool import Tool, ToolParameter
from src.tools.base import ToolResult, resolve_path


class DeleteFile(Tool):
    """Delete a file or directory (recursively). Requires user approval."""

    def __init__(self, workspace_root: str | None = None):
        super().__init__(
            name="delete_file",
            description="Delete a file or an empty directory. "
            "For non-empty directories, use recursive=true (⚠️ careful!). "
            "This is a dangerous operation and requires user approval.",
            is_readonly=False,
            risky=True,
        )
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="path",
                type="string",
                description="Path to the file or directory to delete",
            ),
            ToolParameter(
                name="recursive",
                type="boolean",
                description="If true, recursively delete a directory and all its contents",
                required=False,
            ),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResult:
        path_str = parameters["path"]
        recursive = parameters.get("recursive", False)
        try:
            target = resolve_path(path_str, self.workspace_root)
        except ValueError as e:
            return ToolResult(output=f"Error: {e}")

        if not target.exists():
            return ToolResult(output=f"Error: path not found: {target}")

        # ── 安全边界：禁止删除 workspace 根目录 ──
        if self.workspace_root and target == self.workspace_root:
            return ToolResult(output="Error: cannot delete the workspace root directory")

        # ── 安全边界：禁止删除 workspace 父目录及以上 ──
        if self.workspace_root and self.workspace_root not in target.parents:
            return ToolResult(
                output=f"Error: path '{target}' is outside the workspace scope"
            )

        try:
            if target.is_file():
                size = target.stat().st_size
                target.unlink()
                return ToolResult(
                    output=f"Deleted file: {target} ({size} bytes)",
                    metadata={"affected_paths": [str(target)]},
                )

            elif target.is_dir():
                if recursive:
                    # Recursive directory deletion
                    entries = list(target.rglob("*"))
                    file_count = sum(1 for e in entries if e.is_file())
                    target.rmdir() if not any(target.iterdir()) else _rmtree(target)
                    return ToolResult(
                        output=f"Deleted directory: {target} (recursively, {file_count} files)",
                        metadata={"affected_paths": [str(target)]},
                    )
                else:
                    # Try to remove empty directory only
                    try:
                        target.rmdir()
                        return ToolResult(
                            output=f"Deleted empty directory: {target}",
                            metadata={"affected_paths": [str(target)]},
                        )
                    except OSError:
                        return ToolResult(
                            output=f"Error: directory '{target}' is not empty. "
                            "Use recursive=true to delete non-empty directories."
                        )

            return ToolResult(output=f"Error: unknown path type: {target}")

        except OSError as e:
            return ToolResult(output=f"Error deleting '{target}': {e}")


def _rmtree(path: Path):
    """Recursively delete a directory tree (shutil.rmtree alternative)."""
    import shutil
    shutil.rmtree(path)
