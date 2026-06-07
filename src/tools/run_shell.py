import subprocess
from pathlib import Path
from typing import Any

from src.engine.tool import Tool, ToolParameter
from src.tools.base import ToolResult


class RunShell(Tool):
    """Execute a shell command in the workspace directory."""

    def __init__(self, workspace_root: str | None = None):
        super().__init__(
            name="run_shell",
            description="Run a shell command in the workspace directory. "
                        "Use for listing files, searching, or any CLI operation.",
            is_readonly=False,
            risky=True,
        )
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else Path.cwd()

    def get_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="command", type="string", description="Shell command to execute"),
            ToolParameter(
                name="timeout",
                type="integer",
                description="Timeout in seconds (default 30)",
                required=False,
            ),
        ]

    def run(self, parameters: dict[str, Any]) -> ToolResult:
        command = str(parameters["command"])
        timeout = int(parameters.get("timeout", 30))

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.workspace_root),
            )
            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout.strip())
            if result.stderr:
                output_parts.append(f"[stderr]\n{result.stderr.strip()}")
            output = "\n".join(output_parts) if output_parts else "(no output)"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"

            return ToolResult(
                output=output,
                metadata={"exit_code": result.returncode},
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                output=f"Command timed out after {timeout}s: {command[:200]}",
            )
        except OSError as e:
            return ToolResult(
                output=f"Error running command: {e}",
            )
