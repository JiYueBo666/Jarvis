import os
import sys
from enum import Enum

import typer
from rich import print


class ApprovalPolicy(str, Enum):
    ask = "ask"
    auto = "auto"
    never = "never"


from src.Agent.model import OpenAICompatibleModelClient
from src.Environment.workSpace import WorkSpaceContext
from src.Runtime.runtime import Jarvis, SessionStore, SessionStore
from src.config import settings

app = typer.Typer()
DEFAULT_SECRET_ENV_NAMES = (
    "Jarvis_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_API_TOKEN",
    "Jarvis_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "Jarvis_DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEY",
    "Jarvis_RIGHT_CODES_API_KEY",
    "RIGHT_CODES_API_KEY",
    "GITHUB_PAT",
    "GH_PAT",
)


def _build_model_client(args):
    model = settings.SPEED_MODEL
    base_url = settings.BASE_URL
    api_key = settings.API_KEY
    return OpenAICompatibleModelClient(
        model=model,
        base_url=base_url,
        api_key=api_key,
    )


def build_agent(args):
    workspace = WorkSpaceContext.build(args["cwd"])
    store = SessionStore(workspace.repo_root + "/.jarvis/sessions")
    model = _build_model_client(args)
    session_id = args["resume"]
    if session_id == "latest":
        session_id = store.latest()
    if session_id:
        return Jarvis.from_session(
            model_client=model,
            workspace=workspace,
            session_store=store,
            session_id=session_id,
            approval_policy=args["approval"],
            max_steps=args["max_steps"],
            max_new_tokens=args["max_new_tokens"],
        )
    return Jarvis(
        model_client=model,
        workspace=workspace,
        session_store=store,
        approval_policy=args["approval"],
        max_steps=args["max_steps"],
        max_new_tokens=args["max_new_tokens"],
    )


@app.command()
def start(
    model: str = typer.Option(None, help="模型名称，覆盖 .env 配置"),
    base_url: str = typer.Option(None, help="API base URL，覆盖 .env 配置"),
    max_steps: int = typer.Option(30, help="最大工具调用轮数"),
    max_new_tokens: int = typer.Option(1024, help="模型最大输出 token 数"),
    resume: str = typer.Option(None, help="从上次对话记录继续，传入对话记录文件路径"),
    approval: ApprovalPolicy = typer.Option(
        ApprovalPolicy.ask,
        "--approval",
        help="工具调用审批模式，ask 询问用户是否执行，auto 自动执行",
    ),
    cwd: str = typer.Option(".", help="工作目录，默认为当前目录"),
):
    """启动 Agent 交互循环"""
    args = {
        "model": model,
        "base_url": base_url,
        "max_steps": max_steps,
        "max_new_tokens": max_new_tokens,
        "resume": resume,
        "approval": (
            approval.value if isinstance(approval, ApprovalPolicy) else approval
        ),
        "cwd": cwd,
    }
    agent = build_agent(args)

    # print(welcome)

    while True:
        try:
            user_input = input("\njarvis> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            return 0

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            return 0
        if user_input == "/memory":
            print(agent.memory_text())
            continue
        if user_input == "/session":
            print(agent.session_path)
            continue
        if user_input == "/reset":
            agent.reset()
            print("session reset")
            continue

        print()
        try:
            print(agent.ask(user_input))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)


if __name__ == "__main__":
    app()
