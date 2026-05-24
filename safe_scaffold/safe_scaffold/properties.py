"""Universal property checking via Z3.

The headline contribution of Track 1 is that because the policy is in a DSL
with formal semantics, we can ask Z3 questions like:

    "Is there any conceivable action that this policy would ALLOW, and that
     also matches the pattern `rm -rf <some path>` for any path?"

If Z3 returns UNSAT, no such action exists. The policy provably cannot allow
that family of actions — regardless of what specific argv the agent comes up
with. This is the property that LLM-as-judge approaches structurally cannot
guarantee: a judge can be jailbroken or simply mistaken; a Z3 UNSAT proof
cannot be talked out of.

# Model

We model an abstract action as a record of Z3 variables:

    kind        :: enumerated sort with values from Action._registry
    program     :: String       (= argv[0] for shell_exec)
    argv_set    :: Set[String]  (over-approximation: we track which strings
                                 appear *anywhere* in argv, but lose order
                                 and multiplicity. Sufficient for the
                                 contains_arg predicate)
    path        :: String
    host        :: String
    method      :: String
    port        :: Int
    name        :: String
    signal      :: String

For each policy rule we build a Z3 expression `matches_R(action)` over these
variables. The policy verdict on the abstract action is then:

    allowed(action) ≡ (∃ ALLOW rule R. matches_R) ∧ ¬(∃ DENY rule R. matches_R)

To ask "does the policy ever allow a `rm -rf <anything>` action?", we conjoin
allowed(action) with a Z3 pattern for "rm -rf" and check satisfiability. SAT
means "yes, here is a counterexample"; UNSAT means "no such action exists".

# Over-approximations

Some predicates don't translate exactly to Z3:

- `matches_glob`: we model it as a fresh boolean. Z3 may choose either
  truth value, exploring the worst case.
- `matches_regex`: same.
- `path_under(p)`: modeled as `Z3_path.startswith(p + "/")`, which is
  cleaner than glob and good enough for the canonical safety properties.
- `path_equals(t)`: exact string equality.

These over-approximations are SOUND for proving "policy refuses pattern X":
if Z3 says no action satisfying both the policy and X exists, that conclusion
holds even with the lossy abstraction. They are INCOMPLETE the other way: Z3
might report a SAT counterexample that no real policy would actually admit,
because it picked a glob's truth value adversarially.

# Z3 is optional

If z3-solver isn't installed, this module's functions raise a clear error
message pointing at `pip install safe-scaffold[smt]`. The direct verifier
still works without z3; only the universal-property proofs need it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from safe_scaffold.conditions import (
    And,
    Condition,
    ContainsArg,
    Eq,
    InSet,
    KindIs,
    MatchesGlob,
    MatchesRegex,
    Not,
    Or,
    PathEquals,
    PathUnder,
    StartsWith,
    _False,
    _True,
)
from safe_scaffold.policy import Effect, Policy

# Lazy z3 import: tests still pass without it.
try:
    import z3  # type: ignore[import-untyped]

    Z3_AVAILABLE = True
except ImportError:  # pragma: no cover - environment-dependent
    z3 = None  # type: ignore[assignment]
    Z3_AVAILABLE = False


class Z3UnavailableError(RuntimeError):
    """Raised when a Z3-backed function is called without z3 installed."""

    def __init__(self) -> None:
        super().__init__(
            "z3-solver is not installed. Install with `pip install safe-scaffold[smt]` "
            "to enable universal property proofs. The direct verifier still works."
        )


def _require_z3() -> None:
    if not Z3_AVAILABLE:
        raise Z3UnavailableError()


# ---------------------------------------------------------------------------
# Abstract action: Z3 variables representing a hypothetical action
# ---------------------------------------------------------------------------


@dataclass
class AbstractAction:
    """A record of Z3 variables representing some hypothetical action.

    Used as the universally-quantified subject in property proofs.
    """

    kind: Any  # z3.String
    program: Any  # z3.String
    path: Any  # z3.String
    host: Any  # z3.String
    method: Any  # z3.String
    port: Any  # z3.Int
    name: Any  # z3.String
    signal: Any  # z3.String
    cwd: Any  # z3.String
    url: Any  # z3.String
    content_size: Any  # z3.Int
    recursive: Any  # z3.Bool
    # Strings that appear anywhere in argv. We over-approximate by tracking a
    # finite set of "witnesses" via a function `argv_contains : String -> Bool`.
    argv_contains: Any  # z3.Function

    @classmethod
    def fresh(cls, suffix: str = "") -> "AbstractAction":
        _require_z3()
        s = suffix
        return cls(
            kind=z3.String(f"kind{s}"),
            program=z3.String(f"program{s}"),
            path=z3.String(f"path{s}"),
            host=z3.String(f"host{s}"),
            method=z3.String(f"method{s}"),
            port=z3.Int(f"port{s}"),
            name=z3.String(f"name{s}"),
            signal=z3.String(f"signal{s}"),
            cwd=z3.String(f"cwd{s}"),
            url=z3.String(f"url{s}"),
            content_size=z3.Int(f"content_size{s}"),
            recursive=z3.Bool(f"recursive{s}"),
            argv_contains=z3.Function(
                f"argv_contains{s}", z3.StringSort(), z3.BoolSort()
            ),
        )

    def ref(self, name: str) -> Any:
        """Look up a Z3 variable by reference name."""
        return getattr(self, name)


# ---------------------------------------------------------------------------
# Compile a Condition AST node to a Z3 expression
# ---------------------------------------------------------------------------


def compile_condition(cond: Condition, act: AbstractAction, *, over_approx_tag: int = 0) -> Any:
    """Compile a Condition to a Z3 boolean expression over `act`'s variables.

    `over_approx_tag` is used to make fresh booleans for opaque predicates
    (glob, regex); each call site passes its own so multiple opaque predicates
    in the same condition don't collapse.
    """
    _require_z3()

    if isinstance(cond, _True):
        return z3.BoolVal(True)
    if isinstance(cond, _False):
        return z3.BoolVal(False)
    if isinstance(cond, And):
        return z3.And(*[compile_condition(c, act, over_approx_tag=i)
                        for i, c in enumerate(cond.children, start=over_approx_tag * 100)])
    if isinstance(cond, Or):
        return z3.Or(*[compile_condition(c, act, over_approx_tag=i)
                       for i, c in enumerate(cond.children, start=over_approx_tag * 100)])
    if isinstance(cond, Not):
        return z3.Not(compile_condition(cond.child, act, over_approx_tag=over_approx_tag))
    if isinstance(cond, KindIs):
        return act.kind == z3.StringVal(cond.kind)
    if isinstance(cond, Eq):
        ref = act.ref(cond.ref.field)
        val = cond.value
        if isinstance(val, str):
            return ref == z3.StringVal(val)
        if isinstance(val, bool):
            return ref == z3.BoolVal(val)
        if isinstance(val, int):
            return ref == z3.IntVal(val)
        # Unknown literal type: over-approximate.
        return z3.Bool(f"opaque_eq_{over_approx_tag}")
    if isinstance(cond, InSet):
        ref = act.ref(cond.ref.field)
        clauses = []
        for v in cond.values:
            if isinstance(v, str):
                clauses.append(ref == z3.StringVal(v))
            elif isinstance(v, bool):
                clauses.append(ref == z3.BoolVal(v))
            elif isinstance(v, int):
                clauses.append(ref == z3.IntVal(v))
            else:
                clauses.append(z3.Bool(f"opaque_in_{over_approx_tag}_{v!r}"))
        return z3.Or(*clauses)
    if isinstance(cond, ContainsArg):
        # action.argv contains at least one of the listed values.
        return z3.Or(*[act.argv_contains(z3.StringVal(v)) for v in cond.values])
    if isinstance(cond, PathUnder):
        # path starts with parent + "/" (or equals parent).
        ref = act.ref(cond.ref.field)
        parent = cond.parent.rstrip("/")
        return z3.Or(
            ref == z3.StringVal(parent),
            z3.PrefixOf(z3.StringVal(parent + "/"), ref),
        )
    if isinstance(cond, PathEquals):
        ref = act.ref(cond.ref.field)
        return ref == z3.StringVal(cond.target)
    if isinstance(cond, StartsWith):
        ref = act.ref(cond.ref.field)
        return z3.PrefixOf(z3.StringVal(cond.prefix), ref)
    if isinstance(cond, MatchesGlob):
        # Glob → over-approximation: introduce a fresh boolean per node so Z3
        # may explore both worlds. SOUND for proving DENY-of-pattern; LOSSY
        # for proving ALLOW.
        return z3.Bool(f"glob_{over_approx_tag}_{id(cond):x}")
    if isinstance(cond, MatchesRegex):
        return z3.Bool(f"regex_{over_approx_tag}_{id(cond):x}")
    raise TypeError(f"unsupported condition type for Z3: {type(cond).__name__}")


# ---------------------------------------------------------------------------
# Policy compilation
# ---------------------------------------------------------------------------


def compile_policy(policy: Policy, act: AbstractAction) -> tuple[Any, Any]:
    """Return (allow_clause, deny_clause) — Z3 booleans for the policy on `act`.

    `allow_clause` is true iff some ALLOW rule matches.
    `deny_clause` is true iff some DENY rule matches.

    The verdict ALLOW corresponds to `allow_clause ∧ ¬deny_clause`. DENY to
    `deny_clause`. UNKNOWN (no rule matched) to `¬allow_clause ∧ ¬deny_clause`.
    """
    _require_z3()
    allow_clauses = []
    deny_clauses = []
    for i, rule in enumerate(policy.rules):
        expr = compile_condition(rule.condition, act, over_approx_tag=i)
        if rule.effect is Effect.ALLOW:
            allow_clauses.append(expr)
        else:
            deny_clauses.append(expr)
    allow = z3.Or(*allow_clauses) if allow_clauses else z3.BoolVal(False)
    deny = z3.Or(*deny_clauses) if deny_clauses else z3.BoolVal(False)
    return allow, deny


# ---------------------------------------------------------------------------
# Property API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PropertyResult:
    """Outcome of checking a universal property against a policy."""

    holds: bool
    counterexample: dict[str, Any] | None
    description: str

    def __str__(self) -> str:
        verdict = "HOLDS" if self.holds else "VIOLATED"
        if self.holds:
            return f"{verdict}: {self.description}"
        return (
            f"{verdict}: {self.description}\n"
            f"  counterexample: {self.counterexample}"
        )


def policy_never_allows(
    policy: Policy, forbidden_pattern: Condition, description: str = ""
) -> PropertyResult:
    """Prove that `policy` never allows an action matching `forbidden_pattern`.

    Formally: ∀ action. (policy ALLOWS action) → ¬forbidden_pattern(action).
    Equivalently: UNSAT(∃ action. allow(action) ∧ ¬deny(action) ∧ forbidden(action)).
    """
    _require_z3()
    act = AbstractAction.fresh()
    allow, deny = compile_policy(policy, act)
    forbidden = compile_condition(forbidden_pattern, act, over_approx_tag=10_000)
    solver = z3.Solver()
    solver.add(allow)
    solver.add(z3.Not(deny))
    solver.add(forbidden)
    result = solver.check()
    if result == z3.unsat:
        return PropertyResult(holds=True, counterexample=None, description=description)
    if result == z3.sat:
        model = solver.model()
        cx = _model_to_dict(model, act)
        return PropertyResult(holds=False, counterexample=cx, description=description)
    # unknown (incomplete Z3 result); be conservative
    return PropertyResult(
        holds=False,
        counterexample={"_z3": "unknown"},
        description=description + " [Z3 returned UNKNOWN]",
    )


def policy_always_denies(
    policy: Policy, pattern: Condition, description: str = ""
) -> PropertyResult:
    """Prove that whenever an action matches `pattern`, the policy denies it.

    Formally: ∀ action. pattern(action) → policy DENIES action.
    Equivalently: UNSAT(∃ action. pattern(action) ∧ ¬deny(action)).

    Stronger than `never_allows`: forbids UNKNOWN too. Use this when you want
    the policy to give a definitive DENY for every member of the pattern, with
    no fall-through to the gate's fail-closed default.
    """
    _require_z3()
    act = AbstractAction.fresh()
    _, deny = compile_policy(policy, act)
    pat = compile_condition(pattern, act, over_approx_tag=20_000)
    solver = z3.Solver()
    solver.add(pat)
    solver.add(z3.Not(deny))
    result = solver.check()
    if result == z3.unsat:
        return PropertyResult(holds=True, counterexample=None, description=description)
    if result == z3.sat:
        model = solver.model()
        cx = _model_to_dict(model, act)
        return PropertyResult(holds=False, counterexample=cx, description=description)
    return PropertyResult(
        holds=False,
        counterexample={"_z3": "unknown"},
        description=description + " [Z3 returned UNKNOWN]",
    )


def _model_to_dict(model: Any, act: AbstractAction) -> dict[str, Any]:
    """Best-effort extraction of variable assignments from a Z3 model."""
    out: dict[str, Any] = {}
    for fname in [
        "kind", "program", "path", "host", "method",
        "port", "name", "signal", "cwd", "url",
        "content_size", "recursive",
    ]:
        var = getattr(act, fname)
        try:
            val = model.eval(var, model_completion=True)
            # Stringify in a readable form.
            sval = str(val)
            # Strip Z3's quoting noise on strings.
            if sval.startswith('"') and sval.endswith('"'):
                sval = sval[1:-1]
            out[fname] = sval
        except Exception:  # pragma: no cover
            out[fname] = "<unknown>"
    return out


# ---------------------------------------------------------------------------
# A small library of pre-baked property patterns
# ---------------------------------------------------------------------------


def pattern_rm_recursive_anywhere() -> Condition:
    """`rm` invoked with a recursive flag — regardless of target path.

    A policy that PASSES `policy_never_allows(... pattern_rm_recursive_anywhere())`
    is strong: it forbids recursive rm in *every* form, including ones the
    user never thought to enumerate.
    """
    from safe_scaffold.conditions import parse_condition
    return parse_condition({
        "type": "and",
        "of": [
            {"type": "kind_is", "kind": "shell_exec"},
            {"type": "eq", "ref": "program", "value": "rm"},
            {
                "type": "contains_arg",
                "values": ["-rf", "-fr", "-r", "-R", "--recursive"],
            },
        ],
    })


def pattern_write_under(directory: str) -> Condition:
    """Any file write whose path lies under `directory`."""
    from safe_scaffold.conditions import parse_condition
    return parse_condition({
        "type": "and",
        "of": [
            {"type": "kind_is", "kind": "file_write"},
            {"type": "path_under", "ref": "path", "parent": directory},
        ],
    })


def pattern_network_exfil(banned_hosts: list[str]) -> Condition:
    """Network request to any of the listed hosts."""
    from safe_scaffold.conditions import parse_condition
    return parse_condition({
        "type": "and",
        "of": [
            {"type": "kind_is", "kind": "network_request"},
            {"type": "in_set", "ref": "host", "values": banned_hosts},
        ],
    })


def pattern_credential_env_read() -> Condition:
    """Read of any environment variable whose name suggests credentials."""
    from safe_scaffold.conditions import parse_condition
    # We can't easily over-approximate `matches_regex` in Z3, so this property
    # uses `in_set` for the most common credential variable names. The direct
    # evaluator's regex-based DENY rule is a strict superset.
    return parse_condition({
        "type": "and",
        "of": [
            {"type": "kind_is", "kind": "env_read"},
            {
                "type": "in_set",
                "ref": "name",
                "values": [
                    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                    "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                    "GITHUB_TOKEN", "GH_TOKEN",
                    "DATABASE_PASSWORD", "DB_PASSWORD",
                ],
            },
        ],
    })
