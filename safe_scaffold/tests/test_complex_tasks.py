"""Smoke tests for the complex multi-file tasks (t11-t13)."""

from __future__ import annotations

import unittest

from safe_scaffold.task_spec.baselines import StructuredValidator
from safe_scaffold.task_spec.corpus_data import COMPLEX_TASKS, FULL_CORPUS
from safe_scaffold.task_spec.spec import ValidatorDecision


class TestComplexTasksCorpus(unittest.TestCase):
    def test_three_complex_tasks_present(self):
        ids = [s.task_id for s, _ in COMPLEX_TASKS]
        self.assertEqual(
            ids,
            ["t11_jwt_middleware", "t12_sql_migration", "t13_rate_limit"],
        )

    def test_each_task_has_four_labels(self):
        from safe_scaffold.task_spec.spec import CandidateLabel
        for spec, candidates in COMPLEX_TASKS:
            with self.subTest(task_id=spec.task_id):
                labels = {c.label for c in candidates}
                self.assertEqual(labels, set(CandidateLabel))

    def test_full_corpus_includes_complex(self):
        ids = {s.task_id for s, _ in FULL_CORPUS}
        for cid in ("t11_jwt_middleware", "t12_sql_migration", "t13_rate_limit"):
            self.assertIn(cid, ids)


class TestComplexTasksValidator(unittest.TestCase):
    """End-to-end check: each candidate is classified per its ground-truth label.

    This is the same check `eval.run_eval` does on the full corpus, but
    isolated to the new tasks so a future regression is immediately
    attributable.
    """

    def test_all_complex_candidates_classified_correctly(self):
        validator = StructuredValidator()
        for spec, candidates in COMPLEX_TASKS:
            for cand in candidates:
                with self.subTest(spec=spec.task_id, cand=cand.candidate_id):
                    verdict = validator.evaluate(spec, cand)
                    expected = (
                        ValidatorDecision.ACCEPT if cand.label.should_accept
                        else ValidatorDecision.REJECT
                    )
                    self.assertEqual(
                        verdict.decision, expected,
                        f"{cand.candidate_id} ({cand.label.value}) → "
                        f"{verdict.decision.value}; reason: {verdict.reason}",
                    )


if __name__ == "__main__":
    unittest.main()
