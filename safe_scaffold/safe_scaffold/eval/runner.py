"""Eval runner: compute block-rate, false-positive rate, and fall-through rate.

Given a Policy and a corpus of EvalEntry, returns metrics:

- `block_rate`: of the red-team entries, what fraction did the policy
  correctly block (DENY or UNKNOWN — both block at the gate)?
- `false_allow_rate`: of red-team entries, what fraction did the policy
  silently ALLOW? This is the security-critical false-negative rate.
- `false_deny_rate`: of benign entries, what fraction did the policy
  incorrectly DENY? Affects user experience.
- `unknown_rate`: across both corpora, what fraction fell through to the
  fail-closed default? Low coverage indicates a sparse policy.

The runner is deliberately simple: it loops, calls the verifier, tallies.
The interesting work is the corpus design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from safe_scaffold.eval.redteam import EvalEntry
from safe_scaffold.policy import Policy
from safe_scaffold.verifier import Decision, verify
from safe_scaffold.world import Action


@dataclass(frozen=True)
class EvalMetrics:
    """Aggregate metrics from an eval run."""

    redteam_total: int
    redteam_denied: int
    redteam_allowed: int  # false negatives — the bad number
    redteam_unknown: int
    benign_total: int
    benign_allowed: int
    benign_denied: int  # false positives
    benign_unknown: int
    # Per-entry breakdown for reports.
    redteam_details: tuple[tuple[EvalEntry, Decision, bool], ...]
    benign_details: tuple[tuple[EvalEntry, Decision, bool], ...]

    @property
    def block_rate(self) -> float:
        """Fraction of red-team entries the policy correctly handled (DENY or
        UNKNOWN — both block at the runtime gate). Equivalently: 1 minus the
        false_allow_rate."""
        passing = self.redteam_denied + self.redteam_unknown
        return passing / self.redteam_total if self.redteam_total else 0.0

    @property
    def false_allow_rate(self) -> float:
        """Fraction of red-team entries the policy silently ALLOWED. The
        security-critical number — every percentage point here is a failure."""
        return self.redteam_allowed / self.redteam_total if self.redteam_total else 0.0

    @property
    def benign_pass_rate(self) -> float:
        """Fraction of benign entries the policy did NOT incorrectly deny.
        Equivalently: 1 minus the false_deny_rate."""
        passing = self.benign_allowed + self.benign_unknown
        return passing / self.benign_total if self.benign_total else 0.0

    @property
    def false_deny_rate(self) -> float:
        return self.benign_denied / self.benign_total if self.benign_total else 0.0

    def report(self) -> str:
        lines = [
            "=" * 72,
            "Eval report",
            "=" * 72,
            f"  Red-team corpus: {self.redteam_total} entries",
            f"    denied:   {self.redteam_denied} "
            f"({self.redteam_denied / max(self.redteam_total, 1):.1%})  -- matched DENY rule",
            f"    unknown:  {self.redteam_unknown} "
            f"({self.redteam_unknown / max(self.redteam_total, 1):.1%})  -- no rule matched (gate blocks)",
            f"    allowed:  {self.redteam_allowed} "
            f"({self.false_allow_rate:.1%})  -- FALSE NEGATIVES",
            "",
            f"  Benign corpus:  {self.benign_total} entries",
            f"    allowed:  {self.benign_allowed} "
            f"({self.benign_allowed / max(self.benign_total, 1):.1%})  -- matched ALLOW rule",
            f"    unknown:  {self.benign_unknown} "
            f"({self.benign_unknown / max(self.benign_total, 1):.1%})  -- no rule matched (blocked, but policy could be extended)",
            f"    denied:   {self.benign_denied} "
            f"({self.false_deny_rate:.1%})  -- FALSE POSITIVES",
            "",
            f"  Block rate (red-team denied or fail-closed):  {self.block_rate:.1%}",
            f"  Benign pass rate (allowed or fail-closed):    {self.benign_pass_rate:.1%}",
            "=" * 72,
        ]
        # If there are false negatives or false positives, list them.
        bad_red = [
            (e, d) for e, d, ok in self.redteam_details
            if not ok and d is Decision.ALLOW
        ]
        if bad_red:
            lines.append("\nFALSE NEGATIVES (red-team actions the policy allowed):")
            for e, d in bad_red:
                lines.append(f"  • {e.label}: expected {e.expected}, got {d.value}")
        bad_benign = [
            (e, d) for e, d, ok in self.benign_details
            if not ok and d is Decision.DENY
        ]
        if bad_benign:
            lines.append("\nFALSE POSITIVES (benign actions the policy denied):")
            for e, d in bad_benign:
                lines.append(f"  • {e.label}: expected {e.expected}, got {d.value}")
        return "\n".join(lines)


def run_eval(
    policy: Policy,
    redteam: Iterable[EvalEntry],
    benign: Iterable[EvalEntry],
) -> EvalMetrics:
    """Run the policy against both corpora; return aggregated metrics."""
    redteam_list = list(redteam)
    benign_list = list(benign)

    def _run(entries: list[EvalEntry]) -> tuple[
        int, int, int, tuple[tuple[EvalEntry, Decision, bool], ...]
    ]:
        details: list[tuple[EvalEntry, Decision, bool]] = []
        denied = allowed = unknown = 0
        for e in entries:
            action = Action.from_dict(e.action_dict)
            v = verify(action, policy)
            ok = v.decision.value == e.expected
            details.append((e, v.decision, ok))
            if v.decision is Decision.DENY:
                denied += 1
            elif v.decision is Decision.ALLOW:
                allowed += 1
            else:
                unknown += 1
        return denied, allowed, unknown, tuple(details)

    r_denied, r_allowed, r_unknown, r_details = _run(redteam_list)
    b_denied, b_allowed, b_unknown, b_details = _run(benign_list)

    return EvalMetrics(
        redteam_total=len(redteam_list),
        redteam_denied=r_denied,
        redteam_allowed=r_allowed,
        redteam_unknown=r_unknown,
        benign_total=len(benign_list),
        benign_allowed=b_allowed,
        benign_denied=b_denied,
        benign_unknown=b_unknown,
        redteam_details=r_details,
        benign_details=b_details,
    )
