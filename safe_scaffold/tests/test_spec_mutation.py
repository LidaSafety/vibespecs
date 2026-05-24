"""Unit tests for safe_scaffold.task_spec.spec_mutation.

Uses a tiny synthetic spec (not the full corpus) so tests run in <1s and
don't depend on pytest/subprocess.
"""

from __future__ import annotations

import unittest

from safe_scaffold.task_spec.invariants import (
    DiffSmallerThan,
    FilesUnchanged,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    PositiveTestPasses,
)
from safe_scaffold.task_spec.spec import (
    Candidate,
    CandidateLabel,
    PositiveTest,
    TaskSpec,
)
from safe_scaffold.task_spec.spec_mutation import (
    _mutations_for_invariant,
    mutate_spec,
    run_mutation_analysis,
    summarize,
)


def _trivial_test() -> PositiveTest:
    return PositiveTest(
        path="test_a.py",
        code="from a import f\n\ndef test_f():\n    assert f() == 1\n",
        name="f",
    )


def _spec_with(*invariants) -> TaskSpec:
    return TaskSpec(
        task_id="syn",
        description="x",
        starting_repo={"a.py": "def f():\n    return 1\n"},
        positive_tests=(_trivial_test(),),
        negative_invariants=tuple(invariants) + (PositiveTestPasses("test_a.py"),),
    )


class TestPerInvariantMutations(unittest.TestCase):
    def test_diff_smaller_than_weakens_by_2x_and_10x(self):
        muts = _mutations_for_invariant(DiffSmallerThan(20))
        kinds = sorted(m.kind for m, _ in muts)
        self.assertEqual(kinds, ["drop_invariant", "weaken_bound", "weaken_bound"])
        bounds = sorted(r.max_lines for _, r in muts if r is not None)
        self.assertEqual(bounds, [40, 200])

    def test_no_new_imports_shrinks_one_at_a_time(self):
        muts = _mutations_for_invariant(NoNewImports(("os", "subprocess", "socket")))
        kinds = [m.kind for m, _ in muts]
        # 1 drop + 3 shrinks (one for each forbidden module)
        self.assertEqual(kinds.count("drop_invariant"), 1)
        self.assertEqual(kinds.count("shrink_set"), 3)

    def test_no_new_imports_singleton_only_drops(self):
        muts = _mutations_for_invariant(NoNewImports(("os",)))
        # Singleton: dropping the only item would leave an empty forbidden
        # list which is functionally equivalent to dropping the invariant.
        # So we should only see the drop_invariant mutation.
        self.assertEqual([m.kind for m, _ in muts], ["drop_invariant"])

    def test_only_files_modified_widens_by_candidate_paths(self):
        muts = _mutations_for_invariant(
            OnlyFilesModified(("a.py",)),
            candidate_extra_paths=("evil.py",),
        )
        kinds = sorted(m.kind for m, _ in muts)
        self.assertEqual(kinds, ["drop_invariant", "widen_scope"])
        widen_replacement = [r for m, r in muts if m.kind == "widen_scope"][0]
        self.assertIn("evil.py", widen_replacement.allowed_paths)

    def test_only_files_modified_no_extras_no_widen(self):
        muts = _mutations_for_invariant(
            OnlyFilesModified(("a.py",)),
            candidate_extra_paths=(),
        )
        # Without candidate extras there's nothing meaningful to widen by.
        self.assertEqual([m.kind for m, _ in muts], ["drop_invariant"])

    def test_no_secrets_only_drops(self):
        muts = _mutations_for_invariant(NoSecretsInDiff())
        self.assertEqual([m.kind for m, _ in muts], ["drop_invariant"])

    def test_files_unchanged_shrinks(self):
        muts = _mutations_for_invariant(FilesUnchanged(("a.py", "b.py")))
        kinds = [m.kind for m, _ in muts]
        self.assertEqual(kinds.count("drop_invariant"), 1)
        self.assertEqual(kinds.count("shrink_set"), 2)


class TestMutateSpec(unittest.TestCase):
    def test_drop_test_mutation_removes_positive_test(self):
        spec = _spec_with(DiffSmallerThan(20))
        mutations = mutate_spec(spec)
        drop_tests = [(m, s) for m, s in mutations if m.kind == "drop_test"]
        self.assertEqual(len(drop_tests), 1)
        _, mutated = drop_tests[0]
        self.assertEqual(mutated.positive_tests, ())
        # The PositiveTestPasses marker should also be removed.
        self.assertFalse(any(isinstance(i, PositiveTestPasses)
                              for i in mutated.negative_invariants))

    def test_widen_scope_uses_candidates(self):
        spec = _spec_with(OnlyFilesModified(("a.py",)))
        good = Candidate(
            candidate_id="ok", label=CandidateLabel.CORRECT,
            modified_repo={"a.py": "def f(): return 1\n"},
        )
        creep = Candidate(
            candidate_id="creep", label=CandidateLabel.SCOPE_CREEP,
            modified_repo={"a.py": "def f(): return 1\n",
                            "extra.py": "junk\n"},
        )
        mutations = mutate_spec(spec, (good, creep))
        widens = [m for m, _ in mutations if m.kind == "widen_scope"]
        self.assertEqual(len(widens), 1)
        self.assertIn("extra.py", widens[0].description)


class TestRunMutationAnalysis(unittest.TestCase):
    def test_classifies_load_bearing_for_scope_creep(self):
        # The spec rejects any change to files other than a.py.
        spec = _spec_with(OnlyFilesModified(("a.py",)))
        correct = Candidate(
            candidate_id="c", label=CandidateLabel.CORRECT,
            modified_repo={"a.py": "def f(): return 1\n"},
        )
        scope_creep = Candidate(
            candidate_id="creep", label=CandidateLabel.SCOPE_CREEP,
            modified_repo={"a.py": "def f(): return 1\n",
                            "evil.py": "import os\n"},
        )

        results = run_mutation_analysis(spec, (correct, scope_creep))
        # Find the drop-OnlyFilesModified mutation.
        drop_scope = [r for r in results
                      if r.mutation.kind == "drop_invariant"
                      and r.mutation.target == "OnlyFilesModified"]
        self.assertEqual(len(drop_scope), 1)
        # Dropping the scope check must newly admit scope_creep.
        self.assertEqual(drop_scope[0].newly_accepted, ["creep"])
        self.assertEqual(drop_scope[0].classification, "load_bearing")

    def test_brittle_when_mutation_rejects_correct(self):
        # Pathological setup: a spec whose CORRECT candidate happens to
        # add one line. Tightening DiffSmallerThan to 0 will newly
        # reject it — that's a brittle mutation.
        spec = _spec_with(DiffSmallerThan(5))
        correct = Candidate(
            candidate_id="c", label=CandidateLabel.CORRECT,
            modified_repo={"a.py": "def f():\n    return 1\n# extra\n"},
        )
        # We trigger brittleness by going the other direction: drop the
        # DiffSmallerThan(5) and replace it with a tight bound via the
        # shrink_set machinery isn't applicable here. Instead use the
        # mutate_spec API directly to construct a deliberately-tight spec.
        # For this synthetic test we only check the classification logic
        # by simulating a result manually.
        from safe_scaffold.task_spec.spec_mutation import Mutation, MutationResult
        r = MutationResult(
            mutation=Mutation(kind="x", target="y", description="z"),
            spec_id="syn",
            per_candidate=(("c", True, True, False),),  # was accepted, now rejected
        )
        self.assertEqual(r.classification, "brittle")
        self.assertEqual(r.newly_rejected, ["c"])

    def test_invisible_when_no_verdict_changes(self):
        from safe_scaffold.task_spec.spec_mutation import Mutation, MutationResult
        r = MutationResult(
            mutation=Mutation(kind="x", target="y", description="z"),
            spec_id="syn",
            per_candidate=(("c1", True, True, True), ("c2", False, False, False)),
        )
        self.assertEqual(r.classification, "invisible")
        self.assertFalse(r.verdict_changed)


class TestSummarize(unittest.TestCase):
    def test_summary_counts_match(self):
        from safe_scaffold.task_spec.spec_mutation import Mutation, MutationResult

        def _result(cls: str, kind: str = "drop_invariant") -> MutationResult:
            if cls == "load_bearing":
                per = (("c", False, False, True),)  # newly accepted should-reject
            elif cls == "brittle":
                per = (("c", True, True, False),)
            else:
                per = (("c", True, True, True),)
            return MutationResult(
                mutation=Mutation(kind=kind, target="t", description="d"),
                spec_id="s",
                per_candidate=per,
            )

        results = {
            "s1": [_result("load_bearing"), _result("invisible"),
                    _result("brittle", kind="weaken_bound")],
        }
        summary = summarize(results)
        self.assertEqual(summary.total_mutations, 3)
        self.assertEqual(summary.load_bearing, 1)
        self.assertEqual(summary.brittle, 1)
        self.assertEqual(summary.invisible, 1)
        self.assertIn("drop_invariant", summary.by_kind)
        self.assertIn("weaken_bound", summary.by_kind)


if __name__ == "__main__":
    unittest.main()
