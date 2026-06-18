# Auto-Compaction 设计讨论

## 问题 1: `_handlePostAgentRun` 的调用位置

当前理解：在 `_handle_event(AgentEnd)` 里直接调 → **有 re-entrancy 风险**（`AgentEnd` 是 `Engine.run_stream` emit 过程中触发的，此时再调 `agent.prompt([])` 会嵌套启动新的 `run_stream`）

建议位置：在 `AgentSession.prompt()` 里 `await self._agent.prompt()` 返回后调用：

```python
async def prompt(self, query: str):
    ...
    await self._agent.prompt([msg])
    await self._handlePostAgentRun()   # run 已结束，安全
```

**问：同意这个位置吗？还是坚持在 `_handle_event` 里？**

---

## 问题 2: `context_window` 从哪来？

不同模型的 context window 不同：
- deepseek-chat → 64K
- deepseek-reasoner → 128K
- gpt-4o → 128K

选项 A：在 `Settings` 加 `CONTEXT_WINDOW: int = 64000`
选项 B：从模型名推断（硬编码映射表）
选项 C：从 `ModelClient` 的模型元数据获取

**问：倾向哪个？建议 A（配置简单）。**

---

## 问题 3: 压缩用的 LLM 调用

`_runAutoCompaction` 需要调用 LLM 生成摘要。`complete()` 已删，只剩下 `stream_complete()`。

`stream_complete()` yield blocks，需要遍历提取文本拼成摘要。可以工作，但比同步调用多几行代码。

**问：接受用 `stream_complete()`，还是需要加回 `complete()`？**

---

## 问题 4: cut point 规则

"最早可以安全压缩的消息" 需要明确定义：

- `ToolCallContent` 和对应的 `ToolResultMessage` 必须成对保留，不能切在中间
- 保留最近 N 轮不压缩（N 是多少？建议 N=2）
- 系统提示不在 `messages` 里（单独传给 Engine），不用管
- `CompactionSummaryMessage` 本身不压缩

**问：N 值？还有其他约束？**

---

## 问题 5: 递归压缩

压缩后如果还是超限（摘要本身就很长），怎么办？

选项 A：递归再压一次（最多 3 次）
选项 B：报错让用户手动处理
选项 C：第一次压完后放弃，等下一轮

**问：倾向哪个？建议 A，3 次后仍超限则放弃。**

---

## 问题 6: 用户反馈机制

压缩耗时 1-5 秒（LLM 生成摘要），用户应该看到什么？

选项 A：静默压缩，用户无感知
选项 B：emit 事件让 UI 显示进度（如 `正在压缩对话...`）
选项 C：直接 `_dim()` 打印一行

**问：倾向哪个？建议 C，简单有效。**

---

## 问题 7: `should_compact` 的 token 来源

`context_tokens` 从 `last_assistant_msg.usage.input_tokens` 取。

但如果最后一轮出错了（`stopReason="error"`，没有 usage），怎么办？
- 用倒数第二个有 usage 的消息？
- 跳过阈值检测，只靠 `is_context_overflow` 的错误消息匹配？

**问：出错时怎么算？**
