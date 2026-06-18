# Auto-Compaction 设计答复

## 问题 1：`_handlePostAgentRun` 的调用位置

同意在你说的位置调用。不能在 `_handle_event(AgentEnd)` 里调——AgentEnd 是 Loop 在 emit 过程中发出的，此时 state 还没稳定，嵌套调 `agent.prompt()` 会导致 re-entry。

正确做法：`prompt()` 里 `await agent.prompt()` 返回后（Loop 已结束，state 已收拢），再调 `_handlePostAgentRun()`。这个函数返回 True 就 `agent.continue()`，循环。

## 问题 2：context_window 从哪来

选项 C：从模型元数据获取。ModelClient 在初始化时就知道自己的 model id，在 settings 或 model_registry 里给每个模型配一个 contextWindow 值。这样加新模型只需要改配置文件，不改代码。默认值设为 128000（大多数模型的下限）。

一个典型的配置项就是 `{ "model": "deepseek-chat", "contextWindow": 64000 }`。ModelClient 初始化时查一下这张表。如果在表里找不到，fallback 到 128000。

## 问题 3：压缩用的 LLM 调用

接受用 `stream_complete()`。遍历 blocks 提取最终文本就行了，多写几行遍历代码不是问题。不用加回 `complete()`。

## 问题 4：cut point 规则

从后往前扫，保留最近 1 轮完整的"assistant + tool_results"不动。规则如下：

- 最近的 assistant 消息以及它调用的所有 tool 和对应的 tool_result 全部保留
- 从这轮之前开始压缩
- ToolCallContent 和对应的 ToolResultMessage 必须成对出现，不能切在中间
- CompactionSummaryMessage 本身不参与压缩（跳过它继续往前找）
- 如果所有消息加起来不够一轮，放弃压缩

## 问题 5：递归压缩

先做一次，如果压缩后仍然超限，递归再压，上限 3 次。

实践中一次就够了。设 3 次上限是防止极端情况死循环。3 次后仍然超限的话，放弃并通知用户手动处理。

overflow recovery 多一个保护：同一次 prompt 内如果已经触发过一次 overflow 压缩，不再次触发，防止反复失败。

## 问题 6：用户反馈机制

选 B：emit 事件。不要直接 print。

AgentSession 不知道 UI 层的存在，它 emit `compaction_start` 和 `compaction_end` 事件。UI listener 收到后自己决定怎么显示——可以静默忽略，可以显示 spinner，可以打印一行。这个决定权留给 UI 层。

## 问题 7：should_compact 的 token 来源

出错时（stopReason="error"，没有 usage），往前找倒数第二个有 usage 的 assistant 消息来估算。也不需要精确值，只要大概知道当前上下文占比就够了。

如果整个历史里一条 usage 数据都没有，跳过阈值检测。这时只靠 `is_context_overflow` 的错误消息匹配来触发压缩——LLM 报超限错就直接压缩，不依赖 usage。
