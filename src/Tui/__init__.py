"""
TUI 框架：Component 接口 + 差分渲染 + 原始输入。
"""

from src.Tui.component import Component, Composite
from src.Tui.components import (
    InputLine,
    MarkdownView,
    Separator,
    StatusBar,
    word_wrap,
)
from src.Tui.input import InputReader, LineInput
from src.Tui.layout import Layout
from src.Tui.renderer import Renderer
from src.Tui.terminal import Terminal

__all__ = [
    "Component",
    "Composite",
    "InputLine",
    "Layout",
    "MarkdownView",
    "StatusBar",
    "Separator",
    "Terminal",
    "Renderer",
    "InputReader",
    "LineInput",
    "word_wrap",
]
