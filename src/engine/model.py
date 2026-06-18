import json

from openai import AsyncOpenAI

from src.data.messages import TextContent, ThinkingContent, ToolCallContent, Usage
from src.providers.errors import ProviderError


class ModelClient:
    """OpenAI-compatible chat client. Accepts messages + tools, returns text + tool_calls."""

    def __init__(self, model: str, base_url: str, api_key: str):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def stream_complete(
        self,
        messages: list,
        max_new_tokens: int = 8192,
        tools: list | None = None,
        **kwargs,
    ):
        """纯流式调用，直接 yield OpenAI SDK 原始 chunk，"""
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_new_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            response = await self.async_client.chat.completions.create(**body)
        except Exception as exc:
            raise _to_provider_error(exc, self.model, self.base_url)

        # 工具调用表
        tool_calls_map: dict[int, dict] = {}
        usage = None

        async for chunk in response:
            if not chunk.choices:
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = chunk.usage
                continue
            delta = chunk.choices[0].delta
            if not delta:
                continue
            # 思考
            if getattr(delta, "reasoning_content", None):
                yield ThinkingContent(thinking=delta.reasoning_content)
            if delta.content:
                yield TextContent(text=delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc.id:
                        tool_calls_map[idx]["id"] = tc.id
                    if tc.function.name:
                        tool_calls_map[idx]["name"] += tc.function.name
                    if tc.function.arguments:
                        tool_calls_map[idx]["arguments"] += tc.function.arguments

        for idx in sorted(tool_calls_map):
            tc = tool_calls_map[idx]
            try:
                args = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                args = {}
            yield ToolCallContent(id=tc["id"], name=tc["name"], arguments=args)

        if usage:
            yield Usage(
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                cache_read_tokens=getattr(
                    getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0
                )
                or 0,
                cache_write_tokens=0,
            )


def _to_provider_error(exc: Exception, model: str, base_url: str) -> ProviderError:
    """Map OpenAI SDK exceptions to ProviderError."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text[:500] if exc.response.text else ""
        code_map = {
            400: (
                "prompt_too_long" if "maximum context length" in body else "bad_request"
            ),
            401: "auth_error",
            403: "auth_error",
            429: "rate_limited",
            500: "provider_error",
            502: "provider_error",
            503: "provider_error",
        }
        code = code_map.get(status, f"http_{status}")
        retryable = status in (429, 500, 502, 503)
        return ProviderError(
            message=str(exc),
            provider="openai",
            model=model,
            base_url=base_url,
            code=code,
            http_status=status,
            retryable=retryable,
            body_excerpt=body,
            cause_type=type(exc).__name__,
        )
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(
            message=str(exc),
            provider="openai",
            model=model,
            base_url=base_url,
            code="timeout",
            retryable=True,
            cause_type=type(exc).__name__,
        )
    return ProviderError(
        message=str(exc),
        provider="openai",
        model=model,
        base_url=base_url,
        code="model_client_error",
        retryable=False,
        cause_type=type(exc).__name__,
        body_excerpt=str(exc)[:500],
    )


# ── Context Window 注册表 ──────────────────────────────────

_CONTEXT_WINDOWS = {
    "deepseek-chat": 65536,
    "deepseek-reasoner": 131072,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
}
_DEFAULT_CONTEXT_WINDOW = 128000


def get_context_window(model: str) -> int:
    return _CONTEXT_WINDOWS.get(model, _DEFAULT_CONTEXT_WINDOW)
