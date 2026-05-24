"""
Safety specification: the formal object the verifier checks actions against.

A `Policy` is an ordered list of `Rule`s. Each rule has:
    * a kind filter (which action variants it applies to)
    * a condition (a Z3-checkable predicate over the action's fields)
    * an effect (ALLOW or DENY)
    * provenance (where the rule came from — user NL, default policy, etc.)

Evaluation semantics (deny-overrides, default-deny):
    For a given action A, find all rules whose kind filter matches A.
    Evaluate each rule's condition under A's concrete field values.
    * If ANY matching rule with effect=DENY has a true condition → DENY.
    * Else if ANY matching rule with effect=ALLOW has a true condition → ALLOW.
    * Else → UNKNOWN (which the system surfaces to the human).

This is the standard "default-deny, deny-overrides" semantics from access
control literature (e.g. XACML §7.11). We avoid "default allow" because the
whole point of this layer is to fail closed — if the policy doesn't speak to
an action, we ask the human rather than silently permitting it.

Rule conditions are stored in two forms:
    * `dsl`: a small JSON DSL (see `condition.py`), human- and LLM-readable.
    * `z3_expr_builder`: a callable producing a Z3 BoolRef when given a
       symbolic action context. The verifier uses this directly.
The DSL is the source of truth; `z3_expr_builder` is compiled from it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .world_model import ActionKind


class Effect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class RuleProvenance(BaseModel):
    """How did this rule get into the policy? Critical for audit."""
    source: str  # "user_initial_nl", "user_clarification", "default", "imported"
    original_text: str | None = None  # the NL the user typed, if applicable
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # If a rule was added in response to a specific action the user approved,
    # store its envelope id so we can trace decisions back through the log.
    triggered_by_action_id: str | None = None


class Rule(BaseModel):
    """A single allow- or deny-rule.

    The condition is the JSON DSL form. The verifier compiles it to Z3
    on policy load. We keep the DSL form (rather than a raw Z3 blob) so
    that:
        (a) rules are diff-able and human-reviewable,
        (b) the NL→spec translator only has to emit DSL JSON, never Z3
            Python code (which is harder to validate),
        (c) policies can be serialized to disk as plain JSON.
    """
    id: str = Field(default_factory=lambda: f"rule_{uuid4().hex[:12]}")
    effect: Effect
    applies_to: list[ActionKind]  # which action kinds this rule covers
    condition: dict[str, Any]  # DSL — see condition.py for grammar
    # Human-readable description ("Allow reads under /project") — for UI and
    # for the LLM when revising the policy.
    description: str
    provenance: RuleProvenance


class Policy(BaseModel):
    """A complete policy = ordered list of rules + metadata."""
    name: str = "default"
    version: int = 1
    rules: list[Rule] = Field(default_factory=list)
    # Project root anchors many path-based rules. Captured explicitly so
    # rules can refer to it symbolically ("under $PROJECT_ROOT") rather than
    # baking the absolute path in and breaking when the user moves the repo.
    project_root: str | None = None

    def add_rule(self, rule: Rule) -> "Policy":
        """Return a new Policy with the rule appended. Policies are
        treated as immutable values to keep the audit trail clean."""
        return self.model_copy(update={
            "rules": [*self.rules, rule],
            "version": self.version + 1,
        })

    def rules_for(self, kind: ActionKind) -> list[Rule]:
        return [r for r in self.rules if kind in r.applies_to]
