"""Policy model: rules with effects (ALLOW/DENY), aggregated with deny-overrides.

A policy is an ordered list of rules. Each rule has a condition and an effect.
Evaluating a policy on an action produces a verdict of ALLOW, DENY, or UNKNOWN:

- ALLOW: at least one ALLOW rule matched AND no DENY rule matched.
- DENY: at least one DENY rule matched.
- UNKNOWN: no rule matched at all.

Why deny-overrides? Because the failure mode we are trying to prevent is
"agent quietly does something harmful." When ALLOW and DENY collide we want
the DENY to win. Operators can always loosen restrictions later; the inverse
is hard to undo after `rm -rf` runs.

Why a three-valued verdict instead of binary?  Because both binary defaults
are bad:

- Default-allow re-creates the "skip permissions" failure mode.
- Default-deny blocks every novel action and pushes operators to disable the
  system entirely.

UNKNOWN is a *first-class signal* meaning "this policy has no opinion on this
action." At the runtime gate, UNKNOWN fails closed (the action is blocked).
The downstream story — how operators learn from UNKNOWN events and extend the
policy — is deliberately out of scope for this artifact. Operators can read
the journal (CLI: `safe-scaffold eval`), examine UNKNOWN entries, and add
rules manually. That separation keeps the formal core (this module + the
verifier + the Z3 backend) free of any LLM-driven or interactive code paths
that would muddy the security guarantees.

Every rule carries a Provenance record: who created it and when. Useful for
audit when operators hand-edit policies; not consumed by the verifier itself.
"""

from __future__ import annotations

import enum
import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from safe_scaffold.conditions import Condition, ValidationError, parse_condition


class Effect(enum.Enum):
    """What a matching rule says about the action."""

    ALLOW = "allow"
    DENY = "deny"

    @classmethod
    def parse(cls, value: str) -> "Effect":
        try:
            return cls(value)
        except ValueError as exc:
            raise ValidationError(
                f"effect must be one of {[e.value for e in cls]}, got {value!r}"
            ) from exc


@dataclass(frozen=True)
class Provenance:
    """Audit trail attached to a rule.

    `source_nl` is the natural-language description of the rule's intent,
    preserved so reviewers can compare human intent against the compiled DSL.
    Useful for audit when operators hand-edit policies in production.
    """

    created_at: float = field(default_factory=time.time)
    created_by: str = "unknown"  # "human", "default", "ci", etc.
    source_nl: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Provenance":
        return cls(
            created_at=float(data.get("created_at", time.time())),
            created_by=str(data.get("created_by", "unknown")),
            source_nl=str(data.get("source_nl", "")),
            notes=str(data.get("notes", "")),
        )


@dataclass(frozen=True)
class Rule:
    """A single rule. Application: if `condition` matches the action, the
    rule contributes its `effect`. Otherwise it contributes nothing."""

    id: str
    effect: Effect
    condition: Condition
    provenance: Provenance = field(default_factory=Provenance)
    description: str = ""

    def matches(self, action: Any) -> bool:
        return self.condition.evaluate(action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "effect": self.effect.value,
            "condition": self.condition.to_dict(),
            "provenance": self.provenance.to_dict(),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        try:
            return cls(
                id=str(data["id"]),
                effect=Effect.parse(str(data["effect"])),
                condition=parse_condition(data["condition"]),
                provenance=Provenance.from_dict(data.get("provenance", {})),
                description=str(data.get("description", "")),
            )
        except KeyError as exc:
            raise ValidationError(f"rule missing required field: {exc}") from exc


@dataclass(frozen=True)
class Policy:
    """An ordered collection of rules plus metadata."""

    name: str = "policy"
    version: int = 1
    rules: tuple[Rule, ...] = ()
    description: str = ""

    # ---- Construction helpers ----

    def with_rule(self, rule: Rule) -> "Policy":
        """Return a new Policy with `rule` appended. Version bumps by one."""
        if any(r.id == rule.id for r in self.rules):
            raise ValueError(f"duplicate rule id {rule.id!r}")
        return replace(self, rules=(*self.rules, rule), version=self.version + 1)

    def without_rule(self, rule_id: str) -> "Policy":
        """Return a new Policy with the named rule removed. No-op if absent."""
        new_rules = tuple(r for r in self.rules if r.id != rule_id)
        if len(new_rules) == len(self.rules):
            return self
        return replace(self, rules=new_rules, version=self.version + 1)

    # ---- Serialization ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "rules": [r.to_dict() for r in self.rules],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Policy":
        if not isinstance(data, dict):
            raise ValidationError(f"policy must be a dict, got {type(data).__name__}")
        rules_raw = data.get("rules", [])
        if not isinstance(rules_raw, list):
            raise ValidationError("policy.rules must be a list")
        return cls(
            name=str(data.get("name", "policy")),
            version=int(data.get("version", 1)),
            rules=tuple(Rule.from_dict(r) for r in rules_raw),
            description=str(data.get("description", "")),
        )

    @classmethod
    def from_json(cls, text: str) -> "Policy":
        return cls.from_dict(json.loads(text))

    @classmethod
    def load(cls, path: Path | str) -> "Policy":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: Path | str) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    # ---- Evaluation ----

    def matching(self, action: Any) -> tuple[Rule, ...]:
        """Return the subset of rules that match `action`, preserving order."""
        return tuple(r for r in self.rules if r.matches(action))


# ---------------------------------------------------------------------------
# Built-in safe defaults
# ---------------------------------------------------------------------------


def safe_default_policy() -> Policy:
    """A small starting policy that denies a few catastrophic operations.

    These rules are universally safe and exist to give Z3 something to anchor
    on for invariant proofs. Users layer their own ALLOW rules on top.
    """

    def _r(rid: str, eff: Effect, cond: dict[str, Any], desc: str) -> Rule:
        return Rule(
            id=rid,
            effect=eff,
            condition=parse_condition(cond),
            provenance=Provenance(created_by="default", source_nl=desc),
            description=desc,
        )

    rules = (
        _r(
            "deny_rm_recursive_root",
            Effect.DENY,
            {
                "type": "and",
                "of": [
                    {"type": "kind_is", "kind": "shell_exec"},
                    {"type": "eq", "ref": "program", "value": "rm"},
                    {
                        "type": "contains_arg",
                        "values": ["-rf", "-fr", "-r", "-R", "--recursive"],
                    },
                    {
                        "type": "or",
                        "of": [
                            {"type": "contains_arg", "values": ["/"]},
                            {"type": "contains_arg", "values": ["/*"]},
                            {"type": "contains_arg", "values": ["~"]},
                            {"type": "contains_arg", "values": ["$HOME"]},
                            {"type": "contains_arg", "values": ["${HOME}"]},
                        ],
                    },
                ],
            },
            "Recursive rm targeting / or $HOME is never allowed.",
        ),
        _r(
            "deny_write_etc",
            Effect.DENY,
            {
                "type": "and",
                "of": [
                    {"type": "kind_is", "kind": "file_write"},
                    {"type": "path_under", "ref": "path", "parent": "/etc"},
                ],
            },
            "Writing under /etc is never allowed.",
        ),
        _r(
            "deny_delete_etc",
            Effect.DENY,
            {
                "type": "and",
                "of": [
                    {"type": "kind_is", "kind": "file_delete"},
                    {
                        "type": "or",
                        "of": [
                            {"type": "path_under", "ref": "path", "parent": "/etc"},
                            {"type": "path_under", "ref": "path", "parent": "/usr"},
                            {"type": "path_under", "ref": "path", "parent": "/var"},
                            {"type": "path_equals", "ref": "path", "target": "/"},
                        ],
                    },
                ],
            },
            "Deleting under /etc, /usr, /var, or / is never allowed.",
        ),
        _r(
            "deny_read_credentials_env",
            Effect.DENY,
            {
                "type": "and",
                "of": [
                    {"type": "kind_is", "kind": "env_read"},
                    {
                        "type": "matches_regex",
                        "ref": "name",
                        "pattern": r".*(_TOKEN|_KEY|_SECRET|_PASSWORD|_API_KEY)$",
                    },
                ],
            },
            "Reading environment variables matching *_TOKEN etc. is denied by default.",
        ),
        # A baseline ALLOW for git-status-like reads so the default policy
        # isn't useless. Users override or remove.
        _r(
            "allow_read_in_repo",
            Effect.ALLOW,
            {
                "type": "and",
                "of": [
                    {"type": "kind_is", "kind": "file_read"},
                    {
                        "type": "not",
                        "of": {
                            "type": "or",
                            "of": [
                                {"type": "path_under", "ref": "path", "parent": "/etc"},
                                {"type": "path_under", "ref": "path", "parent": "/root"},
                            ],
                        },
                    },
                ],
            },
            "Reading files outside /etc and /root is allowed by default.",
        ),
    )
    return Policy(
        name="safe_defaults",
        version=1,
        rules=rules,
        description=(
            "Universal safety floor. Denies a small set of universally-bad "
            "operations; intended to be extended, not used alone."
        ),
    )
