import json

HISTORY_BUDGET = 3000
KEEP_LAST_N = 2  # keep last N messages intact when compacting


def compact_history(history: list[dict], budget: int = HISTORY_BUDGET) -> list[dict]:
    """
    尝试将对话历史压缩到指定字符预算内。

    返回处理后的消息列表（可能会插入一条精简后的系统摘要消息）。
    如果原本就在预算内，则直接返回原历史。
    """
    if not history:
        return history

    # 计算当前对话历史总字符数
    total = _chars(history)
    if total <= budget:
        return history

    # 消息条数 ≤ 4 条时，直接从头部裁剪内容
    if len(history) <= 4:
        return _trim_front(history, budget)

    # 保留最后 N 条消息（但不能以 tool 开头，否则会没有对应的 assistant）
    split = len(history) - KEEP_LAST_N
    while split > 0 and history[split].get("role") == "tool":
        split -= 1
    keep = history[split:]
    to_compact = history[:split]

    summary = _build_summary(to_compact)
    compacted = [{"role": "system", "content": summary}] + keep

    # 如果精简后符合预算，直接返回
    if _chars(compacted) <= budget:
        return compacted

    # 摘要本身仍然过长 → 裁剪摘要内容
    keep_chars = _chars(keep)
    allowed = budget - keep_chars
    if allowed > 0:
        compacted[0]["content"] = compacted[0]["content"][:allowed]
    return compacted


def _build_summary(messages: list[dict]) -> str:
    """从对话消息中提取目标、读取的文件、修改的文件，生成精简摘要"""
    # 存储用户目标、读取的文件、修改的文件
    goals = []
    files_read: list[str] = []
    files_modified: list[str] = []

    # 遍历所有对话消息，提取关键信息
    for msg in messages:
        # 用户消息 → 提取需求/目标（只取前120字符）
        if msg.get("role") == "user":
            content = msg.get("content", "") or ""
            goals.append(content[:120])

        # 助手消息 → 提取工具调用（读文件/写文件）
        elif msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                try:
                    # 解析工具调用的参数（JSON格式）
                    args = json.loads(tc["function"]["arguments"])
                except (KeyError, json.JSONDecodeError):
                    args = {}

                # 获取工具名称和操作的文件路径
                name = tc.get("function", {}).get("name", "")
                path = args.get("path", "")

                # 记录读取的文件
                if name == "read_file" and path:
                    files_read.append(path)
                # 记录修改/写入的文件
                elif name in ("write_file", "patch_file") and path:
                    files_modified.append(path)

    # 开始组装精简摘要文本
    lines = ["会话精简摘要："]
    if goals:
        lines.append(f"- 目标：{goals[0]}")  # 只取第一个用户目标
    if files_read:
        lines.append(f"- 已读文件：{_unique(files_read, 12)}")  # 去重，最多12个
    if files_modified:
        lines.append(f"- 修改文件：{_unique(files_modified, 12)}")  # 去重，最多12个

    # 用换行拼接所有行并返回
    return "\n".join(lines)


def _unique(items: list[str], limit: int) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    s = ", ".join(out[:limit])
    if len(out) > limit:
        s += f", ... (+{len(out) - limit})"
    return s


def _trim_front(messages: list[dict], budget: int) -> list[dict]:
    """Trim char by char from the first message's content."""
    if not messages:
        return messages
    total = _chars(messages)
    if total <= budget:
        return messages

    # Trim the first message's content
    first = messages[0]
    content = first.get("content", "") or ""
    over = total - budget
    trimmed = content[over:] if len(content) > over else ""
    result = list(messages)
    result[0] = {**first, "content": trimmed}
    return result


def _chars(messages: list[dict]) -> int:
    return sum(len(m.get("content", "") or "") for m in messages)
