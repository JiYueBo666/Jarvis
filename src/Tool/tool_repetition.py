"""Repeated tool-call guardrails."""

# 会变更文件的操作
FILE_MUTATION_TOOLS = {"write_file", "patch_file"}


def is_repetition_tool_call(history: dict, name: str, args: dict):
    # 1. 获取【当前轮次】的历史（只看这一轮，不看之前的对话）
    current_turn: list[dict] = _current_turn_history(history)
    # 2. 筛选出：历史中所有 role="tool" 的记录（工具调用记录）
    tool_events = [
        (index, item)  # (1,{role:tool,content:xxx,name:xxx,args:xxx})
        for index, item in enumerate(current_turn)
        if item.get("role") == "tool"
    ]
    # 3. 在工具记录里，找【同名+同参数】完全匹配的记录
    #    即：之前是否调用过 完全一样的工具
    matches = [
        (index, item)
        for index, item in tool_events
        if item.get("name") == name and item.get("args") == args
    ]
    # -------------------
    # 特殊逻辑：文件修改类工具单独判断.（write/patch）
    # -------------------
    if name in FILE_MUTATION_TOOLS:
        # 之前没调用过 → 不是重复
        if not matches:
            return False
        # 取最后一次匹配
        last_index, last_match = matches[-1]
        # 检查：是否是“已告知用户的失败重试”
        # 如果是 → 不算重复；否则 → 算重复
        return not _failed_file_write_retry_is_now_informed(
            current_turn, last_index, last_match
        )

    # -------------------
    # 普通工具：重复调用 ≥2 次，就判定为重复
    # -------------------
    return len(matches) >= 2


def _failed_file_write_retry_is_now_informed(
    current_turn: list[dict], last_index, last_match: dict
):
    # 1. 拿到上一次文件工具的返回内容
    content = str(last_match.get("content", ""))

    # 2. 上一次修改文件函数不是失败，则拒绝
    if not content.startswith("error:"):
        return False

    # 3. 拿到出错的文件路径
    path = str((last_match.get("args") or {}).get("path", ""))
    if not path:
        return False  # 无路径 → 不豁免

    """
    AI 写文件失败后，有没有主动去读一下这个文件，看看为啥失败？
    读了 = 懂事 = 允许重试 = 不算重复
    没读，直接重试 = 无脑循环 = 算重复 = 拦住
    """
    # 4. 从上次失败后，遍历后续历史
    for item in current_turn[last_index + 1 :]:
        # 只看 read_file 工具调用
        if item.get("role") != "tool" or item.get("name") != "read_file":
            continue

        args = item.get("args") or {}
        # 判断：是否读取了【同一个文件】 + 读取【没有报错】
        if str(args.get("path", "")) == path and not str(
            item.get("content", "")
        ).startswith("error:"):
            return True  # → 满足：AI 已经尝试修复错误

    # 5. 没有尝试修复 → 不豁免
    return False


def _current_turn_history(history):
    # 1. 把历史转成列表（防止生成器等不可变类型）
    history = list(history)

    # 2. 从后往前倒着遍历历史
    for index in range(len(history) - 1, -1, -1):
        # 3. 找到【最后一次用户提问】
        if history[index].get("role") == "user":
            # 4. 返回：用户消息之后的所有记录（AI/tool 执行记录）
            return history[index + 1 :]

    # 5. 如果没找到用户消息，返回全部历史
    return history
