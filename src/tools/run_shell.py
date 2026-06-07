from pathlib import Path
from typing import Any

from src.config import settings
from src.engine.tool import Tool, ToolParameter
from src.guard.sandbox import SandboxConfig, SandboxMode, SandboxRunner
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

        # ── 初始化沙箱执行器 ────────────────────────────────────
        sandbox_mode = SandboxMode(settings.SANDBOX_MODE)
        self._sandbox = SandboxRunner(
            SandboxConfig(
                mode=sandbox_mode,
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
