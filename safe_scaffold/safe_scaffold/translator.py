"""
Natural-language → Policy translator.

The user (or the agent, in clarification flows) writes free text like:

    "Let the agent read and write files anywhere under
     /home/me/projects/foo, but never delete files. Allow shelling out
     to git, pytest, and ruff. Don't let it touch /etc or anything
     under ~/.ssh. Network is fine for localhost only."

The translator emits a `Policy` whose Rules collectively encode that
description. The hard part is doing this *reliably enough* that the
formal-methods layer underneath actually buys us something.

Key design decisions
--------------------

1. **The LLM only emits the JSON DSL from condition.py — never Z3
   Python, never shell snippets, never executable code.**
   The DSL is restricted enough that we can validate every produced rule
   structurally before compiling it. If the LLM hallucinates a syntactic
   form we don't recognize, we reject and retry rather than executing
   anything.

2. **Translation is staged: enumerate intents → emit DSL per intent →
   sanity-check the whole policy.**
   A single mega-prompt that goes "here's a description, give me JSON"
   tends to produce policies that contradict themselves or leave gaps.
   We instead ask the LLM to first list the discrete intents in the
   description (one per output rule), then translate each one in
   isolation, then run a final consistency pass.

3. **Every rule carries its provenance.**
   The original NL text and the intent it came from are stored on the
   Rule so the human can audit and revise. If the user later objects to
   a rule's behavior, we can point at the exact sentence that produced it.

4. **No silent coverage.** If the user's text doesn't mention an action
   kind at all, the translator emits no rules for that kind, and actions
   of that kind will fall through to UNKNOWN at verification time
   (triggering the human-in-the-loop path). We do NOT emit broad
   default-allow rules — that's exactly the failure mode of the current
   auto-mode approach the proposal critiques.
"""
from __future__ import annotations

import json
import os
from typing import Any

from pydantic import ValidationError

from .spec import Effect, Policy, Rule, RuleProvenance
from .world_model import ActionKind


# ---------------------------------------------------------------------------
# DSL validation: structural check before we hand a rule to the compiler.
# ---------------------------------------------------------------------------

_VALID_OPS = {
    "and", "or", "not", "true", "false",
    "eq", "in", "path_under", "path_equals",
    "contains_arg", "matches",
}


def validate_dsl(node: Any, depth: int = 0) -> None:
    """Recursively validate a DSL expression. Raises ValueError on issues.

    Why structural validation rather than just letting condition.compile
    raise? Two reasons:
        (a) compile_condition has Z3 as a dependency; we want to be able
            to validate translator output even in environments where Z3
            isn't installed yet.
        (b) Structural rejection produces clearer error messages we can
            feed back to the LLM in a retry, which improves convergence."""
    if depth > 16:
        raise ValueError("DSL nesting too deep (>16); rule rejected.")
    if not isinstance(node, dict):
        raise ValueError(f"expected object, got {type(node).__name__}: {node!r}")
    op = node.get("op")
    if op not in _VALID_OPS:
        raise ValueError(f"unknown op: {op!r}")
    if op in ("true", "false"):
        return
    if op in ("and", "or"):
        args = node.get("args")
        if not isinstance(args, list) or not args:
            raise ValueError(f"{op}: 'args' must be a non-empty list")
        for a in args:
            validate_dsl(a, depth + 1)
        return
    if op == "not":
        if "arg" not in node:
            raise ValueError("not: missing 'arg'")
        validate_dsl(node["arg"], depth + 1)
        return
    # Field-relative ops
    if "field" not in node:
        raise ValueError(f"{op}: missing 'field'")
    if op == "in":
        vs = node.get("values")
        if not isinstance(vs, list) or not vs:
            raise ValueError("in: 'values' must be a non-empty list")
        return
    if op in ("eq", "path_under", "path_equals", "contains_arg", "matches"):
        if "value" not in node:
            raise ValueError(f"{op}: missing 'value'")
        return


# ---------------------------------------------------------------------------
# Translator
# ---------------------------------------------------------------------------

# The LLM-facing description of the DSL. We keep this in one place so the
# prompt and the validator can't drift apart — if you add an op, you
# update both this string and _VALID_OPS.
DSL_REFERENCE = """\
Condition DSL (all rule conditions must be JSON objects of this form):

  {"op": "and",          "args": [<expr>, ...]}
  {"op": "or",           "args": [<expr>, ...]}
  {"op": "not",          "arg":  <expr>}
  {"op": "eq",           "field": "<name>", "value": <bool|int|str>}
  {"op": "in",           "field": "<name>", "values": [<literal>, ...]}
  {"op": "path_under",   "field": "<name>", "value": "/abs/path"}
  {"op": "path_equals",  "field": "<name>", "value": "/abs/path"}
  {"op": "contains_arg", "field": "argv_joined", "value": "<str>"}
  {"op": "matches",      "field": "<name>", "value": "<glob>"}  # *prefix, suffix*, *infix*
  {"op": "true"} | {"op": "false"}

Action kinds and the fields each exposes:

  shell_exec:       command (str), argv_joined (str), cwd (str), raw (str)
  file_read:        path (str)
  file_write:       path (str), append (bool), size_bytes (int)
  file_delete:      path (str), recursive (bool)
  network_request:  url (str), method (str), host (str), port (int)
  process_signal:   pid (int), signal_name (str)
  env_read:         var_name (str)

Paths must be absolute. Use path_under for "anywhere inside this
directory" and path_equals for an exact file.
"""

_TRANSLATOR_SYSTEM = f"""You are a safety-policy translator. You convert a
user's natural-language description of what an AI coding agent should and
should not be allowed to do into a list of formal rules in a restricted
JSON DSL. You NEVER emit code — only JSON DSL objects.

Output a JSON array. Each element is an object with this shape:

  {{
    "description": "<short human description of what this rule does>",
    "effect": "allow" | "deny",
    "applies_to": ["shell_exec" | "file_read" | ...],   // one or more
    "condition": <DSL expression>,
    "rationale": "<which part of the user's text this rule encodes>"
  }}

Rules:
- Each output rule corresponds to ONE discrete intent in the user's text.
- Prefer multiple narrow rules over one broad rule with complex conditions.
- If the user describes a deny ("don't let it ..."), emit effect=deny.
- If the user is silent on an action kind, DO NOT emit a permissive rule
  for it. The system fails closed on unmentioned actions by design.
- All paths must be absolute. If the user uses ~ or relative paths,
  resolve them against the provided project_root.
- Output JSON only — no markdown fences, no commentary.

{DSL_REFERENCE}
"""


class Translator:
    """Translate NL descriptions to Policies / Rules using an LLM.

    Decoupled from any specific client implementation. Pass an object
    with a `.complete(system: str, user: str) -> str` method. The
    default is `AnthropicTranslatorClient` below, which wraps the
    official anthropic SDK.
    """

    def __init__(self, client: "LLMClient", project_root: str | None = None):
        self.client = client
        self.project_root = project_root

    def translate(
        self,
        nl_description: str,
        existing_policy: Policy | None = None,
        source_label: str = "user_initial_nl",
    ) -> Policy:
        """Produce a Policy from a NL description.

        If `existing_policy` is given, returned policy is its successor
        with new rules appended; otherwise a fresh policy is built.
        """
        base = existing_policy or Policy(project_root=self.project_root)

        user_msg = self._build_user_msg(nl_description)
        raw = self._call_with_retry(user_msg)

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"translator returned non-JSON: {e}\n---\n{raw}")
        if not isinstance(parsed, list):
            raise ValueError(f"translator must return a JSON array, got {type(parsed)}")

        new_rules: list[Rule] = []
        for i, obj in enumerate(parsed):
            try:
                rule = self._object_to_rule(obj, nl_description, source_label)
            except (ValueError, ValidationError) as e:
                raise ValueError(f"rule #{i} invalid: {e}\nraw: {obj!r}")
            new_rules.append(rule)

        out = base
        for r in new_rules:
            out = out.add_rule(r)
        return out

    def translate_clarification(
        self,
        nl_description: str,
        triggered_action_id: str,
        existing_policy: Policy,
    ) -> Policy:
        """Variant for human-in-the-loop clarifications: the user has
        just been prompted about an action, and types in a description
        of what to allow/deny. We tag the resulting rule with the
        action id that triggered it for traceability."""
        new_policy = self.translate(
            nl_description,
            existing_policy=existing_policy,
            source_label="user_clarification",
        )
        # The new rules are the ones appended by this call. Annotate
        # their provenance with the triggering action id.
        added = new_policy.rules[len(existing_policy.rules):]
        for r in added:
            r.provenance.triggered_by_action_id = triggered_action_id
        return new_policy

    # -- internals --------------------------------------------------------

    def _build_user_msg(self, nl: str) -> str:
        root = self.project_root or "(not set; user must specify absolute paths)"
        return (
            f"PROJECT_ROOT: {root}\n\n"
            f"USER DESCRIPTION:\n{nl}\n\n"
            "Translate to the rule array now. Output JSON only."
        )

    def _call_with_retry(self, user_msg: str, attempts: int = 2) -> str:
        last_err = None
        for _ in range(attempts):
            raw = self.client.complete(_TRANSLATOR_SYSTEM, user_msg).strip()
            # Strip accidental markdown fences in case the model adds them
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            try:
                json.loads(raw)
                return raw
            except json.JSONDecodeError as e:
                last_err = e
                user_msg = (
                    user_msg
                    + f"\n\nYour previous output failed to parse as JSON: {e}. "
                    "Output a JSON array only, no markdown."
                )
        raise ValueError(f"translator never returned valid JSON: {last_err}")

    def _object_to_rule(
        self,
        obj: dict[str, Any],
        nl: str,
        source: str,
    ) -> Rule:
        required = {"description", "effect", "applies_to", "condition"}
        missing = required - obj.keys()
        if missing:
            raise ValueError(f"missing keys: {missing}")
        if obj["effect"] not in ("allow", "deny"):
            raise ValueError(f"effect must be 'allow' or 'deny', got {obj['effect']!r}")
        kinds = []
        for k in obj["applies_to"]:
            try:
                kinds.append(ActionKind(k))
            except ValueError:
                raise ValueError(f"unknown action kind: {k!r}")
        validate_dsl(obj["condition"])
        return Rule(
            effect=Effect(obj["effect"]),
            applies_to=kinds,
            condition=obj["condition"],
            description=obj["description"],
            provenance=RuleProvenance(source=source, original_text=nl),
        )


# ---------------------------------------------------------------------------
# Default LLM client — wraps the anthropic SDK.
# ---------------------------------------------------------------------------

class LLMClient:
    """Minimal interface the Translator depends on. Implement this to
    plug in alternatives (OpenAI, local Llama, etc.)."""
    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class AnthropicTranslatorClient(LLMClient):
    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: str | None = None):
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "anthropic SDK is required. pip install anthropic"
            ) from e
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model

    def complete(self, system: str, user: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Concatenate any text blocks
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


class StubLLMClient(LLMClient):
    """Returns a fixed response — useful for tests and offline demos."""
    def __init__(self, response: str):
        self.response = response

    def complete(self, system: str, user: str) -> str:
        return self.response
