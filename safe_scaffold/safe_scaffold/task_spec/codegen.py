"""Step 4 of the pipeline: spec → Python implementation.

Closes the loop from Mike Dodds' essay: once we have a partial-but-
typed-and-validated spec, can we get an LLM to *write the code that
satisfies it*? The answer is: usually yes, and when not, the validator
tells us exactly why (which invariant tripped, which positive test
failed) so the failure mode is auditable.

Workflow:

  description + starting_repo + drafted_spec
    -> LLM emits {"files": {path: code, ...}} (JSON only)
    -> merge files into starting_repo
    -> run StructuredValidator on the result
    -> return {modified_repo, verdict, raw_response}

If the LLM produces code that doesn't satisfy the spec, the verdict
will be REJECT with a per-invariant trace pointing at the cause. That's
exactly what a human-in-the-loop coding agent would want to feed back
into the next iteration.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from safe_scaffold.task_spec.baselines import StructuredValidator
from safe_scaffold.task_spec.spec import (
    Candidate,
    CandidateLabel,
    TaskSpec,
    Verdict,
)
from safe_scaffold.task_spec.verify_pbt import PBTResult, verify_against_oracle


_CODEGEN_SYSTEM_PROMPT = """You are an AI coding agent implementing a task that has a formal spec.

You will receive:
- A one-sentence intent
- The starting repo state (path -> current contents)
- A list of invariants the produced code MUST satisfy
- The positive test that must pass

You MUST respond with a single JSON object of this shape and nothing else:

{
  "files": {
    "path/to/file.py": "full new contents of this file",
    ...
  },
  "notes": "one short sentence on what you did"
}

Hard rules (re-read carefully):
- Your ENTIRE response must be parseable as a single JSON object.
- NO preamble. NO "Let me analyze...". NO step-by-step reasoning text.
- NO markdown code fences (no ```json, no ```).
- NO <answer>, <result>, <output>, or any other wrapping tags.
- Start your response with `{` and end with `}`. Nothing else.
- If you need to reason internally, do it silently — only emit the JSON.
- Each file's value is its FULL new contents (not a diff). Include only
  files you actually modified or created.
- Do NOT touch files outside the allowed scope (the invariants will tell
  you which paths are allowed).
- Do NOT introduce any of the forbidden imports the invariants list.
- Keep the diff small enough to fit under the max_diff_lines bound.
- The positive test must pass against your output.
"""


@dataclass(frozen=True)
class CodegenResult:
    """Outcome of one round of LLM code generation.

    `verdict` is the structural validator's accept/reject (file scope,
    forbidden imports, diff size, secrets, positive test).
    `pbt_result` is the additional behavioral verification: did the
    LLM-emitted Python oracle agree with the agent's implementation on
    every Hypothesis input? Both must succeed for `ok` to be True.
    """

    modified_repo: dict[str, str] = field(default_factory=dict)
    verdict: Verdict | None = None
    pbt_result: PBTResult | None = None
    raw_response: str = ""
    error: str = ""
    notes: str = ""

    @property
    def ok(self) -> bool:
        structural_ok = self.verdict is not None and self.verdict.accepted
        # If the spec has no behavioral_spec, pbt is skipped (None);
        # don't penalize. If it ran, require `verified`.
        behavioral_ok = self.pbt_result is None or self.pbt_result.ok
        return structural_ok and behavioral_ok

    @property
    def files_changed(self) -> list[str]:
        return sorted(self.modified_repo.keys())


def _extract_json_object(text: str) -> dict | None:
    """Find the first top-level JSON object in `text`, robust to common
    LLM wrappings.

    Tries, in order:
      1. The whole text as-is.
      2. Stripped of a single ```json ... ``` or ``` ... ``` fence.
      3. Stripped of <answer> / <result> / <output> tags.
      4. Brace-counted substring starting at the first `{` —
         finds the matching `}` even if the model emits text after the JSON.
    """
    candidates = [text.strip()]

    m = re.match(r"^```(?:json)?\s*(.+?)\s*```$",
                  text.strip(), flags=re.DOTALL)
    if m:
        candidates.append(m.group(1).strip())

    m = re.search(
        r"<(?:answer|result|output)\s*>\s*(.+?)\s*</(?:answer|result|output)>",
        text, flags=re.DOTALL | re.IGNORECASE,
    )
    if m:
        candidates.append(m.group(1).strip())
        # The inner content might itself be in a fenced code block.
        inner_m = re.match(r"^```(?:json)?\s*(.+?)\s*```$",
                            m.group(1).strip(), flags=re.DOTALL)
        if inner_m:
            candidates.append(inner_m.group(1).strip())

    # Brace-counted substring: find the first '{' and scan forward until
    # we find the matching '}'. Handles preamble + JSON + trailing prose.
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _invariants_for_prompt(spec: TaskSpec) -> str:
    lines = []
    for inv in spec.negative_invariants:
        t = type(inv).__name__
        if t == "OnlyFilesModified":
            lines.append(f"- OnlyFilesModified(allowed={list(inv.allowed_paths)}): "
                         "you may only create/modify files in this list")
        elif t == "NoNewImports":
            lines.append(f"- NoNewImports(forbidden={list(inv.forbidden)}): "
                         "you may NOT introduce any of these top-level imports")
        elif t == "DiffSmallerThan":
            lines.append(f"- DiffSmallerThan(max_lines={inv.max_lines}): "
                         "your total added+removed lines must be <= this")
        elif t == "NoSecretsInDiff":
            lines.append("- NoSecretsInDiff: your code must not include AWS keys, "
                         "API tokens, private-key blocks, or hardcoded password "
                         "literals")
        elif t == "FilesUnchanged":
            lines.append(f"- FilesUnchanged(frozen={list(inv.paths)}): you may "
                         "NOT modify these files at all")
        elif t == "PositiveTestPasses":
            # Behavioural — described via the test itself below.
            continue
        else:
            lines.append(f"- {t}: (consult the validator)")
    return "\n".join(lines) if lines else "(none)"


def _build_user_msg(spec: TaskSpec) -> str:
    repo_blob = "\n\n".join(
        f"=== {p} ===\n{c}" for p, c in spec.starting_repo.items()
    )
    test_blob = "\n\n".join(
        f"=== {t.path} ===\n{t.code}" for t in spec.positive_tests
    ) or "(no positive tests)"
    return (
        f"INTENT: {spec.description}\n\n"
        f"STARTING REPO:\n{repo_blob}\n\n"
        f"INVARIANTS YOUR OUTPUT MUST SATISFY:\n{_invariants_for_prompt(spec)}\n\n"
        f"POSITIVE TEST THAT MUST PASS:\n{test_blob}\n\n"
        f"Produce the JSON now."
    )


def _call_anthropic(
    spec: TaskSpec,
    *,
    api_key: str,
    model: str,
    max_tokens: int,
    timeout_seconds: float,
) -> tuple[str, str]:
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": _CODEGEN_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _build_user_msg(spec)}],
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


def generate_code_only(
    spec: TaskSpec,
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-5",
    max_tokens: int = 3000,
    timeout_seconds: float = 90.0,
) -> CodegenResult:
    """LLM call + JSON parse + merge into starting_repo. NO validation.

    Used by the iterative pipeline's "Generate code" button, which
    intentionally defers validation to the explicit syntax-check /
    test-cases / PBT buttons so the user is in control of when each
    check fires. The existing `generate_code` wraps this and runs the
    structural validator + PBT inline; that variant is what the linear
    Pipeline tab calls.
    """
    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return CodegenResult(error="no ANTHROPIC_API_KEY in environment")

    text, err = _call_anthropic(
        spec,
        api_key=key,
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    if err:
        return CodegenResult(error=err, raw_response=text)
    if not text:
        return CodegenResult(error="empty response", raw_response=text)

    payload = _extract_json_object(text)
    if payload is None:
        return CodegenResult(
            error="response did not contain a parseable JSON object",
            raw_response=text,
        )

    files = payload.get("files")
    if not isinstance(files, dict):
        return CodegenResult(
            error="response missing 'files' object", raw_response=text)
    files_clean: dict[str, str] = {}
    for path, code in files.items():
        if not isinstance(path, str) or not isinstance(code, str):
            return CodegenResult(
                error=f"non-string entry in files: {path!r}", raw_response=text)
        files_clean[path] = code

    # Merge LLM-generated files on top of the starting repo.
    modified_repo = dict(spec.starting_repo)
    modified_repo.update(files_clean)

    return CodegenResult(
        modified_repo=modified_repo,
        raw_response=text,
        notes=str(payload.get("notes", "")),
    )


def generate_code(
    spec: TaskSpec,
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-5",
    max_tokens: int = 3000,
    timeout_seconds: float = 90.0,
) -> CodegenResult:
    """Ask the LLM to write code that satisfies `spec`, then validate the result.

    The verdict in the returned CodegenResult is the structured
    validator's decision on the generated code. ACCEPT means the LLM
    produced an implementation that satisfies every invariant *and*
    passes the positive tests. REJECT means it failed at least one;
    the verdict carries the per-invariant trace explaining which.
    """
    emit = generate_code_only(
        spec,
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
    )
    if emit.error or not emit.modified_repo:
        return emit  # propagate the early failure

    modified_repo = emit.modified_repo
    candidate = Candidate(
        candidate_id="llm_generated",
        label=CandidateLabel.CORRECT,  # arbitrary; not used by validator
        modified_repo=modified_repo,
        note="produced by codegen.generate_code",
    )
    verdict = StructuredValidator().evaluate(spec, candidate)

    # Behavioral verification: if the spec carries an algorithmic
    # behavioral_spec, fuzz the agent's code against the elicited
    # Python reference oracle on inputs drawn from input_strategy.
    # `verified` = no counterexample in N runs; `falsified` = shrunken
    # CE returned; `error` = oracle threw, missing toolchain, etc.
    pbt_result = None
    if spec.behavioral_spec is not None:
        try:
            pbt_result = verify_against_oracle(spec, modified_repo)
        except Exception as exc:
            # Surface as an error PBTResult rather than failing the
            # whole codegen call. CodegenResult.ok = False in this case.
            pbt_result = PBTResult(
                outcome="error",
                detail=f"PBT runner raised: {type(exc).__name__}: {exc}",
            )

    return CodegenResult(
        modified_repo=modified_repo,
        verdict=verdict,
        pbt_result=pbt_result,
        raw_response=emit.raw_response,
        notes=emit.notes,
    )
