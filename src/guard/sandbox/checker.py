"""安全检查入口：决定如何运行命令。

根据 SandboxConfig 和 bwrap 可用性，决定命令应该：
- 直接裸跑（PASS_DIRECT）
- 在 bwrap 沙箱中运行（PASS_BWRAP）
- 被拒绝（FAIL）
"""

from __future__ import annotations

import logging
import shutil
from enum import Enum

from src.guard.sandbox.command_matcher import find_dangerous_pattern
from src.guard.sandbox.config import SandboxConfig, SandboxMode

logger = logging.getLogger(__name__)


class SandboxResult(str, Enum):
    """安全检查结果。"""
    PASS_DIRECT = "pass_direct"   # 直接裸跑（安全或降级）
    PASS_BWRAP = "pass_bwrap"     # 用 bwrap 沙箱运行
    FAIL = "fail"                  # 拒绝执行


def _bwrap_available() -> bool:
    """检查 bwrap 是否可执行。"""
    return shutil.which("bwrap") is not None


def check_command(command: str, config: SandboxConfig) -> SandboxResult:
    """安全检查入口。

    Args:
        command: 待执行的 shell 命令。
        config: 沙箱配置。

    Returns:
        应该以何种方式执行该命令。
    """
    # 1. mode == OFF → 直接允许
    if config.mode == SandboxMode.OFF:
        return SandboxResult.PASS_DIRECT

    bwrap_ok = _bwrap_available()

    # 2. mode == REQUIRED → 必须 bwrap
    if config.mode == SandboxMode.REQUIRED:
        if bwrap_ok:
            return SandboxResult.PASS_BWRAP
        logger.warning("Sandbox mode is REQUIRED but bwrap is not available")
        return SandboxResult.FAIL

    # 3. mode == BEST_EFFORT
    if bwrap_ok:
        return SandboxResult.PASS_BWRAP

    # bwrap 不可用，降级到直接执行，但先做高危模式匹配
    danger = find_dangerous_pattern(command)
    if danger:
        name, desc = danger
        logger.warning("Dangerous command blocked (fallback): %s - %s", name, desc)
        return SandboxResult.FAIL

    return SandboxResult.PASS_DIRECT
