"""
Condition DSL: the small grammar that rule conditions are written in.

The DSL is intentionally restricted. It can only:
    * compare action fields against constants or other fields,
    * test path prefix/suffix containment,
    * test set membership,
    * combine with and/or/not.

We chose this minimal grammar (rather than letting the LLM emit arbitrary
Z3 Python) because:
    1. The translator's output space is small enough to validate exhaustively.
    2. Every operator has a clean Z3 encoding, so soundness of the
       compilation is checkable by inspection.
    3. Humans can read the DSL and spot-check rules without knowing Z3.

Grammar (informal):

    Expr  := {"op": "and", "args": [Expr, ...]}
           | {"op": "or",  "args": [Expr, ...]}
           | {"op": "not", "arg": Expr}
           | {"op": "eq",          "field": <field-ref>, "value": <literal>}
           | {"op": "in",          "field": <field-ref>, "values": [<literal>, ...]}
           | {"op": "path_under",  "field": <field-ref>, "value": <path>}
           | {"op": "path_equals", "field": <field-ref>, "value": <path>}
           | {"op": "contains_arg","field": <field-ref>, "value": <str>}
           | {"op": "matches",     "field": <field-ref>, "value": <regex>}
           | {"op": "true"} | {"op": "false"}

    <field-ref> is one of the action's field names ("path", "command",
    "argv", "host", "port", "var_name", "signal_name", "recursive", ...).

Compilation to Z3:
    Each action variant is encoded as a tuple of Z3 constants — String for
    paths/commands/hosts, Int for port/pid, Bool for flags, and a Seq of
    String for argv. The compiler walks the DSL tree and produces a Z3
    BoolRef over these constants. The verifier asserts the rule's
    BoolRef alongside the concrete values of the proposed action and asks
    Z3 whether the conjunction is sat.

Why use Z3 at all (rather than just Python evaluation)?
    For atomic actions, plain Python would suffice. We use Z3 because
    the same machinery scales to:
        * checking sequences/plans of actions for invariant preservation,
        * proving the policy itself has properties (e.g. "no shell rule
          allows `rm -rf /`"), which is straightforward as a Z3 query but
          painful as an ad-hoc Python checker.
    The single-action verifier is the entry point; the policy-property
    checker (see verifier.check_policy_property) reuses the same encoding.
"""
from __future__ import annotations

from typing import Any

try:
    import z3
    Z3_AVAILABLE = True
except ImportError:  # allow `import safe_scaffold` without z3 for the docs
    z3 = None  # type: ignore
    Z3_AVAILABLE = False

from .world_model import (
    Action, ActionKind, ShellExec, FileRead, FileWrite, FileDelete,
    NetworkRequest, ProcessSignal, EnvRead,
)


class ConditionError(ValueError):
    """Raised for malformed DSL conditions."""


# ---------------------------------------------------------------------------
# Symbolic action contexts: per-kind tuples of Z3 constants.
# ---------------------------------------------------------------------------

class _SymbolicAction:
    """Z3 constants representing each field of an action of a given kind.

    Concrete action values are substituted in at check time (see
    verifier.verify), so we get one symbolic context per kind that the
    compiler can target without knowing the concrete action yet.
    """
    def __init__(self, kind: ActionKind):
        if not Z3_AVAILABLE:
            raise RuntimeError("z3-solver is required for the verifier")
        self.kind = kind
        # Common — every action has these
        self.fields: dict[str, Any] = {}

        S = z3.StringSort()
        I = z3.IntSort()
        B = z3.BoolSort()

        def mk(name: str, sort):
            v = z3.Const(f"act_{kind.value}_{name}", sort)
            self.fields[name] = v
            return v

        if kind == ActionKind.SHELL_EXEC:
            mk("command", S)
            # argv encoded as a single space-joined string for the simple
            # contains_arg check. A full Seq(String) encoding would be
            # cleaner but Z3's string theory handles this well enough for
            # the prototype.
            mk("argv_joined", S)
            mk("cwd", S)
            mk("raw", S)
        elif kind in (ActionKind.FILE_READ, ActionKind.FILE_DELETE):
            mk("path", S)
            if kind == ActionKind.FILE_DELETE:
                mk("recursive", B)
        elif kind == ActionKind.FILE_WRITE:
            mk("path", S)
            mk("append", B)
            mk("size_bytes", I)
        elif kind == ActionKind.NETWORK_REQUEST:
            mk("url", S)
            mk("method", S)
            mk("host", S)
            mk("port", I)
        elif kind == ActionKind.PROCESS_SIGNAL:
            mk("pid", I)
            mk("signal_name", S)
        elif kind == ActionKind.ENV_READ:
            mk("var_name", S)
        else:
            raise ConditionError(f"unknown action kind: {kind}")


# Cache symbolic contexts per kind so rule compilation doesn't recreate them.
_SYM_CACHE: dict[ActionKind, _SymbolicAction] = {}


def symbolic_for(kind: ActionKind) -> _SymbolicAction:
    if kind not in _SYM_CACHE:
        _SYM_CACHE[kind] = _SymbolicAction(kind)
    return _SYM_CACHE[kind]


# ---------------------------------------------------------------------------
# DSL → Z3 compilation
# ---------------------------------------------------------------------------

def compile_condition(dsl: dict[str, Any], sym: _SymbolicAction) -> Any:
    """Compile a DSL expression to a Z3 BoolRef under symbolic context `sym`.

    Raises ConditionError on malformed input. The compiler is total — every
    accepted DSL tree produces a well-typed Z3 expression — so the verifier
    never has to deal with "rule failed to compile" at check time except
    when a policy is first loaded.
    """
    if not Z3_AVAILABLE:
        raise RuntimeError("z3-solver is required for compilation")
    op = dsl.get("op")
    if op is None:
        raise ConditionError(f"missing 'op' in {dsl!r}")

    if op == "true":
        return z3.BoolVal(True)
    if op == "false":
        return z3.BoolVal(False)
    if op == "and":
        return z3.And(*(compile_condition(a, sym) for a in dsl["args"]))
    if op == "or":
        return z3.Or(*(compile_condition(a, sym) for a in dsl["args"]))
    if op == "not":
        return z3.Not(compile_condition(dsl["arg"], sym))

    # All other ops are field-relative
    field_name = dsl.get("field")
    if field_name is None:
        raise ConditionError(f"op {op!r} requires 'field'")
    if field_name not in sym.fields:
        # The rule talks about a field this action kind doesn't have —
        # that means the rule simply doesn't apply, so return False rather
        # than crashing. (Spec-design choice: we *could* reject at load
        # time, but allowing this makes it easier to write polymorphic
        # rules over multiple action kinds.)
        return z3.BoolVal(False)
    f = sym.fields[field_name]

    if op == "eq":
        v = dsl["value"]
        return f == _z3_literal(v)
    if op == "in":
        vs = dsl["values"]
        return z3.Or(*[f == _z3_literal(v) for v in vs])
    if op == "path_equals":
        return f == z3.StringVal(_normalize_path(dsl["value"]))
    if op == "path_under":
        # "p is under d" iff p == d or p starts with d + "/"
        # We normalize d (strip trailing slash) so both forms work.
        d = _normalize_path(dsl["value"]).rstrip("/")
        return z3.Or(
            f == z3.StringVal(d),
            z3.PrefixOf(z3.StringVal(d + "/"), f),
        )
    if op == "contains_arg":
        # True iff the argv-joined string contains the substring " VAL "
        # (with sentinel spaces) OR begins/ends with it. We bracket with
        # spaces to avoid matching substrings of unrelated args.
        val = dsl["value"]
        joined = f  # the act_..._argv_joined string
        bracketed = z3.Concat(z3.StringVal(" "), joined, z3.StringVal(" "))
        return z3.Contains(bracketed, z3.StringVal(f" {val} "))
    if op == "matches":
        # Z3 supports regex via re.* — but writing portable regex is
        # painful and the LLM rarely needs full regex power. We restrict
        # to a small set of patterns: prefix*, *suffix, *infix*.
        return _compile_glob(f, dsl["value"])

    raise ConditionError(f"unknown op: {op!r}")


def _z3_literal(v: Any) -> Any:
    if isinstance(v, bool):
        return z3.BoolVal(v)
    if isinstance(v, int):
        return z3.IntVal(v)
    if isinstance(v, str):
        return z3.StringVal(v)
    raise ConditionError(f"unsupported literal: {v!r}")


def _normalize_path(p: str) -> str:
    """Light normalization. We do NOT resolve symlinks here — that has to
    happen at the interceptor before the action enters the spec layer,
    because by the time we're at the verifier we're reasoning over the
    *intended* path as the spec describes it."""
    if not p.startswith("/"):
        # Relative paths are nonsense in a policy. The translator should
        # never emit them; surface this loudly.
        raise ConditionError(f"path rules must use absolute paths, got {p!r}")
    return p.rstrip("/") or "/"


def _compile_glob(field: Any, pattern: str) -> Any:
    """Compile a restricted glob (only `*` allowed, at start/end) to Z3."""
    if pattern.count("*") > 2:
        raise ConditionError(f"glob too complex (max 2 wildcards): {pattern!r}")
    starts = pattern.startswith("*")
    ends = pattern.endswith("*")
    core = pattern.strip("*")
    if not core:
        return z3.BoolVal(True)
    core_s = z3.StringVal(core)
    if starts and ends:
        return z3.Contains(field, core_s)
    if starts:
        return z3.SuffixOf(core_s, field)
    if ends:
        return z3.PrefixOf(core_s, field)
    return field == core_s


# ---------------------------------------------------------------------------
# Concrete-value substitution
# ---------------------------------------------------------------------------

def field_values_for(action: Action) -> dict[str, Any]:
    """Map an action's concrete fields to the Z3 constants in its symbolic
    context. Returns a dict suitable for use as a substitution / model
    constraint set by the verifier."""
    if isinstance(action, ShellExec):
        return {
            "command": z3.StringVal(action.command),
            "argv_joined": z3.StringVal(" ".join(action.argv)),
            "cwd": z3.StringVal(action.cwd),
            "raw": z3.StringVal(action.raw or ""),
        }
    if isinstance(action, FileRead):
        return {"path": z3.StringVal(action.path)}
    if isinstance(action, FileWrite):
        return {
            "path": z3.StringVal(action.path),
            "append": z3.BoolVal(action.append),
            "size_bytes": z3.IntVal(action.size_bytes),
        }
    if isinstance(action, FileDelete):
        return {
            "path": z3.StringVal(action.path),
            "recursive": z3.BoolVal(action.recursive),
        }
    if isinstance(action, NetworkRequest):
        return {
            "url": z3.StringVal(action.url),
            "method": z3.StringVal(action.method),
            "host": z3.StringVal(action.host),
            "port": z3.IntVal(action.port or 0),
        }
    if isinstance(action, ProcessSignal):
        return {
            "pid": z3.IntVal(action.pid),
            "signal_name": z3.StringVal(action.signal_name),
        }
    if isinstance(action, EnvRead):
        return {"var_name": z3.StringVal(action.var_name)}
    raise ConditionError(f"unhandled action type: {type(action).__name__}")
