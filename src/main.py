"""Coding Agent CLI."""

import asyncio
import os
from dotenv import load_dotenv

from src.Agent import AgentLoop, Message
from src.Client import OpenAIClient
from src.Tools import ToolRegistry, ToolExecutor
from src.Tools.builtin import ReadFileTool, WriteFileTool, RunShellTool, SearchCodeTool


def create_agent() -> tuple[AgentLoop, ToolRegistry]:
    load_dotenv()
    client = OpenAIClient(
        api_key=os.getenv("OPENAI_API_KEY", ""),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=os.getenv("LLM_MODEL", "gpt-4o"),
    )
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(RunShellTool())
    registry.register(SearchCodeTool())
    executor = ToolExecutor(registry)
    return AgentLoop(client=client, executor=executor), registry


async def main():
    loop, registry = create_agent()
    system = Message(
        role="system",
        content=(
            "You are a coding agent. You can read/write files, run shell commands, "
            "and search code. Use these tools to help the user. Keep responses concise."
        ),
    )
    print("Coding Agent ready. Type 'exit' to quit.\n")
    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.lower() in ("exit", "quit"):
            break
        if not user_input:
            continue
        result = await loop.run(
            [system, Message(role="user", content=user_input)],
            tools=registry.get_openai_tools(),
        )
        print(f"\n{result}\n")


if __name__ == "__main__":
    asyncio.run(main())


def cli():
    """Sync entry point for `coding-agent` command."""
    asyncio.run(main())
