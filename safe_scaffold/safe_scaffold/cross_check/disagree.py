"""Find disagreements between a reference and a candidate implementation.

Given a list of TestCases, run each against both implementations and report
every case where the outputs differ. This is the heart of cross-checking:
it doesn't tell us which implementation is right — the spec author must
arbitrate — but it tells us EXACTLY where the disagreement is and gives a
concrete reproducer.

For the Cryspen reproduction, the reference is the FIPS 203 formula and the
candidate is the pre-fix libcrux version. The first disagreement reproduces
the bug; collecting all disagreements quantifies the impact ("the spec
disagreed with the reference on N out of M inputs").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from safe_scaffold.cross_check.runner import TestCase, TestResult, run_test_case


@dataclass(frozen=True)
class Disagreement:
    """A single test case where reference and candidate diverged."""

    case: TestCase
    reference_result: TestResult
    candidate_result: TestResult

    def describe(self) -> str:
        ref_repr = (
            f"output={self.reference_result.output!r}"
            if self.reference_result.success
            else f"raised {self.reference_result.exception}"
        )
        cand_repr = (
            f"output={self.candidate_result.output!r}"
            if self.candidate_result.success
            else f"raised {self.candidate_result.exception}"
        )
        return (
            f"{self.case.function}({', '.join(repr(i) for i in self.case.inputs)}): "
            f"reference {ref_repr}, candidate {cand_repr}"
        )


@dataclass(frozen=True)
class DisagreementReport:
    """Aggregate report from a cross-check run."""

    total: int
    disagreements: tuple[Disagreement, ...]

    @property
    def agreement_count(self) -> int:
        return self.total - len(self.disagreements)

    @property
    def disagreement_rate(self) -> float:
        return len(self.disagreements) / self.total if self.total else 0.0

    def summary(self) -> str:
        lines = [
            f"Cross-check summary: {self.agreement_count}/{self.total} agreed "
            f"({self.disagreement_rate:.1%} disagreement)."
        ]
        if self.disagreements:
            lines.append("First 5 disagreements:")
            for d in self.disagreements[:5]:
                lines.append(f"  • {d.describe()}")
        return "\n".join(lines)


def find_disagreements(
    cases: Iterable[TestCase],
    *,
    reference: Callable[..., Any],
    candidate: Callable[..., Any],
) -> DisagreementReport:
    """Run each case against both impls; collect disagreements."""
    disagreements: list[Disagreement] = []
    total = 0
    for case in cases:
        total += 1
        ref = run_test_case(case, reference)
        cand = run_test_case(case, candidate)
        if _agrees(ref, cand):
            continue
        disagreements.append(Disagreement(case=case, reference_result=ref, candidate_result=cand))
    return DisagreementReport(total=total, disagreements=tuple(disagreements))


def _agrees(a: TestResult, b: TestResult) -> bool:
    if a.success != b.success:
        return False
    if not a.success:
        # Both raised; we treat that as agreement IFF same exception repr.
        return a.exception == b.exception
    return a.output == b.output
