from src.tools.read_file import ReadFile
from src.tools.run_shell import RunShell
from src.tools.write_file import WriteFile
from src.tools.patch_file import PatchFile


def build_registry(workspace_root: str | None = None) -> dict:
    tools = [
        ReadFile(workspace_root=workspace_root),
        WriteFile(workspace_root=workspace_root),
        PatchFile(workspace_root=workspace_root),
        RunShell(workspace_root=workspace_root),
    ]
    return {t.name: t for t in tools}
