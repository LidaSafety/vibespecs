"""Verifier: turn (Action, Policy) into a Decision.

Decision logic (deny-overrides):

    matching = [r for r in policy.rules if r matches action]
    if any matching r is DENY:    DENY
    elif any matching r is ALLOW: ALLOW
    else:                          UNKNOWN

A `Verdict` is the rich return type: the bare decision plus the list of rules
that fired, so the caller (the agent gate, the audit log) can show *why*.

At the runtime gate, callers should treat UNKNOWN as a block (fail closed) —
the policy has no opinion, and "no opinion" must not become "no objection."
The Z3 backend (safe_scaffold.properties) reasons separately about which
ALLOW/DENY *patterns* of action the policy can express, independent of any
particular action.

This module is intentionally tiny. The interesting bits live in `policy` and
`conditions`. Keeping `verify()` to a one-screen function makes it easy to
audit the trust boundary: this is the single place where an action is
admitted or refused.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from safe_scaffold.policy import Effect, Policy, Rule
from safe_scaffold.world import Action


class Decision(enum.Enum):
    """The three possible outcomes for an action."""

    ALLOW = "allow"
    DENY = "deny"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Verdict:
    """Rich verdict: decision plus the rules that justified it."""

    decision: Decision
    matched_rules: tuple[Rule, ...]
    action: Action

    @property
    def deny_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.matched_rules if r.effect is Effect.DENY)

    @property
    def allow_rules(self) -> tuple[Rule, ...]:
        return tuple(r for r in self.matched_rules if r.effect is Effect.ALLOW)

    def explain(self) -> str:
        """Human-readable explanation for prompts and logs."""
        if self.decision is Decision.DENY:
            lines = [f"DENY action {self.action.kind} (id={self.action.id})."]
            for r in self.deny_rules:
                lines.append(f"  matched DENY rule {r.id!r}: {r.description}")
            return "\n".join(lines)
        if self.decision is Decision.ALLOW:
            lines = [f"ALLOW action {self.action.kind} (id={self.action.id})."]
            for r in self.allow_rules:
                lines.append(f"  matched ALLOW rule {r.id!r}: {r.description}")
            return "\n".join(lines)
        return (
            f"UNKNOWN: no policy rule matched action {self.action.kind} "
            f"(id={self.action.id}). At the runtime gate this fails closed "
            f"(action is blocked); operator should extend the policy if "
            f"this action should be permitted."
        )


def verify(action: Action, policy: Policy) -> Verdict:
    """Verify a single action against a policy. Deny-overrides semantics."""
    matched = policy.matching(action)
    if not matched:
        return Verdict(Decision.UNKNOWN, (), action)
    if any(r.effect is Effect.DENY for r in matched):
        return Verdict(Decision.DENY, matched, action)
    return Verdict(Decision.ALLOW, matched, action)
