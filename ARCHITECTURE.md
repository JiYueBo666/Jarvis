# Agent-mini

终端编程智能体，通过自然语言与 LLM 交互完成代码任务。

## 目录结构

```
src/
├── Agent/          Agent 实现，组合所有模块
├── Client/         抽象 LLM 客户端（兼容 OpenAI / Anthropic 格式）
├── Context/        对话上下文管理（消息列表、跨轮历史、JSONL 持久化）
├── EventBus/       项目事件总线（发布订阅）
├── Interact/       模型返回内容封装，让业务流程不依赖具体 API 格式
└── Tui/            前端渲染（REPL、流式输出、状态栏）
```

## 模块职责

### Agent

组合根。持有所有模块实例，对外暴露 `ask(query) -> str`。

职责：
- 初始化所有子模块（EventBus、Client、Context、Tools）
- 调用 Context 构建消息列表
- 调用编排循环，传入 EventBus 和 Context
- 管理 EventBus 订阅（trace 写入、最终文本捕获）
- 调用 Context 完成回合（history 持久化）
- 管理 Session 持久化

依赖：Client, Context, EventBus, Interact, Tools

### Client

抽象 LLM 客户端。封装具体的 API 调用细节。

职责：
- 提供统一的 `stream(messages, tools) -> AsyncIterator[StreamEvent]` 接口
- 支持 OpenAI 格式和 Anthropic 格式的 provider
- 将不同 provider 的返回归一化为统一的 StreamEvent 事件

StreamEvent 包括：
- `Chunk(content: str)` — 流式文本块
- `ToolCall(name: str, args: dict)` — 工具调用请求
- `Usage(input: int, output: int, cache: int)` — token 用量

不依赖：其他模块。只依赖 HTTP 请求库和类型定义。

### Context

对话上下文管理。

职责：
- `start_turn(query)` — 构建 messages = [system, history..., user]
- `append_assistant(text, tool_calls)` — 追加 assistant 消息
- `append_tool_result(id, content)` — 追加 tool result
- `finish_turn()` — 将本轮消息移入跨轮历史
- system prompt 通过外部回调注入，不在 Context 中硬编码
- 跨轮历史压缩（budget 超限时自动摘要）

数据：`_messages`（当前轮），`_history`（跨轮）

依赖：HistoryManager（跨轮历史）

### EventBus

发布订阅事件总线。

方法：
- `on(event_type, handler) -> unsubscribe_fn` — 订阅事件
- `emit(event_type, **data)` — 发布事件
- `request(event_type, **data) -> result` — 发布事件并等待 handler 返回结果

事件类型：
| 事件 | 方向 | 数据 |
|------|------|------|
| `reasoning` | Engine → CLI | `content: str` |
| `tool_call` | Engine → CLI | `name: str, args: dict` |
| `tool_result` | Engine → CLI | `name: str, output: str` |
| `final` | Engine → CLI | `text: str` |
| `ask_user` | Engine → CLI | `question: str` |
| `approval_required` | Engine → CLI | `name: str, args: dict` |

### Interact

模型返回内容封装。将 LLM 返回的原始响应转换成业务流程可消费的格式。

职责：
- 解析 LLM 回复中的文本、工具调用
- 提供统一的 `ModelResult(text, tool_calls)` 数据结构
- 让 Engine 不依赖具体 provider 的消息格式

不依赖：其他模块（纯数据转换）。

### Tui

前端渲染。

职责：
- 提供 REPL（接收用户输入、调用 agent.ask）
- 注册 EventBus 回调（渲染 reasoning、tool_call、tool_result、final）
- 处理 ask_user 和 approval_required 的交互（input/confirm）
- 显示 spinner、token 统计、状态栏

依赖：Agent, EventBus

## 数据流

```
用户输入 → Tui → Agent → Context(start_turn)
                           ↓
                    Engine.run_stream(event_bus, ...)
                           ↓
                    Client.stream(messages, tools)
                           ↓
                    Interact.parse(response)
                           ↓
                    Engine 循环（工具执行）
                           ↓
                    Context(finish_turn)
                           ↓
                    Tui ← EventBus
```

## 事件流

```
Engine loop:
    emit("reasoning")    → Tui 流式打印
    emit("tool_call")    → Tui 打印工具名
    request("ask_user")  → Tui input() 返回回答
    emit("tool_result")  → Tui 打印结果 + token 统计
    emit("final")        → Tui 打印最终回答 + 累计统计
```

## 依赖关系（自下而上）

```
EventBus → 无依赖
Client   → 无依赖（只依赖 HTTP 库）
Interact → 无依赖（纯数据类型）
Context  → EventBus, HistoryManager
Agent    → EventBus, Client, Context, Interact, Tools
Tui      → Agent, EventBus
```

## 核心数据类型

### 消息格式

Engine 和 Context 内部统一用一种消息格式，不直接使用各 provider 的原始格式。转换发生在 Client 层。

```
@dataclass
class SystemMessage:
    content: str

@dataclass
class UserMessage:
    content: str | list[ContentBlock]

@dataclass
class AssistantMessage:
    content: str
    tool_calls: list[ToolCall] | None = None

@dataclass
class ToolResultMessage:
    tool_call_id: str
    content: str
    is_error: bool = False

@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
```

设计决策：UserMessage 的 content 支持 list 是为了预留图文输入。第一版先只支持 str。

---

### 模型返回（Client → Engine）

Client 返回流式事件，Interact 组装为完整结果。

```
@dataclass
class StreamChunk:
    content: str                     # 文本块

@dataclass
class StreamToolCall:
    id: str
    name: str
    args: dict                       # 单次增量，需累积

@dataclass
class StreamUsage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0

@dataclass
class StreamFinish:
    stop_reason: str                 # stop | tool_use | error | max_tokens
    usage: StreamUsage | None = None

StreamEvent = StreamChunk | StreamToolCall | StreamUsage | StreamFinish

@dataclass
class ModelResult:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: StreamUsage | None = None
```

设计决策：Client 只负责调 API 吐 StreamEvent，Interact 负责拼成 ModelResult。Client 不依赖 Interact。

---

### 事件总线

EventBus 事件：

```
reasoning          → { content: str }
tool_call          → { name: str, args: dict }
tool_result        → { name: str, output: str, metadata: dict }
final              → { text: str }
ask_user           → request: { question: str } 返回 str
approval_required  → request: { name: str, args: dict } 返回 { decision: bool, auto: bool }
```

request 事件会阻塞 Engine，等待 handler 返回结果才继续。

---

### 工具

```
@dataclass
class ToolResult:
    output: str
    success: bool
    affected_paths: list[str] | None = None
    metadata: dict | None = None

class Tool(ABC):
    name: str
    description: str
    parameters: dict              # JSON Schema
    risky: bool = False          # 是否需要审批
    @abstractmethod
    def execute(self, args: dict) -> ToolResult: ...
```

设计决策：risky 是工具自带的属性，不是外部策略。外部策略（approval_policy）决定怎么处理 risky 工具，但不负责判断哪个工具是 risky 的。

---

### 模块接口

```
class Client(ABC):
    def stream(self, messages: list, tools: list) -> AsyncIterator[StreamEvent]: ...

class Context:
    def start_turn(self, query: str, system_prompt: str):
        # 构建 messages = [system, history..., user]
    def append_assistant(self, text: str, tool_calls: list | None):
    def append_tool_result(self, call_id: str, content: str, is_error: bool = False):
    def finish_turn(self):
        # 将本轮消息移入 history

class EventBus:
    def on(self, event_type: str, handler) -> Callable[[], None]:  # 返回 unsub
    def emit(self, event_type: str, **data):
    def request(self, event_type: str, **data) -> Any | None:      # 阻塞等结果
```

## 关键设计决策

1. **Client 不依赖 Interact** — Client 只调 API 吐原始 chunk，Interact 负责解析成业务格式。
2. **EventBus request 用于双向交互** — ask_user 和 approval_required 通过 request() 同步阻塞等待。
3. **Context 不持有 system prompt** — system prompt 由 Agent 传入，Context 只负责拼消息列表。
4. **Tui 不直接操作消息列表** — Tui 只知道 EventBus 事件。
5. **risky 是工具属性，不是外部策略** — 工具自己声明是否危险，审批策略决定怎么对待危险工具。
6. **消息格式统一** — Engine 和 Context 内部统一用定义的 Message 类型，不直接使用 provider 的原始格式。
7. **Interact 负责组装** — Client 只吐流式碎片，Interact 负责从碎片拼完整消息。
