# pi 架构参考

## 三层架构

```
                    ┌──────────────────────────────────┐
                    │        AgentSession              │  应用层
                    │  (扩展、重试、溢出恢复、队列)       │
                    └──────────────┬───────────────────┘
                                   │
                    ┌──────────────┴───────────────────┐
                    │        AgentHarness              │  调度层
                    │  (session、compaction、事件分发)    │
                    └──────────────┬───────────────────┘
                                   │
                    ┌──────────────┴───────────────────┐
                    │       agent-loop.ts              │  引擎层
                    │  (调 LLM + 执行工具的纯循环)       │
                    └──────────────────────────────────┘
```

每一层只能依赖下面一层。引擎层不知道调度层的存在，调度层不知道应用层的存在。

---

## 引擎层：agent-loop.ts

### 设计

纯循环，只做一件事：把 messages 发给 LLM，执行返回的 tool calls，往复循环。

```typescript
// 伪代码
function runLoop(context, config, emit, signal) {
    while (true) {
        // 内层循环：一轮 LLM + 工具
        while (hasMoreToolCalls || pendingMessages.length > 0) {
            // 注入 steering 消息
            // transformContext → convertToLlm → streamSimple
            // 执行工具（parallel 或 sequential）
        }
        // 外层循环：检查 follow-up 消息
        followUpMessages = config.getFollowUpMessages()
        if (followUpMessages.length > 0) continue
        break
    }
}
```

### 输入：AgentLoopConfig（全是回调）

| 回调 | 作用 |
|------|------|
| `convertToLlm` | AgentMessage[] → Message[]，内部格式转 LLM 格式 |
| `transformContext` | 在每次 LLM 调用前修改消息列表 |
| `prepareNextTurn` | 回合结束后替换 context/model/thinkingLevel |
| `shouldStopAfterTurn` | 决定是否在此回合后停止 |
| `getSteeringMessages` | 获取"正在工作中"的后续指令 |
| `getFollowUpMessages` | 获取"工作完成后"的后续指令 |
| `beforeToolCall` | 工具执行前拦截 |
| `afterToolCall` | 工具执行后修改结果 |
| `getApiKey` | 动态获取 API key |

### 输出：AgentEvent（统一事件流）

```typescript
类型：
    agent_start / agent_end
    turn_start / turn_end
    message_start / message_update / message_end
    tool_execution_start / tool_execution_update / tool_execution_end
```

### 关键设计决策

**引擎是纯数据入/数据出。** 它不知道 session、不知道 compaction、不知道扩展。所有"智能"行为通过回调注入。

**回调的契约：必须不抛异常。** 如果回调失败，loop 会卡住或崩溃。所以所有回调都需要 try-catch，返回安全 fallback。

---

## Agent 类

### 设计

在引擎外包一层，加状态管理和队列。

```typescript
class Agent {
    state: AgentState        // { systemPrompt, model, tools, messages, isStreaming }
    steeringQueue            // 用户中途插入的指令
    followUpQueue            // 用户想在完成后发的指令

    prompt(input)            // 启动一个 run
    continue()               // 从当前上下文继续
    steer(message)           // 插队（本回合处理完就发）
    followUp(message)        // 排队（当前 run 结束后再发）
    subscribe(listener)      // 订阅 AgentEvent

    // subscribe 返回 unsubscribe 函数
    subscribe(listener) {
        this.listeners.add(listener)
        return () => this.listeners.delete(listener)
    }
}
```

### 关键设计决策

**Agent 自身不产事件，也不消费事件。** 它只负责中转：agent-loop 产事件 → Agent 转发给所有 subscribe 的 listener。每个 listener 按注册顺序 await。

**队列有两种模式：** `"one-at-a-time"`（一次 drain 一条）和 `"all"`（一次 drain 全部）。steer 和 followUp 各自独立配置。

**Agent 有一个 run 的互斥锁。** prompt() 正在执行时不能再次 prompt()。steer 和 followUp 可以在执行中调用。

---

## AgentHarness

### 设计

调度决策层。在 Agent 之上，负责：

| 职责 | 实现 |
|------|------|
| Session 管理 | 积压写入，回合间隙刷新 |
| Compaction 调度 | 在 createTurnState 中检查并触发 |
| 扩展钩子 | on("event_type", handler) 注册，emitHook 触发 |
| Auth 管理 | getApiKeyAndHeaders 回调 |
| 工具管理 | 注册/激活/停用 |

### 三个事件发射方法

```typescript
emitHook(event)     → 有返回值，扩展可以影响流程
                      （context、tool_call、session_before_compact 等）

emitOwn(event)      → 无返回值，通知内部状态变化
                      （model_update、save_point、settled 等）

emitAny(event)      → 透传 agent-loop 的事件
                      （message_start、turn_end 等）
```

### createTurnState()

每个回合开始前调用。返回当前回合的 snapshot：messages、model、tools、systemPrompt。

这也是 compaction 触发器：buildContext 时如果发现有 compaction threshold 被触发，就执行 compaction。

### 关键设计决策

**分层的事件系统。** emitHook 允许扩展返回数据影响流程（如阻止工具执行），emitOwn 和 emitAny 只通知。

**pendingSessionWrites 积压机制。** 元数据变更（model change、thinking level change）不立刻写文件，攒到 turn_end 一次性 flush。

---

## Session 系统

### JSONL 文件格式

每条记录一行 JSON，包含 id、parentId、timestamp。树形结构通过 parentId 实现，支持分支。

```
{ "type": "message", "id": "a", "parentId": null, "message": {...} }
{ "type": "message", "id": "b", "parentId": "a", "message": {...} }
{ "type": "message", "id": "c", "parentId": "b", "message": {...} }
{ "type": "compaction", "id": "d", "parentId": "c", 
  "summary": "...", "firstKeptEntryId": "b", "tokensBefore": 50000 }
```

### 条目类型

| 类型 | 说明 |
|------|------|
| `message` | user/assistant/toolResult 对话消息 |
| `compaction` | 压缩摘要，firstKeptEntryId 指向保留的起始条目 |
| `branch_summary` | 导航到其他分支时生成的摘要 |
| `thinking_level_change` / `model_change` / `active_tools_change` | 元数据条目 |
| `custom_message` | 扩展自定义消息 |
| `label` | 树节点标签，用于书签 |

### buildSessionContext()

从当前活动分支构建 AgentMessage[]：
- 遇到 compaction 条目 → 插入一条 `compactionSummary` 消息（带 `<summary>` 标签）
- 遇到 branch_summary 条目 → 插入一条 `branchSummary` 消息
- 跳过元数据条目（model_change 等）

---

## Compaction

### 触发条件

```
contextTokens > contextWindow - reserveTokens
```

默认 reserveTokens = 16384（为 LLM 输出预留），keepRecentTokens = 20000（保留的最新消息量）。

### 流程

```
1. 计算当前 contextTokens（优先用 provider 的 usage，否则启发式估算）
2. findCutPoint()：从最新消息倒走，累计到 keepRecentTokens
3. 收集需要摘要的消息（从上一个 compaction boundary 到 cut point）
4. 调用 LLM 生成结构化摘要（Goal / Progress / Decisions / Next Steps）
5. 写入 CompactionEntry
6. 下次 buildContext() 时，摘要 + firstKeptEntryId 往后的消息一起发 LLM
```

### 切分规则

- 只允许从 user/assistant/bashExecution/custom 消息处切
- 不允许从 toolResult 处切（必须跟在关联的 toolCall 后面）
- 一个超长 turn 会被切分：生成两份摘要（历史摘要 + turn 前缀摘要）

### 迭代更新

每次 compaction 的 working range 从上一次 compaction 的 `firstKeptEntryId` 开始（不是从 compaction 条目本身）。这样上一轮保留的消息在新的压缩中也会被纳入。

---

## 消息格式转换

### AgentMessage → Message（convertToLlm）

```typescript
AgentMessage 类型       → 转成 LLM Message
──────────────────────────────────────────────────────
user / assistant / toolResult     透传
bashExecution                     user 消息，格式化为文本
custom                            user 消息
branchSummary                     user，带 <summary> 标签
compactionSummary                 user，带 <summary> 标签
```

### 为什么分两步（transformContext + convertToLlm）

transformContext 在 AgentMessage 级别操作（扩展可以增删改消息）。
convertToLlm 做格式转换（纯函数，无副作用）。

两步分离让扩展不必知道 LLM 的消息格式细节。

---

## 扩展事件：hook 模式

### 三种事件类型

| 类型 | 返回值 | 用途 |
|------|--------|------|
| **emitHook** | 扩展可以返回数据 | 拦截、修改流程（beforeToolCall、session_before_compact） |
| **emitOwn** | 无 | 通知内部状态变化（model_update、save_point） |
| **emitAny** | 无 | 透传 agent-loop 事件（message_start、turn_end） |

### emitHook 的实现

```typescript
private async emitHook(event) {
    const handlers = this.handlers.get(event.type)
    if (!handlers) return undefined
    
    let lastResult
    for (const handler of handlers) {
        const result = await handler(event)
        if (result !== undefined) lastResult = result
    }
    return lastResult  // 最后一个返回值胜出
}
```

### 扩展能做什么

| 事件 | 扩展可以 |
|------|---------|
| `context` | 修改即将发给 LLM 的消息列表 |
| `tool_call` | 阻止工具执行 |
| `tool_result` | 修改工具执行结果 |
| `session_before_compact` | 取消 compaction 或提供自定义摘要 |
| `session_before_tree` | 取消树导航或提供自定义分支摘要 |
| `before_provider_request` | 修改请求参数（headers、transport） |

---

## 工具管理

### 设计原则

pi 把工具分成三层：**定义 → 注册 → 激活**。

```
定义：Tool<TParameters>           — schema + name + description
注册：AgentTool extends Tool       — 加入 harness 的 this.tools Map
激活：activeToolNames               — 当前 LLM 可见的工具子集
```

### Tool 定义（pi-ai）

使用 TypeBox 做 schema 定义和参数校验：

```typescript
interface Tool<TParameters extends TSchema> {
    name: string;
    description: string;
    parameters: TParameters;  // TypeBox schema
}
```

参数校验在 `validateToolArguments()`：
- 类型自动转换（如 string → number）
- 缺失参数检测
- 额外参数过滤

### AgentTool（agent 层扩展）

```typescript
interface AgentTool<TParameters, TDetails> extends Tool<TParameters> {
    label: string;              // 人类可读的 UI 标签
    prepareArguments?: (args) => args;  // 参数兼容层
    executionMode?: "sequential" | "parallel";  // 单工具执行模式
    execute: (toolCallId, params, signal, onUpdate) => Promise<AgentToolResult<TDetails>>;
}
```

关键扩展点：
- **prepareArguments**：在 schema 校验前对参数做转换（如处理旧版本传参格式）
- **executionMode**：允许单个工具覆盖全局执行策略（如 bash 必须是 sequential）
- **onUpdate**：工具执行中流式返回中间结果（如 bash 实时输出）

### 注册 vs 激活

**注册（register）：** 工具加入 `this.tools` Map，可被 session 持久化，但 LLM 不一定能看到。

**激活（active）：** 工具名出现在 `activeToolNames` 中，才会出现在每次 LLM 调用的 tool schemas 里。

```typescript
// 只读文件工具可见，不暴露写文件工具
harness.setActiveTools(["read_file", "grep", "bash"])
```

### setActiveTools / setTools

```typescript
async setTools(tools: TTool[], activeToolNames?: string[]) {
    // 1. 检查名字唯一性
    // 2. 替换 tools Map
    // 3. 校验 activeToolNames 都在 tools 里
    // 4. 写 session（idle 时直接写，busy 时积压）
    // 5. 更新内存中的 tools + activeToolNames
    // 6. 发出 tools_update 事件
}

async setActiveTools(toolNames: string[]) {
    // 1. 校验都在 tools 里
    // 2. 写 session（同上）
    // 3. 更新 activeToolNames
    // 4. 发出 tools_update 事件
}
```

### 工具执行的 preflight 管道

在 agent-loop 中，每个工具执行前都经过三条检查：

```
tool_call 进入 executeToolCalls()
    │
    ├── 1. prepareToolCall()
    │       ├── 查找 tool 定义
    │       ├── prepareArguments()（可选兼容层）
    │       ├── validateToolArguments()（schema 校验）
    │       └── beforeToolCall hook（扩展可 block）
    │
    ├── 2. executePreparedToolCall()
    │       └── tool.execute()，带 signal + onUpdate
    │
    └── 3. finalizeExecutedToolCall()
            └── afterToolCall hook（扩展可修改结果）
```

### 工具执行的两种模式

```
parallel（默认）:
    1. preflight 所有工具（串行：查找 + 校验 + beforeToolCall）
    2. 并发执行所有允许的工具
    3. tool_execution_end 按完成顺序发出
    4. tool_result 消息按原始顺序发出

sequential:
    每个工具完整走完 preflight → execute → finalize 才到下一个
```

### 回退策略

如果工具执行抛出异常（不编码在 result 里），loop 自动生成错误 tool result，不会崩溃。

```typescript
try {
    const result = await tool.execute(...);
    return { result, isError: false };
} catch (error) {
    return {
        result: createErrorToolResult(error.message),
        isError: true,
    };
}
```

### 关键设计决策

**两个集合（tools vs activeToolNames）分离。** 一个工具可以注册但暂时隐藏。好处：
- 安全：`/tools` 命令可以临时禁用危险工具
- session：工具切换被持久化，下次启动恢复
- 扩展：可以注册工具但不立刻激活

代价：需要维护两个集合的一致性（activeToolNames 必须是 tools 的子集），每次 setTools 都要校验。

### 模式 1：回调注入（Strategy Pattern）

引擎层把所有决策点暴露为回调，不自己做决策。

```typescript
// 引擎层：不决定"什么时候该停"
interface AgentLoopConfig {
    shouldStopAfterTurn?: (context) => boolean | Promise<boolean>
}

// 调度层：做决策
config.shouldStopAfterTurn = (context) => {
    return estimateTokens(context.messages) > MAX_TOKENS
}
```

好处：引擎可以复用。不同应用可以注入不同的决策逻辑。
代价：回调的契约必须严格遵守（不抛异常），否则引擎行为不可预测。

### 模式 2：事件流 + 订阅（Observer Pattern）

agent-loop 产事件 → Agent 类转发 → 多个订阅者各自消费。

```typescript
// 引擎只管 emit
emit({ type: "message_end", message })

// 订阅者各自决定做什么
ui.subscribe()       → 渲染
session.subscribe()  → 持久化
extension.subscribe() → 扩展逻辑
```

好处：新增消费者不需要改引擎。
代价：事件类型爆炸，需要良好的类型定义保障。

### 模式 3：纯函数转换

消息格式转换全部是纯函数，不修改参数，返回新数组。

```typescript
// 不是
context.messages.push(newMessage)

// 是
const newMessages = convertToLlm(context.messages)
streamSimple(model, { messages: newMessages })
```

好处：可测试（给输入断言输出），可追踪（不变的数据流）。
代价：性能开销（分配新数组），对大多数场景可忽略。

### 模式 4：积压写入（Write-behind Logging）

元数据变更不立刻写文件，攒到自然边界（turn_end）一次性写入。

```typescript
pendingSessionWrites.push({ type: "model_change", ... })
// ...
async flushPendingSessionWrites() {
    while (pendingSessionWrites.length > 0) {
        const write = pendingSessionWrites.shift()
        await session.append(write)
    }
}
```

好处：减少磁盘 I/O，自然批处理。
代价：宕机时丢失最后一次积压的写入（元数据可容忍）。

### 模式 5：结果类型（Railway Oriented Programming）

函数返回 `Result<T, Error>`，不抛异常。

```typescript
type Result<T, E> = { ok: true; value: T } | { ok: false; error: E }

const result = prepareCompaction(entries, settings)
if (!result.ok) return err(result.error)
const preparation = result.value
```

好处：调用方必须处理错误（不能忽略 try-catch），错误类型清晰。
代价：代码冗长（每个调用都需要检查 ok）。

---

## 分层职责速查

```
agent-loop.ts（引擎层）:
    ✔ 调 LLM 并流式接收回复
    ✔ 执行工具（parallel/sequential）
    ✔ 消息队列 drain（steer/followUp）
    ✔ 产 AgentEvent 事件流
    ✗ 不知道 session 是什么
    ✗ 不知道 compaction 是什么
    ✗ 不知道模型切换是什么

Agent 类（引擎封装）:
    ✔ 状态管理（messages、model、tools）
    ✔ subscribe 事件分发
    ✔ steer/followUp 队列
    ✔ 运行互斥锁
    ✗ 不做决策（什么该停、什么该 compact）

AgentHarness（调度层）:
    ✔ 决策什么该停、什么该 compact
    ✔ 管理 session 读写
    ✔ 管理扩展事件
    ✔ 管理 auth、tools
    ✗ 不知道 UI 怎么渲染

AgentSession（应用层）:
    ✔ 扩展重试（overflow recovery）
    ✔ 自动 compaction 触发
    ✔ steering/followUp 的 UI 交互
    ✔ bash 实时输出
    ✗ 不知道终端有多大
