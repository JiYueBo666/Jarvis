# Coding Agent Core — Implementation Plan

> **For Claude:** Use `executing-plans` skill to implement this plan task-by-task.

**Goal:** Build a minimal ReAct-loop coding agent that can read/write files, search code, and run shell commands via LLM-powered tool selection.

**Architecture:** ReAct loop (Think → Act → Observe → Repeat) with internal message models, an abstracted LLM client, and a tool system. The loop orchestrates LLM calls and tool execution without knowing their internals.

**Tech Stack:** Python 3.11+, Pydantic (models), openai SDK (LLM), asyncio (async tools)

---

### Task 1: Internal Message Models

**Files:**
- Create: `src/Agent/models.py`

**Step 1: Define the models**

```python
"""Internal message types for the agent loop."""

from __future__ import annotations

from pydantic import BaseModel
from typing import Literal


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


class ToolResult(BaseModel):
    tool_call_id: str
    content: str
```

**Step 2: Verify it imports cleanly**

Run: `python -c "from src.Agent.models import Message, ToolCall, ToolResult; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/Agent/models.py
git commit -m "feat: add internal message models"
```

---

### Task 2: Tool System — Base + Registry + Executor

**Files:**
- Create: `src/Tools/__init__.py`
- Create: `src/Tools/base.py`
- Test: `tests/test_tools.py`

**Step 1: Write the failing test**

```python
import pytest
from src.Tools.base import Tool, ToolRegistry, ToolExecutor


class EchoTool(Tool):
    name = "echo"
    description = "Echoes input back"
    parameters = {"type": "object", "properties": {"msg": {"type": "string"}}}

    async def execute(self, args: dict) -> str:
        return args.get("msg", "")


@pytest.mark.asyncio
async def test_tool_registry():
    registry = ToolRegistry()
    registry.register(EchoTool())
    assert registry.get("echo") is not None
    assert len(registry.list()) == 1


@pytest.mark.asyncio
async def test_tool_executor():
    registry = ToolRegistry()
    registry.register(EchoTool())
    executor = ToolExecutor(registry)
    result = await executor.execute("echo", {"msg": "hello"})
    assert result == "hello"


@pytest.mark.asyncio
async def test_executor_unknown_tool():
    executor = ToolExecutor(ToolRegistry())
    result = await executor.execute("nonexistent", {})
    assert "Unknown tool" in result
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL with import errors

**Step 3: Write minimal implementation**

`src/Tools/base.py`:
```python
"""Tool system: base class, registry, executor."""

from __future__ import annotations

from pydantic import BaseModel


class Tool(BaseModel):
    name: str
    description: str
    parameters: dict  # JSON Schema

    async def execute(self, args: dict) -> str:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def get_openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    async def execute(self, name: str, args: dict) -> str:
        tool = self._registry.get(name)
        if tool is None:
            return f"Unknown tool: {name}"
        try:
            return await tool.execute(args)
        except Exception as e:
            return f"Tool {name} error: {e}"
```

`src/Tools/__init__.py`:
```python
from .base import Tool, ToolRegistry, ToolExecutor

__all__ = ["Tool", "ToolRegistry", "ToolExecutor"]
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tools.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/Tools/__init__.py src/Tools/base.py tests/test_tools.py
git commit -m "feat: add tool system (base, registry, executor)"
```

---

### Task 3: LLM Client — Abstract + OpenAI

**Files:**
- Create: `src/Client/__init__.py` (update existing)
- Create: `src/Client/base.py`
- Create: `src/Client/openai.py`
- Test: `tests/test_client.py`

**Step 1: Write the failing test**

```python
import pytest
from src.Client.base import LLMClient
from src.Agent.models import Message


# Concrete stub for testing
class StubClient(LLMClient):
    async def chat(self, messages: list[Message]) -> Message:
        return Message(role="assistant", content="stub response")

    @property
    def model_name(self) -> str:
        return "stub"


@pytest.mark.asyncio
async def test_stub_client():
    client = StubClient()
    response = await client.chat([Message(role="user", content="hi")])
    assert response.role == "assistant"
    assert response.content == "stub response"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_client.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

`src/Client/base.py`:
```python
"""Abstract LLM client interface."""

from abc import ABC, abstractmethod
from src.Agent.models import Message


class LLMClient(ABC):
    @abstractmethod
    async def chat(self, messages: list[Message]) -> Message:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...
```

`src/Client/openai.py`:
```python
"""OpenAI-compatible LLM client adapter."""

from openai import AsyncOpenAI
from src.Client.base import LLMClient
from src.Agent.models import Message, ToolCall


class OpenAIClient(LLMClient):
    def __init__(self, api_key: str, base_url: str, model: str, max_retries: int = 3):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=max_retries)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> Message:
        raw = await self._client.chat.completions.create(
            model=self._model,
            messages=[m.model_dump(exclude_none=True) for m in messages],
            tools=tools,
        )
        choice = raw.choices[0]
        msg = choice.message

        if msg.tool_calls:
            return Message(
                role="assistant",
                content=msg.content or "",
                tool_calls=[
                    ToolCall(id=tc.id, name=tc.function.name, arguments=__import__("json").loads(tc.function.arguments))
                    for tc in msg.tool_calls
                ],
            )
        return Message(role="assistant", content=msg.content or "")
```

`src/Client/__init__.py`:
```python
from .base import LLMClient
from .openai import OpenAIClient

__all__ = ["LLMClient", "OpenAIClient"]
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_client.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/Client/__init__.py src/Client/base.py src/Client/openai.py tests/test_client.py
git commit -m "feat: add LLM client abstraction with OpenAI adapter"
```

---

### Task 4: Built-in Tools

**Files:**
- Create: `src/Tools/builtin.py`
- Test: `tests/test_builtin_tools.py`

**Step 1: Write the failing test**

```python
import pytest
import tempfile
from pathlib import Path
from src.Tools.builtin import ReadFileTool, WriteFileTool, RunShellTool, SearchCodeTool


@pytest.mark.asyncio
async def test_read_file():
    tool = ReadFileTool()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("print('hello')")
        p = f.name
    result = await tool.execute({"file_path": p})
    assert "print('hello')" in result


@pytest.mark.asyncio
async def test_write_file():
    tool = WriteFileTool()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.txt"
        result = await tool.execute({"file_path": str(p), "content": "hello"})
        assert "written" in result.lower()
        assert p.read_text() == "hello"


@pytest.mark.asyncio
async def test_run_shell():
    tool = RunShellTool()
    result = await tool.execute({"command": "echo hello"})
    assert "hello" in result


@pytest.mark.asyncio
async def test_search_code():
    tool = SearchCodeTool()
    # Search in our own source
    result = await tool.execute({"pattern": "class Tool", "path": "src/Tools"})
    assert "class Tool" in result
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_builtin_tools.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

`src/Tools/builtin.py`:
```python
"""Built-in tools for the coding agent."""

from pathlib import Path
import subprocess, re

from src.Tools.base import Tool


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a file"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to read"}
        },
        "required": ["file_path"],
    }

    async def execute(self, args: dict) -> str:
        path = Path(args["file_path"])
        if not path.exists():
            return f"File not found: {path}"
        return path.read_text()


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write content to a file (creates or overwrites)"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, args: dict) -> str:
        path = Path(args["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"])
        return f"Written {len(args['content'])} bytes to {path}"


class RunShellTool(Tool):
    name = "run_shell"
    description = "Run a shell command and return its output"
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
        },
        "required": ["command"],
    }

    async def execute(self, args: dict) -> str:
        result = subprocess.run(
            args["command"], shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout or ""
        if result.stderr:
            output += "\nSTDERR:\n" + result.stderr
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output or "(no output)"


class SearchCodeTool(Tool):
    name = "search_code"
    description = "Search for a pattern in code files using grep"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search"},
            "path": {"type": "string", "description": "Directory path to search"},
        },
        "required": ["pattern"],
    }

    async def execute(self, args: dict) -> str:
        search_path = args.get("path", ".")
        result = subprocess.run(
            ["grep", "-rn", args["pattern"], search_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0 and result.returncode != 1:
            return f"Search failed: {result.stderr}"
        return result.stdout or "No matches found"
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_builtin_tools.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/Tools/builtin.py tests/test_builtin_tools.py
git commit -m "feat: add built-in tools (read, write, shell, search)"
```

---

### Task 5: Agent Loop

**Files:**
- Create: `src/Agent/__init__.py` (replace existing)
- Create: `src/Agent/loop.py`
- Test: `tests/test_agent.py`

**Step 1: Write the failing test**

```python
import pytest
from src.Agent.models import Message
from src.Agent.loop import AgentLoop
from src.Client.base import LLMClient
from src.Tools.base import Tool, ToolRegistry, ToolExecutor


class ThinkClient(LLMClient):
    """Simulates an LLM that thinks then acts then answers."""
    def __init__(self, steps: list[dict]):
        self.steps = steps
        self.idx = 0

    @property
    def model_name(self) -> str:
        return "test"

    async def chat(self, messages: list[Message], tools=None) -> Message:
        step = self.steps[self.idx % len(self.steps)]
        self.idx += 1
        if "tool_calls" in step:
            return Message(role="assistant", content=step.get("content", ""), tool_calls=step["tool_calls"])
        return Message(role="assistant", content=step.get("content", ""))


class StubTool(Tool):
    name = "ping"
    description = "ping tool"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict) -> str:
        return "pong"


@pytest.mark.asyncio
async def test_agent_returns_final_answer():
    client = ThinkClient([
        {"content": "The answer is 42"},
    ])
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    loop = AgentLoop(client=client, executor=executor, max_steps=5)

    result = await loop.run([Message(role="user", content="what is the answer?")])
    assert result == "The answer is 42"


@pytest.mark.asyncio
async def test_agent_calls_tool():
    client = ThinkClient([
        {
            "content": "Let me check",
            "tool_calls": [
                {"id": "call_1", "name": "ping", "arguments": {}}
            ],
        },
        {"content": "pong received"},
    ])
    registry = ToolRegistry()
    registry.register(StubTool())
    executor = ToolExecutor(registry)
    loop = AgentLoop(client=client, executor=executor, max_steps=5)

    result = await loop.run([Message(role="user", content="ping?")])
    assert result == "pong received"


@pytest.mark.asyncio
async def test_agent_max_steps():
    client = ThinkClient([
        {
            "content": "still thinking",
            "tool_calls": [
                {"id": "call_1", "name": "ping", "arguments": {}}
            ],
        }
    ])
    registry = ToolRegistry()
    registry.register(StubTool())
    executor = ToolExecutor(registry)
    loop = AgentLoop(client=client, executor=executor, max_steps=2)

    result = await loop.run([Message(role="user", content="do it")])
    assert "max steps" in result.lower()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_agent.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

`src/Agent/loop.py`:
```python
"""ReAct agent loop."""

from __future__ import annotations

from src.Agent.models import Message
from src.Client.base import LLMClient
from src.Tools.base import ToolExecutor


class AgentLoop:
    def __init__(
        self,
        client: LLMClient,
        executor: ToolExecutor,
        max_steps: int = 20,
    ):
        self._client = client
        self._executor = executor
        self._max_steps = max_steps

    async def run(self, messages: list[Message], tools: list[dict] | None = None) -> str:
        history = list(messages)

        for step in range(self._max_steps):
            response = await self._client.chat(history, tools=tools)
            history.append(response)

            if not response.tool_calls:
                return response.content or "(empty response)"

            for tc in response.tool_calls:
                result = await self._executor.execute(tc.name, tc.arguments)
                history.append(Message(role="tool", content=result, tool_call_id=tc.id))

        return f"Reached max steps ({self._max_steps}) without final answer"
```

`src/Agent/__init__.py`:
```python
"""Agent implementation."""

from .models import Message, ToolCall, ToolResult
from .loop import AgentLoop

__all__ = ["Message", "ToolCall", "ToolResult", "AgentLoop"]
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_agent.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/Agent/__init__.py src/Agent/loop.py tests/test_agent.py
git commit -m "feat: add ReAct agent loop"
```

---

### Task 6: CLI Entry Point

**Files:**
- Modify: `src/Agent/__init__.py` (add `main` or create `src/main.py`)
- Test: manual verification

**Step 1: Create CLI entry**

```python
"""Coding Agent CLI."""

import asyncio
import os
from dotenv import load_dotenv

from src.Agent import AgentLoop, Message
from src.Client import OpenAIClient
from src.Tools import ToolRegistry, ToolExecutor
from src.Tools.builtin import ReadFileTool, WriteFileTool, RunShellTool, SearchCodeTool


def create_agent() -> AgentLoop:
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
        content="You are a coding agent. You can read/write files, run shell commands, and search code. "
                "Use these tools to help the user. Keep responses concise.",
    )
    print("Coding Agent ready. Type 'exit' to quit.")
    while True:
        user_input = input("\n> ").strip()
        if user_input.lower() in ("exit", "quit"):
            break
        result = await loop.run([system, Message(role="user", content=user_input)], tools=registry.get_openai_tools())
        print(f"\n{result}")


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 2: Update pyproject.toml entry point**

```toml
[project.scripts]
coding-agent = "src.main:main"
```

**Step 3: Verify it starts**

Run: `python -m src.main`
Expected: Shows "Coding Agent ready" prompt

**Step 4: Commit**

```bash
git add src/main.py pyproject.toml
git commit -m "feat: add CLI entry point for coding agent"
```

---

### Task 7: Run All Tests and Final Verification

**Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

**Step 2: Run a quick smoke test**

Run: `python -m src.main`  (then type "exit")
Expected: Clean start and exit

**Step 3: Final commit if needed**

```bash
git add -A
git commit -m "chore: finalize coding agent core"
```
