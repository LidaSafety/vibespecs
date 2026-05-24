"""Concrete-test-case generation + execution for the iterative pipeline.

Two halves:

1. `generate_test_cases(spec)` — LLM call that produces ~8 concrete
   `{input, expected, rationale}` tuples from the spec's NL description
   + behavioral_spec (Lean predicate, Python reference oracle, input
   strategy). The LLM does NOT see the agent's optimised
   implementation — only the spec — so the cases are independent of
   what the code does.

2. `run_test_cases(files, function_name, cases)` — materialises the
   agent's code in a temp dir, imports `function_name`, runs each case
   (`ast.literal_eval`s input + expected, calls the impl, compares),
   returns per-case pass/fail/error.

The point of generating cases separately from PBT: PBT gives a
statistical "no counterexample in 200 examples" verdict but doesn't
show the inputs. Concrete cases give the reviewer something they can
read, edit, and reason about — useful when iterating on the spec.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from safe_scaffold.task_spec.codegen import _extract_json_object
from safe_scaffold.task_spec.spec import RepoState, TaskSpec


_NUM_CASES = 8


_GEN_SYSTEM_PROMPT = """You are generating concrete test cases for a Python function from its specification.

You will receive:
- The natural-language intent
- The function signature
- An algorithmic Lean predicate describing what the function should compute
- A Python reference oracle (slow but obviously correct implementation)
- The Hypothesis input strategy that bounds the input space

You MUST emit EXACTLY 8 test cases as a JSON array. Each case is an object:
  {"input": "<Python literal expression>",
   "expected": "<Python literal expression>",
   "rationale": "one short sentence"}

Hard rules:
- Output ONLY the JSON array. No preamble, no markdown fences, no commentary.
- `input` and `expected` MUST be Python literal expressions parseable by
  `ast.literal_eval`: numbers, strings, booleans, None, lists, tuples,
  dicts, and combinations. NO function calls, NO names, NO operators.
  Example: `[1, 2, 3]`, `42`, `True`, `("hello", 3)`, `{"k": 1}`.
- Each `input` is a SINGLE value that gets passed to the function. If
  the function takes multiple positional args, wrap them in a tuple and
  the test harness will unpack via `*input`.
- `expected` is the value the oracle would return for `input` —
  compute it by reasoning through the Lean predicate (not by mentally
  running the oracle).
- Cover the input space deliberately: include typical cases, edge
  cases (empty/zero/boundary per the input strategy), and at least one
  case at each numerical boundary the strategy declares.
- Stay within the input strategy's bounds. If it says
  `integers(min_value=0, max_value=200)`, do NOT emit -5 or 1000.
- Do NOT see or reference the agent's implementation. This is a spec-side
  oracle for catching disagreements with the implementation later.

Output ONLY the JSON array of 8 cases.
"""


@dataclass(frozen=True)
class TestCase:
    input: str        # Python literal expression
    expected: str     # Python literal expression
    rationale: str = ""


@dataclass(frozen=True)
class TestCaseSet:
    cases: tuple[TestCase, ...] = field(default_factory=tuple)
    raw_response: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.cases) and not self.error


def _build_gen_user_msg(spec: TaskSpec) -> str:
    bs = spec.behavioral_spec
    assert bs is not None, "test_case_gen requires spec.behavioral_spec"
    return (
        f"INTENT: {spec.description}\n\n"
        f"SIGNATURE: {bs.signature}\n\n"
        f"LEAN PREDICATE:\n{bs.lean_predicate}\n\n"
        f"PYTHON REFERENCE ORACLE (for your reference; do not paste verbatim):\n"
        f"{bs.python_oracle}\n\n"
        f"INPUT STRATEGY (stay within these bounds):\n  {bs.input_strategy}\n\n"
        f"Emit 8 cases as a JSON array now."
    )


def generate_test_cases(
    spec: TaskSpec,
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-4-5",
    max_tokens: int = 1500,
    timeout_seconds: float = 60.0,
) -> TestCaseSet:
    """LLM call: spec → 8 concrete (input, expected, rationale) tuples."""
    if spec.behavioral_spec is None:
        return TestCaseSet(error="spec has no behavioral_spec; cannot generate cases")

    key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return TestCaseSet(error="no ANTHROPIC_API_KEY in environment")

    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": _GEN_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _build_gen_user_msg(spec)}],
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
        return TestCaseSet(error=f"HTTP {exc.code}: {exc.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return TestCaseSet(error=f"network: {exc}")
    except json.JSONDecodeError as exc:
        return TestCaseSet(error=f"non-JSON response: {exc}")

    text = "".join(
        b.get("text", "") for b in payload.get("content", [])
        if isinstance(b, dict) and b.get("type") == "text"
    ).strip()

    # `_extract_json_object` extracts an object; we want an array. Try
    # the array directly first, then fall back to looking for a key.
    array = _extract_json_array(text)
    if array is None:
        return TestCaseSet(
            error="response did not contain a parseable JSON array",
            raw_response=text,
        )

    cases: list[TestCase] = []
    for i, entry in enumerate(array):
        if not isinstance(entry, dict):
            return TestCaseSet(
                error=f"case {i} is not an object", raw_response=text)
        inp = entry.get("input")
        exp = entry.get("expected")
        if not isinstance(inp, str) or not inp.strip():
            return TestCaseSet(
                error=f"case {i}: input must be a non-empty string",
                raw_response=text,
            )
        if not isinstance(exp, str) or not exp.strip():
            return TestCaseSet(
                error=f"case {i}: expected must be a non-empty string",
                raw_response=text,
            )
        # Validate that BOTH are parseable as Python literals here, so a
        # malformed case is surfaced at generation time, not at run time.
        try:
            ast.literal_eval(inp)
            ast.literal_eval(exp)
        except (ValueError, SyntaxError) as exc:
            return TestCaseSet(
                error=f"case {i}: input or expected is not a Python literal: {exc}",
                raw_response=text,
            )
        cases.append(TestCase(
            input=inp.strip(),
            expected=exp.strip(),
            rationale=str(entry.get("rationale", "")).strip(),
        ))

    return TestCaseSet(cases=tuple(cases), raw_response=text)


def _extract_json_array(text: str) -> list | None:
    """Brace-counted JSON array extractor. Symmetric to _extract_json_object
    in codegen.py but for top-level `[...]`."""
    import re as _re
    candidates: list[str] = [text.strip()]

    m = _re.match(r"^```(?:json)?\s*(.+?)\s*```$",
                   text.strip(), flags=_re.DOTALL)
    if m:
        candidates.append(m.group(1).strip())

    # Brace-counted: find first '[' and scan to matching ']'.
    start = text.find("[")
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
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, list):
            return obj
    return None


# ---------------------------------------------------------------------------
# Concrete-case runner
# ---------------------------------------------------------------------------


def _materialize(repo: RepoState, target: Path) -> None:
    for relative_path, contents in repo.items():
        p = target / relative_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")


def _figure_module(files: dict[str, str], function_name: str) -> str | None:
    """Find a module path (stem) that defines `function_name`."""
    for path in files:
        if not path.endswith(".py"):
            continue
        body = files[path]
        if f"def {function_name}" in body:
            return path[:-3].replace("/", ".")
    # Fall back to first .py file
    for path in files:
        if path.endswith(".py"):
            return path[:-3].replace("/", ".")
    return None


def run_test_cases(
    files: dict[str, str],
    function_name: str,
    cases: list[dict],
    *,
    timeout_seconds: float = 20.0,
) -> list[dict]:
    """Run each case against the impl in `files`. Returns per-case results.

    Each result is one of:
      {"input": <s>, "expected": <s>, "got": <repr>, "status": "pass"}
      {"input": <s>, "expected": <s>, "got": <repr>, "status": "fail"}
      {"input": <s>, "expected": <s>, "got": "", "status": "error",
       "error": "<short msg>"}
    """
    if not cases:
        return []

    impl_module = _figure_module(files, function_name)
    if impl_module is None:
        return [
            {"input": c.get("input", ""), "expected": c.get("expected", ""),
             "got": "", "status": "error",
             "error": "no .py file found to import from"}
            for c in cases
        ]

    # Build a tiny driver that imports the impl, runs each case, and
    # writes JSONL to stdout. Each line is one case result. We pass
    # cases via stdin to avoid quoting headaches.
    driver_src = f'''import sys, json, ast
sys.path.insert(0, ".")
from {impl_module} import {function_name} as _impl
for line in sys.stdin:
    case = json.loads(line)
    inp_s = case.get("input", "")
    exp_s = case.get("expected", "")
    out = {{"input": inp_s, "expected": exp_s, "got": "", "status": "error", "error": ""}}
    try:
        inp = ast.literal_eval(inp_s)
        exp = ast.literal_eval(exp_s)
    except Exception as e:
        out["error"] = f"input/expected parse: {{type(e).__name__}}: {{e}}"
        print(json.dumps(out), flush=True)
        continue
    try:
        if isinstance(inp, tuple):
            got = _impl(*inp)
        else:
            got = _impl(inp)
        out["got"] = repr(got)
        out["status"] = "pass" if got == exp else "fail"
    except Exception as e:
        out["error"] = f"impl raised: {{type(e).__name__}}: {{e}}"
    print(json.dumps(out), flush=True)
'''

    with tempfile.TemporaryDirectory(prefix="ssc_cases_") as td:
        td_path = Path(td)
        _materialize(files, td_path)
        driver_path = td_path / "_run_cases.py"
        driver_path.write_text(driver_src, encoding="utf-8")

        stdin_payload = "\n".join(json.dumps(c) for c in cases) + "\n"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(td_path) + os.pathsep + env.get("PYTHONPATH", "")
        try:
            proc = subprocess.run(
                [sys.executable, str(driver_path)],
                input=stdin_payload,
                cwd=td_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return [
                {"input": c.get("input", ""), "expected": c.get("expected", ""),
                 "got": "", "status": "error",
                 "error": f"timed out after {timeout_seconds}s"}
                for c in cases
            ]

    # Parse JSONL on stdout. Pad with errors for any missing.
    results: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if len(results) < len(cases):
        # Some cases didn't run (driver crashed early). Fill in errors.
        for c in cases[len(results):]:
            results.append({
                "input": c.get("input", ""), "expected": c.get("expected", ""),
                "got": "", "status": "error",
                "error": f"driver crashed before this case "
                          f"(stderr: {proc.stderr[-200:]})",
            })
    return results
