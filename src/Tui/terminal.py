"""终端抽象：raw mode、SIGWINCH、stdout 写。"""

from __future__ import annotations

import asyncio
import atexit
import os
import fcntl
import signal
import struct
import sys
import termios
import tty
from collections.abc import Callable


class Terminal:
    """封装终端的原始写入、大小获取、raw mode。"""

    def __init__(self):
        self.width: int = 80
        self.height: int = 24
        self._fd = sys.stdout.fileno()
        self._original_termios: list | None = None
        self._winch_handlers: list[Callable[[], None]] = []
        self._update_size()
        atexit.register(self.cleanup)

    # ── 尺寸 ────────────────────────────────────────

    def _update_size(self) -> None:
        try:
            buf = struct.pack("HHHH", 0, 0, 0, 0)
            result = fcntl.ioctl(self._fd, termios.TIOCGWINSZ, buf)
            h, w = struct.unpack("HHHH", result)[:2]
            if w > 0 and h > 0:
                self.width = w
                self.height = h
        except Exception:
            pass

    def on_resize(self, handler: Callable[[], None]) -> None:
        """注册 SIGWINCH 处理器。"""
        self._winch_handlers.append(handler)

    def _handle_sigwinch(self, signum, frame) -> None:
        old_w, old_h = self.width, self.height
        self._update_size()
        if self.width != old_w or self.height != old_h:
            for h in self._winch_handlers:
                h()

    # ── Raw mode ────────────────────────────────────

    def enter_raw(self) -> None:
        fd = sys.stdin.fileno()
        self._original_termios = termios.tcgetattr(fd)
        tty.setraw(fd)

        # SIGWINCH
        signal.signal(signal.SIGWINCH, self._handle_sigwinch)

        # DEC 同步输出协议启动
        self.write("\x1b[?2026h")

    def cleanup(self) -> None:
        if self._original_termios is not None:
            fd = sys.stdin.fileno()
            termios.tcsetattr(fd, termios.TCSANOW, self._original_termios)
            self._original_termios = None
        # DEC 同步输出协议结束
        self.write("\x1b[?2026l")
        # 显示光标、重置样式
        self.write("\x1b[?25h\x1b[0m")

    # ── 输出 ────────────────────────────────────────

    def write(self, s: str) -> None:
        sys.stdout.buffer.write(s.encode("utf-8", errors="replace"))

    def flush(self) -> None:
        sys.stdout.buffer.flush()

    def move_to(self, row: int, col: int = 0) -> str:
        """返回移动光标到 (row, col) 的 ANSI 序列（1-based）。"""
        return f"\x1b[{row + 1};{col + 1}H"

    def clear_line(self) -> str:
        return "\x1b[2K"

    def hide_cursor(self) -> str:
        return "\x1b[?25l"

    def show_cursor(self) -> str:
        return "\x1b[?25h"
