"""差分渲染引擎：逐行对比、ANSI 增量输出、60fps 节流。"""

from __future__ import annotations

import asyncio
import time

from src.Tui.component import Component
from src.Tui.terminal import Terminal


class Renderer:
    """管理组件树和渲染循环。

    每帧：
      1. 调用 root.render(width) 获得当前帧行列表
      2. 与上一帧逐行对比，只输出变化行
      3. request_render() 唤醒，16ms 节流
    """

    def __init__(self, terminal: Terminal, root: Component, fps: int = 60):
        self.terminal = terminal
        self.root = root
        self._frame_interval = 1.0 / fps
        self._prev_lines: list[str] = []
        self._prev_width = 0
        self._wake = asyncio.Event()
        self._running = False
        self._task: asyncio.Task | None = None

        terminal.on_resize(self.request_render)

    def request_render(self) -> None:
        """唤醒渲染循环。"""
        self._wake.set()

    # ── 启动 / 停止 ────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._render_loop())

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _render_loop(self) -> None:
        while self._running:
            await self._wake.wait()
            self._wake.clear()
            t0 = time.monotonic()
            try:
                self._render_frame()
            except Exception:
                pass  # 渲染异常不影响循环继续
            elapsed = time.monotonic() - t0
            if elapsed < self._frame_interval:
                await asyncio.sleep(self._frame_interval - elapsed)

    # ── 核心渲染 ────────────────────────────────────

    def _render_frame(self) -> None:
        width = self.terminal.width
        height = self.terminal.height

        # 传播终端尺寸（Layout 等需要此信息分配子组件高度）
        self.root.set_terminal_size(height, width)

        lines = self.root.render(width)

        # 限制行数
        if len(lines) > height:
            lines = lines[:height]

        # 全量重绘（宽度变化 or 首帧）
        if width != self._prev_width or not self._prev_lines:
            self._full_redraw(lines)
            self._prev_lines = lines
            self._prev_width = width
            return

        # 差分更新
        buf: list[str] = []
        max_lines = max(len(self._prev_lines), len(lines))

        for i in range(max_lines):
            old = self._prev_lines[i] if i < len(self._prev_lines) else ""
            new = lines[i] if i < len(lines) else ""
            if old != new:
                buf.append(self.terminal.move_to(i, 0))
                buf.append(self.terminal.clear_line())
                buf.append(new)

        # 清除多出的旧行
        if len(lines) < len(self._prev_lines):
            for i in range(len(lines), len(self._prev_lines)):
                buf.append(self.terminal.move_to(i, 0))
                buf.append(self.terminal.clear_line())

        # 光标移回内容末尾
        last_row = min(len(lines), height - 1)
        buf.append(self.terminal.move_to(last_row, 0))

        if buf:
            self.terminal.write("".join(buf))
            self.terminal.flush()

        self._prev_lines = lines
        self._prev_width = width

    def _full_redraw(self, lines: list[str]) -> None:
        buf: list[str] = [self.terminal.hide_cursor()]

        for i, line in enumerate(lines):
            buf.append(self.terminal.move_to(i, 0))
            buf.append(self.terminal.clear_line())
            buf.append(line)

        # 清除剩余区域
        for i in range(len(lines), self.terminal.height):
            buf.append(self.terminal.move_to(i, 0))
            buf.append(self.terminal.clear_line())

        last_row = len(lines) - 1 if lines else 0
        buf.append(self.terminal.move_to(last_row, 0))
        buf.append(self.terminal.show_cursor())
        self.terminal.write("".join(buf))
        self.terminal.flush()
