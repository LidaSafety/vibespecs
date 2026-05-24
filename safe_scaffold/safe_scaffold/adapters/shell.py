"""Generic shell-command adapter.

When wrapping an arbitrary shell-using agent (custom CLI tool, training-loop
harness), it's convenient to feed a raw command line and get back a ShellExec.
This module provides the helper, with the same shlex-based parsing as the
Claude Code adapter so behavior is consistent.
"""

from __future__ import annotations

import shlex

from safe_scaffold.world import ShellExec


def parse_shell_command(
    command: str, *, cwd: str = "", agent: str = "shell"
) -> ShellExec:
    """Parse a shell command string into a ShellExec.

    Raises ValueError if the command is unparseable or empty.
    """
    if not command or not command.strip():
        raise ValueError("command must be non-empty")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"could not parse shell command {command!r}: {exc}") from exc
    if not argv:
        raise ValueError(f"command parses to empty argv: {command!r}")
    return ShellExec(argv=tuple(argv), cwd=cwd, agent=agent)
