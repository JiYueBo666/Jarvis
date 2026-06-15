"""Coding Agent TUI — prompt_toolkit 实现。"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, Window, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Dialog, Button, Label

from src.AgentSession.agent_session import AgentSession
from src.Client.base import StreamEvent

# ── 样式 ───────────────────────────────────────────

STYLE = Style.from_dict({
    "status":             "reverse",
    "thinking":           "fg:ansigray italic",
    "user":               "fg:ansicyan bold",
    "assistant":          "fg:ansigreen",
    "error":              "fg:ansired bold",
    "tool-result":        "fg:ansiyellow dim",
    "input-field":        "bg:#222222 #ffffff",
    "dialog":             "bg:#444488 #ffffff",
    "dialog.body":        "bg:#333344 #ffffff",
    "button":             "bg:#555555 #ffffff",
    "button.focused":     "bg:#ffffff #000000 bold",
})


# ── 应用状态 ──────────────────────────────────────


class AppState:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []  # (style, text)
        self._streaming = ""
        self._thinking = ""
        self.app: Application | None = None

    def add_msg(self, role: str, content: str):
        style_map = {"user": "class:user", "assistant": "class:assistant",
                     "tool": "class:tool-result", "error": "class:error"}
        prefix_map = {"user": ">>> ", "assistant": "", "tool": "  └─ ", "error": "⚠ "}
        style = style_map.get(role, "")
        prefix = prefix_map.get(role, "")
        for i, line in enumerate(content.split("\n")):
            self.lines.append((style, (prefix if i == 0 else "   ") + line))
        self.lines.append(("", ""))  # 空行分隔

    def set_streaming(self, text: str):
        self._streaming = text

    def set_thinking(self, text: str):
        self._thinking = text

    def get_chat_lines(self):
        result = list(self.lines)
        if self._thinking:
            result.append(("class:thinking", f"── thinking ── {self._thinking[-200:]}"))
        if self._streaming:
            result.append(("class:assistant", f"▸ {self._streaming}"))
        return result


# ── 工具审批对话框 ─────────────────────────────────


def make_approval_dialog(tool_name: str, args: dict, on_approve, on_reject) -> Dialog:
    args_text = "\n".join(f"  {k}: {v}" for k, v in args.items())
    return Dialog(
        title="🔧 工具调用审批",
        body=Label(text=f"工具: {tool_name}\n参数:\n{args_text}"),
        buttons=[
            Button(text="✅ 允许", handler=on_approve),
            Button(text="❌ 拒绝", handler=on_reject),
        ],
        width=60,
        with_background=True,
    )


# ── 主函数 ────────────────────────────────────────


async def main():
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o")

    if not api_key:
        print("错误: 请设置 OPENAI_API_KEY 环境变量")
        sys.exit(1)

    session = AgentSession.create(api_key=api_key, base_url=base_url, model=model)

    state = AppState()
    kb = KeyBindings()
    approval_future: asyncio.Future[bool] | None = None
    approval_floats: list[Float] = []
    app: Application[None] | None = None

    # ── 事件处理器 ────────────────────────────────

    async def on_event(event: StreamEvent):
        match event.type:
            case "message_start":
                state.set_streaming("")
                state.set_thinking("")
            case "message_update":
                if event.message and event.message.content:
                    state.set_streaming(event.message.content)
            case "thinking":
                if event.data:
                    state.set_thinking(event.data)
            case "message_end":
                state.set_streaming("")
                state.set_thinking("")
                if event.message and event.message.content:
                    state.add_msg("assistant", event.message.content)
            case "error":
                state.add_msg("error", str(event.data))
        if app:
            app.invalidate()

    session.subscribe(on_event)

    # ── 工具审批 ──────────────────────────────────

    async def _approval_check(tool_name: str, args: dict) -> bool:
        nonlocal approval_future
        future: asyncio.Future[bool] = asyncio.Future()
        approval_future = future

        def approve():
            if not future.done():
                future.set_result(True)
            approval_floats.clear()
            if app:
                app.invalidate()

        def reject():
            if not future.done():
                future.set_result(False)
            approval_floats.clear()
            if app:
                app.invalidate()

        approval_floats.append(Float(
            content=make_approval_dialog(tool_name, args, approve, reject),
            top=4, bottom=4, left=5, right=5,
        ))
        if app:
            app.invalidate()

        try:
            return await future
        finally:
            approval_floats.clear()

    # ── 提交消息 ──────────────────────────────────

    async def handle_submit():
        buf = app.layout.current_buffer if app else None
        if not buf:
            return
        text = buf.text.strip()
        if not text:
            return
        buf.text = ""
        state.add_msg("user", text)
        app.invalidate()

        result = await session.prompt(text, approval_check=_approval_check)
        if result:
            state.add_msg("assistant", result)
            app.invalidate()

    # ── 键绑定 ────────────────────────────────────

    @kb.add("enter")
    async def enter(event):
        await handle_submit()

    @kb.add("c-c")
    def exit_app(event):
        event.app.exit()

    @kb.add("c-d")
    def ctrl_d(event):
        event.app.exit(exception=EOFError)

    # ── 布局 ──────────────────────────────────────

    chat_control = FormattedTextControl(text=lambda: state.get_chat_lines())
    chat_window = Window(content=chat_control, wrap_lines=True)

    status_line = FormattedTextControl(
        text=lambda: f" {model}  |  Ctrl+C 退出  |  Enter 发送",
        style="class:status",
    )
    status_window = Window(content=status_line, height=Dimension.exact(1),
                           align=WindowAlign.LEFT, style="class:status")

    input_buffer = Buffer(multiline=False)
    input_window = Window(
        content=BufferControl(buffer=input_buffer, tempfile_suffix=".txt"),
        height=Dimension.exact(1),
        style="class:input-field",
    )

    # 注意：FloatContainer 的 floats 参数需要一个 list
    # 我们在运行时修改 approval_floats 并通过 invalidate() 触发重绘
    root = FloatContainer(
        content=HSplit([chat_window, status_window, input_window]),
        floats=approval_floats,
    )

    app = Application[None](
        layout=Layout(root),
        key_bindings=kb,
        full_screen=True,
        style=STYLE,
        mouse_support=True,
    )
    state.app = app

    state.add_msg("assistant", f"Coding Agent 已就绪 (模型: {model})")
    state.add_msg("assistant", "输入 /help 查看命令")

    try:
        await app.run_async()
    except (EOFError, KeyboardInterrupt):
        pass


def cli():
    asyncio.run(main())


if __name__ == "__main__":
    cli()
