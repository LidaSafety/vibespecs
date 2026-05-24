"""Tests for the concrete-test-case runner.

Generation is LLM-dependent; we only exercise the runner half here
(hand-craft cases against `is_even`). The LLM-half schema validation
is exercised indirectly through the existing live smoke tests.
"""

from __future__ import annotations

import unittest

from safe_scaffold.task_spec.test_case_gen import run_test_cases


_IS_EVEN_FILES = {"iseven.py": "def is_even(n):\n    return n % 2 == 0\n"}


class TestRunCases(unittest.TestCase):
    def test_all_pass_on_correct_impl(self):
        cases = [
            {"input": "0", "expected": "True", "rationale": "zero"},
            {"input": "1", "expected": "False", "rationale": "smallest odd"},
            {"input": "42", "expected": "True", "rationale": "typical even"},
            {"input": "-7", "expected": "False", "rationale": "negative odd"},
        ]
        results = run_test_cases(_IS_EVEN_FILES, "is_even", cases,
                                  timeout_seconds=15)
        self.assertEqual(len(results), 4)
        for r in results:
            self.assertEqual(r["status"], "pass",
                              f"expected pass; got {r}")
            self.assertEqual(r["got"], r["expected"],
                              f"got/expected disagree: {r}")

    def test_fail_on_wrong_expected(self):
        # is_even(2) is True, but we claim expected=False.
        cases = [{"input": "2", "expected": "False", "rationale": "wrong"}]
        results = run_test_cases(_IS_EVEN_FILES, "is_even", cases,
                                  timeout_seconds=15)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "fail")
        self.assertEqual(results[0]["got"], "True")

    def test_error_on_unparseable_input(self):
        # `eval("x + 1")` would crash; ast.literal_eval rejects names.
        cases = [{"input": "x + 1", "expected": "0", "rationale": "bad"}]
        results = run_test_cases(_IS_EVEN_FILES, "is_even", cases,
                                  timeout_seconds=15)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("parse", results[0]["error"].lower())

    def test_error_on_missing_function(self):
        cases = [{"input": "1", "expected": "1", "rationale": ""}]
        results = run_test_cases(_IS_EVEN_FILES, "nonexistent_function",
                                  cases, timeout_seconds=15)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "error")
        # Either driver crashed or impl raised — both acceptable.

    def test_tuple_input_is_unpacked(self):
        # A function that takes two args + a case with tuple input.
        files = {"add.py": "def add(a, b):\n    return a + b\n"}
        cases = [
            {"input": "(2, 3)", "expected": "5", "rationale": "basic"},
            {"input": "(0, 0)", "expected": "0", "rationale": "zeros"},
        ]
        results = run_test_cases(files, "add", cases, timeout_seconds=15)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r["status"], "pass", r)


if __name__ == "__main__":
    unittest.main()
