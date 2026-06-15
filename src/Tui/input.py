"""原始输入：raw mode、Kitty 键盘协议解码。"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable

from src.Tui.terminal import Terminal

# ── 美化键名映射 ─────────────────────────────────────

_KEY_NAMES: dict[str, str] = {
    "\r": "enter",
    "\n": "enter",
    "\t": "tab",
    "\x1b": "escape",
    "\x7f": "backspace",
    "\x03": "ctrl_c",
    "\x15": "ctrl_u",
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "\x1b[H": "home",
    "\x1b[F": "end",
    "\x1b[2~": "insert",
    "\x1b[3~": "delete",
    "\x1b[5~": "page_up",
    "\x1b[6~": "page_down",
    "\x1b[Z": "shift_tab",
}

# ── UTF-8 解码工具 ──────────────────────────────────

# UTF-8 首字节 → 序列长度映射
_UTF8_LEN: dict[int, int] = {}
for b in range(0x00, 0x80):
    _UTF8_LEN[b] = 1
for b in range(0xC2, 0xE0):
    _UTF8_LEN[b] = 2
for b in range(0xE0, 0xF0):
    _UTF8_LEN[b] = 3
for b in range(0xF0, 0xF8):
    _UTF8_LEN[b] = 4
# 0x80-0xBF / 0xF8-0xFF 不是合法首字节 → 非法


def _expected_len(first_byte: int) -> int:
    """返回 UTF-8 序列预期长度，非法首字节返回 1（跳过一个字节）。"""
    return _UTF8_LEN.get(first_byte, 1)


def _decode_one(data: bytes) -> tuple[str, bytes] | None:
    """尝试从 data 头部解码一个 UTF-8 字符。

    返回 (char, rest) 解码成功。
    返回 None 表示数据不够，需要读更多字节。
    """
    if not data:
        return None
    b0 = data[0]
    n = _expected_len(b0)
    if n > len(data):
        return None  # 等待更多字节
    try:
        ch = data[:n].decode("utf-8")
        return ch, data[n:]
    except UnicodeDecodeError:
        # 非法序列，跳过这个字节
        ch = data[:1].decode("utf-8", errors="replace")
        return ch, data[1:]


# ── 输入读取器 ──────────────────────────────────────


class InputReader:
    """异步读取 stdin，解码键盘事件。"""

    def __init__(self, terminal: Terminal):
        self.terminal = terminal
        self._buffer = b""

    async def read(self) -> str:
        """读取一个事件。返回原始字符串。"""
        loop = asyncio.get_event_loop()

        while True:
            # 尝试解码一个字符
            result = _decode_one(self._buffer)
            if result is not None:
                ch, self._buffer = result
                # ESC 开头：尝试收集完整 CSI 序列
                if ch == "\x1b":
                    seq = ch
                    for _ in range(8):
                        b = await loop.run_in_executor(
                            None, os.read, sys.stdin.fileno(), 1
                        )
                        if not b:
                            break
                        s = b.decode("utf-8", errors="replace")
                        seq += s
                        if b == b"\x1b":
                            self._buffer = seq[1:].encode() + self._buffer
                            return seq[0]
                        if b[-1] in b"ABCDEFGHabcdefgh~u":
                            return seq
                    return seq
                return ch

            # 数据不够 → 读一个字节
            b = await loop.run_in_executor(None, os.read, sys.stdin.fileno(), 1)
            if b:
                self._buffer += b

    def decode(self, raw: str) -> str:
        """将原始输入转为美化键名（已知序列）或原样返回。"""
        return _KEY_NAMES.get(raw, raw)


# ── 主动输入（历史积累） ─────────────────────────────


class LineInput:
    """单行文本输入，支持编辑。"""

    def __init__(self):
        self.text = ""
        self.cursor = 0  # 字节偏移
        self.history: list[str] = []
        self._history_idx = -1
        self._dirty = True

    def insert(self, ch: str) -> None:
        self.text = self.text[: self.cursor] + ch + self.text[self.cursor :]
        self.cursor += len(ch)
        self._dirty = True

    def delete_before(self) -> None:
        if self.cursor > 0:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
            self.cursor -= 1
            self._dirty = True

    def delete_after(self) -> None:
        if self.cursor < len(self.text):
            self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]
            self._dirty = True

    def move_left(self) -> None:
        if self.cursor > 0:
            self.cursor -= 1

    def move_right(self) -> None:
        if self.cursor < len(self.text):
            self.cursor += 1

    def home(self) -> None:
        self.cursor = 0

    def end(self) -> None:
        self.cursor = len(self.text)

    def history_back(self) -> None:
        if self.history and self._history_idx > 0:
            self._history_idx -= 1
            self.text = self.history[self._history_idx]
            self.cursor = len(self.text)

    def history_forward(self) -> None:
        if self._history_idx < len(self.history) - 1:
            self._history_idx += 1
            self.text = self.history[self._history_idx]
            self.cursor = len(self.text)
        else:
            self._history_idx = len(self.history)
            self.text = ""
            self.cursor = 0

    def submit(self) -> str | None:
        if not self.text.strip():
            return None
        self.history.append(self.text)
        self._history_idx = len(self.history)
        result = self.text
        self.text = ""
        self.cursor = 0
        self._dirty = True
        return result
