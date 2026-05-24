"""Tests for the cheap ast.parse-based Python syntax checker."""

from __future__ import annotations

import unittest

from safe_scaffold.task_spec.syntax_check import check_python_syntax


class TestSyntaxCheck(unittest.TestCase):
    def test_good_python_passes(self):
        files = {"a.py": "def f(x):\n    return x + 1\n"}
        results = check_python_syntax(files)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "a.py")
        self.assertTrue(results[0]["ok"])
        self.assertNotIn("line", results[0])

    def test_syntax_error_reports_line_and_message(self):
        # Missing colon on line 2.
        files = {"bad.py": "def f(x)\n    return x + 1\n"}
        results = check_python_syntax(files)
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["path"], "bad.py")
        self.assertFalse(r["ok"])
        self.assertEqual(r["line"], 1)  # the def line is malformed
        self.assertIsInstance(r["msg"], str)
        self.assertTrue(r["msg"])

    def test_multifile_mix(self):
        files = {
            "ok.py": "x = 1\n",
            "bad.py": "def g(x:\n    pass\n",
            "also_ok.py": "import sys\n",
        }
        results = check_python_syntax(files)
        by_path = {r["path"]: r for r in results}
        self.assertTrue(by_path["ok.py"]["ok"])
        self.assertFalse(by_path["bad.py"]["ok"])
        self.assertTrue(by_path["also_ok.py"]["ok"])

    def test_empty_file_passes(self):
        results = check_python_syntax({"empty.py": ""})
        self.assertTrue(results[0]["ok"])

    def test_unicode_passes(self):
        # ast.parse must handle non-ASCII identifiers / strings.
        results = check_python_syntax({"u.py": "α = '日本語'\n"})
        self.assertTrue(results[0]["ok"])


if __name__ == "__main__":
    unittest.main()
