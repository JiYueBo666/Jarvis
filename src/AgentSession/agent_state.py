from dataclasses import dataclass
from typing import List

from Messages.messages import TextContent


@dataclass
class AgentState:
    systemPrompt: str
    messages: List[TextContent]
    isStreaming: bool = True
    streamingMessage: TextContent  # 正在生成的消息
