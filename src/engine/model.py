import json

from openai import OpenAI

from src.providers.base import ModelResult
from src.providers.errors import ProviderError


class ModelClient:
    """OpenAI-compatible chat client. Accepts messages + tools, returns text + tool_calls."""

    def __init__(self, model: str, base_url: str, api_key: str):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.last_completion_metadata: dict = {}

    def complete(
        self,
        messages: list,
        max_new_tokens: int,
        tools: list | None = None,
        **kwargs,
    ) -> ModelResult:
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_new_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            response = self.client.chat.completions.create(**body)
        except Exception as exc:
            raise _to_provider_error(exc, self.model, self.base_url)

        choice = response.choices[0]
        message = choice.message
        text = (message.content or "").strip()
        reasoning_content = getattr(message, "reasoning_content", None) or None

        tool_calls = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "args": args,
                })

        usage = response.usage
        cached_tokens = 0
        if usage:
            details = getattr(usage, "prompt_tokens_details", None)
            if details:
                cached_tokens = getattr(details, "cached_tokens", 0) or 0

        self.last_completion_metadata = {
            "model": self.model,
            "usage": dict(usage or {}),
            "finish_reason": choice.finish_reason or "",
            "tool_calls_count": len(tool_calls) if tool_calls else 0,
            "cached_tokens": cached_tokens,
        }
        return ModelResult(
            text=text,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            metadata=dict(self.last_completion_metadata),
        )

    def stream_complete(
        self,
        messages: list,
        max_new_tokens: int,
        tools: list | None = None,
        **kwargs,
    ):
        """Stream the model completion, yielding reasoning chunks in real-time.

        Yields:
            {"type": "reasoning", "content": str}  — a chunk of reasoning text
            {"type": "result",   "result": ModelResult}  — final result (last event)
        """
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
            response = self.client.chat.completions.create(**body)
        except Exception as exc:
            raise _to_provider_error(exc, self.model, self.base_url)

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_map: dict[int, dict] = {}
        finish_reason = ""
        usage = None

        for chunk in response:
            if not chunk.choices:
                # May contain usage info in the final chunk
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = chunk.usage
                continue

            delta = chunk.choices[0].delta
            if not delta:
                continue

            # ── Stream reasoning content ──
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
                yield {"type": "reasoning", "content": reasoning}

            # ── Accumulate content ──
            if delta.content:
                text_parts.append(delta.content)

            # ── Accumulate tool calls ──
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {"id": "", "function": {"name": "", "arguments": ""}}
                    if tc.id:
                        tool_calls_map[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_map[idx]["function"]["name"] += tc.function.name
                        if tc.function.arguments:
                            tool_calls_map[idx]["function"]["arguments"] += tc.function.arguments

            # Track finish_reason from the last chunk
            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

        # ── Build final result ──
        text = "".join(text_parts).strip()
        reasoning_content = "".join(reasoning_parts) if reasoning_parts else None

        tool_calls = None
        if tool_calls_map:
            tool_calls = []
            for idx in sorted(tool_calls_map.keys()):
                tc = tool_calls_map[idx]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "args": args,
                })

        # ── 用量数据：流式可能不返回 → 回退非流式探针 ──
        if not usage or not getattr(usage, "prompt_tokens", None):
            try:
                probe = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=1,
                    tools=tools,
                    stream=False,
                )
                usage = probe.usage
            except Exception:
                usage = None

        cached_tokens = 0
        if usage:
            details = getattr(usage, "prompt_tokens_details", None)
            if details:
                cached_tokens = getattr(details, "cached_tokens", 0) or 0

        metadata = {
            "model": self.model,
            "usage": dict(usage or {}),
            "finish_reason": finish_reason,
            "tool_calls_count": len(tool_calls) if tool_calls else 0,
            "cached_tokens": cached_tokens,
        }
        self.last_completion_metadata = metadata

        yield {
            "type": "result",
            "result": ModelResult(
                text=text,
                tool_calls=tool_calls,
                reasoning_content=reasoning_content,
                metadata=dict(metadata),
            ),
        }


def _to_provider_error(exc: Exception, model: str, base_url: str) -> ProviderError:
    """Map OpenAI SDK exceptions to ProviderError."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text[:500] if exc.response.text else ""
        code_map = {
            400: "prompt_too_long" if "maximum context length" in body else "bad_request",
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
