"""Component 接口：每个 UI 组件实现 render() 和可选的 handle_input()。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Component(ABC):
    """UI 组件基类。

    render(width) → list[str]：返回当前宽度下的纯文本行列表。
    handle_input(data) → bool：处理键盘事件，返回 True 表示事件已消费。
    """

    @abstractmethod
    def render(self, width: int) -> list[str]:
        ...

    def handle_input(self, data: str) -> bool:
        return False

    def set_terminal_size(self, height: int, width: int) -> None:
        """可选：接收终端尺寸变化。Layout 等容器需要此方法。"""
        pass


class Composite(Component):
    """容器组件，管理子组件列表。"""

    def __init__(self, children: list[Component] | None = None):
        self.children = children or []

    def add(self, child: Component):
        self.children.append(child)

    def remove(self, child: Component):
        self.children.remove(child)

    def render(self, width: int) -> list[str]:
        lines: list[str] = []
        for child in self.children:
            lines.extend(child.render(width))
        return lines

    def handle_input(self, data: str) -> bool:
        for child in reversed(self.children):
            if child.handle_input(data):
                return True
        return False
