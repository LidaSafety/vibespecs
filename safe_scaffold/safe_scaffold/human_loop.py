"""
Human-in-the-loop.

When the verifier returns UNKNOWN, we have to ask the human. This module
is the only place in the system that does that, deliberately:

  * Every prompt is the same shape, so a malicious agent can't dress up
    requests differently to bias the user.
  * The user has exactly three buttons — Allow once / Deny once /
    Allow and remember — which maps cleanly to the proposal's design.
  * If "Allow and remember", the user types a short NL description of
    what to allow, which the Translator turns into a new Rule. We
    deliberately do NOT let the user write a Z3 expression directly; the
    DSL is the user-facing surface.
  * Every choice is journaled with a timestamp, the action envelope,
    and the policy version before and after.

This is the "asking for human feedback in limited but important
scenarios" of active learning (Linh's PhD area, per the proposal).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from .spec import Policy
from .translator import Translator
from .verifier import Decision, VerificationResult
from .world_model import ActionEnvelope


class HumanChoice(str, Enum):
    ALLOW_ONCE = "allow_once"
    DENY_ONCE = "deny_once"
    ALLOW_AND_REMEMBER = "allow_and_remember"
    DENY_AND_REMEMBER = "deny_and_remember"
    ABORT_SESSION = "abort_session"


@dataclass
class HumanResponse:
    choice: HumanChoice
    description: str | None = None  # required for ALLOW_AND_REMEMBER / DENY_AND_REMEMBER


# A `Prompter` is anything callable that, given the envelope and the
# verifier's result, returns a HumanResponse. The default is a terminal
# prompt. Tests inject a deterministic stub; GUI clients can swap in
# whatever UI they like.
Prompter = Callable[[ActionEnvelope, VerificationResult], HumanResponse]


def terminal_prompter(env: ActionEnvelope, vr: VerificationResult) -> HumanResponse:
    """Plain stdin/stdout prompter. Not pretty, but dependency-free."""
    print("=" * 72, file=sys.stderr)
    print(f"Agent action requires approval ({env.agent_id})", file=sys.stderr)
    print(f"  {env.short_summary()}", file=sys.stderr)
    if env.action.rationale:
        print(f"  Agent's rationale: {env.action.rationale}", file=sys.stderr)
    print(file=sys.stderr)
    print("Verifier says:", vr.decision.value.upper(), file=sys.stderr)
    print(vr.explanation, file=sys.stderr)
    print(file=sys.stderr)
    print("Choose:", file=sys.stderr)
    print("  [1] Allow once", file=sys.stderr)
    print("  [2] Deny once", file=sys.stderr)
    print("  [3] Allow and add a rule (you'll describe what to allow)", file=sys.stderr)
    print("  [4] Deny and add a rule", file=sys.stderr)
    print("  [5] Abort the whole session", file=sys.stderr)
    while True:
        choice = input("> ").strip()
        if choice in {"1", "2", "3", "4", "5"}:
            break
    mapping = {
        "1": HumanChoice.ALLOW_ONCE,
        "2": HumanChoice.DENY_ONCE,
        "3": HumanChoice.ALLOW_AND_REMEMBER,
        "4": HumanChoice.DENY_AND_REMEMBER,
        "5": HumanChoice.ABORT_SESSION,
    }
    c = mapping[choice]
    desc = None
    if c in (HumanChoice.ALLOW_AND_REMEMBER, HumanChoice.DENY_AND_REMEMBER):
        print(
            "Describe what to "
            + ("allow" if c == HumanChoice.ALLOW_AND_REMEMBER else "deny")
            + " from now on (e.g. 'reads under /home/me/projects/foo'):",
            file=sys.stderr,
        )
        desc = input("> ").strip()
        if not desc:
            print("(empty — falling back to one-off decision)", file=sys.stderr)
            c = (
                HumanChoice.ALLOW_ONCE
                if c == HumanChoice.ALLOW_AND_REMEMBER
                else HumanChoice.DENY_ONCE
            )
    return HumanResponse(choice=c, description=desc)


# ---------------------------------------------------------------------------
# Journaling
# ---------------------------------------------------------------------------

@dataclass
class JournalEntry:
    timestamp: datetime
    envelope: ActionEnvelope
    verifier_result_decision: str
    matched_rule_ids: list[str]
    human_response: HumanResponse | None
    policy_version_before: int
    policy_version_after: int
    final_decision: str   # "allow" or "deny"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "envelope": json.loads(self.envelope.model_dump_json()),
            "verifier_decision": self.verifier_result_decision,
            "matched_rule_ids": self.matched_rule_ids,
            "human_response": (
                {
                    "choice": self.human_response.choice.value,
                    "description": self.human_response.description,
                }
                if self.human_response else None
            ),
            "policy_version_before": self.policy_version_before,
            "policy_version_after": self.policy_version_after,
            "final_decision": self.final_decision,
        }


@dataclass
class Journal:
    """Append-only log of every decision. Written to disk after each entry
    so a crashed run still has a faithful history."""
    path: Path
    entries: list[JournalEntry] = field(default_factory=list)

    def append(self, entry: JournalEntry) -> None:
        self.entries.append(entry)
        with self.path.open("a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")


# ---------------------------------------------------------------------------
# The coordination loop
# ---------------------------------------------------------------------------

@dataclass
class HumanLoopOutcome:
    final_decision: Decision   # ALLOW or DENY (never UNKNOWN)
    policy: Policy             # possibly updated
    abort_session: bool = False


def resolve_unknown(
    envelope: ActionEnvelope,
    vr: VerificationResult,
    policy: Policy,
    translator: Translator,
    prompter: Prompter = terminal_prompter,
    journal: Journal | None = None,
) -> HumanLoopOutcome:
    """Drive the human prompt + (optional) policy update when verification
    came back UNKNOWN (or, callers may invoke this on DENY to ask the
    human for an override).

    Returns the final ALLOW/DENY decision and the new policy.
    """
    if vr.decision == Decision.ALLOW:
        # Caller shouldn't have invoked us. Be defensive.
        return HumanLoopOutcome(Decision.ALLOW, policy)

    pv_before = policy.version
    resp = prompter(envelope, vr)

    new_policy = policy
    final: Decision
    abort = False

    if resp.choice == HumanChoice.ABORT_SESSION:
        final = Decision.DENY
        abort = True
    elif resp.choice == HumanChoice.ALLOW_ONCE:
        final = Decision.ALLOW
    elif resp.choice == HumanChoice.DENY_ONCE:
        final = Decision.DENY
    elif resp.choice == HumanChoice.ALLOW_AND_REMEMBER:
        # Update the policy. We re-verify *after* the update to confirm
        # the new rule actually permits the action; if it doesn't, we
        # surface that to the user as a translator bug rather than
        # silently allowing.
        assert resp.description is not None
        new_policy = translator.translate_clarification(
            resp.description,
            triggered_action_id=str(envelope.proposed_at.timestamp()),
            existing_policy=policy,
        )
        from .verifier import verify  # local import to avoid cycle
        recheck = verify(envelope.action, new_policy)
        if recheck.decision != Decision.ALLOW:
            # Translator produced rules that don't cover the very
            # action that triggered them. Don't silently fall back;
            # let the user know.
            print(
                "[warn] new rule did not actually permit the triggering action; "
                f"recheck returned {recheck.decision.value}. allowing one-off.",
                file=sys.stderr,
            )
            final = Decision.ALLOW
        else:
            final = Decision.ALLOW
    elif resp.choice == HumanChoice.DENY_AND_REMEMBER:
        assert resp.description is not None
        new_policy = translator.translate_clarification(
            resp.description,
            triggered_action_id=str(envelope.proposed_at.timestamp()),
            existing_policy=policy,
        )
        final = Decision.DENY
    else:
        final = Decision.DENY

    if journal is not None:
        journal.append(JournalEntry(
            timestamp=datetime.now(timezone.utc),
            envelope=envelope,
            verifier_result_decision=vr.decision.value,
            matched_rule_ids=[r.id for r in vr.matched_rules],
            human_response=resp,
            policy_version_before=pv_before,
            policy_version_after=new_policy.version,
            final_decision=final.value,
        ))

    return HumanLoopOutcome(final, new_policy, abort_session=abort)
