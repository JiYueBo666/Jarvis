import time
from typing import Callable
from src.data.messages import (
    AssistantMessage,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    Usage,
)
from src.data.event import (
    AgentEnd,
    TurnStart,
    TurnEnd,
    MessageStart,
    MessageUpdate,
    MessageEnd,
    ToolExecutionStart,
    ToolExecutionEnd,
)
from src.engine.model import ModelClient
from src.engine.tool import Tool


def _find_tool(tools: list[Tool], name: str) -> Tool | None:
    for tool in tools:
        if tool.name == name:
            return tool
    return None


class Engine:
    """纯编排。没有状态、没有初始化——只有 run()。"""

    @staticmethod
    async def run_stream(
        model_client: ModelClient,
        tools: list[Tool],
        system_prompt: str,
        messages: list,
        convert_to_llm: Callable,
        emit: Callable,
        before_tool_call: Callable | None = None,
        max_steps: int = 100,
    ):
        steps = 0
        current_messages = list(messages)
        schemas = [t.to_openai_schema() for t in tools]

        while steps < max_steps:
            steps += 1
            await emit(TurnStart())
            content_blocks = []
            usage = None

            # 转换消息格式
            llm_messages = convert_to_llm(current_messages)
            llm_messages.insert(0, {"role": "system", "content": system_prompt})

            # 获取流式数据
            async for block in model_client.stream_complete(
                llm_messages, max_new_tokens=8192, tools=schemas
            ):
                if isinstance(block, (TextContent, ThinkingContent, ToolCallContent)):
                    content_blocks.append(block)
                    await emit(
                        MessageUpdate(
                            AssistantMessage(
                                role="assistant",
                                content=[block],
                                timestamp=time.time(),
                            )
                        )
                    )
                elif isinstance(block, Usage):
                    usage = block

            assistant_msg = AssistantMessage(
                role="assistant",
                content=content_blocks,
                timestamp=time.time(),
                usage=usage,
            )
            await emit(MessageStart(assistant_msg))
            current_messages.append(assistant_msg)
            await emit(MessageEnd(assistant_msg))

            # 获取工具调用
            tool_calls = [b for b in content_blocks if isinstance(b, ToolCallContent)]
            tool_results: list[ToolResultMessage] = []

            for tc in tool_calls:
                tool_fn = _find_tool(tools, tc.name)
                await emit(ToolExecutionStart(tc.id, tc.name, tc.arguments))

                # 执行前审批 hook
                approved = True
                denial_message = "Tool execution denied"
                if before_tool_call is not None:
                    decision = await before_tool_call(tc.name, tc.arguments)
                    approved = decision.get("approved", True)
                    denial_message = decision.get("message", denial_message)

                if approved:
                    if tool_fn is not None:
                        output = tool_fn.run(tc.arguments)
                        output_text = (
                            str(output) if not isinstance(output, str) else output
                        )
                    else:
                        output_text = f"error: unknown tool: {tc.name}"
                else:
                    output_text = denial_message

                tool_result = ToolResultMessage(
                    content=[TextContent(text=output_text)],
                    timestamp=time.time(),
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                )
                tool_results.append(tool_result)
                await emit(ToolExecutionEnd(tc.id, tc.name, tool_result))
                await emit(MessageStart(tool_result))
                current_messages.append(tool_result)
                await emit(MessageEnd(tool_result))

            await emit(TurnEnd(assistant_msg, tool_results))

            if not tool_calls:
                break

        await emit(AgentEnd(current_messages))
