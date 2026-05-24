"""Restricted condition DSL: AST, validation, evaluation, and Z3 compilation.

The DSL is intentionally tiny. Everything an end-user "rule" can do reduces to:

- boolean structure: and / or / not
- equality and membership: eq, in_set
- typed reference into the action: $action.path, $action.argv, $action.host, etc.
- path predicates: path_under(ref, "/etc"), path_equals(ref, "/etc/passwd")
- string predicates: matches_glob(ref, "/tmp/*.txt"), starts_with(ref, "rm")
- the literals true and false

The shape is JSON-as-AST: a condition is a dict with a "type" tag plus type-
specific fields. We never execute user-supplied code; conditions only describe
predicates over a fixed schema of action fields.

Two backends:

1. `evaluate(condition, action)`: direct Python evaluation. Fast, used at the
   gate on every proposed action.
2. `to_z3(condition, z3_action_record)`: compile to a Z3 expression for use in
   universal property proofs ("can any action satisfying this policy also
   match `rm -rf /`?"). Z3 is optional; the module imports it lazily and
   raises a clear error if asked to compile while z3 is absent.

A condition that uses a feature Z3 cannot model precisely (e.g. arbitrary regex
or full glob matching) is encoded as an OVER-APPROXIMATION in the Z3 model:
the Z3 predicate is treated as a fresh boolean variable that could be either
true or false. This is sound for proving DENY-style properties (if Z3 says no
action can be allowed, then certainly no action matching the original glob can
be), but lossy: the Z3 model may report counterexamples that the direct
evaluator would reject. We document this clearly and never claim Z3 proofs are
complete with respect to globs/regex.

References:
    Bengio et al., "Towards Guaranteed Safe AI", arXiv:2405.06624, §3 (Safety
    Specifications) on why a restricted, formally-tractable spec language is
    preferable to free-form natural-language judges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from safe_scaffold import paths
from safe_scaffold.world import Action


class ValidationError(Exception):
    """Raised when a condition dict fails structural validation."""


# ---------------------------------------------------------------------------
# References into the action being checked
# ---------------------------------------------------------------------------


# The set of field names a condition is allowed to reference. Extending this
# requires updating both the direct evaluator and the Z3 compiler. Kept
# explicit so the surface area of trust is visible at a glance.
_ALLOWED_REFS: frozenset[str] = frozenset({
    "kind",
    # ShellExec
    "argv", "program", "cwd",
    # File*
    "path",
    # FileWrite
    "content_size",
    # FileDelete
    "recursive",
    # NetworkRequest
    "method", "url", "host", "port",
    # ProcessSignal
    "pid", "signal",
    # EnvRead
    "name",
})


@dataclass(frozen=True)
class Reference:
    """A reference into a field of the action being verified.

    Example: `Reference("path")` evaluates to `action.path`. For ShellExec,
    `Reference("program")` is shorthand for `action.argv[0]`.
    """

    field: str

    def __post_init__(self) -> None:
        if self.field not in _ALLOWED_REFS:
            raise ValidationError(
                f"reference to unknown action field {self.field!r}. "
                f"Allowed: {sorted(_ALLOWED_REFS)}"
            )

    def resolve(self, action: Action) -> Any:
        """Return the actual value on a concrete action, or `_UNSET` if absent.

        Returning a sentinel rather than raising means a rule that mentions
        `path` can be safely written; it will simply fail to match against a
        ShellExec action that has no `path` attribute.
        """
        # 'program' is a virtual field on ShellExec.
        if self.field == "program":
            return getattr(action, "program", _UNSET)
        return getattr(action, self.field, _UNSET)


class _Unset:
    def __repr__(self) -> str:
        return "<UNSET>"

    def __bool__(self) -> bool:
        return False


_UNSET = _Unset()
"""Singleton sentinel for "this action lacks this field"."""


# ---------------------------------------------------------------------------
# Conditions (AST)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    """Base class for all condition AST nodes."""

    type: ClassVar[str] = "_base"

    # Populated by __init_subclass__.
    _registry: ClassVar[dict[str, type["Condition"]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.type == "_base":
            raise TypeError(f"Condition subclass {cls.__name__} must override `type`")
        Condition._registry[cls.type] = cls

    def evaluate(self, action: Action) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass(frozen=True)
class _True(Condition):
    type: ClassVar[str] = "true"

    def evaluate(self, action: Action) -> bool:
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"type": "true"}


@dataclass(frozen=True)
class _False(Condition):
    type: ClassVar[str] = "false"

    def evaluate(self, action: Action) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"type": "false"}


TRUE = _True()
FALSE = _False()


@dataclass(frozen=True)
class And(Condition):
    children: tuple[Condition, ...]
    type: ClassVar[str] = "and"

    def __post_init__(self) -> None:
        if not self.children:
            raise ValidationError("`and` must have at least one child")

    def evaluate(self, action: Action) -> bool:
        return all(c.evaluate(action) for c in self.children)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "and", "of": [c.to_dict() for c in self.children]}


@dataclass(frozen=True)
class Or(Condition):
    children: tuple[Condition, ...]
    type: ClassVar[str] = "or"

    def __post_init__(self) -> None:
        if not self.children:
            raise ValidationError("`or` must have at least one child")

    def evaluate(self, action: Action) -> bool:
        return any(c.evaluate(action) for c in self.children)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "or", "of": [c.to_dict() for c in self.children]}


@dataclass(frozen=True)
class Not(Condition):
    child: Condition
    type: ClassVar[str] = "not"

    def evaluate(self, action: Action) -> bool:
        return not self.child.evaluate(action)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "not", "of": self.child.to_dict()}


@dataclass(frozen=True)
class KindIs(Condition):
    """`action.kind == kind`. Most rules start with this."""

    kind: str
    type: ClassVar[str] = "kind_is"

    def evaluate(self, action: Action) -> bool:
        return action.kind == self.kind

    def to_dict(self) -> dict[str, Any]:
        return {"type": "kind_is", "kind": self.kind}


@dataclass(frozen=True)
class Eq(Condition):
    """`$ref == value`."""

    ref: Reference
    value: Any
    type: ClassVar[str] = "eq"

    def evaluate(self, action: Action) -> bool:
        actual = self.ref.resolve(action)
        if actual is _UNSET:
            return False
        return actual == self.value

    def to_dict(self) -> dict[str, Any]:
        return {"type": "eq", "ref": self.ref.field, "value": self.value}


@dataclass(frozen=True)
class InSet(Condition):
    """`$ref in {value0, value1, ...}`. Handy for "host in {github.com, pypi.org}"."""

    ref: Reference
    values: tuple[Any, ...]
    type: ClassVar[str] = "in_set"

    def __post_init__(self) -> None:
        if not self.values:
            raise ValidationError("`in_set` must have at least one value")

    def evaluate(self, action: Action) -> bool:
        actual = self.ref.resolve(action)
        if actual is _UNSET:
            return False
        return actual in self.values

    def to_dict(self) -> dict[str, Any]:
        return {"type": "in_set", "ref": self.ref.field, "values": list(self.values)}


@dataclass(frozen=True)
class ContainsArg(Condition):
    """Some element of `action.argv` equals `value` (or matches any of them).

    Used for shell-flag detection: contains_arg(["-rf", "-fr", "-r", "--recursive"]).
    """

    values: tuple[str, ...]
    type: ClassVar[str] = "contains_arg"

    def __post_init__(self) -> None:
        if not self.values:
            raise ValidationError("`contains_arg` must have at least one value")

    def evaluate(self, action: Action) -> bool:
        argv = getattr(action, "argv", _UNSET)
        if argv is _UNSET:
            return False
        return any(v in argv for v in self.values)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "contains_arg", "values": list(self.values)}


@dataclass(frozen=True)
class PathUnder(Condition):
    """The path at `ref` lies under the absolute parent `parent`."""

    ref: Reference
    parent: str
    type: ClassVar[str] = "path_under"

    def __post_init__(self) -> None:
        if not self.parent.startswith("/"):
            raise ValidationError(
                f"path_under.parent must be absolute, got {self.parent!r}"
            )

    def evaluate(self, action: Action) -> bool:
        actual = self.ref.resolve(action)
        if actual is _UNSET or not isinstance(actual, str):
            return False
        try:
            return paths.is_under(actual, self.parent)
        except ValueError:
            # Bad input from the agent → fail closed.
            return False

    def to_dict(self) -> dict[str, Any]:
        return {"type": "path_under", "ref": self.ref.field, "parent": self.parent}


@dataclass(frozen=True)
class PathEquals(Condition):
    """The path at `ref` equals (after normalization) the absolute path `target`."""

    ref: Reference
    target: str
    type: ClassVar[str] = "path_equals"

    def __post_init__(self) -> None:
        if not self.target.startswith("/"):
            raise ValidationError(
                f"path_equals.target must be absolute, got {self.target!r}"
            )

    def evaluate(self, action: Action) -> bool:
        actual = self.ref.resolve(action)
        if actual is _UNSET or not isinstance(actual, str):
            return False
        try:
            return paths.normalize(actual) == paths.normalize(self.target)
        except ValueError:
            return False

    def to_dict(self) -> dict[str, Any]:
        return {"type": "path_equals", "ref": self.ref.field, "target": self.target}


@dataclass(frozen=True)
class MatchesGlob(Condition):
    """The path at `ref` matches glob `pattern` (see paths.matches_glob)."""

    ref: Reference
    pattern: str
    type: ClassVar[str] = "matches_glob"

    def evaluate(self, action: Action) -> bool:
        actual = self.ref.resolve(action)
        if actual is _UNSET or not isinstance(actual, str):
            return False
        try:
            return paths.matches_glob(actual, self.pattern)
        except ValueError:
            return False

    def to_dict(self) -> dict[str, Any]:
        return {"type": "matches_glob", "ref": self.ref.field, "pattern": self.pattern}


@dataclass(frozen=True)
class MatchesRegex(Condition):
    """Regex match against the value at `ref`.

    The regex is anchored at both ends — partial matches are confusing in
    security contexts. To match a substring, write `.*foo.*`.
    """

    ref: Reference
    pattern: str
    type: ClassVar[str] = "matches_regex"
    # We compile once when the condition is parsed, but dataclass(frozen=True)
    # forbids __post_init__ from setting attributes — so we store the compiled
    # form in a field with default = None and use object.__setattr__.
    _compiled: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            compiled = re.compile(self.pattern)
        except re.error as exc:
            raise ValidationError(f"invalid regex {self.pattern!r}: {exc}") from exc
        object.__setattr__(self, "_compiled", compiled)

    def evaluate(self, action: Action) -> bool:
        actual = self.ref.resolve(action)
        if actual is _UNSET or not isinstance(actual, str):
            return False
        assert self._compiled is not None  # set in __post_init__
        return self._compiled.fullmatch(actual) is not None

    def to_dict(self) -> dict[str, Any]:
        return {"type": "matches_regex", "ref": self.ref.field, "pattern": self.pattern}


@dataclass(frozen=True)
class StartsWith(Condition):
    """The value at `ref` starts with `prefix`."""

    ref: Reference
    prefix: str
    type: ClassVar[str] = "starts_with"

    def evaluate(self, action: Action) -> bool:
        actual = self.ref.resolve(action)
        if actual is _UNSET or not isinstance(actual, str):
            return False
        return actual.startswith(self.prefix)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "starts_with", "ref": self.ref.field, "prefix": self.prefix}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_condition(data: Any) -> Condition:
    """Construct a Condition AST from a JSON-shaped dict.

    Validates structure and raises ValidationError on any issue. Reviewers can
    audit a translated policy by running this and inspecting failures rather
    than executing anything.
    """
    if isinstance(data, bool):
        return TRUE if data else FALSE
    if not isinstance(data, dict):
        raise ValidationError(f"condition must be a dict or bool, got {type(data).__name__}")
    t = data.get("type")
    if t is None:
        raise ValidationError(f"condition missing `type`: {data!r}")
    if t not in Condition._registry:
        raise ValidationError(
            f"unknown condition type {t!r}. Known: {sorted(Condition._registry)}"
        )

    if t == "true":
        return TRUE
    if t == "false":
        return FALSE
    if t == "and":
        of = data.get("of")
        if not isinstance(of, list):
            raise ValidationError("`and` requires `of: [..conditions..]`")
        return And(tuple(parse_condition(c) for c in of))
    if t == "or":
        of = data.get("of")
        if not isinstance(of, list):
            raise ValidationError("`or` requires `of: [..conditions..]`")
        return Or(tuple(parse_condition(c) for c in of))
    if t == "not":
        of = data.get("of")
        if of is None:
            raise ValidationError("`not` requires `of: <condition>`")
        return Not(parse_condition(of))
    if t == "kind_is":
        kind = data.get("kind")
        if not isinstance(kind, str):
            raise ValidationError("`kind_is` requires string `kind`")
        return KindIs(kind)
    if t == "eq":
        ref = _parse_ref(data)
        if "value" not in data:
            raise ValidationError("`eq` requires `value`")
        return Eq(ref, data["value"])
    if t == "in_set":
        ref = _parse_ref(data)
        values = data.get("values")
        if not isinstance(values, list) or not values:
            raise ValidationError("`in_set` requires non-empty `values` list")
        return InSet(ref, tuple(values))
    if t == "contains_arg":
        values = data.get("values")
        if not isinstance(values, list) or not values:
            raise ValidationError("`contains_arg` requires non-empty `values` list")
        return ContainsArg(tuple(str(v) for v in values))
    if t == "path_under":
        ref = _parse_ref(data)
        parent = data.get("parent")
        if not isinstance(parent, str):
            raise ValidationError("`path_under` requires string `parent`")
        return PathUnder(ref, parent)
    if t == "path_equals":
        ref = _parse_ref(data)
        target = data.get("target")
        if not isinstance(target, str):
            raise ValidationError("`path_equals` requires string `target`")
        return PathEquals(ref, target)
    if t == "matches_glob":
        ref = _parse_ref(data)
        pattern = data.get("pattern")
        if not isinstance(pattern, str):
            raise ValidationError("`matches_glob` requires string `pattern`")
        return MatchesGlob(ref, pattern)
    if t == "matches_regex":
        ref = _parse_ref(data)
        pattern = data.get("pattern")
        if not isinstance(pattern, str):
            raise ValidationError("`matches_regex` requires string `pattern`")
        return MatchesRegex(ref, pattern)
    if t == "starts_with":
        ref = _parse_ref(data)
        prefix = data.get("prefix")
        if not isinstance(prefix, str):
            raise ValidationError("`starts_with` requires string `prefix`")
        return StartsWith(ref, prefix)

    raise ValidationError(f"unhandled condition type {t!r}")  # pragma: no cover


def _parse_ref(data: dict[str, Any]) -> Reference:
    ref = data.get("ref")
    if not isinstance(ref, str):
        raise ValidationError(f"`{data.get('type')}` requires string `ref` field")
    return Reference(ref)
