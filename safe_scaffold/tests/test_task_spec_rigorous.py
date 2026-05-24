"""Unit tests for the rigorous-evaluation additions: metrics, ablation,
strong baselines, and the extended corpus."""

from __future__ import annotations

import math
import os
import unittest

from safe_scaffold.task_spec.ablation import run_ablation
from safe_scaffold.task_spec.baselines import (
    PositiveTestsOnly,
    StructuredValidator,
)
from safe_scaffold.task_spec.corpus_data import CORPUS, EXTENDED_CORPUS
from safe_scaffold.task_spec.corpus_data.auto_mutants import MUTATED_TASKS
from safe_scaffold.task_spec.eval import run_eval
from safe_scaffold.task_spec.metrics import (
    authoring_cost_per_far_reduction,
    cohen_kappa,
    discriminative_power,
    per_invariant_precision_recall,
)
from safe_scaffold.task_spec.spec import CandidateLabel
from safe_scaffold.task_spec.strong_baselines import NL2PostcondJudge, PRDStyleJudge


class TestMetrics(unittest.TestCase):
    """Statistical metrics."""

    def setUp(self):
        self.run = run_eval(evaluators=[StructuredValidator(), PositiveTestsOnly()])

    def test_discriminative_power_perfect_evaluator(self):
        """For an evaluator that's right on every pair, disc.power should be 1.0."""
        struct = self.run.report_named("structured")
        # Our structured validator misses 1 of 30 (correct,bad) pairs
        # (t09_subtle_wrong slips through). So disc.power should be < 1 but high.
        dp = discriminative_power(struct)
        self.assertGreaterEqual(dp, 0.90, "structured disc.power should be >= 90%")
        self.assertLessEqual(dp, 1.0)

    def test_discriminative_power_weak_evaluator(self):
        """Positive-only catches only OBVIOUS_WRONG. Disc.power should be ~1/3."""
        po = self.run.report_named("positive_only")
        dp = discriminative_power(po)
        # 1 of 3 bad candidates per task is OBVIOUS_WRONG; PO catches it.
        # So 1/3 of pairs are distinguished.
        self.assertAlmostEqual(dp, 1/3, delta=0.05)

    def test_cohen_kappa_strong_for_structured(self):
        struct = self.run.report_named("structured")
        k = cohen_kappa(struct)
        # Landis-Koch: 0.81+ is "almost perfect"
        self.assertGreaterEqual(k, 0.80, f"structured kappa was {k:.3f}, expected >= 0.80")

    def test_cohen_kappa_weak_for_positive_only(self):
        po = self.run.report_named("positive_only")
        k = cohen_kappa(po)
        # Should be in "slight" or "fair" range, not "almost perfect"
        self.assertLessEqual(k, 0.40, f"positive_only kappa was {k:.3f}, expected <= 0.40")

    def test_kappa_bounded(self):
        for r in self.run.reports:
            k = cohen_kappa(r)
            self.assertGreaterEqual(k, -1.0)
            self.assertLessEqual(k, 1.0)

    def test_per_invariant_precision_100_percent(self):
        """No invariant should false-alarm on a CORRECT candidate."""
        struct = self.run.report_named("structured")
        per_inv = per_invariant_precision_recall(struct)
        self.assertGreater(len(per_inv), 0)
        for name, stats in per_inv.items():
            with self.subTest(invariant=name):
                self.assertEqual(stats.precision, 1.0,
                    f"{name} has precision {stats.precision} — it false-alarmed on a CORRECT")

    def test_authoring_cost_ratio_finite_and_reasonable(self):
        struct = self.run.report_named("structured")
        po = self.run.report_named("positive_only")
        ratio = authoring_cost_per_far_reduction(
            structured=struct,
            baseline=po,
            total_authoring_seconds=self.run.total_authoring_seconds,
        )
        self.assertTrue(math.isfinite(ratio))
        # We expect ~25 seconds per percentage point of FAR reduction.
        # Allow a wide band so the test isn't brittle.
        self.assertGreater(ratio, 5)
        self.assertLess(ratio, 100)


class TestAblation(unittest.TestCase):
    """The per-invariant ablation should identify OnlyFilesModified and
    NoNewImports as the most important invariants on this corpus."""

    @classmethod
    def setUpClass(cls):
        cls.ablation = run_ablation(verbose=False)

    def test_no_ablation_increases_accuracy(self):
        """Removing an invariant should never make the validator more
        accurate (otherwise the invariant is harmful, not helpful)."""
        for r in self.ablation.results:
            with self.subTest(invariant=r.invariant_type):
                self.assertLessEqual(r.accuracy_without, r.accuracy_with + 1e-9)

    def test_most_important_is_only_files_modified_or_no_new_imports(self):
        """On this corpus, scope-creep and forbidden-import patterns
        dominate. So the top of the ablation list should be one of those."""
        top = self.ablation.sorted_by_importance()[0]
        self.assertIn(top.invariant_type, ("OnlyFilesModified", "NoNewImports"))

    def test_at_least_one_invariant_has_zero_unique_catches(self):
        """We honestly report when an invariant is redundant on the corpus.
        FilesUnchanged is expected to be redundant here (it's a stricter
        version of OnlyFilesModified)."""
        zeros = [r for r in self.ablation.results if r.pairs_newly_accepted == 0]
        self.assertGreaterEqual(len(zeros), 1)


class TestExtendedCorpus(unittest.TestCase):
    def test_extended_corpus_size(self):
        self.assertEqual(len(EXTENDED_CORPUS), 15)
        self.assertEqual(sum(len(c) for _, c in EXTENDED_CORPUS), 60)

    def test_mutated_tasks_have_one_per_label(self):
        from safe_scaffold.task_spec.spec import CandidateLabel
        for spec, candidates in MUTATED_TASKS:
            with self.subTest(task=spec.task_id):
                labels = sorted([c.label for c in candidates], key=lambda l: l.value)
                expected = sorted(CandidateLabel, key=lambda l: l.value)
                self.assertEqual(labels, expected)

    def test_structured_validator_holds_at_scale(self):
        """The structured validator's FAR shouldn't get dramatically worse
        on the larger corpus. We expect FAR to stay below 10%."""
        run = run_eval(
            evaluators=[StructuredValidator()],
            corpus=list(EXTENDED_CORPUS),
        )
        struct = run.report_named("structured")
        self.assertLess(struct.false_accept_rate, 0.10)
        self.assertEqual(struct.false_reject_rate, 0.0)


class TestStrongBaselinesSkipping(unittest.TestCase):
    """Without an API key, both strong baselines should skip gracefully."""

    def test_nl2postcond_skips_without_key(self):
        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            ev = NL2PostcondJudge(api_key=None)
            spec, candidates = CORPUS[0]
            verdict = ev.evaluate(spec, candidates[0])
            self.assertTrue(
                any(r.details.startswith("SKIPPED") for r in verdict.invariant_results),
            )
        finally:
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original

    def test_prd_style_skips_without_key(self):
        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            ev = PRDStyleJudge(api_key=None)
            spec, candidates = CORPUS[0]
            verdict = ev.evaluate(spec, candidates[0])
            self.assertTrue(
                any(r.details.startswith("SKIPPED") for r in verdict.invariant_results),
            )
        finally:
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original


if __name__ == "__main__":
    unittest.main()
