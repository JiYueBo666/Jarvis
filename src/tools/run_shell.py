from pathlib import Path
from typing import Any

from src.engine.tool import Tool, ToolParameter
from src.tools.base import ToolResult

# stubs for deferred sandbox — sandbox/guard isn't implemented yet
class SandboxConfig:
    def __init__(self, mode: str = "best_effort", workspace_root: str = "/tmp"):
        self.mode = mode
        self.workspace_root = workspace_root


class SandboxRunner:
    def __init__(self, config: SandboxConfig):
        self._config = config

    def run(self, command: str, timeout: int = 30) -> ToolResult:
        import subprocess
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._config.workspace_root,
            )
            output = result.stdout + result.stderr
            return ToolResult(output=output)
        except subprocess.TimeoutExpired:
            return ToolResult(output=f"Command timed out after {timeout}s")
        except Exception as e:
            return ToolResult(output=str(e))


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

        # ── 初始化沙箱执行器 ────────────────────────────────────
        from src.config import settings
        mode = settings.SANDBOX_MODE
        self._sandbox = SandboxRunner(
            SandboxConfig(
                mode=mode,
                workspace_root=str(self.workspace_root),
            )
        )

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

        # 委托给沙箱执行器
        return self._sandbox.run(command, timeout=timeout)
