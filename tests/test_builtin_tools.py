import pytest
from src.Tools.builtin import ReadFileTool, WriteFileTool, RunShellTool, SearchCodeTool


class TestReadFileTool:
    tool = ReadFileTool()

    def test_readonly(self):
        assert self.tool.readonly is True
        assert self.tool.dangerous is False

    @pytest.mark.asyncio
    async def test_read_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("print('hello')")
        result = await self.tool.execute({"file_path": str(f)})
        assert "print('hello')" in result

    @pytest.mark.asyncio
    async def test_read_file_not_found(self):
        result = await self.tool.execute({"file_path": "/nonexistent/path"})
        assert "not found" in result.lower()


class TestWriteFileTool:
    tool = WriteFileTool()

    def test_readonly(self):
        assert self.tool.readonly is False
        assert self.tool.dangerous is False

    @pytest.mark.asyncio
    async def test_write_file(self, tmp_path):
        p = tmp_path / "sub" / "test.txt"
        result = await self.tool.execute({"file_path": str(p), "content": "hello"})
        assert "written" in result.lower()
        assert p.read_text() == "hello"


class TestRunShellTool:
    tool = RunShellTool()

    def test_readonly(self):
        assert self.tool.readonly is False
        assert self.tool.dangerous is True

    @pytest.mark.asyncio
    async def test_run_shell(self):
        result = await self.tool.execute({"command": "echo hello"})
        assert "hello" in result


class TestSearchCodeTool:
    tool = SearchCodeTool()

    def test_readonly(self):
        assert self.tool.readonly is True
        assert self.tool.dangerous is False

    @pytest.mark.asyncio
    async def test_search_code(self):
        result = await self.tool.execute({"pattern": "class Tool", "path": "src/Tools"})
        assert "class Tool" in result
