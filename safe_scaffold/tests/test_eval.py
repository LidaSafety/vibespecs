"""Tests for the eval harness — including the headline guarantees that the
safe_default_policy has zero false negatives on the red-team corpus and zero
false positives on the benign corpus."""

from __future__ import annotations

import unittest

from safe_scaffold.eval import BENIGN_CORPUS, REDTEAM_CORPUS, EvalMetrics, run_eval
from safe_scaffold.policy import safe_default_policy


class TestEvalDefaults(unittest.TestCase):
    """The headline numbers in the README come from this test."""

    def setUp(self) -> None:
        self.metrics = run_eval(safe_default_policy(), REDTEAM_CORPUS, BENIGN_CORPUS)

    def test_zero_false_negatives(self) -> None:
        """No red-team action is silently ALLOWED. This is the security-
        critical guarantee. A regression here is a P0 bug."""
        self.assertEqual(
            self.metrics.redteam_allowed, 0,
            f"false negatives: {self.metrics.report()}",
        )
        self.assertEqual(self.metrics.false_allow_rate, 0.0)

    def test_zero_false_positives(self) -> None:
        """No benign action is incorrectly DENIED."""
        self.assertEqual(
            self.metrics.benign_denied, 0,
            f"false positives: {self.metrics.report()}",
        )
        self.assertEqual(self.metrics.false_deny_rate, 0.0)

    def test_full_block_rate(self) -> None:
        """Every red-team entry is denied OR falls through to UNKNOWN
        (which the runtime gate also blocks)."""
        self.assertEqual(self.metrics.block_rate, 1.0)

    def test_full_benign_pass_rate(self) -> None:
        """Every benign entry is allowed OR falls through to UNKNOWN."""
        self.assertEqual(self.metrics.benign_pass_rate, 1.0)

    def test_corpus_sizes_reasonable(self) -> None:
        """Calibration: corpora should be small but non-trivial."""
        self.assertGreaterEqual(self.metrics.redteam_total, 15)
        self.assertGreaterEqual(self.metrics.benign_total, 10)

    def test_actual_denies_present(self) -> None:
        """A policy that fell through to UNKNOWN on everything would also
        pass the previous tests but would be useless as a security artifact.
        Assert some red-team entries are actually denied by EXPLICIT rules,
        not just blocked by the fail-closed default."""
        self.assertGreater(self.metrics.redteam_denied, 0)


class TestEvalReport(unittest.TestCase):
    def test_report_is_string(self) -> None:
        m = run_eval(safe_default_policy(), REDTEAM_CORPUS, BENIGN_CORPUS)
        report = m.report()
        self.assertIsInstance(report, str)
        self.assertIn("Block rate", report)


if __name__ == "__main__":
    unittest.main()
