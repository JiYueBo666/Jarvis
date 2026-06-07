from dataclasses import dataclass, field
from typing import Any

from src.engine.tool import Tool


@dataclass
class ExecutionResult:
    success: bool
    output: str
    tool_name: str
    error_code: str = ""
    affected_paths: list[str] = field(default_factory=list)


class ToolExecutor:
    """Holds tool registry, validates params, executes tools.

    Engine only sees this facade — all existence checking, parameter
    validation, and future guards (permissions, repetition, sandbox)
    live here.
    """

    def __init__(self, tools: dict[str, Tool]):
        self._tools = tools

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def is_risky(self, name: str) -> bool:
        """返回该工具是否需要审批。"""
        tool = self._tools.get(name)
        return tool.risky if tool else False

    @property
    def schemas(self) -> list[dict]:
        """OpenAI-compatible tool schemas for the model."""
        return [t.to_openai_schema() for t in self._tools.values()]

    def allowed_tools(self, mode: str) -> set[str]:
        """根据 mode 返回当前可用的工具名集合。

        | mode     | 白名单                                                |
        |----------|-------------------------------------------------------|
        | default  | 全部工具                                              |
        | plan     | read_file + write_file（仅限 .jarvis/plans/* 路径）   |
        """
        if mode == "plan":
            return {"read_file", "write_file"}
        return set(self._tools.keys())

    def execute(self, name: str, args: dict[str, Any], mode: str = "default") -> ExecutionResult:
        # ── Mode 过滤（策略级限制，不走审批流程） ──
        allowed = self.allowed_tools(mode)
        if name not in allowed:
            return ExecutionResult(
                success=False,
                output=f"Error: tool '{name}' is not allowed in {mode} mode",
                tool_name=name,
                error_code="mode_denied",
            )
        # Plan mode 下 write_file 仅允许写入 .jarvis/plans/ 目录
        if mode == "plan" and name == "write_file":
            path = args.get("path", "")
            if not path.startswith(".jarvis/plans/"):
                return ExecutionResult(
                    success=False,
                    output=f"Error: write_file is only allowed for .jarvis/plans/ paths in plan mode",
                    tool_name=name,
                    error_code="mode_denied",
                )

        # 存在性检测.
        tool = self._tools.get(name)
        if not tool:
            return ExecutionResult(
                success=False,
                output=f"Error: unknown tool '{name}'",
                tool_name=name,
                error_code="unknown_tool",
            )
        # 参数校验
        error = self._validate(tool, args)
        if error:
            return ExecutionResult(
                success=False,
                output=error,
                tool_name=name,
                error_code="invalid_params",
            )

        try:
            result = tool.run(args)
            affected_paths = list(result.metadata.get("affected_paths", [])) if result.metadata else []
            return ExecutionResult(
                success=True,
                output=result.output,
                tool_name=name,
                affected_paths=affected_paths,
            )
        except Exception as exc:
            return ExecutionResult(
                success=False,
                output=f"Error executing '{name}': {exc}",
                tool_name=name,
                error_code="execution_error",
            )

    def _validate(self, tool: Tool, args: dict[str, Any]) -> str | None:
        """Returns an error message if validation fails, else None."""
        params = tool.get_parameters()
        valid_names = {p.name for p in params}

        # Required params
        for p in params:
            if p.required and p.name not in args:
                return f"Error: missing required parameter '{p.name}' for tool '{tool.name}'"

        # Unknown params
        for key in args:
            if key not in valid_names:
                return f"Error: unexpected parameter '{key}' for tool '{tool.name}'"

        # Basic type check
        # TODO:改为pydantic校验
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
        }
        for p in params:
            if p.name not in args:
                continue
            value = args[p.name]
            expected = type_map.get(p.type)
            if expected and not isinstance(value, expected):
                return (
                    f"Error: parameter '{p.name}' should be {p.type}, "
                    f"got {type(value).__name__}"
                )

        return None
