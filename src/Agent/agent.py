from typing import Any, List
from datetime import datetime
from Messages.messages import TextContent, UserMessage


class JarvisAgent:
    def __init__(self):

        pass

    def prompt(self, messages: Any):

        normalizedPromptInput()
        runPromptMessage(messages)

    def normalizedPromptInput(self, messages: List[TextContent]):
        normalized_messages = []
        for msg in messages:
            text = msg.text
            message = UserMessage(content=text, time=datetime.now())
