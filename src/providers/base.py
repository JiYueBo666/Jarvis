from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelResult:
    text: str
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None
    metadata: dict = field(default_factory=dict)


def complete_model(model_client, messages: list, max_new_tokens: int, **kwargs):
    """Unified entry point. Delegates to model_client.complete()."""
    return model_client.complete(messages, max_new_tokens, **kwargs)
