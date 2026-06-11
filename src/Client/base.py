"""Abstract LLM client interface."""

from abc import ABC, abstractmethod
from src.Agent.models import Message


class LLMClient(ABC):
    @abstractmethod
    async def chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> Message:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...
