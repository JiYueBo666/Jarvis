from dataclasses import dataclass

SANDBOX_MODES = {"off", "best_effort", "required"}
SANDBOX_BACKENDS = {"auto", "bubblewrap", "none"}


@dataclass
class SandboxConfig:
    mode: str = "off"
    backend: str = "auto"
    workspace_write: bool = True
    excluded_commands: tuple[str, ...] = ()
    extra_readonly_paths: tuple[str, ...] = ()
    deny_read: tuple[str, ...] = ()
    deny_write: tuple[str, ...] = ()

    @property
    def enabled(self):
        return self.mode != "off"
