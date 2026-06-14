"""
核心全局调度器
"""

from datetime import datetime

from Agent.agent import JarvisAgent
from Sessions.session_manager import SessionManager
from src.Messages.messages import TextContent

from pydantic import BaseModel


class Option(BaseModel):
    cwd: str = ""


class AgentSession:

    def __init__(self, session_manager: SessionManager, agent: JarvisAgent):
        self.session_manager = session_manager
        self.agent = agent

    def prompt(self, userInput: str):
        # 1.检查是否斜杠命令
        # 2.展开skill，prompt等
        # 3.验证模型和API key
        # 4.检查是否需要压缩

        # 5.构建消息  仅添加本轮消息。
        messages = []
        userContent: TextContent = TextContent(type="text", text=userInput)
        messages.append(userContent)

        # 6. 调用agent
        _runAgentPrompt(messages)

    def _runAgentPrompt(self, messages: list[TextContent]):
        try:
            pass
        except Exception as e:
            pass
        finally:
            pass
