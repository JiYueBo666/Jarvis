"""Sandbox 模式的渐进式配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SandboxMode(str, Enum):
    OFF = "off"                 # 裸 subprocess，零开销
    BEST_EFFORT = "best_effort"  # 优先 bwrap，不可用则降级 + 高危模式匹配
    REQUIRED = "required"       # 强制 bwrap，不可用则报错


# 默认网络白名单（常见的开发/包管理域名）
DEFAULT_NETWORK_WHITELIST: list[str] = [
    "github.com",
    "raw.githubusercontent.com",
    "pypi.org",
    "files.pythonhosted.org",
    "archives.ubuntu.com",
    "security.ubuntu.com",
    "registry.npmjs.org",
    "nodejs.org",
]


@dataclass
class SandboxConfig:
    """沙箱配置，可从 Settings 或环境变量读取。"""

    mode: SandboxMode = SandboxMode.BEST_EFFORT
    workspace_root: str = ""
    allow_network: bool = False          # 是否允许网络访问
    network_whitelist: list[str] = field(
        default_factory=lambda: list(DEFAULT_NETWORK_WHITELIST)
    )                                   # 允许的域名列表（allow_network=True 时生效）
    command_timeout: int = 120          # 命令超时（秒）
    max_output_size: int = 10 * 1024 * 1024  # 最大输出 10MB
