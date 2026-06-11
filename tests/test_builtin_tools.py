import pytest
from pathlib import Path
from src.Tools.builtin import ReadFileTool, WriteFileTool, RunShellTool, SearchCodeTool


@pytest.mark.asyncio
async def test_read_file(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("print('hello')")
    tool = ReadFileTool()
    result = await tool.execute({"file_path": str(f)})
    assert "print('hello')" in result


@pytest.mark.asyncio
async def test_read_file_not_found():
    tool = ReadFileTool()
    result = await tool.execute({"file_path": "/nonexistent/path"})
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_write_file(tmp_path):
    tool = WriteFileTool()
    p = tmp_path / "sub" / "test.txt"
    result = await tool.execute({"file_path": str(p), "content": "hello"})
    assert "written" in result.lower()
    assert p.read_text() == "hello"


@pytest.mark.asyncio
async def test_run_shell():
    tool = RunShellTool()
    result = await tool.execute({"command": "echo hello"})
    assert "hello" in result


@pytest.mark.asyncio
async def test_search_code():
    tool = SearchCodeTool()
    result = await tool.execute({"pattern": "class Tool", "path": "src/Tools"})
    assert "class Tool" in result
