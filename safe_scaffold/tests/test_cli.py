"""CLI smoke tests via subprocess.

Verify that each subcommand exits with the expected status code and prints
something sensible. We don't lock down exact output strings — that's
brittle — but we do lock down exit codes and required substrings."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO = Path(__file__).resolve().parent.parent


def _run(argv: list[str], stdin: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "safe_scaffold.cli", *argv],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestVersion(unittest.TestCase):
    def test_version_flag(self) -> None:
        code, out, _ = _run(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("safe-scaffold", out)


class TestInitAndCheck(unittest.TestCase):
    def test_init_then_check(self) -> None:
        with TemporaryDirectory() as td:
            policy_path = str(Path(td) / "policy.json")
            code, _, _ = _run(["init-policy", "-o", policy_path])
            self.assertEqual(code, 0)
            self.assertTrue(Path(policy_path).exists())

            # An allow action.
            code, out, _ = _run([
                "check", "--policy", policy_path,
                "--action", '{"kind":"file_read","path":"/home/u/notes.txt"}',
            ])
            self.assertEqual(code, 0)
            self.assertIn("ALLOW", out)

            # A deny action.
            code, out, _ = _run([
                "check", "--policy", policy_path,
                "--action", '{"kind":"shell_exec","argv":["rm","-rf","/"]}',
            ])
            self.assertEqual(code, 1)
            self.assertIn("DENY", out)

            # An unknown action.
            code, out, _ = _run([
                "check", "--policy", policy_path,
                "--action", '{"kind":"shell_exec","argv":["pytest"]}',
            ])
            self.assertEqual(code, 2)
            self.assertIn("UNKNOWN", out)


class TestEvalCli(unittest.TestCase):
    def test_eval_against_defaults(self) -> None:
        code, out, _ = _run(["eval"])
        self.assertEqual(code, 0)
        self.assertIn("Block rate", out)

    def test_eval_json_output(self) -> None:
        code, out, _ = _run(["eval", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["false_allow_rate"], 0.0)


class TestCrossCheckCli(unittest.TestCase):
    def test_cryspen_demo(self) -> None:
        code, out, _ = _run(["cross-check", "--demo", "cryspen"])
        self.assertEqual(code, 0)
        self.assertIn("disagreement", out.lower())


if __name__ == "__main__":
    unittest.main()
