from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Literal, Union
from datetime import datetime


# 消息基类：存放所有消息公共字段，不限制 role
class Message(BaseModel):
    role: str
    content: Union[str, List[Union["TextContent", "ThinkingContent", "ToolCall"]]]
    time: datetime


class UserMessage(Message):
    # 强约束：role 只能是 user
    role: Literal["user"]
    # 用户消息 content 固定为纯文本
    content: str


class TextContent(BaseModel):
    type: Literal["text"]
    text: str


class ThinkingContent(BaseModel):
    type: Literal["Thinking"]
    thinking: str


class ToolCall(BaseModel):
    type: Literal["toolCall"]
    id: str
    name: str
    arguments: Dict[str, Any]
    # 可选字段，对应之前的 thoughtSignature
    thought_signature: Optional[str] = None


class AssistantMessage(Message):
    role: Literal["assistant"]
    content: List[Union[TextContent, ThinkingContent, ToolCall]]
    # 结束原因：枚举约束，必填
    stop_reason: Literal["error", "length", "stop", "toolUse", "aborted"]
    error_message: str


# 工具返回消息，补上 BaseModel 继承
class ToolResultMessage(BaseModel):
    role: Literal["toolResult"]
    tool_call_id: str
    tool_name: str
    content: TextContent
    is_error: bool
