# Pico v4 重构设计

## 核心问题

1. session/memory/checkpoint 都是裸 dict，缺乏类型约束，到处是防御性访问
2. runtime.py 是 God Object（939 行，导入 23 个模块）
3. 可观测代码散落在 6+ 个文件里，业务逻辑和日志记录混在一起
4. memory 有旧格式镜像（task/files/notes），需要 normalize 双向同步
5. tool_executor 和 runtime 存在循环依赖
6. checkpoint 14 个字段中有 7 个冗余（可从 TaskState 实时计算）
7. finish_stopped_run 和 finish_limited_run 代码重复

## 重构原则

- 从 leaf 到 root，每阶段可独立测试
- 数据结构 dataclass 化，替代裸 dict
- engine/loop.py 是唯一编排点，所有可观测在这里触发
- agent.py 是极薄组合根（~100 行），只持有状态 + 组装子系统
- 依赖方向只能向下：agent → engine/guard/context/... → data/

## 文件结构

```
pico/
├── data/                 # 纯 dataclass，零业务逻辑
│   ├── session.py        # Session, SessionStore
│   ├── memory.py         # MemoryState, FileSummary, EpisodicNote
│   ├── task.py           # TaskState
│   ├── checkpoint.py     # Checkpoint, CheckpointStore
│   ├── trace.py          # TraceEvent, Span
│   ├── identity.py       # RuntimeIdentity, ResumeState
│   └── events.py         # EventBus (session event log)
│
├── providers/             # 不变
│   ├── base.py
│   ├── errors.py
│   └── clients.py
│
├── tools/                 # 不变
│   ├── schemas.py
│   ├── base.py
│   └── ...
│
├── workspace.py           # 不变
│
├── engine/                # 引擎层：只有编排 + 纯函数
│   ├── loop.py            # run_turn() —— 唯一编排点
│   ├── tool.py            # execute_tool() —— 纯函数，不写日志
│   ├── model.py           # model_output 解析 + 模型错误处理
│   └── lifecycle.py       # finish_run() —— 统一的退出路径
│
├── guard/                 # 安全约束（五级门链）
│   ├── permissions.py
│   ├── policy.py
│   ├── profiles.py
│   ├── repetition.py
│   └── sandbox/
│
├── context/               # 上下文治理
│   ├── assemble.py        # prompt 拼装 + 预算控制
│   ├── compact.py         # 历史压缩
│   └── render.py          # TurnHistoryBuilder
│
├── memory/                # 记忆系统
│   ├── store.py           # LayeredMemory CRUD
│   ├── fresh.py           # file_freshness, invalidate_stale
│   ├── recall.py          # retrieval, render
│   └── durable.py         # DurableMemoryStore, dream
│
├── life/                  # 生命周期管理
│   ├── checkpoint.py      # create_checkpoint, evaluate_resume_state
│   ├── resume.py          # resume/clear session
│   └── plan.py            # PlanModeController
│
├── trace/                 # 可观测基建
│   ├── bus.py             # Audit 统一入口
│   ├── span.py            # TraceEvent 构建
│   ├── consumers.py       # ArtifactGraph + Verifier + Reminder
│   └── report.py          # build_report, write_report
│
├── skills/                # 不变
├── workers/               # 不变
├── agent.py               # 组合根（~100 行）
├── cli.py
└── testing.py
```

## 依赖方向

```
                    cli.py / tui/
                         │
                    agent.py  ← 组合根
                    /  |  \   \
                   /   |   \   \
          engine/  context/  guard/  life/  trace/
           /  \       \       /      /      /
          /    \       \     /      /      /
    memory/  tools/  skills/  workers/  providers/
          \     \       \     /        /
           \     \       \   /        /
            └─────┴───┴───┴─────────┘
                      │
                    data/    ← 被所有层依赖
```

- data/ 不被任何其他 pico 模块引用
- guard/ 不依赖 agent.py（打破原来的循环依赖）
- engine/ 不直接写日志，通过 Audit 实例
- trace/ 只被 engine/ 和 agent.py 调用

## 数据结构改进

### Session：裸 dict → dataclass

```python
@dataclass
class Session:
    id: str
    created_at: str
    workspace_root: str
    history: list = field(default_factory=list)
    memory: MemoryState = field(default_factory=MemoryState)
    checkpoints: CheckpointStore = field(default_factory=CheckpointStore)
    runtime_identity: RuntimeIdentity = field(default_factory=RuntimeIdentity)
    resume_state: ResumeState = field(default_factory=ResumeState)
    runtime_mode: RuntimeMode = field(default_factory=RuntimeMode)
    todos: TodoState = field(default_factory=TodoState)
    workers: WorkerState = field(default_factory=WorkerState)
    compactions: list = field(default_factory=list)
```

### MemoryState：删掉旧格式镜像

```python
@dataclass
class MemoryState:
    task_summary: str = ""
    recent_files: list[str] = field(default_factory=list)
    episodic_notes: list[EpisodicNote] = field(default_factory=list)
    file_summaries: dict[str, FileSummary] = field(default_factory=dict)
    next_note_index: int = 0
    # 删掉 task, files, notes
```

### Checkpoint：14 → 7 个字段

```python
@dataclass
class Checkpoint:
    checkpoint_id: str
    parent_checkpoint_id: str = ""
    schema_version: str = "phase1-v2"
    created_at: str = field(default_factory=now)
    current_goal: str = ""                  # 必须持久化（resume 时 prompt 已不在）
    key_files: list[KeyFile] = field(default_factory=list)  # 必须持久化（freshness 校验）
    runtime_identity: RuntimeIdentity = field(default_factory=RuntimeIdentity)  # 必须持久化
    # 删掉：completed, excluded, current_blocker, next_step, summary, freshness
    # 这些从 TaskState 实时计算
```

### TraceEvent：setdefault → dataclass

```python
@dataclass
class TraceEvent:
    event: str
    phase: str = "runtime"
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    duration_ms: int = 0
    input_chars: int = 0
    output_chars: int = 0
    # 不需要 setdefault 补默认值
```

## 可观测统一

```python
# trace/bus.py
class Audit:
    def __init__(self, event_bus, run_store, task_state=None):
        self.event_bus = event_bus
        self.run_store = run_store
        self.task_state = task_state

    def emit(self, event: str, **kwargs):
        payload = {"event": event, "created_at": now(), **kwargs}
        self.event_bus.emit(event, payload)
        if self.task_state:
            self._append_trace(event, payload)

    def with_task(self, task_state):
        return Audit(self.event_bus, self.run_store, task_state)
```

engine/loop.py 中每条 log 调用：

```
turn 开始      → audit.emit("turn_started", run_id=..., task_id=...)
prompt 构建    → audit.emit("prompt_built", metadata=...)
模型调用       → audit.emit("model_requested", attempts=...)
工具开始       → audit.emit("tool_started", name=name, args=args)
工具完成       → audit.emit("tool_finished", name=name, status=...)
checkpoint     → audit.emit("checkpoint_created", trigger=..., id=...)
最终回答       → audit.emit("assistant_message", kind="final", content=...)
run 结束       → audit.emit("run_finished", status=...)
turn 结束      → audit.emit("turn_finished", status=..., duration=...)
```

## 分阶段实施（围绕 agent loop 递增）

### 阶段 1：裸 Loop

**目标**：能发 prompt 给模型，拿到文本回来。

```
输入 → prompt → 模型 → 文本 → 返回
```

新增文件：agent.py, engine/loop.py, engine/model.py, data/task.py
已有：providers/, workspace.py

### 阶段 2：Loop + 工具执行

**目标**：模型能调用工具，执行后把结果喂回去。

```
输入 → prompt → 模型 → 解析（tool/final）
                         │
                    tool → 五级门链 → 执行 → 结果追加到历史 → 回到 prompt
                         │
                   final → 返回
```

新增文件：guard/, engine/tool.py, tools/
新增到 data/：按需加 schema 相关 dataclass

### 阶段 3：Loop + 工具 + 上下文治理

**目标**：prompt 超限时自动压缩。

新增文件：context/, data/session.py

### 阶段 4：Loop + 工具 + 上下文 + 记忆

**目标**：agent 跨 turn 记住做了什么。

新增文件：memory/store.py, memory/fresh.py, memory/recall.py, data/memory.py
更新：guard/policy.py（prior_read_required）

### 阶段 5：Loop + 工具 + 上下文 + 记忆 + 断点续跑

**目标**：session 关掉后，下次启动能接着做。

新增文件：life/checkpoint.py, life/resume.py, data/checkpoint.py, data/identity.py

### 阶段 6：Loop + 工具 + 上下文 + 记忆 + 续跑 + 可观测

**目标**：所有 agent 行为可追溯。

新增文件：trace/, data/trace.py, data/events.py

### 阶段 7：外挂

skills/, workers/, life/plan.py, memory/durable.py

## agent.py 组合根（~100 行）

```python
class Agent:
    def __init__(self, model_client, workspace, session_store, *, session=None, ...):
        self.session = session or Session.create()
        self.workspace = workspace
        self.model_client = model_client
        self.session_store = session_store
        self.memory = LayeredMemory(self.session.memory, workspace.root)
        self.engine = Engine(self)
        self.context_assembler = ContextAssembler(self)
        self.tools = build_tools(self)
        self.tool_profile = select_profile(...)
        self.permission_checker = PermissionChecker()
        self.tool_policy_checker = ToolPolicyChecker()
        self.event_bus = EventBus(self.session.id, ...)
        self.run_store = RunStore(workspace.root)

    def ask(self, message: str) -> str:
        return self.engine.ask(message)

    def run_turn(self, user_message):
        return self.engine.run_turn(user_message)
```

## engine/loop.py 关键结构

```python
def run_turn(self, user_message):
    task_state = TaskState.create(user_message)
    audit = self.agent.make_audit(task_state)

    audit.emit("turn_started", run_id=task_state.run_id, ...)
    audit.emit("run_started", task_id=..., user_request=...)

    record_user_message(user_message)
    update_memory_task_summary(user_message)

    tool_steps = 0
    attempts = 0
    max_attempts = max_steps + 2

    while tool_steps < max_steps and attempts < max_attempts:
        if abort_requested:
            yield from finish_run(self, task_state, ..., stop_reason="aborted")
            return

        # checkpoint 触发判断
        prompt, metadata = build_prompt(user_message)
        audit.emit("prompt_built", metadata=metadata)
        if metadata.resume_status == "partial-stale":
            checkpoint = create_checkpoint(task_state, trigger="freshness_mismatch")
            audit.emit("checkpoint_created", id=checkpoint.id, trigger="freshness_mismatch")

        # 调用模型
        result = complete_model(model_client, prompt, max_tokens)
        kind, payload = parse(result.text)
        audit.emit("model_parsed", kind=kind, duration_ms=...)

        # 路由
        if kind == "tool":
            audit.emit("tool_started", name=payload.name, args=payload.args)
            tool_result = execute_tool(agent, payload.name, payload.args)
            audit.emit("tool_finished", name=tool_result.name, status=tool_result.status, ...)
            audit.emit("tool_executed", ...)
            checkpoint = create_checkpoint(task_state, trigger="tool_executed")
            audit.emit("checkpoint_created", id=checkpoint.id, trigger="tool_executed")
            tool_steps += 1
            continue

        if kind == "final":
            record_in_history("assistant", payload)
            promote_durable_memory(user_message, payload)
            maintain_memory(payload)
            checkpoint = create_checkpoint(task_state, trigger="run_finished")
            audit.emit("checkpoint_created", id=checkpoint.id, trigger="run_finished")
            audit.emit("run_finished", status="completed", ...)
            audit.emit("turn_finished", status="completed", ...)
            write_report(task_state)
            yield {"type": "final", "content": payload}
            return

    # 预算耗尽
    yield from finish_run(self, task_state, ..., stop_reason="step_limit_reached")
```

## 与原结构对应

| 原来 | 重构后 |
|------|--------|
| core/runtime.py（939 行 God Object） | agent.py（~100 行）+ 逻辑分散到各层 |
| core/engine.py + engine_helpers.py + model_errors.py | engine/loop.py + tool.py + model.py + lifecycle.py |
| core/session.py 不存在 | data/session.py（dataclass） |
| core/permissions.py + tool_policy.py + tool_profiles.py + tool_repetition.py + tool_executor.py | guard/ 下 5 个文件 + engine/tool.py 的 execute_tool() |
| core/session_events.py + runtime_events.py + runtime_consumers.py + artifacts.py | trace/ 下 4 个文件 |
| features/memory.py（1375 行） | memory/ 下 4 个文件 |
| core/runtime_checkpoints.py | life/checkpoint.py |
| core/session_lifecycle.py | life/resume.py |
| core/context_manager.py + compact.py + turn_history.py | context/ 下 3 个文件 |
| providers/ | 不变 |
| tools/ | 不变 |
| cli.py + tui/ | 不变 |
