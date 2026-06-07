"""真正的执行引擎：bwrap 沙箱或裸 subprocess。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from src.guard.sandbox.checker import SandboxResult, check_command
from src.guard.sandbox.config import SandboxConfig
from src.result import ToolResult

logger = logging.getLogger(__name__)


class SandboxRunner:
    """沙箱执行器。"""

    def __init__(self, config: SandboxConfig):
        self.config = config

    # ── public API ───────────────────────────────────────────────

    def run(self, command: str, timeout: int = 30) -> ToolResult:
        """安全检查后执行命令。

        Args:
            command: shell 命令。
            timeout: 超时秒数。

        Returns:
            ToolResult，失败时 output 包含拒绝原因。
        """
        result = check_command(command, self.config)

        if result == SandboxResult.FAIL:
            return ToolResult(
                output=f"⛔ Command blocked by sandbox: {command[:300]}",
                metadata={"blocked": True},
            )

        try:
            if result == SandboxResult.PASS_BWRAP:
                return self._run_bwrap(command, timeout)
            else:
                return self._run_direct(command, timeout)
        except subprocess.TimeoutExpired:
            return ToolResult(
                output=f"Command timed out after {timeout}s: {command[:200]}",
                metadata={"exit_code": -1, "timed_out": True},
            )
        except OSError as e:
            return ToolResult(
                output=f"Error running command: {e}",
                metadata={"error": str(e)},
            )

    # ── direct execution (no sandbox) ────────────────────────────

    def _run_direct(self, command: str, timeout: int) -> ToolResult:
        """直接在当前环境执行命令。"""
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(self.config.workspace_root) if self.config.workspace_root else None,
        )
        return self._build_result(result)

    # ── bwrap sandboxed execution ────────────────────────────────

    def _run_bwrap(self, command: str, timeout: int) -> ToolResult:
        """在 bwrap 沙箱中执行命令。

        沙箱规则：
        - 根目录 / 只读 bind mount
        - /tmp, /var/tmp 临时文件系统
        - /dev 基本设备
        - /proc 进程信息
        - 工作区可读写
        - 默认无网络（除非 allow_network=True）
        - 进程退出时自动清理
        """
        workspace = (
            str(Path(self.config.workspace_root).resolve())
            if self.config.workspace_root
            else str(Path.cwd().resolve())
        )

        bwrap_args = [
            "bwrap",
            "--ro-bind",
            "/",
            "/",  # 根目录只读
            "--tmpfs",
            "/tmp",  # /tmp 临时
            "--tmpfs",
            "/var/tmp",  # /var/tmp 临时
            "--dev",
            "/dev",  # 基本设备
            "--proc",
            "/proc",  # /proc
            "--bind",
            workspace,
            workspace,  # 工作区可读写
            "--chdir",
            workspace,  # 切换到工作区
            "--die-with-parent",  # 父进程退出时清理
            "--unshare-pid",  # 隔离 PID 命名空间
            "--unshare-ipc",  # 隔离 IPC
            "--unshare-uts",  # 隔离 hostname
        ]

        # 网络控制
        if self.config.allow_network:
            # 有限网络：通过 --share-net 共享网络命名空间
            # 但可以在沙箱内用 iptables/nftables 做白名单
            # 这里简化处理：放行全部网络
            pass
        else:
            bwrap_args.append("--unshare-net")

        # 构造完整命令
        full_cmd = bwrap_args + ["sh", "-c", command]

        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return self._build_result(result)

    # ── helpers ──────────────────────────────────────────────────

    @staticmethod
    def _build_result(result: subprocess.CompletedProcess) -> ToolResult:
        """将 subprocess 结果转为 ToolResult。"""
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
