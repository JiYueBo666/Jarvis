"""纯数据类型——不依赖任何其他 agent 模块，避免循环导入。"""

from dataclasses import dataclass, field


@dataclass
class ToolResult:
    """工具执行结果。"""
    output: str
    metadata: dict = field(default_factory=dict)
