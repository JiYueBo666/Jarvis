"""Command pattern matching for sandbox exclusions."""

from fnmatch import fnmatch


def command_is_excluded(command: str, patterns: list | tuple):
    command = str(command or "").strip()
    return any(fnmatch(command, str(pattern)) for pattern in patterns or ())
