"""Sandbox — 命令执行沙箱。

三层防御：
1. bwrap 容器隔离（主要防线）
2. 高危模式正则匹配（bwrap 不可用时的 fallback）
3. 配置灵活降级（off / best_effort / required）
"""

from src.guard.sandbox.checker import check_command, SandboxResult
from src.guard.sandbox.config import SandboxConfig, SandboxMode, DEFAULT_NETWORK_WHITELIST
from src.guard.sandbox.command_matcher import find_dangerous_pattern, DANGEROUS_PATTERNS
from src.guard.sandbox.runner import SandboxRunner

__all__ = [
    "SandboxConfig",
    "SandboxMode",
    "SandboxResult",
    "SandboxRunner",
    "check_command",
    "find_dangerous_pattern",
    "DANGEROUS_PATTERNS",
    "DEFAULT_NETWORK_WHITELIST",
]
