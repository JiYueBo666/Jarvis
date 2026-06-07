"""带输入框、实时进度、Token 统计和环状 Spinner 的 REPL。"""

import itertools
import os
import sys
import threading
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

from src.agent import Agent
from src.trace.store import RunStore

style = Style.from_dict({"prompt": "ansicyan bold"})

# ── 计费参考（$/1M tokens）──
PRICING = {
    "gpt-4o":        (2.50, 10.00),
    "gpt-4o-mini":   (0.15, 0.60),
    "deepseek-chat": (0.27, 1.10),
    "claude-3.5":    (3.00, 15.00),
}


# ── Spinner ──────────────────────────────────────────────────────

class Spinner:
    """在后台线程运行的环状转圈指示器。

    每次输出固定占一行（末尾不换行），stop 时用空白覆盖该行，
    确保后续 print 不会和 spinner 行重叠。
    """

    def __init__(self, message: str = ""):
        self._chars = itertools.cycle(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"])
        self._message = message
        self._line_pos = 0
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        """启动 spinner。第一帧在主线程输出，后续帧后台线程覆盖同一行。"""
        sys.stdout.write(f"  {next(self._chars)} {self._message}")
        sys.stdout.flush()
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(0.3)
        # 用空格覆盖 spinner 行，后续 print 直接覆盖这一行
        sys.stdout.write(f"\r{'':>50}\r")
        sys.stdout.flush()

    def _spin(self):
        while self._running:
            sys.stdout.write(f"\r  {next(self._chars)} {self._message}")
            sys.stdout.flush()
            time.sleep(0.08)


# ── Token 统计 ───────────────────────────────────────────────────

def _estimate_cost(model: str, prompt_tok: int, completion_tok: int) -> float:
    price = PRICING.get(model.split("/")[0].lower(), (2.50, 10.00))  # 默认 gpt-4o
    return (prompt_tok * price[0] + completion_tok * price[1]) / 1_000_000


# ── CLI 主循环 ──────────────────────────────────────────────────

def main():
    workspace = os.getcwd()
    approval = "ask"
    resume = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ("ask", "auto", "never"):
            approval = args[i]
        elif args[i] == "--resume" and i + 1 < len(args):
            resume = args[i + 1]; i += 1
        elif args[i] == "--resume":
            resume = _find_latest_session(workspace)
        i += 1

    if resume:
        agent = Agent(workspace_root=workspace, approval_policy=approval, session_dir=resume)
    else:
        agent = Agent(workspace_root=workspace, approval_policy=approval)

    history_path = Path(workspace) / ".jarvis" / ".cli_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    psession = PromptSession(
        history=FileHistory(str(history_path)),
        style=style, enable_history_search=True,
    )

    if resume:
        _notice(f"已恢复会话: {agent.session_id}")
    _print_banner(agent, workspace, approval)

    while True:
        try:
            query = psession.prompt("jarvis> ", style=style, multiline=False).strip()
        except (EOFError, KeyboardInterrupt):
            print(); break

        if not query:
            continue
        if query in ("/exit", "/quit"):
            break
        if query == "/session":
            print(f"  {agent.session_id} @ {agent.workspace_root}")
            continue
        if query == "/sessions":
            _list_sessions(agent); continue
        if query == "/resume":
            resumed = _do_resume(agent)
            if resumed:
                agent = resumed
            continue
        if query == "/help":
            _print_help(); continue

        print(flush=True)
        spinner = None
        prompt_tok = 0
        completion_tok = 0
        cached_tok = 0

        try:
            for event in agent.ask_stream(query):
                # 停 spinner
                if spinner:
                    spinner.stop()
                    spinner = None

                # 渲染
                _render_event(event)

                # 累计用量
                if event.get("type") in ("tool_result", "final", "step_limit", "error"):
                    meta = agent.model_client.last_completion_metadata
                    usage = meta.get("usage", {}) or {}
                    prompt_tok += usage.get("prompt_tokens", 0) or 0
                    completion_tok += usage.get("completion_tokens", 0) or 0
                    cached_tok += meta.get("cached_tokens", 0) or 0

                # 启动 spinner
                if event["type"] == "trace" and event.get("event") == "model_requested":
                    spinner = Spinner("模型推理中...")
                    spinner.start()
                elif event["type"] == "tool_call":
                    spinner = Spinner(f"执行 {event['name']}...")
                    spinner.start()
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)

        if spinner:
            spinner.stop()

        # Token 汇总
        if prompt_tok or completion_tok:
            model = agent.model_client.model
            cost = _estimate_cost(model, prompt_tok, completion_tok)
            rate = cached_tok / prompt_tok * 100 if prompt_tok else 0
            _dim(f"  ├─ 输入 {prompt_tok:>6} tok | 输出 {completion_tok:>6} tok | "
                 f"缓存 {cached_tok:>6} tok ({rate:.0f}%) | 费用 ${cost:.4f}")
        print(flush=True)


# ── 工具函数 ──────────────────────────────────────────────────────

def _render_event(event: dict):
    t = event["type"]
    if t == "trace" and event.get("event") == "model_requested":
        _dim(f"  ── 模型调用 {event.get('seq', '')} ──")
    elif t == "reasoning":
        for line in (event.get("content", "") or "").strip().split("\n"):
            _dim(f"  🧠 {line[:200]}")
    elif t == "tool_call":
        _tool_line(event["name"], _compact_args(event.get("args", {})))
    elif t == "tool_result":
        output = event.get("output", "")
        first = output.split("\n")[0] if output else "(空)"
        _dim(f"  → {first[:200]}")
    elif t == "approval_required":
        _handle_approval(event)
    elif t == "final":
        print(f"\n{event.get('text', '')}", flush=True)
    elif t == "step_limit":
        print(f"\n  [步数上限] {event.get('text', '')}", flush=True)
    elif t == "error":
        print(f"\n  [错误] {event.get('message', '')}", flush=True)


def _handle_approval(event: dict):
    name = event["name"]
    args = _compact_args(event.get("args", {}))
    try:
        answer = input(f"  允许 {name}({args})？[Y/n/a] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    if answer == "a":
        event["approval"]["decision"] = True
        event["approval"]["auto"] = True
        _dim("  → 已允许，本轮后续不再询问")
    elif answer == "n":
        event["approval"]["decision"] = False
        _dim("  → 已拒绝")
    else:
        event["approval"]["decision"] = True


def _tool_line(name: str, args_str: str):
    print(f"  \033[36m{name}\033[0m({args_str})", flush=True)


def _dim(text: str):
    print(f"\033[2m{text}\033[0m", flush=True)


def _notice(text: str):
    print(f"\033[33m{text}\033[0m", flush=True)


def _compact_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 80:
            s = s[:77] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


def _find_latest_session(workspace: str) -> str | None:
    """返回最近一个会话目录的路径。"""
    sessions = Agent.list_sessions(workspace)
    if not sessions:
        return None
    latest = sessions[0]
    sid = latest.get("session_id", "")
    path = Path(workspace) / ".jarvis" / "sessions" / sid
    return str(path) if path.exists() else None


def _do_resume(agent: Agent) -> Agent | None:
    """恢复上一个会话，返回新 Agent 或 None。"""
    path = _find_latest_session(agent.workspace_root)
    if not path:
        print("  没有历史会话。")
        return None
    new_agent = Agent(
        workspace_root=agent.workspace_root,
        approval_policy=agent.approval_policy,
        session_dir=str(path),
        prompt_caching=agent.prompt_caching,
        max_new_tokens=agent.max_new_tokens,
        history_budget=agent.history_budget,
    )
    _notice(f"已恢复会话: {new_agent.session_id}")
    return new_agent


def _list_sessions(agent: Agent):
    sessions = Agent.list_sessions(agent.workspace_root)
    if not sessions:
        print("  （无会话记录）")
        return
    for s in sessions[:10]:
        sid = s.get("session_id", "?")
        status = s.get("status", "?")
        count = s.get("turn_count", 0)
        print(f"  {sid}  turn={count}  status={status}")
    if len(sessions) > 10:
        print(f"  ... 共 {len(sessions)} 个会话")


def _print_banner(agent: Agent, workspace: str, approval: str):
    print("\033[2m" + "─" * 50 + "\033[0m")
    print("  Agent-mini")
    print(f"  Model:   {agent.model_client.model}")
    print(f"  Session: {agent.session_id}")
    print(f"  Cwd:     {workspace}")
    print(f"  审批:     {approval}")
    print("\033[2m" + "─" * 50 + "\033[0m")
    print("  /help 查看命令。\n")


def _print_help():
    print("  命令:")
    print("    /exit, /quit    退出")
    print("    /session        查看当前会话")
    print("    /sessions       列出所有会话")
    print("    /resume         恢复最近中断的会话")
    print("    /help           帮助")
    print()
    print("  启动:")
    print("    python main.py             新建会话（ask 审批）")
    print("    python main.py auto        自动审批")
    print("    python main.py --resume    恢复最近中断的会话")
    print("    python main.py --resume <路径>  恢复指定会话")


if __name__ == "__main__":
    main()
