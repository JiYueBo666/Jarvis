import asyncio
from typing import List
from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.layout import VSplit, HSplit, Window, FormattedTextControl
from prompt_toolkit.widgets import Frame
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import ScrollOffsets
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

# ===================== 初始化 =====================
rich_console = Console()
chat_history: List[dict] = []

# 输入缓冲区（多行输入）
input_buffer = Buffer(multiline=True)

# 快捷键绑定
kb = KeyBindings()


# ===================== 快捷键 =====================
@kb.add("enter")
def send_message(event):
    """Enter 发送消息"""
    text = input_buffer.text.strip()
    if not text:
        return
    chat_history.append({"role": "user", "content": text})
    input_buffer.reset()
    asyncio.create_task(ai_reply_stream())


@kb.add("s-enter")
def new_line(event):
    """Shift+Enter 换行"""
    input_buffer.insert_text("\n")


@kb.add("ctrl-q")
def quit_app(event):
    event.app.exit()


@kb.add("ctrl-c")
def clear_input(event):
    input_buffer.reset()


# ===================== 核心渲染函数 =====================
def render_chat():
    """把聊天记录渲染成 ANSI 字符串给 prompt_toolkit 显示"""
    lines = []
    for msg in chat_history:
        if msg["role"] == "user":
            panel = Panel(msg["content"], title="👤 You", border_style="blue", width=80)
        else:
            panel = Panel(
                Markdown(msg["content"]),
                title="🤖 Claude",
                border_style="green",
                width=80,
            )

        with rich_console.capture() as capture:
            rich_console.print(panel)
            rich_console.print("")
        lines.append(capture.get())

    return "".join(lines)


# ===================== AI 流式回复 =====================
async def ai_reply_stream():
    chat_history.append({"role": "assistant", "content": ""})
    content = (
        "## 运行成功！\n\n"
        "这是一个 **Claude Code 风格终端 TUI**\n\n"
        "```python\nprint('Hello TUI!')\n```\n\n"
        "- 左侧：聊天区（支持 Markdown / 代码高亮）\n"
        "- 右侧：多行输入\n"
        "- Enter 发送 | Shift+Enter 换行 | Ctrl+Q 退出"
    )

    partial = ""
    for char in content:
        partial += char
        chat_history[-1]["content"] = partial
        app.invalidate()  # 强制刷新界面
        await asyncio.sleep(0.01)


# ===================== 布局 =====================
def build_layout():
    # 聊天窗口
    chat_control = FormattedTextControl(text=render_chat)
    chat_window = Window(
        chat_control, scroll_offsets=ScrollOffsets(bottom=2), wrap_lines=True
    )
    chat_frame = Frame(chat_window, title="Chat")

    # 输入窗口
    input_win = Window(
        content=FormattedTextControl(lambda: input_buffer.text), wrap_lines=True
    )
    input_frame = Frame(input_win, title="Input (Enter=Send, Shift+Enter=Newline)")

    # 左右分栏
    root = VSplit([chat_frame, Window(width=1), input_frame])
    return HSplit([root])


# ===================== 启动 =====================
app = Application(
    layout=build_layout(), key_bindings=kb, full_screen=True, mouse_support=True
)

if __name__ == "__main__":
    asyncio.run(app.run_async())
