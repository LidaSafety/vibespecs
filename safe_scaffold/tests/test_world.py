"""Tests for the typed world model."""

from __future__ import annotations

import unittest

from safe_scaffold.world import (
    Action,
    EnvRead,
    FileDelete,
    FileRead,
    FileWrite,
    NetworkRequest,
    ProcessSignal,
    ShellExec,
)


class TestActionConstruction(unittest.TestCase):
    def test_shellexec_basic(self) -> None:
        a = ShellExec(argv=("git", "status"))
        self.assertEqual(a.kind, "shell_exec")
        self.assertEqual(a.program, "git")
        self.assertEqual(a.argv, ("git", "status"))
        self.assertTrue(a.id.startswith("act_"))

    def test_shellexec_rejects_empty_argv(self) -> None:
        with self.assertRaises(ValueError):
            ShellExec(argv=())

    def test_shellexec_rejects_non_string_argv(self) -> None:
        with self.assertRaises(TypeError):
            ShellExec(argv=("git", 42))  # type: ignore[arg-type]

    def test_shellexec_rejects_zero_timeout(self) -> None:
        with self.assertRaises(ValueError):
            ShellExec(argv=("git",), timeout_seconds=0)

    def test_fileread_requires_path(self) -> None:
        with self.assertRaises(ValueError):
            FileRead(path="")

    def test_filewrite_rejects_negative_content_size(self) -> None:
        with self.assertRaises(ValueError):
            FileWrite(path="/tmp/x", content_size=-1)

    def test_filedelete_basic(self) -> None:
        a = FileDelete(path="/tmp/x", recursive=True)
        self.assertEqual(a.kind, "file_delete")
        self.assertTrue(a.recursive)

    def test_network_request_rejects_bad_method(self) -> None:
        with self.assertRaises(ValueError):
            NetworkRequest(method="EVIL", url="http://x", host="x", port=80)

    def test_network_request_rejects_bad_port(self) -> None:
        with self.assertRaises(ValueError):
            NetworkRequest(method="GET", url="http://x", host="x", port=99999)

    def test_processsignal_rejects_non_positive_pid(self) -> None:
        with self.assertRaises(ValueError):
            ProcessSignal(pid=0, signal="SIGTERM")
        with self.assertRaises(ValueError):
            ProcessSignal(pid=-1, signal="SIGTERM")

    def test_envread_requires_name(self) -> None:
        with self.assertRaises(ValueError):
            EnvRead(name="")


class TestActionSerialization(unittest.TestCase):
    def test_round_trip_shellexec(self) -> None:
        a = ShellExec(argv=("git", "log"))
        b = Action.from_dict(a.to_dict())
        self.assertEqual(a, b)

    def test_round_trip_all_kinds(self) -> None:
        cases: list[Action] = [
            ShellExec(argv=("ls", "-l")),
            FileRead(path="/etc/hosts"),
            FileWrite(path="/tmp/x", content_size=10),
            FileDelete(path="/tmp/y", recursive=True),
            NetworkRequest(method="POST", url="https://x.test/", host="x.test", port=443),
            ProcessSignal(pid=4321, signal="SIGTERM"),
            EnvRead(name="PATH"),
        ]
        for a in cases:
            b = Action.from_dict(a.to_dict())
            self.assertEqual(a, b, f"round-trip failed for {a}")

    def test_unknown_kind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Action.from_dict({"kind": "bogus_kind"})

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Action.from_dict({"kind": "shell_exec", "argv": ["ls"], "evil": "x"})

    def test_missing_kind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Action.from_dict({"argv": ["ls"]})


if __name__ == "__main__":
    unittest.main()
