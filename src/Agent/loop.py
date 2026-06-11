"""ReAct agent loop."""

from __future__ import annotations

from src.Agent.models import Message
from src.Client.base import LLMClient
from src.Tools.base import ToolExecutor


class AgentLoop:
    """ReAct loop: think → act → observe → repeat until final answer."""

    def __init__(
        self,
        client: LLMClient,
        executor: ToolExecutor,
        max_steps: int = 20,
    ):
        self._client = client
        self._executor = executor
        self._max_steps = max_steps

    async def run(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
    ) -> str:
        history = list(messages)

        for step in range(self._max_steps):
            response = await self._client.chat(history, tools=tools)
            history.append(response)

            # No tool calls = final answer
            if not response.tool_calls:
                return response.content or "(empty response)"

            # Execute each tool call and append results
            for tc in response.tool_calls:
                result = await self._executor.execute(tc.name, tc.arguments)
                history.append(
                    Message(role="tool", content=result, tool_call_id=tc.id)
                )

        return f"Reached max steps ({self._max_steps}) without final answer"
