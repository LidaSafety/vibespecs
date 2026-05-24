"""Claude Code PreToolUse hook adapter.

Claude Code exposes a hook system where every tool call is announced to a
subprocess before execution; the subprocess can approve or reject. The hook
payload is JSON on stdin, the response is JSON on stdout, exit code controls
the action. This adapter translates the payload into an `Action` we can
verify.

We handle the documented tool types: Bash, Read, Write, Edit, MultiEdit,
WebFetch, WebSearch. Anything we don't recognize raises
UnsupportedActionError, which the caller should treat as "block until a
human reviews this".

Spec source: Anthropic Claude Code documentation, "Hooks" section, plus
empirical observation of the payload format on production hook invocations.
We are intentionally tolerant about unknown extra fields (the field set evolves)
but strict about the fields we DO read.

References:
    https://docs.claude.com/en/docs/claude-code/hooks
"""

from __future__ import annotations

import shlex
from typing import Any
from urllib.parse import urlparse

from safe_scaffold.world import (
    Action,
    FileRead,
    FileWrite,
    NetworkRequest,
    ShellExec,
)


class ClaudeCodeHookError(Exception):
    """Raised when the hook payload itself is malformed."""


class UnsupportedActionError(Exception):
    """Raised when the agent proposes a tool we don't model.

    Callers should treat this as a default-deny: anything we don't know how to
    reason about, we don't let through automatically.
    """


def parse_claude_code_hook_payload(payload: dict[str, Any]) -> Action:
    """Translate a Claude Code PreToolUse hook payload into an Action.

    The payload roughly looks like:

        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": { ... tool-specific args ... },
            "session_id": "...",
            ...
        }

    The shape of `tool_input` varies per `tool_name`.
    """
    if not isinstance(payload, dict):
        raise ClaudeCodeHookError(
            f"hook payload must be a dict, got {type(payload).__name__}"
        )
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str):
        raise ClaudeCodeHookError("hook payload missing string `tool_name`")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise ClaudeCodeHookError("hook payload missing dict `tool_input`")

    if tool_name == "Bash":
        return _parse_bash(tool_input)
    if tool_name == "Read":
        return _parse_read(tool_input)
    if tool_name in {"Write", "Edit", "MultiEdit"}:
        return _parse_write(tool_input)
    if tool_name == "WebFetch":
        return _parse_web_fetch(tool_input)
    raise UnsupportedActionError(
        f"Claude Code tool {tool_name!r} is not modeled. "
        f"Default policy is to refuse — extend the adapter to handle it."
    )


def _parse_bash(tool_input: dict[str, Any]) -> ShellExec:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ClaudeCodeHookError("Bash tool_input.command must be a non-empty string")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        # Unparseable shell: refuse rather than guess.
        raise ClaudeCodeHookError(
            f"could not shell-parse Bash command {command!r}: {exc}"
        ) from exc
    if not argv:
        raise ClaudeCodeHookError(f"Bash command parses to empty argv: {command!r}")
    cwd = tool_input.get("cwd", "") or ""
    timeout = tool_input.get("timeout")
    timeout_seconds: float | None = None
    if timeout is not None:
        try:
            timeout_seconds = float(timeout) / 1000.0  # Claude Code uses ms
        except (TypeError, ValueError):
            timeout_seconds = None
    return ShellExec(
        argv=tuple(argv),
        cwd=str(cwd),
        timeout_seconds=timeout_seconds,
        agent="claude-code",
    )


def _parse_read(tool_input: dict[str, Any]) -> FileRead:
    path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path, str) or not path:
        raise ClaudeCodeHookError(
            "Read tool_input missing string `file_path` or `path`"
        )
    return FileRead(path=path, agent="claude-code")


def _parse_write(tool_input: dict[str, Any]) -> FileWrite:
    path = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path, str) or not path:
        raise ClaudeCodeHookError(
            "Write tool_input missing string `file_path` or `path`"
        )
    # Multiple fields might carry the content: `content` (Write), `new_string`
    # (Edit), or a list of edits (MultiEdit). We estimate the size; rules
    # generally care about path more than exact byte count.
    content_size = 0
    content = tool_input.get("content")
    if isinstance(content, str):
        content_size = len(content.encode("utf-8"))
    new_string = tool_input.get("new_string")
    if isinstance(new_string, str):
        content_size = max(content_size, len(new_string.encode("utf-8")))
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        total = 0
        for e in edits:
            if isinstance(e, dict):
                ns = e.get("new_string")
                if isinstance(ns, str):
                    total += len(ns.encode("utf-8"))
        content_size = max(content_size, total)
    return FileWrite(
        path=path,
        content_size=content_size,
        create_new=bool(tool_input.get("create_new", False)),
        agent="claude-code",
    )


def _parse_web_fetch(tool_input: dict[str, Any]) -> NetworkRequest:
    url = tool_input.get("url")
    if not isinstance(url, str) or not url:
        raise ClaudeCodeHookError("WebFetch tool_input missing string `url`")
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        raise ClaudeCodeHookError(f"WebFetch.url could not be parsed: {url!r}")
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return NetworkRequest(
        method=str(tool_input.get("method", "GET")).upper(),
        url=url,
        host=parsed.hostname,
        port=int(port),
        agent="claude-code",
    )
