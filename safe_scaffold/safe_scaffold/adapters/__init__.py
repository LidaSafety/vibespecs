"""Adapters: translate agent-native tool calls into safe_scaffold Action objects.

Each adapter exposes a `parse(...)` function that takes the agent's native
payload and returns an Action. If the payload describes an action we don't
model, the adapter raises an UnsupportedActionError rather than silently
falling through — fail closed.
"""

from safe_scaffold.adapters.claude_code import (
    ClaudeCodeHookError,
    UnsupportedActionError,
    parse_claude_code_hook_payload,
)
from safe_scaffold.adapters.shell import parse_shell_command

__all__ = [
    "parse_claude_code_hook_payload",
    "parse_shell_command",
    "ClaudeCodeHookError",
    "UnsupportedActionError",
]
