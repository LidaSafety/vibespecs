"""Eval runner for the task-spec validator and its baselines.

Given the corpus and three evaluators (structured / positive_only / llm_judge),
produces a results table per evaluator with the 2x2 confusion matrix:

                    Should ACCEPT          Should REJECT
    Evaluator ACCEPT   true_positive   |   false_positive (FALSE ACCEPT)
    Evaluator REJECT   false_negative  |   true_negative

The eval treats `Should ACCEPT` as "candidate label is CORRECT" and `Should
REJECT` as anything else. The headline metric is false_accept_rate — what
fraction of should-reject candidates the evaluator wrongly approves.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from safe_scaffold.task_spec.baselines import (
    Evaluator,
    LLMJudge,
    PositiveTestsOnly,
    StructuredValidator,
)
from safe_scaffold.task_spec.corpus_data import CORPUS
from safe_scaffold.task_spec.spec import (
    Candidate,
    CandidateLabel,
    TaskSpec,
    ValidatorDecision,
    Verdict,
)


@dataclass(frozen=True)
class PairOutcome:
    """One (spec, candidate, evaluator) outcome."""

    spec: TaskSpec
    candidate: Candidate
    verdict: Verdict
    elapsed_seconds: float
    # True iff this verdict was a "skipped" placeholder (e.g. LLM judge with
    # no API key). Filtered out of confusion matrices.
    skipped: bool


@dataclass(frozen=True)
class EvaluatorReport:
    """Aggregate report for one evaluator across the whole corpus."""

    evaluator_name: str
    outcomes: tuple[PairOutcome, ...]

    @property
    def total(self) -> int:
        return sum(1 for o in self.outcomes if not o.skipped)

    @property
    def true_positive(self) -> int:
        return sum(
            1 for o in self.outcomes
            if not o.skipped
            and o.candidate.label.should_accept
            and o.verdict.accepted
        )

    @property
    def false_negative(self) -> int:
        return sum(
            1 for o in self.outcomes
            if not o.skipped
            and o.candidate.label.should_accept
            and not o.verdict.accepted
        )

    @property
    def false_positive(self) -> int:
        """Security-critical: evaluator accepted something it shouldn't."""
        return sum(
            1 for o in self.outcomes
            if not o.skipped
            and not o.candidate.label.should_accept
            and o.verdict.accepted
        )

    @property
    def true_negative(self) -> int:
        return sum(
            1 for o in self.outcomes
            if not o.skipped
            and not o.candidate.label.should_accept
            and not o.verdict.accepted
        )

    @property
    def skipped_count(self) -> int:
        return sum(1 for o in self.outcomes if o.skipped)

    @property
    def false_accept_rate(self) -> float:
        denom = self.false_positive + self.true_negative
        return self.false_positive / denom if denom else 0.0

    @property
    def false_reject_rate(self) -> float:
        denom = self.true_positive + self.false_negative
        return self.false_negative / denom if denom else 0.0

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.true_positive + self.true_negative) / self.total

    @property
    def total_elapsed(self) -> float:
        return sum(o.elapsed_seconds for o in self.outcomes if not o.skipped)

    def per_label_breakdown(self) -> dict[CandidateLabel, dict[str, int]]:
        """For dashboard: per-label accept/reject counts."""
        out: dict[CandidateLabel, dict[str, int]] = {
            lbl: {"accept": 0, "reject": 0, "total": 0}
            for lbl in CandidateLabel
        }
        for o in self.outcomes:
            if o.skipped:
                continue
            key = "accept" if o.verdict.accepted else "reject"
            out[o.candidate.label][key] += 1
            out[o.candidate.label]["total"] += 1
        return out

    def per_category_breakdown(self) -> dict[str, dict[str, int]]:
        """For dashboard: per-category accuracy."""
        out: dict[str, dict[str, int]] = {}
        for o in self.outcomes:
            if o.skipped:
                continue
            cat = o.spec.category
            d = out.setdefault(cat, {"correct": 0, "wrong": 0})
            expected = o.candidate.label.should_accept
            actual = o.verdict.accepted
            if expected == actual:
                d["correct"] += 1
            else:
                d["wrong"] += 1
        return out


@dataclass(frozen=True)
class EvalRun:
    """Full eval run: a report per evaluator + the corpus it ran against."""

    corpus_size: int
    reports: tuple[EvaluatorReport, ...]
    # Spec authoring cost aggregates (from the spec's own annotations).
    total_authoring_seconds: int = 0
    total_authoring_loc: int = 0
    median_authoring_seconds: int = 0
    median_authoring_loc: int = 0

    def report_named(self, name: str) -> EvaluatorReport | None:
        for r in self.reports:
            if r.evaluator_name == name:
                return r
        return None


def run_eval(
    evaluators: list[Evaluator] | None = None,
    corpus: list | None = None,
    *,
    verbose: bool = False,
) -> EvalRun:
    """Run every evaluator against every (spec, candidate) in the corpus."""
    if evaluators is None:
        evaluators = [StructuredValidator(), PositiveTestsOnly(), LLMJudge()]
    if corpus is None:
        corpus = list(CORPUS)

    reports: list[EvaluatorReport] = []
    all_specs: list[TaskSpec] = []

    for evaluator in evaluators:
        outcomes: list[PairOutcome] = []
        for spec, candidates in corpus:
            if spec not in all_specs:
                all_specs.append(spec)
            for cand in candidates:
                t0 = time.perf_counter()
                verdict = evaluator.evaluate(spec, cand)
                elapsed = time.perf_counter() - t0
                # Detect SKIPPED via inv result details prefix.
                skipped = any(
                    r.details.startswith("SKIPPED")
                    for r in verdict.invariant_results
                )
                outcomes.append(PairOutcome(
                    spec=spec,
                    candidate=cand,
                    verdict=verdict,
                    elapsed_seconds=elapsed,
                    skipped=skipped,
                ))
                if verbose:
                    sym = "S" if skipped else ("✓" if verdict.accepted == cand.label.should_accept else "✗")
                    print(f"  [{evaluator.name}] {sym} {spec.task_id}/{cand.candidate_id}: {verdict.decision.value}")
        reports.append(EvaluatorReport(evaluator_name=evaluator.name, outcomes=tuple(outcomes)))

    # Authoring cost aggregates.
    authoring_secs = [s.authoring_seconds for s in all_specs if s.authoring_seconds]
    authoring_loc = [s.authoring_loc for s in all_specs if s.authoring_loc]
    total_secs = sum(authoring_secs)
    total_loc = sum(authoring_loc)
    median_secs = sorted(authoring_secs)[len(authoring_secs) // 2] if authoring_secs else 0
    median_loc = sorted(authoring_loc)[len(authoring_loc) // 2] if authoring_loc else 0

    corpus_size = sum(len(cands) for _, cands in corpus)
    return EvalRun(
        corpus_size=corpus_size,
        reports=tuple(reports),
        total_authoring_seconds=total_secs,
        total_authoring_loc=total_loc,
        median_authoring_seconds=median_secs,
        median_authoring_loc=median_loc,
    )


def print_summary(run: EvalRun) -> None:
    """Compact text summary to stdout."""
    print("=" * 72)
    print(f"Task-spec evaluation report ({run.corpus_size} (task, candidate) pairs)")
    print("=" * 72)
    print()
    print(f"Spec authoring cost (across {run.total_authoring_loc and 'all' or 'no'} annotated specs):")
    print(f"  total time:       {run.total_authoring_seconds} seconds")
    print(f"  median time/spec: {run.median_authoring_seconds} seconds")
    print(f"  total LOC:        {run.total_authoring_loc} lines")
    print(f"  median LOC/spec:  {run.median_authoring_loc} lines")
    print()
    print(f"{'evaluator':18s}  {'acc':>5s}  {'FAR':>5s}  {'FRR':>5s}  {'TP':>3s}  {'FN':>3s}  {'FP':>3s}  {'TN':>3s}  {'skip':>4s}  {'time(s)':>8s}")
    print("-" * 72)
    for r in run.reports:
        print(
            f"{r.evaluator_name:18s}  "
            f"{r.accuracy:>5.1%}  "
            f"{r.false_accept_rate:>5.1%}  "
            f"{r.false_reject_rate:>5.1%}  "
            f"{r.true_positive:>3d}  "
            f"{r.false_negative:>3d}  "
            f"{r.false_positive:>3d}  "
            f"{r.true_negative:>3d}  "
            f"{r.skipped_count:>4d}  "
            f"{r.total_elapsed:>8.2f}"
        )
    print()
    print("Legend: acc=accuracy, FAR=false accept rate (security-critical),")
    print("        FRR=false reject rate, skip=evaluator-skipped pairs (e.g. no API key).")
    print("=" * 72)
