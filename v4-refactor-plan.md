# V4 重构计划：采用 pi 架构

## 目标

将 Agent-mini 的架构从"薄组合根 + 静态引擎"重构为 pi 的"AgentSession（应用层）+ Agent（状态机/事件源）+ Loop（无状态内核）"三层架构。

## 当前架构（V3）

```
cli.py → Agent.ask_stream()
           │  ctx.start_turn()
           └→ Engine.run_stream(model_client, executor, ctx, bus, query, ...)
                │  9 个参数
                │  内部创建 TaskState、RepetitionDetector
                │  内部调 ctx.append_assistant() / append_tool_result()
                │  yield 裸 dict
                └→ cli.py for 循环消费
```

## 目标架构

```
cli.py → AgentSession.prompt("query")
           │  subscribe(ui_handler)
           │  /command → 扩展命令
           │  skill/template 展开
           │  扩展 input 钩子
           │  装配 AgentMessage[]
           │  设置 system prompt
           │
           └→ Agent.prompt(messages)
                │  状态快照：{ systemPrompt, messages, tools }
                │  广播事件：subscribe() → AgentSession._handle_event()
                │                     → 持久化 / 扩展 / UI
                │
                └→ Loop.run_stream(model_client, tools, messages, system_prompt,
                │                  convert_to_llm, options)
                │     纯循环：调 LLM → 执行工具 → emit 事件
                │     不知道持久化、UI、扩展的存在
                │
                └→ yield 类型化事件 ← Agent.process_events() 消费
```

---

## 第一步：定义统一消息类型

**现状**：`list[dict]` 裸字典，key 名跟随 OpenAI 格式。

**改为**：dataclass + Union 类型，内部统一用 `AgentMessage`，LLM 边界用 `convert_to_llm` 转换。

```python
# data/messages.py

@dataclass
class TextContent:
    type: Literal["text"] = "text"
    text: str

@dataclass
class ToolCallContent:
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict

@dataclass
class Message:
    role: Literal["user", "assistant", "tool_result"]
    content: list[TextContent | ToolCallContent]
    timestamp: int

@dataclass
class AssistantMessage(Message):
    role: Literal["assistant"] = "assistant"
    api: str = ""
    provider: str = ""
    model: str = ""
    stop_reason: str = "stop"
    usage: Usage | None = None
    error_message: str | None = None

# 扩展：用 Union 类型增加应用层自定义消息
# AgentMessage = Message | BashExecutionMessage | CustomMessage | CompactionSummaryMessage

# context/convert_to_llm.py
def convert_to_llm(messages: list[AgentMessage]) -> list[dict]:
    """AgentMessage → OpenAI-compatible dict。
    bash_execution → user, compaction_summary → user,
    custom → user, user/assistant/tool → 透传。
    """
```

### 涉及文件

| 文件 | 改动 |
|---|---|
| 新建 `src/data/messages.py` | 定义 Message、AgentMessage、AssistantMessage 等 |
| 新建 `src/context/convert_to_llm.py` | convert_to_llm 函数 |
| 全局搜索 `role": "tool"` 替换为 `role: "tool_result"` | 统一角色名 |

---

## 第二步：Loop 纯函数化

**现状**：`Engine.run_stream()` 是静态方法，9 个参数，内部混入 TaskState、RepetitionDetector、文件变更检测、ctx.append_*。

**改为**：类似 pi 的 `agent-loop.ts`——只调 LLM + 执行工具 + emit 事件。

```python
# engine/loop.py

@dataclass
class LoopOptions:
    max_steps: int = 100
    max_tool_steps: int = 100
    max_new_tokens: int = 8192
    signal: asyncio.Event | None = None

# 事件类型（纯数据，不包含如何消费的指令）
@dataclass
class TextDelta:
    content_index: int
    delta: str

@dataclass
class ToolCallStart:
    content_index: int
    tool_call: ToolCallContent

@dataclass
class ToolCallEnd:
    content_index: int
    tool_call: ToolCallContent

@dataclass
class Done:
    message: AssistantMessage

@dataclass
class Error:
    message: str

LoopEvent = TextDelta | ReasoningDelta | ToolCallStart | ToolCallDelta | ToolCallEnd | Done | Error

class Loop:
    """无状态循环内核。不知道消息格式、不知道持久化、不知道 UI。"""

    @staticmethod
    async def run_stream(
        model_client: ModelClient,
        tools: dict[str, Tool],
        messages: list[dict],
        system_prompt: str,
        *,
        convert_to_llm: Callable,
        options: LoopOptions | None = None,
    ) -> AsyncGenerator[LoopEvent, None]:
        """纯循环：调 LLM → 执行工具 → 回到调 LLM。

        Yields 类型化事件供调用方处理。
        最后 yield Done(message) 或 Error(message)。
        """
```

### 具体变化

| 当前 Engine.run_stream() | 改为 |
|---|---|
| 接收 9 个参数 | 接收 6 个：model_client, tools, messages, system_prompt, convert_to_llm, options |
| 内部 new TaskState() | 移到 AgentSession |
| 内部 new RepetitionDetector() | 移到 AgentSession，通过 tool_call 钩子注入 |
| ctx.append_assistant() | 追加到 messages 列表，由调用方管理 |
| ctx.append_tool_result() | 同上 |
| yield {"type": "reasoning", ...} | yield ReasoningDelta(...) |
| yield {"type": "tool_call", ...} | yield ToolCallStart(...) / ToolCallEnd(...) |
| yield {"type": "final", ...} | yield Done(message=AssistantMessage(...)) |
| 异常 yield {"type": "error"} | yield Error(message=...) |

### 涉及文件

| 文件 | 改动 |
|---|---|
| `src/engine/loop.py` | 重写为纯函数 Loop.run_stream() |
| `src/engine/executor.py` | ToolExecutor 改为纯执行器，不包含重试/审批逻辑 |
| 新建 `src/engine/events.py` | 定义 LoopEvent 联合类型 |

---

## 第三步：Agent（状态机 + 事件源）

**现状**：没有独立的状态机，状态散落在 Agent 类 + Engine 局部变量 + ContextManager 中。

**改为**：新建 Agent 类（类似 pi 的 `agent.ts`），管理状态 + 事件广播。

```python
# engine/agent.py

class Agent:
    """状态机 + 事件源。不知道 AgentSession 的存在。"""

    def __init__(self, *, convert_to_llm, stream_fn, ...):
        self._state = {
            "system_prompt": "",
            "messages": [],
            "tools": [],
            "model": None,
            "is_streaming": False,
        }
        self._listeners: set[Callable] = set()
        self._steering_queue: list[AgentMessage] = []
        self._follow_up_queue: list[AgentMessage] = []

    def subscribe(self, listener) -> Callable:
        self._listeners.add(listener)
        return lambda: self._listeners.remove(listener)

    async def prompt(self, messages: list[AgentMessage]):
        """新的一轮：拍快照 → 调 Loop.run_stream() → 广播事件。"""

    async def continue_(self):
        """恢复：最后一条消息不是 assistant 时触发。"""

    def steer(self, message: AgentMessage): ...
    def follow_up(self, message: AgentMessage): ...
    def abort(self): ...
    async def wait_for_idle(self): ...

    @property
    def state(self) -> dict: ...

    def _process_event(self, event):
        """reduce state + broadcast to listeners。"""
```

### 关键设计

```python
async def prompt(self, messages):
    context_snapshot = {
        "system_prompt": self._state["system_prompt"],
        "messages": list(self._state["messages"]) + list(messages),
        "tools": list(self._state["tools"]),
    }

    async for event in Loop.run_stream(
        self._model_client,
        self._state["tools"],
        context_snapshot["messages"],
        context_snapshot["system_prompt"],
        convert_to_llm=self._convert_to_llm,
    ):
        self._process_event(event)  # reduce state + 广播
```

### 涉及文件

| 文件 | 改动 |
|---|---|
| 新建 `src/engine/agent.py` | Agent 类 |
| `src/agent.py` 中的 Agent 类改名 | 改为 AgentSession，或新建 |

---

## 第四步：AgentSession（应用逻辑层）

**现状**：`Agent` 类既管组合根又管业务逻辑（mode、plan、_pending_plan）。

**改为**：AgentSession 封装所有应用层逻辑。

```python
# session.py 或 agent.py（保留文件名但重写）

class AgentSession:
    """应用逻辑层。装配 prompt、管理扩展、重试、压缩、持久化。"""

    def __init__(self, workspace_root, approval_policy, ...):
        # ── 先创建底层依赖 ──
        self._model_client = ModelClient(...)
        self._tool_registry = self._build_tool_registry()
        self._agent = Agent(convert_to_llm=convert_to_llm, stream_fn=..., ...)
        self._agent.subscribe(self._handle_event)  # ← 监听事件

        # ── 应用状态 ──
        self.mode = "default"
        self.topic = ""
        self.plan_path = ""

    def _build_tool_registry(self):
        """创建工具定义，写入 Agent.state.tools。"""
        registry = ToolRegistry()
        registry.register(create_read_tool(...))
        registry.register(create_write_tool(...))
        registry.register(create_bash_tool(...))
        registry.register(create_patch_tool(...))
        self._agent.state.tools = registry.get_active_tools()
        return registry

    async def prompt(self, query: str):
        """完整的装配管线。"""
        # 1. 预处理
        if query.startswith("/"):
            if self._try_extension_command(query):
                return

        # 2. input 钩子（扩展可转换/拦截）
        # ...

        # 3. skill/template 展开
        expanded = self._expand_skill(query)
        expanded = expand_template(expanded, ...)

        # 4. 装配 AgentMessage[]
        messages = [UserMessage(content=expanded)]
        messages.extend(self._pending_next_turn_messages)
        # ...

        # 5. system prompt
        self._agent.state.system_prompt = build_system_prompt(
            workspace_root=self._cwd,
            tools=self._tool_registry.get_definitions(),
            mode=self.mode,
        )

        # 6. 交给 Agent
        await self._agent.prompt(messages)

    async def _handle_event(self, event):
        """同一事件处理多件事。"""
        # 转发给扩展
        # 转发给 UI（通过自己的 subscribe）
        if event.type == "message_end":
            self._persist(event.message)
        if event.type == "agent_end":
            if self._should_retry(event):
                await self._retry(event)
            if self._should_compact(event):
                await self._compact(event)
```

### 涉及文件

| 文件 | 改动 |
|---|---|
| `src/agent.py` | 重写为 AgentSession |
| `src/cli.py` | 改为调用 session.prompt() + subscribe(ui_handler) |
| 新建 `src/core/system_prompt.py` | build_system_prompt() 纯函数 |
| 新建 `src/core/session_persistence.py` | JSONL 持久化 |

---

## 第五步：事件订阅机制

**现状**：`cli.py` 用 `for event in agent.ask_stream(query)` 消费事件，UI 渲染和业务逻辑混在 for 循环体里。

**改为**：Agent 通过 `subscribe()` 广播事件，消费方注册 listener。

```python
# cli.py

session = AgentSession(workspace_root="...")

# 注册 UI 监听器
unsub = session.subscribe(ui_handler)

# 一行提交，不需要自己写循环
await session.prompt("帮我看看这个文件")

# ui_handler 收到所有事件自动渲染
def ui_handler(event):
    if isinstance(event, TextDelta):
        sys.stdout.write(event.delta)
    elif isinstance(event, ToolCallStart):
        print(f"\n工具调用: {event.tool_call.name}")
    elif isinstance(event, Done):
        print(f"\n完成: {event.message.content}")
```

### 涉及文件

| 文件 | 改动 |
|---|---|
| `src/cli.py` | 删除 for 循环，改为 subscribe + 单次 prompt |
| `src/agent.py` | AgentSession.subscribe() 和 _handle_event() |

---

## 第六步：ToolRegistry + 钩子系统

**现状**：`build_registry()` 硬编码，`ToolExecutor` 管所有。

**改为**：ToolRegistry 只注册和查询，执行由 Agent 触发。

```python
# tools/registry.py

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict       # JSON Schema
    execute: Callable
    risky: bool = False

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition): ...
    def get_schemas(self) -> list[dict]: ...
    def execute(self, name: str, args: dict) -> ToolResult: ...
    def is_allowed(self, name: str, mode: str) -> bool: ...
```

钩子系统（ExtensionRunner）：

```python
# core/extension_runner.py

class ExtensionRunner:
    def __init__(self):
        self._handlers: dict[str, list[Callable]] = {}

    def on(self, event: str, handler: Callable): ...
    def emit(self, event: str, data: dict) -> Any: ...

# 使用示例：plan mode 的 tool 限制
runner.on("tool_call", _plan_mode_tool_check)
runner.on("before_agent_start", _plan_mode_prompt_inject)
```

### 涉及文件

| 文件 | 改动 |
|---|---|
| `src/tools/__init__.py` | 改为用 ToolRegistry 注册 |
| 删除 `src/engine/executor.py` | 功能分散到 ToolRegistry + 钩子 |
| 新建 `src/tools/registry.py` | ToolRegistry 类 |
| 新建 `src/core/extension_runner.py` | ExtensionRunner 类 |

---

## 第七步：持久化改为 JSONL

**现状**：每个 session 一个目录，内含 session.json、events.jsonl、task_state.json、history.jsonl 等。

**改为**（可选，逐步迁移）：pi 风格的 append-only JSONL + parentId 树结构。

```python
# data/session_store.py

class SessionStore:
    """Append-only JSONL with parentId branching."""

    def __init__(self, path: Path):
        self._path = path
        self._entries: list[dict] = []

    def append(self, entry: dict):
        entry["id"] = uuid7()
        entry["parent_id"] = self._entries[-1]["id"] if self._entries else None
        entry["timestamp"] = now()
        self._entries.append(entry)
        append_to_jsonl(self._path, entry)

    def build_context(self) -> list[AgentMessage]:
        """回放所有 message 类型条目，重建消息列表。"""
```

---

## 执行顺序

| 步骤 | 改动内容 | 独立验证 |
|---|---|---|
| 1 | 定义 Message dataclass，创建 convert_to_llm | 可以逐步替换现有 list[dict] |
| 2 | Loop 纯函数化（engine/loop.py） | 可用单元测试验证输入/输出 |
| 3 | 新建 Agent（状态机 + 事件源） | 旧 Agent 改名为 AgentSession，新建 Agent 独立 |
| 4 | AgentSession 重写 prompt() 管线 | CLI 层暂时保留 for 循环兼容 |
| 5 | subscribe 机制，CLI 改用事件监听 | 最后改 CLI，之前不变 |
| 6 | ToolRegistry + ExtensionRunner | 逐步替换 ToolExecutor |
| 7 | JSONL 持久化 | 最后可选 |

每一步的验证方式：
1. 运行 `pytest tests/` 确保现有测试不失败
2. 手动跑 `python -m src.cli "hello"` 确保基本对话功能正常
3. 逐步打开断言/类型检查确保新代码覆盖旧代码路径
