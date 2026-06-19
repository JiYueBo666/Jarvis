"""带输入框、实时进度和 Token 统计的 REPL。

通过 AgentSession 订阅事件流，保持原有 UI 风格。
"""

import asyncio
import itertools
import os
import queue
import shutil
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from prompt_toolkit.application import Application as PTApp
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window, FloatContainer, Float
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from src.AgentSession.agent_session import AgentSession
from src.context.session_manager import SessionManager
from src.TaskManager import TaskManager
from src.data.event import (
    AgentEnd,
    ApprovalRequired,
    CompactionEnd,
    CompactionStart,
    RetryEnd,
    RetryStart,
    MessageEnd,
    MessageStart,
    MessageUpdate,
    ToolExecutionEnd,
    ToolExecutionStart,
    TurnStart,
)
from src.data.messages import TextContent, ThinkingContent, ToolCallContent, Usage
from src.engine.model import ModelClient
from src.tools import build_registry
from loguru import logger as log

PRICING = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}


class Spinner:
    def __init__(self, message: str = ""):
        self._chars = itertools.cycle(
            ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        )
        self._message = message
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        sys.stdout.write(f"  {next(self._chars)} {self._message}")
        sys.stdout.flush()
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(0.3)
        sys.stdout.write(f"\r{'':>50}\r")
        sys.stdout.flush()

    def _spin(self):
        while self._running:
            sys.stdout.write(f"\r  {next(self._chars)} {self._message}")
            sys.stdout.flush()
            time.sleep(0.08)


def _estimate_cost(model: str, prompt_tok: int, completion_tok: int) -> float:
    price = PRICING.get(model.lower(), (0.27, 1.10))
    return (prompt_tok * price[0] + completion_tok * price[1]) / 1_000_000


def _extract_usage(msg) -> Usage | None:
    if hasattr(msg, "usage") and msg.usage:
        return msg.usage
    for block in getattr(msg, "content", []) or []:
        if isinstance(block, Usage):
            return block
    return None


def _close_panels():
    global _thinking_panel, _text_panel, _reasoning_active
    if _thinking_panel:
        _thinking_panel.end()
        _thinking_panel = None
    if _text_panel:
        _text_panel.end()
        _text_panel = None
    _reasoning_active = False


def _term_width():
    try:
        return shutil.get_terminal_size().columns
    except OSError:
        return 80


class StreamPanel:
    """Streaming text panel with top/bottom borders and optional background.

    Handles arbitrary text chunks by splitting on newlines. Completed lines
    get full-width background via \\033[K (erase-to-end-of-line honors bg color).
    """

    def __init__(self, title: str = "", bg: str = "", fg: str = ""):
        self.title = title
        self.bg = bg
        self.fg = fg
        self._open = False
        self._at_line_start = True

    def _codes(self) -> str:
        c = ""
        if self.bg:
            c += f"\033[{self.bg}m"
        if self.fg:
            c += f"\033[{self.fg}m"
        return c

    def start(self):
        w = _term_width()
        label = f" {self.title} " if self.title else ""
        fill = "\u2500" * max(0, w - 2 - len(label))
        sys.stdout.write(f"  \u250c{label}{fill}\n")
        sys.stdout.flush()
        self._open = True
        self._at_line_start = True

    def write(self, text: str):
        if not self._open:
            self.start()
        if not text:
            return
        parts = text.split("\n")
        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1
            if self._at_line_start and (part or not is_last):
                sys.stdout.write(f"  \u2502 {self._codes()}")
                self._at_line_start = False
            if part:
                sys.stdout.write(part)
            if not is_last:
                if self.bg:
                    sys.stdout.write(f"\033[{self.bg}m\033[K")
                sys.stdout.write("\033[0m\n")
                self._at_line_start = True
        sys.stdout.flush()

    def end(self):
        if not self._open:
            return
        if not self._at_line_start:
            if self.bg:
                sys.stdout.write(f"\033[{self.bg}m\033[K")
            sys.stdout.write("\033[0m\n")
        w = _term_width()
        fill = "\u2500" * max(0, w - 2)
        sys.stdout.write(f"  \u2514{fill}\n")
        sys.stdout.flush()
        self._open = False


_reasoning_active = False
_thinking_panel: StreamPanel | None = None
_text_panel: StreamPanel | None = None


def _render_event(event):
    global _reasoning_active, _thinking_panel, _text_panel

    if isinstance(event, TurnStart):
        _dim(f"  ── 模型调用 ──")

    elif isinstance(event, MessageStart):
        pass

    elif isinstance(event, MessageUpdate):
        msg = event.message
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, ThinkingContent):
                    if not _reasoning_active:
                        _reasoning_active = True
                        _thinking_panel = StreamPanel(
                            title="\U0001f9e0 思考", bg="48;5;237", fg="38;5;248"
                        )
                        _thinking_panel.start()
                    _thinking_panel.write(block.thinking)
                elif isinstance(block, TextContent):
                    if _reasoning_active:
                        if _thinking_panel:
                            _thinking_panel.end()
                            _thinking_panel = None
                        _reasoning_active = False
                    if not _text_panel:
                        _text_panel = StreamPanel()
                        _text_panel.start()
                    _text_panel.write(block.text)

    elif isinstance(event, MessageEnd):
        msg = event.message
        if msg.role == "assistant":
            _close_panels()

    elif isinstance(event, ToolExecutionStart):
        intent = event.args.get("intent", "")
        _tool_line(event.tool_name, intent, _compact_args(event.args))

    elif isinstance(event, ToolExecutionEnd):
        output = ""
        for block in getattr(event.result, "content", []) or []:
            if isinstance(block, TextContent):
                output += block.text
        first = output.split("\n")[0] if output else "(空)"
        _dim(f"  → {first[:200]}")

    elif isinstance(event, ApprovalRequired):
        _handle_approval(event)

    elif isinstance(event, CompactionStart):
        _close_panels()
        _dim(f"  ⏳ 正在压缩对话历史...")

    elif isinstance(event, CompactionEnd):
        _dim(
            f"  ✅ 压缩完成: {event.messages_before} → {event.messages_after} 条"
        )

    elif isinstance(event, RetryStart):
        _close_panels()
        _dim(f"  🔄 正在重试...")

    elif isinstance(event, RetryEnd):
        if event.success:
            _dim(f"  ✅ 重试成功")
        else:
            _dim(f"  ❌ 重试失败")

    elif isinstance(event, AgentEnd):
        _close_panels()


def _tool_line(name: str, intent: str, args_str: str):
    if intent:
        print(f"  \033[2m▶\033[0m \033[37m{intent}\033[0m", flush=True)
    print(
        (
            f"  \033[36m⚙ {name}\033[0m({args_str})"
            if args_str
            else f"  \033[36m⚙ {name}\033[0m"
        ),
        flush=True,
    )


def _handle_approval(event: ApprovalRequired):
    name = event.tool_name
    intent = event.args.get("intent", "")
    args = _compact_args(event.args)
    inner = _term_width() - 4
    print()
    print(f"  \033[33m┌─── 🛡️  审批请求 {'─' * (inner - 12)}┐\033[0m")
    if intent:
        print(f"  \033[33m│\033[0m  \033[37m▶ {intent}\033[0m")
    print(
        f"  \033[33m│\033[0m  \033[1m{name}\033[0m({args})"
        if args
        else f"  \033[33m│\033[0m  \033[1m{name}\033[0m"
    )
    print(f"  \033[33m│\033[0m  \033[2mY 允许本次  a 允许本轮  n 拒绝\033[0m")
    print(f"  \033[33m└{'─' * inner}┘\033[0m")
    try:
        answer = (
            input(f"  \033[33m你的选择\033[0m [\033[32mY\033[0m/n/a]: ").strip().lower()
        )
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    if answer == "a":
        event.approve()
        print(f"  \033[32m✓ 已允许，本轮后续不再询问\033[0m")
    elif answer == "n":
        event.deny()
        print(f"  \033[31m✗ 已拒绝\033[0m")
    else:
        event.approve()
        print(f"  \033[32m✓ 已允许本次\033[0m")


def _dim(text: str):
    print(f"\033[2m{text}\033[0m", flush=True)


def _compact_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        if k == "intent":
            continue
        s = str(v)
        if len(s) > 80:
            s = s[:77] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


def _print_banner(model: str, workspace: str):
    print("\033[2m" + "─" * 50 + "\033[0m")
    print("  Agent-mini")
    print(f"  Model:   {model}")
    print(f"  Cwd:     {workspace}")
    print("\033[2m" + "─" * 50 + "\033[0m")
    print("  /help 查看命令。  Alt+Enter 换行输入。")


def _print_help():
    print("  命令:")
    print("    /exit, /quit          退出")
    print("    /help                 帮助")
    print("    /sessions             列出历史会话")
    print()
    print("  启动:")
    print("    python main.py             新建会话")


class SlashCommandCompleter(Completer):
    _COMMANDS = {
        "/exit": "退出程序",
        "/quit": "退出程序",
        "/help": "查看帮助",
        "/sessions": "列出历史会话",
    }

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/"):
            return
        query = text[1:].lower()
        for cmd, desc in self._COMMANDS.items():
            name = cmd[1:]
            if not query or name.startswith(query):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=cmd,
                    display_meta=desc,
                )


def bordered_prompt(
    history: FileHistory | None = None,
    completer: Completer | None = None,
) -> str:
    """Bordered input box with optional slash-command completer."""

    def _accept(b):
        get_app().exit(result=b.text)
        return True

    buf = Buffer(
        history=history,
        completer=completer,
        complete_while_typing=False,
        accept_handler=_accept,
    )

    def _auto_complete():
        if buf.text.lstrip().startswith("/"):
            try:
                import asyncio

                loop = asyncio.get_event_loop()
                loop.call_soon(lambda: buf.start_completion(select_first=False))
            except RuntimeError:
                pass

    buf.on_text_changed += lambda _: _auto_complete()

    def _top():
        import os as _os

        try:
            w = _os.get_terminal_size().columns
        except OSError:
            w = 80
        fill = "\u2500" * max(0, w - 2)
        return [("bold fg:ansicyan", f"\u256d{fill}")]

    def _bot():
        import os as _os

        try:
            w = _os.get_terminal_size().columns
        except OSError:
            w = 80
        hints = "\u2500 Enter \u00b7 Alt+Enter \u00b7 /help "
        fill = "\u2500" * max(0, w - 2 - len(hints))
        return [("fg:ansicyan", f"\u2570{fill}{hints}")]

    def _line_prefix(lineno, wrap_count):
        if lineno == 0 and wrap_count == 0:
            return [("bold fg:ansicyan", "> ")]
        return [("", "  ")]

    body = FloatContainer(
        content=HSplit(
            [
                Window(FormattedTextControl(_top), height=1, dont_extend_height=True),
                Window(
                    BufferControl(buffer=buf),
                    get_line_prefix=_line_prefix,
                    height=Dimension(min=1),
                    dont_extend_height=True,
                    wrap_lines=True,
                ),
                Window(FormattedTextControl(_bot), dont_extend_height=True),
            ]
        ),
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                content=CompletionsMenu(max_height=8, scroll_offset=1),
            ),
        ],
    )

    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        buf.validate_and_handle()

    @kb.add("escape", "enter")
    def _newline(event):
        buf.insert_text("\n")

    @kb.add("c-c")
    def _cancel(event):
        event.app.exit(exception=KeyboardInterrupt())

    @kb.add("c-d")
    def _eof(event):
        if not buf.text:
            event.app.exit(exception=EOFError())

    app = PTApp(
        layout=Layout(body),
        key_bindings=kb,
        full_screen=False,
    )
    app.layout.focus(buf)
    return app.run()


slash_completer = SlashCommandCompleter()


def _handle_command(query: str, session: AgentSession | None = None) -> str | None:
    """Handle slash commands. Returns 'exit', 'handled', or None."""
    if not query.startswith("/"):
        return None
    parts = query.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    if cmd in ("/exit", "/quit"):
        return "exit"
    if cmd == "/help":
        _print_help()
        return "handled"
    if cmd == "/sessions":
        if session is None:
            _dim("无会话管理器")
            return "handled"
        sessions = session.list_sessions()
        if not sessions:
            _dim("没有保存的会话")
        else:
            print(f"  会话列表 ({len(sessions)}):")
            for i, s in enumerate(sessions, 1):
                _dim(f"  {i}. {s.session_id}  {s.title}  ({s.message_count} 条消息)")
        return "handled"
    _dim(f"未知命令：{cmd}，输入 /help 查看可用命令")
    return "handled"


def main():
    log.remove()
    log.add(
        sys.stderr,
        format="<dim>{time:HH:mm:ss}</dim> | <level>{message}</level>",
        colorize=True,
    )
    log.add(
        ".jarvis/logs/{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention=7,
        level="DEBUG",
    )

    workspace = os.getcwd()
    model = os.environ.get("LLM_MODEL", os.environ.get("MODEL", "deepseek-chat"))
    base_url = os.environ.get(
        "OPENAI_BASE_URL", os.environ.get("BASE_URL", "https://api.deepseek.com/v1")
    )
    api_key = os.environ.get("OPENAI_API_KEY", os.environ.get("API_KEY", ""))

    model_client = ModelClient(model=model, base_url=base_url, api_key=api_key)
    system_prompt = "你是一个 helpful 的编程助手。"
    tools = list(build_registry(workspace_root=workspace).values())
    session_mgr = SessionManager(Path(workspace) / ".jarvis" / "sessions")

    _print_banner(model, workspace)

    task_mgr = TaskManager(str(Path(workspace) / ".jarvis" / "tasks"))

    def _on_tool_result(name, args, result):
        files = getattr(result, "metadata", {}).get("affected_paths", [])
        task_mgr.record_tool(name, affected_files=files)

    session = AgentSession(
        model_client=model_client,
        system_prompt=system_prompt,
        tools=tools,
        session_manager=session_mgr,
        on_tool_result=_on_tool_result,
    )

    history_path = Path(workspace) / ".jarvis" / ".cli_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    file_history = FileHistory(str(history_path))

    loop = asyncio.new_event_loop()

    def run_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    loop_thread = threading.Thread(target=run_loop, daemon=True)
    loop_thread.start()

    while True:
        try:
            query = bordered_prompt(history=file_history, completer=slash_completer)
            query = query.strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query:
            continue

        cmd_result = _handle_command(query, session)
        if cmd_result == "exit":
            break
        if cmd_result:
            continue

        task_mgr.record_task(query)
        spinner: Spinner | None = None
        prompt_tok = 0
        completion_tok = 0
        cached_tok = 0

        event_queue: queue.Queue = queue.Queue()

        def listener(event):
            event_queue.put(event)

        unsub = session.subscribe(listener)

        try:
            future = asyncio.run_coroutine_threadsafe(session.prompt(query), loop)

            while True:
                try:
                    event = event_queue.get(timeout=0.05)
                except queue.Empty:
                    if future.done():
                        try:
                            future.result()
                        except Exception as exc:
                            print(f"Error: {exc}", file=sys.stderr)
                            break
                    continue

                if spinner:
                    spinner.stop()
                    spinner = None

                _render_event(event)

                if isinstance(event, TurnStart):
                    spinner = Spinner("模型推理中...")
                    spinner.start()
                elif isinstance(event, ToolExecutionStart):
                    spinner = Spinner(f"执行 {event.tool_name}...")
                    spinner.start()

                usage = None
                if isinstance(event, MessageEnd) and event.message.role == "assistant":
                    usage = _extract_usage(event.message)
                if usage:
                    prompt_tok += usage.input_tokens
                    completion_tok += usage.output_tokens
                    cached_tok += usage.cache_read_tokens

                if isinstance(event, AgentEnd):
                    task_mgr.finish("completed")
                    break

            future.result(timeout=5)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
        finally:
            unsub()
            if spinner:
                spinner.stop()
            _close_panels()

        if prompt_tok or completion_tok:
            cost = _estimate_cost(model, prompt_tok, completion_tok)
            _dim(
                f"  ├─ 输入 {prompt_tok:>6} tok | 输出 {completion_tok:>6} tok | "
                f"缓存 {cached_tok:>6} tok | 费用 ${cost:.4f}"
            )
        print(flush=True)

    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=1)
    loop.close()
    session.dispose()


if __name__ == "__main__":
    main()
