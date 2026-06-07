"""高危命令模式匹配——轻量 fallback 防线。

当 SandboxMode=BEST_EFFORT 且 bwrap 不可用时，作为最后的兜底线。
匹配到任一模式即拒绝执行。
"""

from __future__ import annotations

import re
from typing import Pattern

# ── 高危命令模式 ────────────────────────────────────────────────
# 每条：(名称, 描述, 编译后正则)
DANGEROUS_PATTERNS: list[tuple[str, str, Pattern]] = [
    (
        "rm_root",
        "递归删除根目录",
        re.compile(r'\brm\s+(-rf?\s+)?(/|/\w+)'),
    ),
    (
        "dd_block_device",
        "直接写入块设备",
        re.compile(r'\bdd\s+if='),
    ),
    (
        "write_block_device",
        "重定向到块设备",
        re.compile(r'>\s*/dev/sd'),
    ),
    (
        "mkfs",
        "格式化文件系统",
        re.compile(r'\bmkfs\.'),
    ),
    (
        "fork_bomb",
        "Fork 炸弹",
        re.compile(r':\(\)\s*\{'),
    ),
    (
        "curl_pipe_bash",
        "curl 管道到 shell",
        re.compile(r'\bcurl\s+\S+.*\|\s*(bash|sh|zsh)'),
    ),
    (
        "wget_pipe_bash",
        "wget 管道到 shell",
        re.compile(r'\bwget\s+\S+.*\|\s*(bash|sh|zsh)'),
    ),
    (
        "sudo",
        "sudo 提权",
        re.compile(r'\bsudo\b'),
    ),
    (
        "chmod_777",
        "危险权限 777",
        re.compile(r'\bchmod\s+777\b'),
    ),
    (
        "chown",
        "篡改文件所有权",
        re.compile(r'\bchown\b'),
    ),
    (
        "passwd",
        "修改密码",
        re.compile(r'\bpasswd\b'),
    ),
    (
        "usermod_useradd",
        "用户管理",
        re.compile(r'\b(usermod|useradd|userdel)\b'),
    ),
]


def find_dangerous_pattern(command: str) -> tuple[str, str] | None:
    """检查命令是否匹配高危模式。

    Args:
        command: 要检查的 shell 命令。

    Returns:
        (模式名, 描述) 如果匹配到任一模式。
        None 如果安全通过。
    """
    for name, desc, pattern in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return name, desc
    return None
