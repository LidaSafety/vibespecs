"""Ablation study: which invariant carries the false-accept-rate reduction?

For each invariant type T, re-run the corpus with T removed from every
spec, leave the other invariants in place, and see how the FAR changes.
If removing T causes FAR to spike, T is doing useful work. If FAR is
unchanged, T is redundant given the others.

This is the kind of analysis that's standard in ML papers (drop one
feature at a time) and missing from the spec-validation literature. It
directly answers the question "why does your structured validator beat
positive-only?" — the per-invariant ablation says exactly which structural
constraints matter and which are along for the ride.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Type

from safe_scaffold.task_spec.baselines import StructuredValidator
from safe_scaffold.task_spec.corpus_data import CORPUS
from safe_scaffold.task_spec.eval import EvaluatorReport, run_eval
from safe_scaffold.task_spec.invariants import (
    DiffSmallerThan,
    FilesUnchanged,
    Invariant,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    PositiveTestPasses,
)
from safe_scaffold.task_spec.spec import TaskSpec


# The invariant types we ablate. PositiveTestPasses is dispatched by the
# validator (not a real invariant in the spec), so it's not in this list —
# the "positive_tests" column of the eval already gives us the
# "no invariants at all" baseline.
ABLATABLE_INVARIANTS: list[Type[Invariant]] = [
    OnlyFilesModified,
    FilesUnchanged,
    NoNewImports,
    NoSecretsInDiff,
    DiffSmallerThan,
]


@dataclass(frozen=True)
class AblationResult:
    """Result of running the eval with one invariant type removed."""

    invariant_type: str
    far_with: float
    far_without: float
    delta_far: float  # far_without - far_with (positive = invariant helped)
    accuracy_with: float
    accuracy_without: float
    delta_accuracy: float
    pairs_newly_accepted: int  # rejected before, accepted after; bad ones spike here
    pairs_newly_rejected: int  # accepted before, rejected after; rare


def _strip_invariant(spec: TaskSpec, inv_type: Type[Invariant]) -> TaskSpec:
    """Return a copy of `spec` with every instance of `inv_type` removed
    from its negative_invariants tuple."""
    new_invariants = tuple(
        inv for inv in spec.negative_invariants
        if not isinstance(inv, inv_type)
    )
    return replace(spec, negative_invariants=new_invariants)


def _corpus_with_stripped_invariant(inv_type: Type[Invariant]) -> list:
    """Return a corpus where every spec has had `inv_type` removed."""
    return [(_strip_invariant(spec, inv_type), candidates) for spec, candidates in CORPUS]


def run_ablation(*, verbose: bool = False) -> "AblationRun":
    """Run the structured validator on the full corpus, then re-run with
    each invariant type ablated in turn. Return the per-ablation deltas."""
    # Baseline: full structured validator on the full corpus.
    baseline_run = run_eval(
        evaluators=[StructuredValidator()],
        corpus=list(CORPUS),
        verbose=False,
    )
    baseline = baseline_run.report_named("structured")
    assert baseline is not None

    # Per-ablation results.
    results: list[AblationResult] = []
    for inv_type in ABLATABLE_INVARIANTS:
        ablated_corpus = _corpus_with_stripped_invariant(inv_type)
        ablated_run = run_eval(
            evaluators=[StructuredValidator()],
            corpus=ablated_corpus,
            verbose=False,
        )
        ablated = ablated_run.report_named("structured")
        assert ablated is not None

        # Pair-level deltas.
        baseline_by_key = {
            (o.spec.task_id, o.candidate.candidate_id): o.verdict.accepted
            for o in baseline.outcomes
        }
        newly_accepted = 0
        newly_rejected = 0
        for o in ablated.outcomes:
            key = (o.spec.task_id, o.candidate.candidate_id)
            was_accepted = baseline_by_key[key]
            now_accepted = o.verdict.accepted
            if not was_accepted and now_accepted:
                newly_accepted += 1
            elif was_accepted and not now_accepted:
                newly_rejected += 1

        results.append(AblationResult(
            invariant_type=inv_type.__name__,
            far_with=baseline.false_accept_rate,
            far_without=ablated.false_accept_rate,
            delta_far=ablated.false_accept_rate - baseline.false_accept_rate,
            accuracy_with=baseline.accuracy,
            accuracy_without=ablated.accuracy,
            delta_accuracy=ablated.accuracy - baseline.accuracy,
            pairs_newly_accepted=newly_accepted,
            pairs_newly_rejected=newly_rejected,
        ))

        if verbose:
            print(f"  ablated {inv_type.__name__:20s} → FAR {baseline.false_accept_rate:>5.1%} → {ablated.false_accept_rate:>5.1%} ({newly_accepted} bad now slip through)")

    return AblationRun(baseline_report=baseline, results=tuple(results))


@dataclass(frozen=True)
class AblationRun:
    baseline_report: EvaluatorReport
    results: tuple[AblationResult, ...]

    def sorted_by_importance(self) -> tuple[AblationResult, ...]:
        """Return ablation results sorted by how much FAR spikes when the
        invariant is removed (most-important first)."""
        return tuple(sorted(self.results, key=lambda r: -r.delta_far))


def print_ablation_summary(ablation: AblationRun) -> None:
    """Pretty-print the ablation results."""
    print("=" * 84)
    print("Per-invariant ablation: which invariant carries the FAR reduction?")
    print("=" * 84)
    print()
    print("If you remove an invariant from every spec and re-run, how much does the")
    print("structured validator's FAR get worse? Higher Δ FAR = more important invariant.")
    print()
    print(f"  {'ablated invariant':22s}  {'FAR with':>9s}  {'FAR w/o':>8s}  {'Δ FAR':>7s}  {'newly slipped':>14s}")
    print("  " + "-" * 70)
    for r in ablation.sorted_by_importance():
        delta_sign = "+" if r.delta_far > 0 else ""
        print(
            f"  {r.invariant_type:22s}  "
            f"{r.far_with:>9.1%}  "
            f"{r.far_without:>8.1%}  "
            f"{delta_sign}{r.delta_far:>+5.1%}  "
            f"{r.pairs_newly_accepted:>14d}"
        )
    print()
    print("'newly slipped' = candidates that were caught by the full validator but")
    print("escape rejection once this invariant is removed. Each one is a case the")
    print("invariant uniquely catches that no other invariant covers.")
    print("=" * 84)
