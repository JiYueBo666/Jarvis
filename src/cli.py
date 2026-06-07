"""带输入框和实时进度显示的 REPL。"""

import os
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

from src.agent import Agent

style = Style.from_dict({
    "prompt": "ansicyan bold",
})


def main():
    workspace = os.getcwd()
    # 从命令行参数读取审批策略（预留）
    approval = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in (
        "ask", "auto", "never",
    ) else "ask"

    agent = Agent(workspace_root=workspace, approval_policy=approval)

    history_path = Path(workspace) / ".jarvis" / ".cli_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    session = PromptSession(
        history=FileHistory(str(history_path)),
        style=style,
        enable_history_search=True,
    )

    _print_banner(agent, workspace, approval)

    while True:
        try:
            query = session.prompt("jarvis> ", style=style, multiline=False).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query:
            continue
        if query in ("/exit", "/quit"):
            break
        if query == "/session":
            print(f"  {agent.session_id} @ {agent.workspace_root}")
            continue
        if query == "/help":
            _print_help()
            continue

        print()
        try:
            for event in agent.ask_stream(query):
                _render_event(event)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
        print()


def _render_event(event: dict):
    t = event["type"]
    if t == "model_requested":
        _dim(f"  ── model call {event.get('seq', '')} ──")
    elif t == "reasoning":
        for line in (event.get("content", "") or "").strip().split("\n"):
            _dim(f"  🧠 {line[:200]}")
    elif t == "tool_call":
        _tool_line(event["name"], _compact_args(event.get("args", {})))
    elif t == "tool_result":
        output = event.get("output", "")
        first = output.split("\n")[0] if output else "(empty)"
        _dim(f"  → {first[:200]}")
    elif t == "approval_required":
        _handle_approval(event)
    elif t == "final":
        print(f"\n{event.get('text', '')}")
    elif t == "step_limit":
        print(f"\n  [步数上限] {event.get('text', '')}")
    elif t == "error":
        print(f"\n  [错误] {event.get('message', '')}")


def _handle_approval(event: dict):
    """向用户询问是否允许执行危险工具。"""
    name = event["name"]
    args = _compact_args(event.get("args", {}))
    prompt = f"  允许 {name}({args})？[Y/n/a] "
    try:
        answer = input(prompt).strip().lower()
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
    print(f"  \033[36m{name}\033[0m({args_str})")


def _dim(text: str):
    print(f"\033[2m{text}\033[0m")


def _compact_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 80:
            s = s[:77] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)


def _print_banner(agent: Agent, workspace: str, approval: str):
    print("\033[2m" + "─" * 50 + "\033[0m")
    print("  Agent-mini")
    print(f"  Session: {agent.session_id}")
    print(f"  Cwd:     {workspace}")
    print(f"  审批策略: {approval}")
    print("\033[2m" + "─" * 50 + "\033[0m")
    print("  /help 查看命令。\n")


def _print_help():
    print("  命令:")
    print("    /exit, /quit    退出")
    print("    /session        查看会话信息")
    print("    /help           帮助")
    print()


if __name__ == "__main__":
    main()
