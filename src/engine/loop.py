import asyncio
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


_TOOL_TIMEOUT = 120


class Engine:
    """编排循环。接收依赖后通过 run_stream 驱动 LLM + 工具调用。"""

    def __init__(self, model_client: ModelClient, convert_to_llm: Callable):
        self._model_client = model_client
        self._convert_to_llm = convert_to_llm

    async def run_stream(
        self,
        tools: list[Tool],
        system_prompt: str,
        messages: list,
        emit: Callable,
        before_tool_call: Callable | None = None,
        after_tool_call: Callable | None = None,
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

            llm_messages = self._convert_to_llm(current_messages)
            llm_messages.insert(0, {"role": "system", "content": system_prompt})

            assistant_msg = AssistantMessage(
                role="assistant", content=[], timestamp=time.time()
            )
            await emit(MessageStart(assistant_msg))

            async for block in self._model_client.stream_complete(
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

            assistant_msg.content = content_blocks
            assistant_msg.usage = usage
            assistant_msg.timestamp = time.time()
            current_messages.append(assistant_msg)
            await emit(MessageEnd(assistant_msg))

            # 工具调用
            tool_calls = [b for b in content_blocks if isinstance(b, ToolCallContent)]
            tool_results: list[ToolResultMessage] = []

            for tc in tool_calls:
                tool_fn = _find_tool(tools, tc.name)
                await emit(ToolExecutionStart(tc.id, tc.name, tc.arguments))

                approved = True
                denial_message = "Tool execution denied"
                if before_tool_call is not None:
                    decision = await before_tool_call(tc.name, tc.arguments)
                    approved = decision.get("approved", True)
                    denial_message = decision.get("message", denial_message)

                if approved:
                    if tool_fn is not None:
                        output = await asyncio.wait_for(
                            asyncio.get_event_loop().run_in_executor(
                                None, tool_fn.run, tc.arguments
                            ),
                            timeout=_TOOL_TIMEOUT,
                        )
                        output_text = (
                            str(output) if not isinstance(output, str) else output
                        )
                        if after_tool_call:
                            after_tool_call(tc.name, tc.arguments, output)
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
