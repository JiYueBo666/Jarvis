"""Built-in tools for the coding agent."""

from pathlib import Path
import subprocess

from src.Tools.base import Tool, ToolParameter


class ReadFileTool(Tool):
    name = "read_file"
    readonly = True
    dangerous = False
    description = "Read the contents of a file"
    parameters = [
        ToolParameter(name="file_path", type="string", description="Path to the file to read"),
    ]

    async def execute(self, args: dict) -> str:
        path = Path(args["file_path"])
        if not path.exists():
            return f"File not found: {path}"
        return path.read_text()


class WriteFileTool(Tool):
    name = "write_file"
    readonly = False
    dangerous = False
    description = "Write content to a file (creates or overwrites)"
    parameters = [
        ToolParameter(name="file_path", type="string", description="File path to write to"),
        ToolParameter(name="content", type="string", description="Content to write"),
    ]

    async def execute(self, args: dict) -> str:
        path = Path(args["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"])
        return f"Written {len(args['content'])} bytes to {path}"


class RunShellTool(Tool):
    name = "run_shell"
    readonly = False
    dangerous = True
    description = "Run a shell command and return its output"
    parameters = [
        ToolParameter(name="command", type="string", description="Shell command to execute"),
    ]

    async def execute(self, args: dict) -> str:
        result = subprocess.run(
            args["command"],
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout or ""
        if result.stderr:
            output += "\nSTDERR:\n" + result.stderr
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output or "(no output)"


class SearchCodeTool(Tool):
    name = "search_code"
    readonly = True
    dangerous = False
    description = "Search for a pattern in code files using grep"
    parameters = [
        ToolParameter(name="pattern", type="string", description="Regex pattern to search"),
        ToolParameter(name="path", type="string", description="Directory path to search", required=False),
    ]

    async def execute(self, args: dict) -> str:
        search_path = args.get("path", ".")
        result = subprocess.run(
            ["grep", "-rn", args["pattern"], search_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode not in (0, 1):
            return f"Search failed: {result.stderr}"
        return result.stdout or "No matches found"
