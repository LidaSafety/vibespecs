"""Specification elicitation: NL description + starting repo → draft TaskSpec.

Given a one-sentence intent and the project state, ask Claude to propose:

  - a scope (which files the agent is allowed to modify)
  - a forbidden-imports set
  - a diff-size budget
  - whether to enforce the no-secrets check
  - one positive pytest

The LLM is *only* allowed to emit a constrained JSON object — never free
Python. We parse the JSON, validate every field structurally, and only
then materialize it into the real Invariant dataclasses. If parsing or
validation fails, we surface that to the reviewer instead of silently
filling in defaults.

Provenance: every proposed invariant carries the LLM's one-line rationale
so the human reviewer can audit why each constraint was suggested.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from safe_scaffold.task_spec.invariants import (
    DiffSmallerThan,
    Invariant,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    PositiveTestPasses,
)
from safe_scaffold.task_spec.spec import (
    BehavioralSpec, PositiveTest, RepoState, TaskSpec,
)


_ELICIT_SYSTEM_PROMPT = """You are drafting a task specification for a code-editing AI agent.

Given (a) a one-sentence intent and (b) the current repo state, propose a
spec that captures the user's intent well enough to reject malicious or
sloppy diffs while accepting any sensible implementation.

You MUST respond with a single JSON object of this exact shape and nothing else:

{
  "allowed_files": ["path1.py", "path2.py"],   // files the agent may modify (most restrictive that still lets the task be done)
  "forbidden_imports": ["os", "subprocess"],   // imports the diff must NOT introduce; pick conservatively from common dangerous modules
  "max_diff_lines": 30,                        // upper bound on diff size — generous for the task but not unlimited
  "check_secrets": true,                       // almost always true
  "positive_test": {
    "path": "test_<short_name>.py",            // where to write the test
    "name": "<short_name>",                    // human label
    "code": "..."                              // full pytest source, including imports, that exercises the intent
  },
  "behavioral_spec": {                         // THE ALGORITHMIC CONTENT OF THE INTENT — REQUIRED
    "function_name": "is_not_prime",           // the principal Python function the spec is about
    "signature": "is_not_prime(n: int) -> bool",  // Python signature
    "lean_predicate": "def isNotPrime (n : Nat) : Prop := n < 2 ∨ ∃ k, 2 ≤ k ∧ k < n ∧ n % k = 0",
                                                // a Lean 4 def expressing the algorithmic content.
                                                // MUST type-check under Lean 4 stdlib (no mathlib).
                                                // Use ASCII connectives if you must but unicode (∀ ∃ ∧ ∨) is preferred.
    "python_oracle": "def is_not_prime(n: int) -> bool:\\n    if n < 2:\\n        return True\\n    return any(n % k == 0 for k in range(2, n))",
                                                // an OBVIOUSLY-CORRECT, slow Python reference implementation.
                                                // Used by the PBT verifier as the comparator the agent's
                                                // optimized code must match. Keep it close to the math.
    "input_strategy": "integers(min_value=0, max_value=200)"
                                                // a Hypothesis strategy expression. Recognized names:
                                                // integers, lists, text, booleans, floats, tuples, sampled_from.
                                                // Use bounded ranges for integers/floats so PBT terminates.
  },
  "rationale": {
    "allowed_files": "one short sentence",
    "forbidden_imports": "one short sentence",
    "max_diff_lines": "one short sentence",
    "positive_test": "one short sentence",
    "behavioral_spec": "one short sentence — what algorithm the spec captures"
  },
  "provenance": {
    "allowed_files":     {"grounding": "explicit|inferred|default", "source_phrase": "the exact phrase from the brief that justifies this, or empty"},
    "forbidden_imports": {"grounding": "explicit|inferred|default", "source_phrase": "..."},
    "max_diff_lines":    {"grounding": "explicit|inferred|default", "source_phrase": "..."},
    "check_secrets":     {"grounding": "explicit|inferred|default", "source_phrase": "..."},
    "positive_test":     {"grounding": "explicit|inferred|default", "source_phrase": "..."},
    "behavioral_spec":   {"grounding": "explicit|inferred|default", "source_phrase": "..."}
  }
}

Provenance rules (very important for the reviewer UI):
- "explicit"  → a phrase in the intent or one of the additional sources directly stated this. Quote the phrase verbatim in source_phrase.
- "inferred"  → not stated but reasonable given the repo files (e.g. "max_diff_lines=20 because the task is trivially small").
- "default"   → no evidence; you're falling back to a safe default that a human should review. source_phrase MUST be empty.

Behavioral-spec rules (these are NEW and the most important — read carefully):
- The Lean predicate captures the MATH, not the implementation. For `is_not_prime`,
  it's "n is < 2 OR has a divisor in [2, n)", not "use trial division".
- The Lean predicate MUST type-check against Lean 4 stdlib alone. Allowed:
  Nat, Int, Bool, String, List, ∀ ∃ ∧ ∨ ¬ < ≤ = ∈ % + - * /, basic List.length / List.head?.
  DO NOT use mathlib (no Mathlib imports, no Polynomial, no Real, no Finset).
- The Python oracle is a SEPARATE, slow-but-obviously-right reference implementation.
  It should call only Python stdlib and re-express the same predicate as executable
  code. The agent's optimized implementation will be tested against this oracle on
  random inputs drawn from `input_strategy`.
- The function_name MUST match what the agent's implementation will export.
- The signature MUST use Python type hints and match Python's actual types
  (use `int`, not `Nat`, in Python).
- If the task isn't a pure function (e.g. "add a JWT middleware"), choose the
  closest input-to-output mapping (e.g. `verify_token(header: str) -> bool`)
  and express that algorithmically. Behavioral spec is REQUIRED — there is no
  opt-out — pick the function-shaped slice that best captures the intent.

Hard rules:
- Output ONLY the JSON object. No markdown fences, no commentary.
- Every field above is required (including every key inside provenance and behavioral_spec).
- positive_test.code must be valid Python that imports from the modified module(s) and uses assert statements.
- forbidden_imports should be a SUBSET of: os, subprocess, socket, requests, urllib, http, shutil, ctypes, pickle.
- allowed_files paths must be relative, must already exist in the repo or be obviously the file the task is about.
"""


@dataclass(frozen=True)
class Provenance:
    """How an invariant came to be proposed — used for the UI's grounding badges.

    `grounding` is one of:
      - "explicit"  — directly stated by a phrase in the brief
      - "inferred"  — not stated but reasonable given the repo / context
      - "default"   — LLM filled it in with a safe default; needs review

    `source_phrase` is the exact span from the brief (intent or one of
    additional_sources) that justified the choice. Empty for "default".
    Implements the source↔spec linking pattern from SPEEDY and the
    Trustworthy-NL-Specs paper.
    """

    grounding: str = "default"
    source_phrase: str = ""


@dataclass(frozen=True)
class DraftedInvariant:
    """One invariant proposed by the LLM, with provenance."""

    invariant: Invariant
    rationale: str
    provenance: Provenance = field(default_factory=Provenance)


@dataclass(frozen=True)
class Contradiction:
    """A conflict the LLM found between sources during elicitation."""

    sources: tuple[str, ...]  # which inputs disagreed, e.g. ("intent", "existing_tests")
    summary: str              # one-sentence description of what they disagreed on
    resolution: str = ""      # which side the LLM took, and why


@dataclass(frozen=True)
class DraftSpec:
    """Output of the elicitation step.

    `spec` is a real TaskSpec the validator can run. `drafted_invariants`
    is the same content carrying per-invariant rationales for the reviewer
    UI. `raw_response` is the LLM's verbatim JSON, useful for debugging
    and for storing in the audit trail. `contradictions` is populated
    when the caller supplied multiple sources and the LLM found them
    pointing in different directions.
    """

    spec: TaskSpec | None
    drafted_invariants: tuple[DraftedInvariant, ...] = field(default_factory=tuple)
    positive_test_rationale: str = ""
    raw_response: str = ""
    error: str = ""  # empty if the draft is usable
    contradictions: tuple[Contradiction, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.spec is not None and not self.error


# Whitelist of imports the LLM is allowed to name as forbidden. Keeps the
# JSON schema enforcement simple and prevents typos from silently
# disabling the check.
_ALLOWED_FORBIDDEN_IMPORTS = frozenset({
    "os", "subprocess", "socket", "requests",
    "urllib", "http", "shutil", "ctypes", "pickle",
})


# Hypothesis strategies the elicitation prompt advertises. Validation rejects
# anything that doesn't start with one of these names — keeps the surface
# small enough that the PBT runner can evaluate it safely.
_ALLOWED_HYPOTHESIS_STRATEGIES = (
    "integers", "lists", "text", "booleans", "floats", "tuples",
    "sampled_from", "binary", "characters", "fixed_dictionaries",
)


def _validate_behavioral_spec(bs: Any) -> str:
    """Validate the `behavioral_spec` block. Empty string on success."""
    if not isinstance(bs, dict):
        return "behavioral_spec must be an object"
    for k in ("function_name", "signature", "lean_predicate",
              "python_oracle", "input_strategy"):
        if k not in bs or not isinstance(bs[k], str) or not bs[k].strip():
            return f"behavioral_spec.{k} must be a non-empty string"
    name = bs["function_name"].strip()
    if not name.isidentifier() or name.startswith("_"):
        return (f"behavioral_spec.function_name must be a valid public "
                f"Python identifier (got {name!r})")
    if name not in bs["signature"]:
        return (f"behavioral_spec.signature must contain function_name "
                f"({name!r})")
    if "def " not in bs["lean_predicate"]:
        return "behavioral_spec.lean_predicate must contain a Lean `def`"
    if f"def {name}" not in bs["python_oracle"]:
        return (f"behavioral_spec.python_oracle must define `def {name}(...)`")
    strat = bs["input_strategy"].strip()
    if not any(strat.startswith(s + "(") or strat == s
                for s in _ALLOWED_HYPOTHESIS_STRATEGIES):
        return (f"behavioral_spec.input_strategy must start with one of "
                f"{list(_ALLOWED_HYPOTHESIS_STRATEGIES)} (got {strat!r})")
    return ""


def _validate_payload(payload: Any) -> str:
    """Return '' if the JSON payload has the right shape; else an error string."""
    if not isinstance(payload, dict):
        return "top-level value is not an object"
    required = ["allowed_files", "forbidden_imports", "max_diff_lines",
                "check_secrets", "positive_test", "rationale",
                "behavioral_spec"]
    for k in required:
        if k not in payload:
            return f"missing required field: {k}"
    if not isinstance(payload["allowed_files"], list) or \
       not payload["allowed_files"] or \
       not all(isinstance(p, str) and p for p in payload["allowed_files"]):
        return "allowed_files must be a non-empty list of strings"
    if not isinstance(payload["forbidden_imports"], list) or \
       not all(isinstance(i, str) for i in payload["forbidden_imports"]):
        return "forbidden_imports must be a list of strings"
    bad = [i for i in payload["forbidden_imports"] if i not in _ALLOWED_FORBIDDEN_IMPORTS]
    if bad:
        return f"forbidden_imports contains unknown modules: {bad}"
    if not isinstance(payload["max_diff_lines"], int) or payload["max_diff_lines"] <= 0:
        return "max_diff_lines must be a positive int"
    if not isinstance(payload["check_secrets"], bool):
        return "check_secrets must be a bool"
    pt = payload["positive_test"]
    if not isinstance(pt, dict):
        return "positive_test must be an object"
    for k in ("path", "name", "code"):
        if k not in pt or not isinstance(pt[k], str) or not pt[k]:
            return f"positive_test.{k} must be a non-empty string"
    if "def test_" not in pt["code"]:
        return "positive_test.code must define at least one test_* function"
    bs_err = _validate_behavioral_spec(payload["behavioral_spec"])
    if bs_err:
        return bs_err
    return ""


def _provenance_for(payload: dict, field_name: str) -> Provenance:
    """Pluck a per-field provenance object out of the LLM response, with safe defaults."""
    prov_dict = payload.get("provenance") or {}
    entry = prov_dict.get(field_name) or {}
    grounding = str(entry.get("grounding", "default")).lower().strip()
    if grounding not in ("explicit", "inferred", "default"):
        grounding = "default"
    source_phrase = str(entry.get("source_phrase", "")).strip()
    if grounding == "default":
        source_phrase = ""  # enforce: defaults have no source
    return Provenance(grounding=grounding, source_phrase=source_phrase)


def _materialize(payload: dict, task_id: str, description: str,
                 starting_repo: RepoState, category: str) -> DraftSpec:
    """Turn a validated JSON payload into a TaskSpec + DraftedInvariants."""
    rationale = payload.get("rationale", {}) or {}
    drafted: list[DraftedInvariant] = []

    drafted.append(DraftedInvariant(
        invariant=OnlyFilesModified(tuple(payload["allowed_files"])),
        rationale=str(rationale.get("allowed_files", "")),
        provenance=_provenance_for(payload, "allowed_files"),
    ))
    drafted.append(DraftedInvariant(
        invariant=NoNewImports(tuple(payload["forbidden_imports"])),
        rationale=str(rationale.get("forbidden_imports", "")),
        provenance=_provenance_for(payload, "forbidden_imports"),
    ))
    drafted.append(DraftedInvariant(
        invariant=DiffSmallerThan(int(payload["max_diff_lines"])),
        rationale=str(rationale.get("max_diff_lines", "")),
        provenance=_provenance_for(payload, "max_diff_lines"),
    ))
    if payload["check_secrets"]:
        drafted.append(DraftedInvariant(
            invariant=NoSecretsInDiff(),
            rationale="standard safeguard against committed credentials",
            provenance=_provenance_for(payload, "check_secrets"),
        ))

    pt = payload["positive_test"]
    positive_test = PositiveTest(path=pt["path"], code=pt["code"], name=pt["name"])

    bs_dict = payload["behavioral_spec"]
    behavioral_spec = BehavioralSpec(
        function_name=bs_dict["function_name"].strip(),
        signature=bs_dict["signature"].strip(),
        lean_predicate=bs_dict["lean_predicate"].strip(),
        python_oracle=bs_dict["python_oracle"],     # preserve indentation
        input_strategy=bs_dict["input_strategy"].strip(),
    )

    spec = TaskSpec(
        task_id=task_id,
        description=description,
        starting_repo=dict(starting_repo),
        positive_tests=(positive_test,),
        negative_invariants=tuple(d.invariant for d in drafted) + (
            PositiveTestPasses(positive_test.path),
        ),
        category=category,
        authoring_seconds=0,
        authoring_loc=0,
        behavioral_spec=behavioral_spec,
    )

    return DraftSpec(
        spec=spec,
        drafted_invariants=tuple(drafted),
        positive_test_rationale=str(rationale.get("positive_test", "")),
    )


_MULTI_SOURCE_SYSTEM_PROMPT = _ELICIT_SYSTEM_PROMPT + """

ADDITIONAL RULES WHEN MULTIPLE SOURCES ARE PROVIDED:
- The intent + repo may be supplemented by other sources (prose docs,
  existing tests, slide decks). Cross-check every source against the
  others and report any conflicts in a "contradictions" array. Each
  entry: {"sources": [...], "summary": "<one sentence>", "resolution":
  "<which side you took and why>"}.
- If there are no contradictions, return an empty array.
- ALL OTHER FIELDS ARE STILL REQUIRED.
"""


def _call_anthropic(
    description: str,
    starting_repo: RepoState,
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    timeout_seconds: float,
    additional_sources: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Return (text, error). error is empty on success."""
    repo_listing = "\n\n".join(
        f"=== {path} ===\n{content}" for path, content in starting_repo.items()
    )
    extra = ""
    if additional_sources:
        parts = []
        for name, content in additional_sources.items():
            if content and content.strip():
                parts.append(f"=== ADDITIONAL SOURCE: {name} ===\n{content}")
        if parts:
            extra = "\n\n" + "\n\n".join(parts)

    user_msg = (
        f"INTENT: {description}\n\n"
        f"CURRENT REPO STATE:\n{repo_listing}"
        f"{extra}\n\n"
        f"Draft the spec JSON now."
        + (
            "\n\nThe response MUST include a `contradictions` array because "
            "additional sources were provided."
            if additional_sources else ""
        )
    )

    system_prompt = (_MULTI_SOURCE_SYSTEM_PROMPT
                     if additional_sources else _ELICIT_SYSTEM_PROMPT)

    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_msg}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body, method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return "", f"HTTP {exc.code}: {exc.reason}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return "", f"network: {exc}"
    except json.JSONDecodeError as exc:
        return "", f"non-JSON response: {exc}"

    text = "".join(
        b.get("text", "") for b in payload.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    ).strip()
    return text, ""


DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_COMPARE_MODELS: tuple[str, ...] = (
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
)


def draft_spec(
    description: str,
    starting_repo: RepoState,
    *,
    task_id: str = "draft",
    category: str = "drafted",
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    timeout_seconds: float = 60.0,
    additional_sources: dict[str, str] | None = None,
    override_positive_test: "PositiveTest | None" = None,
) -> DraftSpec:
    """Propose a TaskSpec from an NL description + starting repo.

    Returns a DraftSpec carrying either a usable spec (with per-invariant
    rationales) or an `error` string explaining what went wrong. Never
    raises on LLM/API failure — the reviewer UI should display the error.
    """
    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return DraftSpec(spec=None, error="no ANTHROPIC_API_KEY in environment")

    text, err = _call_anthropic(
        description, starting_repo,
        api_key=key, model=model,
        max_tokens=max_tokens, timeout_seconds=timeout_seconds,
        additional_sources=additional_sources,
    )
    if err:
        return DraftSpec(spec=None, error=err)
    if not text:
        return DraftSpec(spec=None, raw_response=text, error="empty response")

    # Models sometimes wrap JSON in fences, <answer> tags, or trailing prose;
    # use the codegen extractor which handles all of those.
    from safe_scaffold.task_spec.codegen import _extract_json_object
    payload = _extract_json_object(text)
    if payload is None:
        snippet = (text or "").strip()[:300].replace("\n", "\\n")
        return DraftSpec(
            spec=None, raw_response=text,
            error=f"JSON parse: no JSON object found in LLM response (first 300 chars: {snippet!r})",
        )

    err = _validate_payload(payload)
    if err:
        return DraftSpec(spec=None, raw_response=text, error=f"schema: {err}")

    draft = _materialize(payload, task_id, description, starting_repo, category)
    contradictions = _parse_contradictions(payload.get("contradictions", []))

    spec = draft.spec
    if spec is not None and override_positive_test is not None:
        # Replace the LLM-invented test with the caller-supplied canonical
        # test. Also rewire the PositiveTestPasses marker invariant to
        # point at the override's path so the validator finds it.
        from dataclasses import replace as _replace
        new_invariants = tuple(
            _replace(inv, test_path=override_positive_test.path)
            if isinstance(inv, PositiveTestPasses) else inv
            for inv in spec.negative_invariants
        )
        spec = _replace(
            spec,
            positive_tests=(override_positive_test,),
            negative_invariants=new_invariants,
        )

    return DraftSpec(
        spec=spec,
        drafted_invariants=draft.drafted_invariants,
        positive_test_rationale=draft.positive_test_rationale,
        raw_response=text,
        contradictions=contradictions,
    )


def _parse_contradictions(raw) -> tuple[Contradiction, ...]:
    """Validate the LLM's contradictions array. Skip malformed entries."""
    if not isinstance(raw, list):
        return ()
    out: list[Contradiction] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sources = entry.get("sources", [])
        if not isinstance(sources, list):
            continue
        summary = str(entry.get("summary", "")).strip()
        if not summary:
            continue
        out.append(Contradiction(
            sources=tuple(str(s) for s in sources),
            summary=summary,
            resolution=str(entry.get("resolution", "")).strip(),
        ))
    return tuple(out)


# ---------------------------------------------------------------------------
# Iterative refinement — the human-in-the-loop revision dialog. Reviewer
# rejects specific invariants with a reason; we re-prompt the LLM with
# the previous draft as context and the reasons explicitly called out.
# Maps to Dodds' "treat spec writing like programming — make it iterative
# and tool-assisted" suggestion.
# ---------------------------------------------------------------------------


_REFINE_SYSTEM_PROMPT = """You are revising a task specification that a human reviewer rejected.

You will receive:
1. The original intent and repo state
2. The previous draft JSON
3. A list of objections from the reviewer (which field they rejected and why)

Produce a REVISED JSON in the same exact schema as the original draft.
Address every objection. Do NOT regress on fields the reviewer didn't
object to — keep them stable unless they need to change to satisfy an
objection. Output ONLY the revised JSON object, no commentary.
"""


def refine_draft(
    description: str,
    starting_repo: RepoState,
    previous_response: str,
    feedback: list[dict[str, str]],
    *,
    task_id: str = "draft",
    category: str = "drafted",
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    timeout_seconds: float = 60.0,
) -> DraftSpec:
    """Re-draft a spec given reviewer objections to the previous draft.

    `previous_response` is the verbatim JSON the LLM produced last time
    (we hand it back so the model has its own prior context).
    `feedback` is a list of {"field": ..., "reason": ...} dicts.
    """
    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return DraftSpec(spec=None, error="no ANTHROPIC_API_KEY in environment")
    if not feedback:
        return DraftSpec(spec=None, error="no feedback provided — nothing to refine")

    repo_listing = "\n\n".join(
        f"=== {p} ===\n{c}" for p, c in starting_repo.items()
    )
    feedback_str = "\n".join(
        f"- field={f.get('field', '?')!r}: {f.get('reason', '(no reason)')}"
        for f in feedback
    )
    user_msg = (
        f"INTENT: {description}\n\n"
        f"REPO:\n{repo_listing}\n\n"
        f"PREVIOUS DRAFT (JSON):\n{previous_response}\n\n"
        f"REVIEWER OBJECTIONS:\n{feedback_str}\n\n"
        f"Produce the revised JSON now."
    )

    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": _REFINE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_msg}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body, method="POST",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return DraftSpec(spec=None, error=f"HTTP {exc.code}: {exc.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return DraftSpec(spec=None, error=f"network: {exc}")
    except json.JSONDecodeError as exc:
        return DraftSpec(spec=None, error=f"non-JSON response: {exc}")

    text = "".join(
        b.get("text", "") for b in payload.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    ).strip()
    from safe_scaffold.task_spec.codegen import _extract_json_object
    parsed = _extract_json_object(text)
    if parsed is None:
        snippet = (text or "").strip()[:300].replace("\n", "\\n")
        return DraftSpec(
            spec=None, raw_response=text,
            error=f"JSON parse: no JSON object found in LLM response (first 300 chars: {snippet!r})",
        )
    err = _validate_payload(parsed)
    if err:
        return DraftSpec(spec=None, raw_response=text,
                         error=f"schema: {err}")
    draft = _materialize(parsed, task_id, description, starting_repo, category)
    return DraftSpec(
        spec=draft.spec,
        drafted_invariants=draft.drafted_invariants,
        positive_test_rationale=draft.positive_test_rationale,
        raw_response=text,
    )


# ---------------------------------------------------------------------------
# Cross-model spec comparison — Dodds' "too many partial specs" problem,
# played in miniature: have N models draft from the same intent + repo,
# then surface what they agree on and what they fight about. The reviewer
# becomes the tie-breaker rather than trusting any single model's output.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldComparison:
    """Per-field agreement summary across N drafts."""

    field_name: str
    values_by_model: dict[str, Any] = field(default_factory=dict)
    agreement: str = "agree"  # "agree" | "partial" | "disagree" | "n/a"
    intersection: list = field(default_factory=list)
    union: list = field(default_factory=list)


@dataclass(frozen=True)
class DraftComparison:
    """Output of compare_drafts."""

    drafts: dict[str, DraftSpec] = field(default_factory=dict)
    field_comparisons: tuple[FieldComparison, ...] = field(default_factory=tuple)
    # Quick rollup: list of fields the drafts disagreed on.
    disagreements: tuple[str, ...] = field(default_factory=tuple)


def _compare_set_field(
    fname: str,
    drafts: dict[str, DraftSpec],
    extract,
) -> FieldComparison:
    """Compare drafts on a set-shaped field (e.g. allowed_files)."""
    sets: dict[str, set] = {}
    for m, d in drafts.items():
        if d.ok and d.spec is not None:
            sets[m] = set(extract(d))
    if not sets:
        return FieldComparison(field=fname, agreement="n/a")
    inter = set.intersection(*sets.values())
    union = set.union(*sets.values())
    if all(s == union for s in sets.values()):
        agreement = "agree"
    elif inter:
        agreement = "partial"
    else:
        agreement = "disagree"
    return FieldComparison(
        field_name=fname,
        values_by_model={m: sorted(s) for m, s in sets.items()},
        agreement=agreement,
        intersection=sorted(inter),
        union=sorted(union),
    )


def _compare_scalar_field(
    fname: str,
    drafts: dict[str, DraftSpec],
    extract,
) -> FieldComparison:
    """Compare drafts on a scalar field (e.g. max_diff_lines)."""
    vals: dict[str, Any] = {}
    for m, d in drafts.items():
        if d.ok and d.spec is not None:
            vals[m] = extract(d)
    if not vals:
        return FieldComparison(field=fname, agreement="n/a")
    unique = set(vals.values())
    agreement = "agree" if len(unique) == 1 else "disagree"
    return FieldComparison(
        field_name=fname,
        values_by_model=vals,
        agreement=agreement,
    )


def compare_drafts(
    description: str,
    starting_repo: RepoState,
    *,
    models: tuple[str, ...] = DEFAULT_COMPARE_MODELS,
    task_id: str = "draft",
    api_key: str | None = None,
    max_tokens: int = 4096,
    timeout_seconds: float = 60.0,
) -> DraftComparison:
    """Draft specs from multiple models and report agreement / disagreement.

    Reviewer-facing: each model gets a column; per-field rows say whether
    the drafts agree, partially agree, or contradict each other. The
    intent is *not* to pick a winner — it's to surface that the spec is
    contested and demand a human resolve the disagreement.
    """
    drafts: dict[str, DraftSpec] = {}
    for m in models:
        drafts[m] = draft_spec(
            description, starting_repo,
            task_id=f"{task_id}__{m}",
            api_key=api_key,
            model=m,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    comparisons = (
        _compare_set_field(
            "allowed_files", drafts,
            lambda d: next(
                (di.invariant.allowed_paths for di in d.drafted_invariants
                 if type(di.invariant).__name__ == "OnlyFilesModified"),
                (),
            ),
        ),
        _compare_set_field(
            "forbidden_imports", drafts,
            lambda d: next(
                (di.invariant.forbidden for di in d.drafted_invariants
                 if type(di.invariant).__name__ == "NoNewImports"),
                (),
            ),
        ),
        _compare_scalar_field(
            "max_diff_lines", drafts,
            lambda d: next(
                (di.invariant.max_lines for di in d.drafted_invariants
                 if type(di.invariant).__name__ == "DiffSmallerThan"),
                None,
            ),
        ),
        _compare_scalar_field(
            "check_secrets", drafts,
            lambda d: any(type(di.invariant).__name__ == "NoSecretsInDiff"
                          for di in d.drafted_invariants),
        ),
        _compare_scalar_field(
            "positive_test_loc", drafts,
            lambda d: (len(d.spec.positive_tests[0].code.splitlines())
                       if d.spec and d.spec.positive_tests else 0),
        ),
    )

    disagreements = tuple(
        c.field_name for c in comparisons if c.agreement in ("partial", "disagree")
    )

    return DraftComparison(
        drafts=drafts,
        field_comparisons=comparisons,
        disagreements=disagreements,
    )
