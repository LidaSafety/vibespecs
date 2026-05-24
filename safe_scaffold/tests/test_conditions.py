"""Tests for the conditions DSL."""

from __future__ import annotations

import unittest

from safe_scaffold.conditions import (
    And,
    KindIs,
    Or,
    Reference,
    ValidationError,
    parse_condition,
)
from safe_scaffold.world import (
    EnvRead,
    FileRead,
    FileWrite,
    NetworkRequest,
    ShellExec,
)


class TestReference(unittest.TestCase):
    def test_known_field(self) -> None:
        r = Reference("path")
        self.assertEqual(r.field, "path")

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Reference("evil_field")


class TestParseCondition(unittest.TestCase):
    def test_true_false(self) -> None:
        self.assertTrue(parse_condition({"type": "true"}).evaluate(ShellExec(argv=("x",))))
        self.assertFalse(parse_condition({"type": "false"}).evaluate(ShellExec(argv=("x",))))

    def test_bool_shorthand(self) -> None:
        self.assertTrue(parse_condition(True).evaluate(ShellExec(argv=("x",))))
        self.assertFalse(parse_condition(False).evaluate(ShellExec(argv=("x",))))

    def test_kind_is(self) -> None:
        c = parse_condition({"type": "kind_is", "kind": "shell_exec"})
        self.assertTrue(c.evaluate(ShellExec(argv=("x",))))
        self.assertFalse(c.evaluate(FileRead(path="/x")))

    def test_and_or_not(self) -> None:
        c = parse_condition({
            "type": "and",
            "of": [
                {"type": "kind_is", "kind": "shell_exec"},
                {"type": "not", "of": {"type": "eq", "ref": "program", "value": "rm"}},
            ],
        })
        self.assertTrue(c.evaluate(ShellExec(argv=("ls",))))
        self.assertFalse(c.evaluate(ShellExec(argv=("rm", "-rf"))))
        self.assertFalse(c.evaluate(FileRead(path="/x")))

    def test_path_under(self) -> None:
        c = parse_condition({"type": "path_under", "ref": "path", "parent": "/etc"})
        self.assertTrue(c.evaluate(FileWrite(path="/etc/passwd", content_size=1)))
        self.assertFalse(c.evaluate(FileWrite(path="/tmp/x", content_size=1)))

    def test_path_under_rejects_relative_parent(self) -> None:
        with self.assertRaises(ValidationError):
            parse_condition({"type": "path_under", "ref": "path", "parent": "etc"})

    def test_matches_regex(self) -> None:
        c = parse_condition({
            "type": "matches_regex",
            "ref": "name",
            "pattern": r".*(_TOKEN|_KEY)$",
        })
        self.assertTrue(c.evaluate(EnvRead(name="GITHUB_TOKEN")))
        self.assertFalse(c.evaluate(EnvRead(name="PATH")))

    def test_matches_regex_invalid_pattern(self) -> None:
        with self.assertRaises(ValidationError):
            parse_condition({"type": "matches_regex", "ref": "name", "pattern": "[unclosed"})

    def test_in_set(self) -> None:
        c = parse_condition({
            "type": "in_set",
            "ref": "host",
            "values": ["github.com", "api.github.com"],
        })
        self.assertTrue(
            c.evaluate(NetworkRequest(method="GET", url="x", host="github.com", port=443))
        )
        self.assertFalse(
            c.evaluate(NetworkRequest(method="GET", url="x", host="evil.test", port=443))
        )

    def test_contains_arg(self) -> None:
        c = parse_condition({"type": "contains_arg", "values": ["-rf", "-fr"]})
        self.assertTrue(c.evaluate(ShellExec(argv=("rm", "-rf", "/tmp"))))
        self.assertFalse(c.evaluate(ShellExec(argv=("rm", "/tmp"))))

    def test_starts_with(self) -> None:
        c = parse_condition({"type": "starts_with", "ref": "program", "prefix": "git-"})
        self.assertTrue(c.evaluate(ShellExec(argv=("git-lfs", "track"))))
        self.assertFalse(c.evaluate(ShellExec(argv=("git", "status"))))

    def test_unknown_type_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            parse_condition({"type": "nope"})

    def test_round_trip(self) -> None:
        src = {
            "type": "and",
            "of": [
                {"type": "kind_is", "kind": "file_write"},
                {"type": "path_under", "ref": "path", "parent": "/etc"},
            ],
        }
        c = parse_condition(src)
        self.assertEqual(c.to_dict(), src)


class TestUnsetField(unittest.TestCase):
    """A reference to a field absent on the action evaluates to False."""

    def test_path_on_shellexec(self) -> None:
        c = parse_condition({"type": "path_under", "ref": "path", "parent": "/etc"})
        # ShellExec has no `path` field → should be False, not raise.
        self.assertFalse(c.evaluate(ShellExec(argv=("ls",))))


if __name__ == "__main__":
    unittest.main()
