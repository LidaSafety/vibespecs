"""
Tests for the Claude Code → Action adapter.

These insulate the rest of the system from changes in Claude Code's
hook payload shape. If Anthropic updates the schema, ONLY this file
and interceptor.parse_claude_code_hook_payload should need touching.
"""
from __future__ import annotations

import pytest

from safe_scaffold import (
    FileRead, FileWrite, NetworkRequest, ShellExec,
    parse_claude_code_hook_payload,
)
from safe_scaffold.interceptor import HookParseError


def test_bash_payload():
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git status --porcelain",
                       "description": "check repo state"},
        "session_id": "s1",
    }
    a = parse_claude_code_hook_payload(payload, cwd="/work")
    assert isinstance(a, ShellExec)
    assert a.command == "git"
    assert a.argv == ["status", "--porcelain"]
    assert a.cwd == "/work"
    assert a.raw == "git status --porcelain"
    assert a.rationale == "check repo state"


def test_bash_with_quoting():
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "echo 'hello world'"},
    }
    a = parse_claude_code_hook_payload(payload, cwd="/")
    assert isinstance(a, ShellExec)
    assert a.argv == ["hello world"]


def test_read_resolves_relative_path():
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": "src/main.py"},
    }
    a = parse_claude_code_hook_payload(payload, cwd="/work/proj")
    assert isinstance(a, FileRead)
    assert a.path == "/work/proj/src/main.py"


def test_write_payload():
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "/abs/file.txt", "content": "abc"},
    }
    a = parse_claude_code_hook_payload(payload, cwd="/")
    assert isinstance(a, FileWrite)
    assert a.path == "/abs/file.txt"
    assert a.size_bytes == 3


def test_edit_payload():
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "/x", "new_string": "xyz"},
    }
    a = parse_claude_code_hook_payload(payload, cwd="/")
    assert isinstance(a, FileWrite)
    assert a.size_bytes == 3


def test_webfetch_payload():
    payload = {
        "tool_name": "WebFetch",
        "tool_input": {"url": "https://example.com:8080/x"},
    }
    a = parse_claude_code_hook_payload(payload, cwd="/")
    assert isinstance(a, NetworkRequest)
    assert a.host == "example.com"
    assert a.port == 8080


def test_unknown_tool_raises():
    payload = {"tool_name": "Telepathy", "tool_input": {}}
    with pytest.raises(HookParseError):
        parse_claude_code_hook_payload(payload, cwd="/")
