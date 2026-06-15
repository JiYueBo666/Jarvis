"""内置组件：Markdown 渲染、文本查看器、状态栏等。"""

from __future__ import annotations

import re
from pygments import highlight
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.formatters import Terminal256Formatter
from wcwidth import wcswidth

from src.Tui.component import Component

# ── 宽度工具 ────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def visible_width(s: str) -> int:
    """中文字符占 2 列（先剥除 ANSI 序列）。"""
    clean = _ANSI_RE.sub("", s)
    w = wcswidth(clean)
    return w if w >= 0 else len(clean)


def pad_to(s: str, w: int) -> str:
    """用空格补齐到宽度 w。"""
    curr = visible_width(s)
    if curr >= w:
        return s
    return s + " " * (w - curr)


def word_wrap(text: str, width: int) -> list[str]:
    """按可见宽度折行，保留 ANSI 样式。"""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        words = para.split(" ")
        current = ""
        for word in words:
            candidate = current + (" " if current else "") + word
            if visible_width(candidate) <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


# ── Markdown 渲染 ───────────────────────────────────


class MarkdownView(Component):
    """渲染 Markdown 文本为 ANSI 彩色行。"""

    STYLES = {
        "h1": "\x1b[1;38;5;220m",  # bold yellow
        "h2": "\x1b[1;38;5;214m",
        "h3": "\x1b[1;38;5;208m",
        "code": "\x1b[38;5;83m",
        "code_bg": "\x1b[48;5;235m",
        "quote": "\x1b[38;5;244m",
        "list": "\x1b[38;5;39m",
        "bold": "\x1b[1m",
        "reset": "\x1b[0m",
    }

    def __init__(self, text: str = ""):
        self.text = text
        self._cached_lines: list[str] = []
        self._cached_width = 0

    def set_text(self, text: str) -> None:
        self.text = text
        self._cached_width = 0  # invalidate cache

    def render(self, width: int) -> list[str]:
        if width == self._cached_width and self._cached_lines:
            return self._cached_lines
        self._cached_width = width
        self._cached_lines = self._render_markdown(width)
        return self._cached_lines

    def _render_markdown(self, width: int) -> list[str]:
        lines: list[str] = []
        for raw_line in self.text.split("\n"):
            line = raw_line.rstrip()
            if not line:
                lines.append("")
                continue

            # 标题
            m = re.match(r"^(#{1,3})\s+(.*)", line)
            if m:
                tag = f"h{len(m.group(1))}"
                text = self._apply_inline(m.group(2))
                lines.append(self.STYLES[tag] + text + self.STYLES["reset"])
                continue

            # 代码块
            if line.startswith("```"):
                continue

            # 引用
            if line.startswith(">"):
                text = self._apply_inline(line[1:].strip())
                lines.append(self.STYLES["quote"] + "│ " + text + self.STYLES["reset"])
                continue

            # 无序列表
            if line.startswith("- ") or line.startswith("* "):
                text = self._apply_inline(line[2:])
                lines.append(self.STYLES["list"] + " • " + self.STYLES["reset"] + text)
                continue

            # 内联样式
            lines.append(self._apply_inline(line))

        return lines

    def _apply_inline(self, text: str) -> str:
        """处理 **加粗** `行内代码` 等。"""
        # 行内代码 `code`
        text = re.sub(
            r"`([^`]+)`",
            lambda m: self.STYLES["code_bg"] + self.STYLES["code"]
            + m.group(1) + self.STYLES["reset"],
            text,
        )
        # 加粗 **text**
        text = re.sub(
            r"\*\*(.+?)\*\*",
            lambda m: self.STYLES["bold"] + m.group(1) + self.STYLES["reset"],
            text,
        )
        return text


# ── 语法高亮代码块 ──────────────────────────────────


class CodeBlock(Component):
    """渲染带语法高亮的代码块。"""

    def __init__(self, code: str, language: str = ""):
        self.code = code
        self.language = language
        self._cached: list[str] = []
        self._cached_width = 0

    def render(self, width: int) -> list[str]:
        if width == self._cached_width and self._cached:
            return self._cached
        self._cached_width = width

        try:
            lexer = get_lexer_by_name(self.language) if self.language else guess_lexer(self.code)
        except Exception:
            from pygments.lexers import PythonLexer
            lexer = PythonLexer()

        raw = highlight(self.code, lexer, Terminal256Formatter()).rstrip("\n")
        lines = raw.split("\n")

        # 添加行号
        result: list[str] = []
        digits = len(str(len(lines)))
        for i, line in enumerate(lines):
            num = f"\x1b[38;5;240m{str(i+1).rjust(digits)}\x1b[0m "
            result.append(num + line)

        self._cached = result
        return result


# ── 状态栏 ──────────────────────────────────────────


class StatusBar(Component):
    """底部状态栏，左侧信息，右侧信息。"""

    def __init__(self, left: str = "", right: str = ""):
        self.left = left
        self.right = right

    def render(self, width: int) -> list[str]:
        if width <= 0:
            return [""]
        # 反色
        l = self.left[: width - 2]
        r = self.right[: width - 2]
        space = width - visible_width(l) - visible_width(r)
        if space < 2:
            l = l[: max(0, width - visible_width(r) - 2)]
            space = 2
        line = l + " " * space + r
        return [f"\x1b[7m{pad_to(line, width)}\x1b[0m"]


# ── 分隔线 ──────────────────────────────────────────


class Separator(Component):
    """水平分隔线。"""

    def __init__(self, char: str = "─"):
        self.char = char

    def render(self, width: int) -> list[str]:
        return [f"\x1b[38;5;240m{self.char * width}\x1b[0m"]


# ── 输入行 ───────────────────────────────────────────


class InputLine(Component):
    """渲染用户当前输入 + 可见光标。"""

    _PROMPT = "\x1b[38;5;39m>\x1b[0m "

    def __init__(self):
        self.text = ""
        self.cursor = 0

    def render(self, width: int) -> list[str]:
        prompt_w = visible_width(self._PROMPT)
        avail = max(1, width - prompt_w)

        # 显示光标：反色当前字符
        if self.cursor < len(self.text):
            before = self.text[:self.cursor]
            at = self.text[self.cursor]
            after = self.text[self.cursor + 1:]
            display = before + f"\x1b[7m{at}\x1b[0m" + after
        else:
            display = self.text + "\x1b[7m \x1b[0m"

        # 超宽时左滚（保持光标在可视区域右侧）
        d_w = visible_width(display)
        if d_w > avail:
            # 找到合适的截断点
            short = display
            while visible_width(short) > avail - 2 and len(short) > 4:
                short = short[1:]
            display = "\x1b[38;5;240m..\x1b[0m" + short

        return [self._PROMPT + display]
