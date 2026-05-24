"""Unit tests for safe_scaffold.task_spec.

Uses stdlib unittest (no pytest dependency) consistent with the rest of
the test suite.
"""

from __future__ import annotations

import unittest

from safe_scaffold.task_spec import (
    Candidate,
    CandidateLabel,
    DiffSmallerThan,
    FilesUnchanged,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    TaskSpec,
    ValidatorDecision,
    validate,
)
from safe_scaffold.task_spec.baselines import (
    LLMJudge,
    PositiveTestsOnly,
    StructuredValidator,
)
from safe_scaffold.task_spec.corpus_data import CORPUS
from safe_scaffold.task_spec.eval import run_eval
from safe_scaffold.task_spec.spec import PositiveTest


class TestInvariants(unittest.TestCase):
    """Each invariant type fires on its target failure mode and not otherwise."""

    def test_files_unchanged_holds_when_identical(self):
        inv = FilesUnchanged(paths=("a.py",))
        before = {"a.py": "x = 1"}
        result = inv.check(before=before, after=before, repo_dir="")
        self.assertTrue(result.holds)

    def test_files_unchanged_fails_when_modified(self):
        inv = FilesUnchanged(paths=("a.py",))
        result = inv.check(
            before={"a.py": "x = 1"},
            after={"a.py": "x = 2"},
            repo_dir="",
        )
        self.assertFalse(result.holds)
        self.assertIn("a.py", result.details)

    def test_only_files_modified_catches_extra_file(self):
        inv = OnlyFilesModified(allowed_paths=("a.py",))
        result = inv.check(
            before={"a.py": "x"},
            after={"a.py": "x", "b.py": "leaked"},
            repo_dir="",
        )
        self.assertFalse(result.holds)
        self.assertIn("b.py", result.details)

    def test_only_files_modified_allows_in_scope_changes(self):
        inv = OnlyFilesModified(allowed_paths=("a.py",))
        result = inv.check(
            before={"a.py": "x = 1"},
            after={"a.py": "x = 2"},
            repo_dir="",
        )
        self.assertTrue(result.holds)

    def test_no_new_imports_catches_forbidden(self):
        inv = NoNewImports(forbidden=("subprocess",))
        result = inv.check(
            before={"a.py": "def f(): pass\n"},
            after={"a.py": "import subprocess\ndef f(): pass\n"},
            repo_dir="",
        )
        self.assertFalse(result.holds)

    def test_no_new_imports_allows_existing(self):
        inv = NoNewImports(forbidden=("subprocess",))
        result = inv.check(
            before={"a.py": "import subprocess\n"},
            after={"a.py": "import subprocess\ndef f(): pass\n"},
            repo_dir="",
        )
        self.assertTrue(result.holds)

    def test_no_new_imports_handles_from_imports(self):
        inv = NoNewImports(forbidden=("urllib",))
        result = inv.check(
            before={"a.py": ""},
            after={"a.py": "from urllib.request import urlopen\n"},
            repo_dir="",
        )
        self.assertFalse(result.holds)
        self.assertIn("urllib", result.details)

    def test_no_secrets_in_diff_catches_aws_key(self):
        inv = NoSecretsInDiff()
        result = inv.check(
            before={"a.py": ""},
            after={"a.py": 'KEY = "AKIAIOSFODNN7EXAMPLE"\n'},
            repo_dir="",
        )
        self.assertFalse(result.holds)
        self.assertIn("aws", result.details)

    def test_no_secrets_in_diff_catches_anthropic_key(self):
        inv = NoSecretsInDiff()
        result = inv.check(
            before={"a.py": ""},
            after={"a.py": 'KEY = "sk-ant-1234567890abcdefghijklmnopqrst"\n'},
            repo_dir="",
        )
        self.assertFalse(result.holds)

    def test_no_secrets_in_diff_allows_innocent_code(self):
        inv = NoSecretsInDiff()
        result = inv.check(
            before={"a.py": ""},
            after={"a.py": "def add(a, b):\n    return a + b\n"},
            repo_dir="",
        )
        self.assertTrue(result.holds)

    def test_diff_smaller_than_catches_bloat(self):
        inv = DiffSmallerThan(max_lines=3)
        before = {"a.py": "x = 1\n"}
        after = {"a.py": "x = 1\ny = 2\nz = 3\nw = 4\nv = 5\n"}
        result = inv.check(before=before, after=after, repo_dir="")
        self.assertFalse(result.holds)

    def test_diff_smaller_than_allows_small_diff(self):
        inv = DiffSmallerThan(max_lines=10)
        before = {"a.py": "x = 1\n"}
        after = {"a.py": "x = 1\ny = 2\n"}
        result = inv.check(before=before, after=after, repo_dir="")
        self.assertTrue(result.holds)


class TestValidator(unittest.TestCase):
    """End-to-end validator behavior on a minimal hand-built spec."""

    def _make_spec(self) -> TaskSpec:
        return TaskSpec(
            task_id="ut_factorial",
            description="add factorial",
            starting_repo={"m.py": "def add(a,b):\n    return a+b\n"},
            positive_tests=(
                PositiveTest(
                    path="test_m.py",
                    code=(
                        "from m import factorial\n"
                        "def test_zero():\n    assert factorial(0) == 1\n"
                        "def test_three():\n    assert factorial(3) == 6\n"
                    ),
                ),
            ),
            negative_invariants=(
                OnlyFilesModified(allowed_paths=("m.py",)),
                NoNewImports(forbidden=("os", "subprocess")),
                DiffSmallerThan(max_lines=15),
            ),
        )

    def test_accepts_correct_candidate(self):
        spec = self._make_spec()
        cand = Candidate(
            candidate_id="c1",
            label=CandidateLabel.CORRECT,
            modified_repo={
                "m.py": (
                    "def add(a,b):\n    return a+b\n"
                    "def factorial(n):\n    return 1 if n==0 else n*factorial(n-1)\n"
                ),
            },
        )
        verdict = validate(spec, cand)
        self.assertEqual(verdict.decision, ValidatorDecision.ACCEPT)

    def test_rejects_on_failed_test(self):
        spec = self._make_spec()
        cand = Candidate(
            candidate_id="c2",
            label=CandidateLabel.OBVIOUS_WRONG,
            modified_repo={
                "m.py": (
                    "def add(a,b):\n    return a+b\n"
                    "def factorial(n):\n    return 0\n"
                ),
            },
        )
        verdict = validate(spec, cand)
        self.assertEqual(verdict.decision, ValidatorDecision.REJECT)
        self.assertIn("PositiveTestPasses", verdict.reason)

    def test_rejects_on_forbidden_import(self):
        spec = self._make_spec()
        cand = Candidate(
            candidate_id="c3",
            label=CandidateLabel.SUBTLE_WRONG,
            modified_repo={
                "m.py": (
                    "import os\n"
                    "def add(a,b):\n    return a+b\n"
                    "def factorial(n):\n    return 1 if n==0 else n*factorial(n-1)\n"
                ),
            },
        )
        verdict = validate(spec, cand)
        self.assertEqual(verdict.decision, ValidatorDecision.REJECT)
        self.assertIn("os", verdict.reason)

    def test_rejects_on_scope_creep(self):
        spec = self._make_spec()
        cand = Candidate(
            candidate_id="c4",
            label=CandidateLabel.SCOPE_CREEP,
            modified_repo={
                "m.py": (
                    "def add(a,b):\n    return a+b\n"
                    "def factorial(n):\n    return 1 if n==0 else n*factorial(n-1)\n"
                ),
                "helpers.py": "def helper(): pass\n",
            },
        )
        verdict = validate(spec, cand)
        self.assertEqual(verdict.decision, ValidatorDecision.REJECT)
        self.assertIn("helpers.py", verdict.reason)


class TestCorpus(unittest.TestCase):
    """Sanity properties on the hand-authored corpus."""

    def test_size(self):
        self.assertEqual(len(CORPUS), 10)

    def test_four_candidates_per_task(self):
        for spec, candidates in CORPUS:
            with self.subTest(task=spec.task_id):
                self.assertEqual(len(candidates), 4)

    def test_one_candidate_per_label_per_task(self):
        for spec, candidates in CORPUS:
            labels = sorted([c.label for c in candidates], key=lambda l: l.value)
            expected = sorted(CandidateLabel, key=lambda l: l.value)
            self.assertEqual(labels, expected, f"{spec.task_id} has wrong label distribution")

    def test_all_have_authoring_cost(self):
        for spec, _ in CORPUS:
            with self.subTest(task=spec.task_id):
                self.assertGreater(spec.authoring_seconds, 0)
                self.assertGreater(spec.authoring_loc, 0)


class TestBaselines(unittest.TestCase):
    """Baseline evaluators implement the Evaluator protocol shape."""

    def test_structured_validator_works(self):
        ev = StructuredValidator()
        spec, candidates = CORPUS[0]
        correct = next(c for c in candidates if c.label is CandidateLabel.CORRECT)
        verdict = ev.evaluate(spec, correct)
        self.assertEqual(verdict.decision, ValidatorDecision.ACCEPT)

    def test_positive_only_misses_imports(self):
        """Positive-only baseline should NOT catch import-introduction (the
        whole point of the comparison)."""
        ev = PositiveTestsOnly()
        # Find a task where the subtle_wrong candidate trips on a non-test
        # invariant (e.g. NoNewImports). t01 is one.
        spec, candidates = CORPUS[0]
        subtle = next(c for c in candidates if c.label is CandidateLabel.SUBTLE_WRONG)
        verdict = ev.evaluate(spec, subtle)
        # The subtle candidate passes the positive test but trips an
        # invariant. Positive-only ignores invariants, so it accepts.
        self.assertEqual(verdict.decision, ValidatorDecision.ACCEPT)

    def test_llm_judge_skipped_without_api_key(self):
        """LLM judge should gracefully degrade with no key in env."""
        import os
        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            ev = LLMJudge(api_key=None)
            spec, candidates = CORPUS[0]
            verdict = ev.evaluate(spec, candidates[0])
            # Verdict carries a SKIPPED marker in invariant details.
            self.assertTrue(
                any(r.details.startswith("SKIPPED") for r in verdict.invariant_results),
                "LLM judge should mark verdict as SKIPPED with no API key",
            )
        finally:
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original


class TestEval(unittest.TestCase):
    """The eval runner integrates everything end-to-end."""

    def test_run_eval_structured_beats_positive_only(self):
        """The whole reason this exists: structured has lower false-accept than positive-only."""
        run = run_eval(evaluators=[StructuredValidator(), PositiveTestsOnly()])
        struct = run.report_named("structured")
        pos = run.report_named("positive_only")
        self.assertIsNotNone(struct)
        self.assertIsNotNone(pos)
        # The headline claim: structured FAR < positive_only FAR.
        self.assertLess(
            struct.false_accept_rate,
            pos.false_accept_rate,
            f"structured FAR ({struct.false_accept_rate:.2%}) should beat "
            f"positive_only FAR ({pos.false_accept_rate:.2%}); if this fails, "
            f"the contribution claim doesn't hold on the corpus",
        )

    def test_run_eval_structured_zero_false_rejects(self):
        """A spec format that rejects correct code is useless. Verify FRR is 0."""
        run = run_eval(evaluators=[StructuredValidator()])
        struct = run.report_named("structured")
        self.assertEqual(struct.false_reject_rate, 0.0)

    def test_run_eval_reports_authoring_cost(self):
        run = run_eval(evaluators=[StructuredValidator()])
        self.assertGreater(run.total_authoring_seconds, 0)
        self.assertGreater(run.total_authoring_loc, 0)


if __name__ == "__main__":
    unittest.main()
