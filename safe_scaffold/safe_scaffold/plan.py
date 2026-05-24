"""Multi-action plan verification.

The single-action verifier in `verifier.py` answers "is THIS action allowed?".
Agents typically have plans: a sequence of actions that achieve a goal. A
plan-level question is "is this whole sequence safe?", which is strictly
stronger because:

1. Each action might be individually fine but the SEQUENCE might be harmful
   (write a key to /tmp, then read /tmp, then exfiltrate over network).
2. The operator may want a single pass/fail verdict on a whole plan rather
   than per-action gating in a tight loop.

This module provides:

- `PlanVerdict`: a per-action breakdown plus an aggregate decision.
- `verify_plan(actions, policy)`: returns a PlanVerdict.
- `find_unsafe_pair(plan, predicate)`: spot a forbidden combination
  ("any plan that writes credentials then makes an external request").

The aggregation rule is: ALLOW iff every action is ALLOW. DENY if any action
is DENY. UNKNOWN if no DENY but at least one UNKNOWN — and the index of the
first UNKNOWN action is surfaced so the operator can see where the policy
needs extending.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from safe_scaffold.policy import Policy
from safe_scaffold.verifier import Decision, Verdict, verify
from safe_scaffold.world import Action


@dataclass(frozen=True)
class PlanVerdict:
    """Outcome of verifying a plan."""

    decision: Decision
    per_action: tuple[Verdict, ...]
    # Index of the first action that the policy did not ALLOW (i.e. UNKNOWN
    # or DENY). None if the entire plan was ALLOW. Useful for surfacing the
    # exact step where the operator needs to extend the policy.
    first_unresolved_index: int | None

    @property
    def deny_indices(self) -> tuple[int, ...]:
        return tuple(i for i, v in enumerate(self.per_action) if v.decision is Decision.DENY)

    @property
    def unknown_indices(self) -> tuple[int, ...]:
        return tuple(i for i, v in enumerate(self.per_action) if v.decision is Decision.UNKNOWN)


def verify_plan(actions: list[Action] | tuple[Action, ...], policy: Policy) -> PlanVerdict:
    """Verify each action in order. Aggregate per deny-overrides + leftmost-first."""
    verdicts = tuple(verify(a, policy) for a in actions)

    deny_idx = next(
        (i for i, v in enumerate(verdicts) if v.decision is Decision.DENY),
        None,
    )
    if deny_idx is not None:
        return PlanVerdict(
            decision=Decision.DENY,
            per_action=verdicts,
            first_unresolved_index=deny_idx,
        )
    unknown_idx = next(
        (i for i, v in enumerate(verdicts) if v.decision is Decision.UNKNOWN),
        None,
    )
    if unknown_idx is not None:
        return PlanVerdict(
            decision=Decision.UNKNOWN,
            per_action=verdicts,
            first_unresolved_index=unknown_idx,
        )
    return PlanVerdict(decision=Decision.ALLOW, per_action=verdicts, first_unresolved_index=None)


# ---------------------------------------------------------------------------
# Trace-level patterns
# ---------------------------------------------------------------------------


def find_unsafe_pair(
    plan: list[Action] | tuple[Action, ...],
    earlier: Callable[[Action], bool],
    later: Callable[[Action], bool],
) -> tuple[int, int] | None:
    """Find (i, j) with i < j where `earlier(plan[i])` and `later(plan[j])` both hold.

    Useful for "wrote credentials to disk, then made a network request" style
    flow checks. The caller supplies the two predicates as plain Python
    callables — these aren't part of the policy DSL because they reason about
    the action history, not the action alone. Future work: lift these to a
    proper temporal-logic spec language and verify by model checking.
    """
    for i, ai in enumerate(plan):
        if not earlier(ai):
            continue
        for j in range(i + 1, len(plan)):
            if later(plan[j]):
                return i, j
    return None


def looks_like_credential_write(action: Action) -> bool:
    """Heuristic: does this look like writing a credential to disk?"""
    from safe_scaffold.world import FileWrite

    if not isinstance(action, FileWrite):
        return False
    p = action.path.lower()
    suspicious = (
        "/.aws/credentials",
        "/.ssh/id_",
        "/.netrc",
        "/.git-credentials",
        "/credentials",
        "/secret",
        "/token",
        ".env",
    )
    return any(s in p for s in suspicious)


def looks_like_external_network(action: Action) -> bool:
    """Heuristic: outbound network to a non-loopback host."""
    from safe_scaffold.world import NetworkRequest

    if not isinstance(action, NetworkRequest):
        return False
    host = action.host
    return host not in {"localhost", "127.0.0.1", "::1"} and not host.startswith("10.")
