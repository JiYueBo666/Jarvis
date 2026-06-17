import json

from src.data.messages import (
    UserMessage,
    AssistantMessage,
    ToolResultMessage,
    CompactionSummaryMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    AgentMessage,
)

COMPACTION_PREFIX = (
    "The conversation history before this point was compacted:\n\n<summary>\n"
)
COMPACTION_SUFFIX = "\n</summary>"


def convert_to_llm(messages: list[AgentMessage]) -> list[dict]:
    result = []
    for m in messages:
        converted = _convert_one(m)
        if converted:
            result.append(converted)
    return result


def _convert_one(message: AgentMessage) -> dict | None:
    match message.role:
        case "user":
            text = "".join(
                block.text for block in message.content if isinstance(block, TextContent)
            )
            return {"role": "user", "content": text}

        case "assistant":
            text = "".join(
                block.text for block in message.content if isinstance(block, TextContent)
            )
            thinking = "".join(
                block.thinking for block in message.content if isinstance(block, ThinkingContent)
            )
            msg: dict = {"role": "assistant", "content": text if text else None}
            if thinking:
                msg["reasoning_content"] = thinking
            tool_calls = [
                block for block in message.content if isinstance(block, ToolCallContent)
            ]
            if tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ]
            return msg

        case "tool_result":
            text = "".join(
                block.text for block in message.content if isinstance(block, TextContent)
            )
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": text,
            }

        case "compaction_summary":
            return {
                "role": "user",
                "content": COMPACTION_PREFIX + message.summary + COMPACTION_SUFFIX,
            }

        case _:
            return None
