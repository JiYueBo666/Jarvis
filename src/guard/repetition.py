"""Tool call repetition detection.

Rules:
- Read operations (read_file, run_shell):
  Same tool + same args as the immediate previous call → repeat.
- Write operations (write_file, patch_file):
  Last call was same tool + same args:
    - If that call succeeded → repeat (wrote same thing already).
    - If that call failed → repeat unless a read of the same file happened after it.
"""

from typing import Any

READ_TOOLS = {"read_file", "run_shell"}
WRITE_TOOLS = {"write_file", "patch_file"}


class RepetitionDetector:
    """Tracks tool call history and detects repetitive patterns."""

    def __init__(self):
        self._history: list[dict[str, Any]] = []

    def record_call(self, name: str, args: dict, success: bool):
        """Call after each tool execution."""
        self._history.append({
            "name": name,
            "args": dict(args),
            "success": success,
        })

    def check(self, name: str, args: dict) -> str | None:
        """Return a warning string if this call looks like a repeat, else None."""
        if not self._history:
            return None

        if name in READ_TOOLS:
            return self._check_read(name, args)
        if name in WRITE_TOOLS:
            return self._check_write(name, args)
        return None

    # ── internal ─────────────────────────────────────────────

    def _check_read(self, name: str, args: dict) -> str | None:
        last = self._history[-1]
        if last["name"] == name and last["args"] == args:
            return (
                f"[System] You just called {name} with identical arguments. "
                "Try a different approach."
            )
        return None

    def _check_write(self, name: str, args: dict) -> str | None:
        last = self._history[-1]
        if not (last["name"] == name and last["args"] == args):
            return None

        path = args.get("path", "")
        if last["success"]:
            return (
                f"[System] You already successfully wrote to {path}. "
                "No need to repeat the same write."
            )

        # Previous write failed — check if a read of that file followed
        if not self._has_read_after(path, last):
            return (
                f"[System] Previous write to {path} failed and you haven't "
                "verified the file since. Read it first before retrying."
            )
        return None

    def _has_read_after(self, path: str, after_call: dict) -> bool:
        started = False
        for call in self._history:
            if call is after_call:
                started = True
                continue
            if started and call["name"] == "read_file" and call["args"].get("path") == path:
                return True
        return False
