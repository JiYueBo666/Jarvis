"""Layout 组件：高度感知的容器，按终端尺寸分配行数。"""

from __future__ import annotations

from src.Tui.component import Component
from src.Tui.components import InputLine, StatusBar


class Layout(Component):
    """划分屏幕空间：

    rows 0 .. height-3: Chat 内容区（可滚动）
    row  height-2:      输入行
    row  height-1:      状态栏
    """

    def __init__(
        self,
        content: Component,
        input_line: InputLine,
        status_bar: StatusBar,
    ):
        self.content = content
        self.input_line = input_line
        self.status_bar = status_bar
        self._height = 24
        self._width = 80

    def set_terminal_size(self, height: int, width: int) -> None:
        self._height = height
        self._width = width

    def render(self, width: int) -> list[str]:
        lines: list[str] = []

        # Chat 内容区：剩余高度
        avail_height = self._height - 2  # 保留给 input line + status bar
        if hasattr(self.content, "set_max_lines"):
            self.content.set_max_lines(avail_height)
        lines.extend(self.content.render(width))

        # 补齐到可用高度
        while len(lines) < avail_height:
            lines.append("")

        # 输入行
        lines.extend(self.input_line.render(width))

        # 状态栏
        lines.extend(self.status_bar.render(width))

        return lines
