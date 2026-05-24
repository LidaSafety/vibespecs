"""Tests for the agent adapters."""

from __future__ import annotations

import unittest

from safe_scaffold.adapters import (
    ClaudeCodeHookError,
    UnsupportedActionError,
    parse_claude_code_hook_payload,
    parse_shell_command,
)
from safe_scaffold.world import (
    FileRead,
    FileWrite,
    NetworkRequest,
    ShellExec,
)


class TestClaudeCodeBash(unittest.TestCase):
    def test_simple_command(self) -> None:
        a = parse_claude_code_hook_payload({
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
        })
        self.assertIsInstance(a, ShellExec)
        assert isinstance(a, ShellExec)
        self.assertEqual(a.argv, ("git", "status"))
        self.assertEqual(a.agent, "claude-code")

    def test_quoted_args(self) -> None:
        a = parse_claude_code_hook_payload({
            "tool_name": "Bash",
            "tool_input": {"command": "echo 'hello world'"},
        })
        assert isinstance(a, ShellExec)
        self.assertEqual(a.argv, ("echo", "hello world"))

    def test_unparseable_command_raises(self) -> None:
        with self.assertRaises(ClaudeCodeHookError):
            parse_claude_code_hook_payload({
                "tool_name": "Bash",
                "tool_input": {"command": "echo 'unclosed"},
            })

    def test_empty_command_raises(self) -> None:
        with self.assertRaises(ClaudeCodeHookError):
            parse_claude_code_hook_payload({
                "tool_name": "Bash",
                "tool_input": {"command": ""},
            })


class TestClaudeCodeRead(unittest.TestCase):
    def test_file_path_field(self) -> None:
        a = parse_claude_code_hook_payload({
            "tool_name": "Read",
            "tool_input": {"file_path": "/repo/x.py"},
        })
        self.assertIsInstance(a, FileRead)
        assert isinstance(a, FileRead)
        self.assertEqual(a.path, "/repo/x.py")

    def test_alternate_path_field(self) -> None:
        a = parse_claude_code_hook_payload({
            "tool_name": "Read",
            "tool_input": {"path": "/repo/x.py"},
        })
        assert isinstance(a, FileRead)
        self.assertEqual(a.path, "/repo/x.py")


class TestClaudeCodeWrite(unittest.TestCase):
    def test_write_with_content(self) -> None:
        a = parse_claude_code_hook_payload({
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/x", "content": "hello"},
        })
        assert isinstance(a, FileWrite)
        self.assertEqual(a.path, "/tmp/x")
        self.assertEqual(a.content_size, 5)

    def test_edit_with_new_string(self) -> None:
        a = parse_claude_code_hook_payload({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/x", "new_string": "replacement"},
        })
        assert isinstance(a, FileWrite)
        self.assertEqual(a.content_size, 11)

    def test_multiedit_sums_edits(self) -> None:
        a = parse_claude_code_hook_payload({
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": "/tmp/x",
                "edits": [
                    {"new_string": "aaaa"},  # 4 bytes
                    {"new_string": "bbb"},   # 3 bytes
                ],
            },
        })
        assert isinstance(a, FileWrite)
        self.assertEqual(a.content_size, 7)


class TestClaudeCodeWebFetch(unittest.TestCase):
    def test_https(self) -> None:
        a = parse_claude_code_hook_payload({
            "tool_name": "WebFetch",
            "tool_input": {"url": "https://api.github.com/repos/x/y"},
        })
        self.assertIsInstance(a, NetworkRequest)
        assert isinstance(a, NetworkRequest)
        self.assertEqual(a.host, "api.github.com")
        self.assertEqual(a.port, 443)

    def test_http_default_port(self) -> None:
        a = parse_claude_code_hook_payload({
            "tool_name": "WebFetch",
            "tool_input": {"url": "http://example.com/x"},
        })
        assert isinstance(a, NetworkRequest)
        self.assertEqual(a.port, 80)

    def test_unparseable_url_raises(self) -> None:
        with self.assertRaises(ClaudeCodeHookError):
            parse_claude_code_hook_payload({
                "tool_name": "WebFetch",
                "tool_input": {"url": "not-a-url"},
            })


class TestUnsupportedTool(unittest.TestCase):
    def test_unknown_tool_raises(self) -> None:
        with self.assertRaises(UnsupportedActionError):
            parse_claude_code_hook_payload({
                "tool_name": "WizardTool",
                "tool_input": {},
            })


class TestShellAdapter(unittest.TestCase):
    def test_basic(self) -> None:
        a = parse_shell_command("pytest -x tests/")
        self.assertEqual(a.argv, ("pytest", "-x", "tests/"))

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_shell_command("")

    def test_unparseable_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_shell_command("echo 'unclosed")


if __name__ == "__main__":
    unittest.main()
