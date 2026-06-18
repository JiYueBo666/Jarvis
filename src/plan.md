# Auto-Compaction 实现计划

## 背景

`_is_retryable_error` 目前对上下文超限返回 False（不原地重试），但没有任何后续处理——只是放弃。Auto-compaction 填补这个缺口：不原地重试，而是压缩后重试。

## 流程图

```
LLM 返回结果
  ↓
_handlePostAgentRun()
  │
  ├─ 可重试错误? (rate limit, 500...) ──→ _prepare_retry() → agent.prompt([])  ← 原地重试
  │
  ├─ 上下文超限? ──→ _runAutoCompaction() ──→ agent.prompt([])  ← 压缩后重试
  │
  └─ 正常结束
```

## Step 1: 事件类型 — `src/data/event.py`

```python
@dataclass
class CompactionStart:
    """上下文压缩开始。"""


@dataclass
class CompactionEnd:
    """上下文压缩完成。"""
    messages_before: int
    messages_after: int
```

加到 `AgentEvent` union 里。

## Step 2: 配置 — `src/config.py`

`Settings` 类新增：

```python
COMPACTION_ENABLED: bool = True
COMPACTION_RESERVE_TOKENS: int = 24000
```

## Step 3: context window 注册表 — `src/engine/model.py`

文件末新增：

```python
_CONTEXT_WINDOWS = {
    "deepseek-chat": 65536,
    "deepseek-reasoner": 131072,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
}
_DEFAULT_CONTEXT_WINDOW = 128000


def get_context_window(model: str) -> int:
    return _CONTEXT_WINDOWS.get(model, _DEFAULT_CONTEXT_WINDOW)
```

## Step 4: AgentSession — `src/AgentSession/agent_session.py`

### 4a. 补齐 `_is_retryable_error`

第 76 行的空注释 `# 上下文超限制，不重试` 变成真实 guard：

```python
# 上下文超限制，不重试
overflow_patterns = ("context length", "maximum context", "too many tokens", "token limit")
if any(p in err for p in overflow_patterns):
    return False
```

### 4b. 新增方法

| 方法 | 签名 | 职责 |
|---|---|---|
| `_handlePostAgentRun()` | `async → None` | 入口：判断错误类型 → 重试或压缩 |
| `_last_assistant_message()` | `→ AssistantMessage \| None` | 取最后一个 assistant 消息 |
| `_checkCompaction(msg)` | `async → bool` | 判断是否需要压缩 |
| `_is_context_overflow(msg)` | `static → bool` | 错误消息匹配 overflow 模式 |
| `_should_compact(input_tokens)` | `static → bool` | usage > (ctx_window - reserve) |
| `_runAutoCompaction()` | `async → None` | 选 cut point → 摘要 → 替换 |
| `_select_cut_point(messages)` | `static → int \| None` | 找安全压缩边界 |
| `_generate_summary(messages)` | `async → str` | 调 LLM 生成摘要 |

### 4c. `_handlePostAgentRun()` 伪代码

```python
async def _handlePostAgentRun(self):
    if not self._session_manager:
        return
    ctx_window = get_context_window(self.model_client.model)

    for attempt in range(3):
        msg = self._last_assistant_message()
        if not msg:
            break

        # 1. 可重试错误（rate limit, 500 等）→ 原地重试
        if await self._is_retryable_error(msg):
            if await self._prepare_retry(msg):
                # 移除最后一条错误消息，重新跑
                self._agent._state.messages = self._agent._state.messages[:-1]
                await self._agent.prompt([])
                continue
            break

        # 2. 上下文超限 → 压缩后重试
        if await self._checkCompaction(msg, ctx_window):
            await self._runAutoCompaction()
            await self._agent.prompt([])
            continue

        # 3. 正常 → 退出
        break
```

### 4d. `_checkCompaction()` 伪代码

```python
async def _checkCompaction(self, msg: AssistantMessage, ctx_window: int) -> bool:
    if not self._session_manager:
        return False

    # 显式 overflow 错误消息
    if self._is_context_overflow(msg):
        return True

    # 静默超限：usage.input > context_window
    if msg.usage and msg.usage.input_tokens > ctx_window:
        return True

    # 阈值检测：接近上限
    if msg.usage and self._should_compact(msg.usage.input_tokens, ctx_window):
        return True

    return False
```

### 4e. `_is_context_overflow()` 静态方法

```python
@staticmethod
def _is_context_overflow(msg: AssistantMessage) -> bool:
    if msg.stop_reason != "error" or not msg.error_message:
        return False
    err = msg.error_message.lower()
    patterns = ("context length", "maximum context", "too many tokens", "token limit")
    return any(p in err for p in patterns)
```

### 4f. `_should_compact()` 静态方法

```python
@staticmethod
def _should_compact(input_tokens: int, ctx_window: int) -> bool:
    from src.config import settings
    reserve = settings.COMPACTION_RESERVE_TOKENS
    return input_tokens > ctx_window - reserve
```

### 4g. `_select_cut_point()` 静态方法

```python
@staticmethod
def _select_cut_point(messages: list) -> int | None:
    """从后往前找安全压缩边界。返回保留的起始 index，None 表示不足以压缩。"""
    # 找到最后一个 AssistantMessage
    last_assistant_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], AssistantMessage):
            last_assistant_idx = i
            break
    if last_assistant_idx is None:
        return None

    # 收集它 emit 的 tool_call_ids
    tool_ids = set()
    for block in messages[last_assistant_idx].content:
        if isinstance(block, ToolCallContent):
            tool_ids.add(block.id)

    # 找到最后一个关联的 ToolResultMessage
    last_result_idx = last_assistant_idx
    for i in range(last_assistant_idx + 1, len(messages)):
        if isinstance(messages[i], ToolResultMessage) and messages[i].tool_call_id in tool_ids:
            last_result_idx = i

    # 这之前如果有 CompactionSummaryMessage，跳过它
    cut = last_result_idx
    for i in range(last_result_idx - 1, -1, -1):
        if not isinstance(messages[i], CompactionSummaryMessage):
            cut = i + 1
            break

    if cut < 2:  # 至少留 1 条 user message + 1 条 assistant
        return None
    return cut
```

### 4h. `_generate_summary()` 伪代码

```python
async def _generate_summary(self, messages: list) -> str:
    lines = []
    for m in messages:
        role = m.role
        text_parts = []
        for block in getattr(m, "content", []) or []:
            if isinstance(block, TextContent):
                text_parts.append(block.text)
            elif isinstance(block, ToolCallContent):
                text_parts.append(f"[tool_call: {block.name}({block.arguments})]")
        if text_parts:
            lines.append(f"{role}: {' '.join(text_parts)}")
    summary_prompt = (
        "Summarize the following conversation concisely. "
        "Preserve all facts, decisions, file paths, and code references.\n\n"
        + "\n".join(lines)
    )
    llm_messages = [
        {"role": "system", "content": "You are a conversation summarizer."},
        {"role": "user", "content": summary_prompt},
    ]
    # retry up to 2 times
    for _ in range(2):
        try:
            text_parts = []
            async for block in self.model_client.async_client.stream_complete(
                model=self.model_client.model,
                messages=llm_messages,
                max_tokens=1024,
                stream=True,
            ):
                # 实际需适配 ModelClient.stream_complete 的 yield 格式
                pass
            return "".join(text_parts)
        except Exception:
            continue
    return ""
```

### 4i. `_runAutoCompaction()` 伪代码

```python
async def _runAutoCompaction(self):
    msgs = self._agent._state.messages
    cut = self._select_cut_point(msgs)
    if cut is None or cut < 1:
        return

    to_compress = msgs[:cut]
    to_keep = msgs[cut:]

    summary = await self._generate_summary(to_compress)
    summary_msg = CompactionSummaryMessage(
        summary=summary,
        tokens_before=sum(m.usage.input_tokens for m in to_compress if hasattr(m, "usage") and m.usage),
    )

    self._agent._state.messages = [summary_msg] + to_keep
```

## Step 5: 接入 `prompt()` 方法

```python
async def prompt(self, query: str):
    self._agent._state.systemPrompt = self._build_systemPrompt()
    if self._agent._state.isStreaming:
        raise RuntimeError("Agent is already processing")
    msg = UserMessage(
        role="user",
        content=[TextContent(text=query)],
        timestamp=int(time.time()),
    )
    await self._agent.prompt([msg])
    await self._handlePostAgentRun()
```

注意：`_handlePostAgentRun` 内部可能递归调 `agent.prompt([])`，所以 `prompt(query)` 返回时，消息状态已经是压缩+重试后的最终状态。

## Step 6: UI 渲染 — `src/cli.py`

`_render_event` 新增：

```python
elif isinstance(event, CompactionStart):
    _dim(f"  ⏳ 正在压缩对话历史...")
elif isinstance(event, CompactionEnd):
    _dim(f"  ✅ 压缩完成: {event.messages_before} → {event.messages_after} 条")
```

## Step 7: `_handle_after` 的去留

当前 `_handle_after` + `_prepare_retry` 是重试机制的旧骨架。`_handlePostAgentRun` 接管后，`_handle_after` 可以删除。

`_prepare_retry(msg)` 的内部逻辑（移除最后一条 error message）保留，融入 `_handlePostAgentRun` 的原地重试分支。

## 未解决的问题（待确认）

1. `_generate_summary` 里怎么调 `ModelClient`？当前 `AgentSession` 持有 `self.model_client`（`ModelClient` 实例）。用 `self.model_client.async_client.chat.completions.create()` 直接调 sync 还是 `stream_complete`？建议直接复用 `ModelClient.stream_complete`，但需要 import。

2. `_select_cut_point` 对 `CompactionSummaryMessage` 的处理：跳过它往前找，但保留它本身不被压缩。如果连续多个 CompactionSummaryMessage，保留最近一个。
