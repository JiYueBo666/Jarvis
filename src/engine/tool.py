from pydantic import BaseModel
from abc import ABC
from typing import Dict, Any, List


class ToolParameter(BaseModel):
    """工具参数定义"""

    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None


class Tool(ABC):
    def __init__(
        self, name: str, description: str, is_readonly: bool = False, risky: bool = True
    ):
        self.name = name
        self.description = description
        self.is_readonly = is_readonly
        self.risky = False if self.is_readonly else risky  # 只读工具安全，其他的看情况

    @classmethod
    def run(self, parameters: Dict[str, Any]):
        pass

    def to_openai_schema(self) -> dict:
        parameters = self.get_parameters()
        properties = {}
        required = []
        for p in parameters:
            properties[p.name] = {
                "type": p.type,
                "description": p.description,
            }
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    @classmethod
    def get_parameters(self) -> List[ToolParameter]:
        """获取工具参数定义"""
        pass
