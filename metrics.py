"""Statistical metrics for task-spec evaluation.

Each metric here is implemented from first principles (stdlib only) so the
results are reproducible without numpy/scipy. The four metrics:

1. `discriminative_power` — direct port of nl2postcond's metric (Endres et
   al., FSE 2024). Fraction of (correct, bad) candidate pairs within the
   same task where the evaluator accepts the correct one and rejects the
   bad one. This is the apples-to-apples comparison metric for spec
   validators in the existing literature.

2. `cohen_kappa` — Cohen's κ between an evaluator's verdicts and the
   ground-truth labels. Better than raw accuracy because it corrects for
   agreement-by-chance. None of the prior work in this space reports κ,
   which makes "structured vs positive-only is 47.5 pts better" feel
   anecdotal; reporting κ pins the comparison down.

3. `per_invariant_precision_recall` — for each invariant type in a
   structured validator, how often did it fire? When it fired, was the
   ground-truth label actually one it should have caught? This is the
   ablation-lite that tells us *which* invariant does the work.

4. `authoring_cost_ratio` — minutes of spec authoring per percentage
   point of FAR reduction over the positive-tests-only baseline. A
   defensible "spec ROI" number that directly addresses Lahiri 2026's
   research-agenda item 3 ("identifying what to clarify cost-effectively").
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from safe_scaffold.task_spec.eval import EvaluatorReport, EvalRun
from safe_scaffold.task_spec.spec import CandidateLabel


# ---------------------------------------------------------------------------
# 1. Discriminative power (nl2postcond's metric)
# ---------------------------------------------------------------------------


def discriminative_power(report: EvaluatorReport) -> float:
    """Fraction of (correct, bad) candidate pairs within the same task that
    the evaluator distinguishes correctly.

    A pair (c, b) where c.label = CORRECT and b.label != CORRECT is
    *distinguished* iff the evaluator accepts c and rejects b. This is
    exactly the metric from Endres et al. (FSE 2024) §4.1.2, applied to
    diff-level invariants instead of method-level postconditions.

    For each task, with 1 correct + 3 bad candidates, there are 3 pairs.
    Across 10 tasks that's 30 pairs total. Discriminative power = (pairs
    distinguished) / (pairs total).

    Higher is better. Random verdicts give 0.25 (P(accept) * P(reject))
    in the worst case; an all-reject evaluator gives 0; a perfect
    evaluator gives 1.
    """
    # Index outcomes by task.
    by_task: dict[str, list] = {}
    for o in report.outcomes:
        if o.skipped:
            continue
        by_task.setdefault(o.spec.task_id, []).append(o)

    distinguished = 0
    total = 0
    for task_outcomes in by_task.values():
        correct_outcomes = [o for o in task_outcomes if o.candidate.label is CandidateLabel.CORRECT]
        bad_outcomes = [o for o in task_outcomes if o.candidate.label is not CandidateLabel.CORRECT]
        for c in correct_outcomes:
            for b in bad_outcomes:
                total += 1
                if c.verdict.accepted and not b.verdict.accepted:
                    distinguished += 1
    return distinguished / total if total else 0.0


# ---------------------------------------------------------------------------
# 2. Cohen's kappa
# ---------------------------------------------------------------------------


def cohen_kappa(report: EvaluatorReport) -> float:
    """Cohen's κ between evaluator verdicts and ground-truth labels.

    Treats the problem as binary classification: evaluator says ACCEPT
    or REJECT; ground truth says CORRECT (positive class) or other
    (negative class).

        po = observed agreement
        pe = expected agreement by chance
        κ  = (po - pe) / (1 - pe)

    κ = 1.0 is perfect; κ = 0 is chance-level; κ < 0 is worse than chance.
    Landis-Koch (1977) guidance: 0.41-0.60 = moderate, 0.61-0.80 =
    substantial, 0.81-1.00 = almost perfect.

    None of the prior work in spec-elicitation reports this. Doing so
    here makes the structured-vs-positive-only comparison feel less
    cherry-picked.
    """
    eval_yes_truth_yes = 0  # TP
    eval_yes_truth_no = 0  # FP
    eval_no_truth_yes = 0  # FN
    eval_no_truth_no = 0  # TN

    for o in report.outcomes:
        if o.skipped:
            continue
        truth_pos = o.candidate.label.should_accept
        eval_pos = o.verdict.accepted
        if truth_pos and eval_pos:
            eval_yes_truth_yes += 1
        elif truth_pos and not eval_pos:
            eval_no_truth_yes += 1
        elif not truth_pos and eval_pos:
            eval_yes_truth_no += 1
        else:
            eval_no_truth_no += 1

    n = eval_yes_truth_yes + eval_yes_truth_no + eval_no_truth_yes + eval_no_truth_no
    if n == 0:
        return 0.0

    po = (eval_yes_truth_yes + eval_no_truth_no) / n

    # Expected agreement under independence of marginals
    eval_yes_marginal = (eval_yes_truth_yes + eval_yes_truth_no) / n
    truth_yes_marginal = (eval_yes_truth_yes + eval_no_truth_yes) / n
    pe = (eval_yes_marginal * truth_yes_marginal
          + (1 - eval_yes_marginal) * (1 - truth_yes_marginal))

    if pe >= 1.0:
        # Degenerate (everyone agreed on the same label).
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


# ---------------------------------------------------------------------------
# 3. Per-invariant precision/recall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvariantStats:
    """For one invariant type, across one corpus run."""

    invariant_name: str
    times_fired: int  # how many candidates this invariant rejected
    times_fired_correctly: int  # of those, how many were should-reject
    times_should_have_fired: int  # candidates that were bad AND this
                                  # invariant would in principle apply

    @property
    def precision(self) -> float:
        """When this invariant fires, how often is the candidate actually bad?

        High precision = this invariant doesn't false-alarm on correct code.
        """
        return self.times_fired_correctly / self.times_fired if self.times_fired else 0.0

    @property
    def recall(self) -> float:
        """Of all bad candidates this invariant could in principle catch,
        what fraction did it catch?

        Note: recall here is computed over *all* bad candidates the
        invariant *type* might apply to (i.e. across all tasks where the
        invariant was attached). It's not strictly "all bad outputs the
        invariant could catch" because that would require knowing the
        ground truth of each invariant for each candidate. Treat this
        as a useful lower bound, not a tight measure.
        """
        return self.times_fired_correctly / self.times_should_have_fired if self.times_should_have_fired else 0.0


def per_invariant_precision_recall(report: EvaluatorReport) -> dict[str, InvariantStats]:
    """For each invariant type used by the structured validator, report
    precision and recall on the corpus.

    Only meaningful for the structured validator — other evaluators don't
    report per-invariant outcomes.
    """
    # Aggregate counts per invariant *type* (strip parametrization in name).
    # E.g. "OnlyFilesModified(['mymath.py'])" → "OnlyFilesModified".
    def normalize(name: str) -> str:
        return name.split("(")[0]

    fired: Counter[str] = Counter()
    fired_correctly: Counter[str] = Counter()
    eligible_bad: Counter[str] = Counter()

    for o in report.outcomes:
        if o.skipped:
            continue
        bad = not o.candidate.label.should_accept
        # Which invariants applied to this (spec, candidate)?
        invariants_applied = {normalize(r.invariant_name) for r in o.verdict.invariant_results}
        for r in o.verdict.invariant_results:
            inv_type = normalize(r.invariant_name)
            if not r.holds:
                fired[inv_type] += 1
                if bad:
                    fired_correctly[inv_type] += 1
        if bad:
            for inv_type in invariants_applied:
                eligible_bad[inv_type] += 1

    out: dict[str, InvariantStats] = {}
    for name in set(fired) | set(eligible_bad):
        out[name] = InvariantStats(
            invariant_name=name,
            times_fired=fired[name],
            times_fired_correctly=fired_correctly[name],
            times_should_have_fired=eligible_bad[name],
        )
    return out


# ---------------------------------------------------------------------------
# 4. Authoring-cost / FAR-reduction ratio
# ---------------------------------------------------------------------------


def authoring_cost_per_far_reduction(
    structured: EvaluatorReport,
    baseline: EvaluatorReport,
    total_authoring_seconds: int,
) -> float:
    """Seconds of spec authoring per percentage point of FAR reduction.

    Lower is better — less authoring cost per unit of safety.

    Formula:
        far_reduction_pct = (baseline_FAR - structured_FAR) * 100
        ratio = total_authoring_seconds / far_reduction_pct

    Returns +inf if structured doesn't reduce FAR at all (denominator <= 0).

    This metric explicitly addresses Lahiri 2026's research-agenda item 3:
    "identifying what to clarify cost-effectively." It's a single number
    that says: was the spec-authoring effort worth it? If a 1500-second
    authoring budget gives you a 60-point FAR reduction, that's 25
    seconds per percentage point. Defensible as "spec ROI."
    """
    far_reduction_pct = (baseline.false_accept_rate - structured.false_accept_rate) * 100.0
    if far_reduction_pct <= 0:
        return math.inf
    return total_authoring_seconds / far_reduction_pct


# ---------------------------------------------------------------------------
# Glue: an aggregate report combining all four metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RigorousReport:
    """All four metrics for one evaluator, plus its base confusion-matrix
    stats. The dashboard renders this directly."""

    evaluator_name: str
    discriminative_power: float
    cohen_kappa: float
    accuracy: float
    false_accept_rate: float
    false_reject_rate: float
    per_invariant: dict[str, InvariantStats]


def build_rigorous_report(report: EvaluatorReport) -> RigorousReport:
    return RigorousReport(
        evaluator_name=report.evaluator_name,
        discriminative_power=discriminative_power(report),
        cohen_kappa=cohen_kappa(report),
        accuracy=report.accuracy,
        false_accept_rate=report.false_accept_rate,
        false_reject_rate=report.false_reject_rate,
        per_invariant=per_invariant_precision_recall(report),
    )


def print_rigorous_summary(run: EvalRun, baseline_name: str = "positive_only") -> None:
    """Pretty-print all metrics across all evaluators in the run."""
    baseline = run.report_named(baseline_name)
    print("=" * 92)
    print(f"Rigorous metrics summary ({run.corpus_size} pairs)")
    print("=" * 92)
    print()
    header = f"{'evaluator':18s}  {'acc':>5s}  {'FAR':>5s}  {'FRR':>5s}  {'disc.pow':>8s}  {'kappa':>6s}"
    if baseline is not None:
        header += f"  {'sec/Δ%FAR':>10s}"
    print(header)
    print("-" * 92)
    for r in run.reports:
        rr = build_rigorous_report(r)
        line = (
            f"{rr.evaluator_name:18s}  "
            f"{rr.accuracy:>5.1%}  "
            f"{rr.false_accept_rate:>5.1%}  "
            f"{rr.false_reject_rate:>5.1%}  "
            f"{rr.discriminative_power:>8.1%}  "
            f"{rr.cohen_kappa:>6.3f}"
        )
        if baseline is not None and r.evaluator_name != baseline_name:
            ratio = authoring_cost_per_far_reduction(
                structured=r,
                baseline=baseline,
                total_authoring_seconds=run.total_authoring_seconds,
            )
            line += f"  {ratio:>10.1f}" if math.isfinite(ratio) else f"  {'inf':>10s}"
        elif baseline is not None:
            line += f"  {'(base)':>10s}"
        print(line)
    print()

    # Per-invariant breakdown for the structured evaluator only.
    structured = run.report_named("structured")
    if structured is not None:
        per_inv = per_invariant_precision_recall(structured)
        print("Per-invariant precision/recall (structured evaluator):")
        print(f"  {'invariant':25s}  {'fired':>5s}  {'correct':>7s}  {'P':>5s}  {'R':>5s}")
        print("  " + "-" * 60)
        for name in sorted(per_inv):
            s = per_inv[name]
            print(
                f"  {name:25s}  "
                f"{s.times_fired:>5d}  "
                f"{s.times_fired_correctly:>7d}  "
                f"{s.precision:>5.1%}  "
                f"{s.recall:>5.1%}"
            )
    print("=" * 92)
