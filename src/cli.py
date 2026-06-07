"""带输入框、实时进度、Token 统计和环状 Spinner 的 REPL。"""

import itertools
import os
import re
import sys
import threading
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from src.agent import Agent
from src.trace.store import RunStore

style = Style.from_dict({"prompt": "ansicyan bold"})

# ── 推理流式渲染状态 ──
_reasoning_active = False

# ── 上下文进度条 ──
_ctx_bar = {"prompt_tokens": 0, "max_tokens": 1_000_000}


def _ctx_bar_text() -> str:
    total = _ctx_bar["prompt_tokens"]
    mx = _ctx_bar["max_tokens"]
    pct = total / mx * 100 if mx > 0 else 0
    bar_len = 16
    filled = int(pct / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    pct_str = f"{pct:.1f}%" if total > 0 else "-"
    tok_str = f"{total:,}" if total > 0 else "0"
    return f" ctx: {bar} {pct_str} ({tok_str} / {mx:,})"

# ── 键绑定：Enter 提交，Alt+Enter（Esc+Enter）换行 ──
kb = KeyBindings()


@kb.add("enter")
def _submit(event):
    """按 Enter 提交当前输入。"""
    event.current_buffer.validate_and_handle()


@kb.add("escape", "enter")
def _newline(event):
    """按 Alt+Enter（或 Esc 后跟 Enter）插入换行。"""
    event.current_buffer.insert_text("\n")

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
    global _reasoning_active
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
    _ctx_bar["max_tokens"] = agent.context_window

    history_path = Path(workspace) / ".jarvis" / ".cli_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    psession = PromptSession(
        history=FileHistory(str(history_path)),
        style=style, enable_history_search=True,
        multiline=True, key_bindings=kb,
        bottom_toolbar=lambda: _ctx_bar_text(),
    )

    if resume:
        _notice(f"已恢复会话: {agent.session_id}")
    _print_banner(agent, workspace, approval)

    while True:
        try:
            query = psession.prompt("jarvis> ", style=style).strip()
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
                _ctx_bar["max_tokens"] = agent.context_window
            continue
        if query == "/context":
            _show_context(agent)
            continue

        # ── Plan Mode 命令 ────────────────────────────────────────
        if query.startswith("/plan "):
            topic = query[6:].strip()
            if not topic:
                print("  Usage: /plan <topic>")
                continue
            agent.mode = "plan"
            agent.topic = topic
            # slugify: 保留中文和字母数字，其它转短横线
            safe_topic = re.sub(r'[^\w\u4e00-\u9fff-]', '-', topic.lower())
            agent.plan_path = f".jarvis/plans/{safe_topic}-plan.md"
            # 确保 plans 目录存在
            (Path(agent.workspace_root) / ".jarvis" / "plans").mkdir(parents=True, exist_ok=True)
            _dim(f"  📋 进入计划模式，计划文件将写入 {agent.plan_path}")
            query = (
                f"请分析以下需求并制定详细计划，将完整的计划文件写入 `{agent.plan_path}`。\n\n"
                f"## 需求\n{topic}\n\n"
                f"请先探索相关代码，然后写出包含步骤、涉及文件、风险分析的计划。"
            )
            # 继续执行（进入 plan mode 的 agent.ask_stream）
        elif query == "/execute":
            if agent.mode != "plan" or not agent.plan_path:
                print("  没有待执行的计划。请先用 /plan <topic> 创建计划。")
                continue
            plan_file = Path(agent.workspace_root) / agent.plan_path
            if not plan_file.exists():
                print(f"  计划文件不存在: {agent.plan_path}")
                continue
            plan_content = plan_file.read_text(encoding="utf-8")
            agent._pending_plan = plan_content
            agent.mode = "default"
            saved_topic = agent.topic
            agent.topic = ""
            agent.plan_path = ""
            query = f"请按已被批准的计划执行：「{saved_topic}」"
            _dim(f"  ✅ 计划已批准，开始执行「{saved_topic}」")
        elif query == "/cancel":
            if agent.mode == "plan":
                agent.mode = "default"
                agent.topic = ""
                agent.plan_path = ""
                print("  计划已取消。")
            else:
                print("  当前没有活跃的计划。")
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

                # 推理流结束 → 换行
                if _reasoning_active and event["type"] != "reasoning":
                    print(flush=True)
                    _reasoning_active = False

                # 渲染
                _render_event(event)

                # 累计用量 & 上下文进度条
                if event.get("type") in ("tool_result", "final", "step_limit", "error"):
                    meta = agent.model_client.last_completion_metadata
                    usage = meta.get("usage", {}) or {}
                    prompt_tok += usage.get("prompt_tokens", 0) or 0
                    completion_tok += usage.get("completion_tokens", 0) or 0
                    cached_tok += meta.get("cached_tokens", 0) or 0
                    _ctx_bar["prompt_tokens"] = usage.get("prompt_tokens", 0) or 0

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

        # 安全清理：防止推理流未正常结束
        if _reasoning_active:
            print(flush=True)
            _reasoning_active = False

        # Token 汇总
        if prompt_tok or completion_tok:
            model = agent.model_client.model
            cost = _estimate_cost(model, prompt_tok, completion_tok)
            rate = cached_tok / prompt_tok * 100 if prompt_tok else 0
            mx = _ctx_bar["max_tokens"]
            ctx_pct = _ctx_bar["prompt_tokens"] / mx * 100 if mx > 0 else 0
            _dim(f"  ├─ 输入 {prompt_tok:>6} tok | 输出 {completion_tok:>6} tok | "
                 f"缓存 {cached_tok:>6} tok ({rate:.0f}%) | 费用 ${cost:.4f}")
            _dim(f"  └─ ctx {_ctx_bar['prompt_tokens']:>6} / {mx:,} tok ({ctx_pct:.1f}%)")
        print(flush=True)


# ── 工具函数 ──────────────────────────────────────────────────────

def _render_event(event: dict):
    global _reasoning_active
    t = event["type"]
    if t == "trace" and event.get("event") == "model_requested":
        _dim(f"  ── 模型调用 {event.get('seq', '')} ──")
    elif t == "reasoning":
        content = (event.get("content", "") or "")
        if not _reasoning_active:
            _reasoning_active = True
            sys.stdout.write(f"  🧠 {content}")
        else:
            sys.stdout.write(content)
        sys.stdout.flush()
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


def _find_latest_session(workspace: str, skip_id: str = "") -> str | None:
    """返回除了 skip_id 之外最近的一个有实际数据的会话目录路径。

    跳过空会话（创建后从未产生过 turn 数据），解决连续重启应用后
    /resume 恢复到了空会话而非真正有数据的会话的问题。
    """
    skipped_empty = 0
    sessions = Agent.list_sessions(workspace)
    for s in sessions:
        sid = s.get("session_id", "")
        if sid == skip_id:
            continue
        path = Path(workspace) / ".jarvis" / "sessions" / sid
        if not path.exists():
            continue
        # 跳过没有实际 turn 数据的空会话
        turns_dir = path / "turns"
        if not turns_dir.is_dir() or not any(turns_dir.iterdir()):
            skipped_empty += 1
            continue
        if skipped_empty:
            _dim(f"  （跳过 {skipped_empty} 个空会话）")
        return str(path)
    if skipped_empty:
        _dim(f"  （跳过 {skipped_empty} 个空会话，未找到有数据的会话）")
    return None


def _do_resume(agent: Agent) -> Agent | None:
    """恢复上一个会话，返回新 Agent 或 None。"""
    path = _find_latest_session(agent.workspace_root, skip_id=agent.session_id)
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


def _show_context(agent: Agent):
    """显示当前上下文的各 section 占比。"""
    stats = agent.ctx.context_stats()
    order = ["system", "history", "user", "assistant", "tool"]
    labels = {
        "system": "系统提示", "history": "历史消息",
        "user": "用户输入", "assistant": "模型回复",
        "tool": "工具结果",
    }
    total = stats["_total"]
    print(f"  总上下文: {total['chars']} 字符 ≈ {total['tokens']} tok")
    for key in order:
        s = stats[key]
        bar_len = 20
        filled = int(s["pct"] / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"  {bar} {labels[key]:8s} {s['chars']:>6} chars {s['pct']:>5.1f}% ({s['messages']} 条)")
    print()


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
    print("  /help 查看命令。  Alt+Enter 换行输入。")


def _print_help():
    print("  命令:")
    print("    /exit, /quit          退出")
    print("    /session              查看当前会话")
    print("    /sessions             列出所有会话")
    print("    /resume               恢复最近中断的会话")
    print("    /context              查看上下文各 section 占比")
    print("    /plan <topic>         进入计划模式，分析并写出计划文件")
    print("    /execute              批准计划并开始执行")
    print("    /cancel               取消当前计划")
    print("    /help                 帮助")
    print()
    print("  计划模式 (Plan Mode):")
    print("    /plan <topic>         分析代码、制定计划 → 写入 .jarvis/plans/")
    print("    /execute              读取计划、注入上下文 → 开始执行")
    print("    /cancel               丢弃当前计划，退出计划模式")
    print()
    print("  启动:")
    print("    python main.py             新建会话（ask 审批）")
    print("    python main.py auto        自动审批")
    print("    python main.py --resume    恢复最近中断的会话")
    print("    python main.py --resume <路径>  恢复指定会话")


if __name__ == "__main__":
    main()
