"""Adapters for external Track 1 / Track 2 datasets.

Bundled samples (5 problems each, JSONL fixtures committed to the repo):

Foundational (2021):
  - mbpp_sample.jsonl              — Google MBPP (Austin et al., 2021)
  - humaneval_sample.jsonl         — OpenAI HumanEval (Chen et al., 2021)

Recent (2024-2026):
  - bigcodebench_sample.jsonl      — BigCodeBench (Zhuo et al., NeurIPS 2024)
                                      — realistic library-integration tasks
  - humaneval_pro_sample.jsonl     — HumanEval Pro (Yu et al., ACL 2025 Findings)
                                      — self-invoking variants of HumanEval
  - livecodebench_sample.jsonl     — LiveCodeBench (Jain et al., ICLR 2025)
                                      — contamination-free contest problems

These are the canonical NL→code benchmarks the elicitation literature
(TiCoder, nl2postcond, FeatureBench, PRDBench) cites, so running our
4-step pipeline on them lets us compare apples-to-apples against the
existing baselines.

We adapt each record into the same `AmbiguousBrief` shape the demo
already speaks. The intent is the NL prompt; the starting_repo gets a
stub file with the function signature; the existing_tests source carries
the official test_list as an extra cross-source for elicitation.

Why we picked these and not the others:

  - MBPP / HumanEval (2021): canonical foundational benchmarks, low cost.
  - BigCodeBench (2024): realistic library integration; the
    instruct_prompt is a clean NL description we can hand to the LLM.
  - HumanEval Pro (2025): self-invoking variants — the spec must cover
    both a base problem AND a derived problem that uses it.
    Stress-tests the elicitation pipeline on a richer intent shape.
  - LiveCodeBench (2024-2025): contamination-free; contest-style
    problems with stdin/stdout that we adapt as functions.

Out of scope (would require more adapter code or a Java runtime):

  - nl2postcond / Defects4J — Java; our validator is Python-only.
  - SWE-bench / Multi-SWE-bench — repo-level multi-file resolution; out
    of scope for our single-file invariant DSL.
  - FeatureBench / PRDBench — gated / paywalled at time of writing.
  - TiCoder discriminating tests — a sub-method-level benchmark whose
    "spec" is a single test; less natural to view as an elicitation pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from safe_scaffold.task_spec.ambiguous_briefs import AmbiguousBrief


_DATA_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# MBPP
# ---------------------------------------------------------------------------


def _mbpp_records() -> list[dict]:
    """Read the bundled 5-record MBPP sample (JSONL)."""
    out = []
    for line in (_DATA_DIR / "mbpp_sample.jsonl").read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _stub_from_mbpp(record: dict) -> dict[str, str]:
    """MBPP doesn't ship function stubs, only canonical code + tests. Build a
    minimal stub from the first test's call site so the agent has something
    to fill in. Module name is `mbpp_<task_id>.py`."""
    test = record["test_list"][0] if record.get("test_list") else ""
    m = re.search(r"assert\s+(\w+)\s*\(", test)
    fn = m.group(1) if m else "f"
    module = f"mbpp_{record['task_id']}.py"
    stub = (
        f"def {fn}(*args, **kwargs):\n"
        f"    # TODO: implement\n"
        f"    raise NotImplementedError\n"
    )
    return {module: stub}


def mbpp_to_brief(record: dict) -> AmbiguousBrief:
    """Convert one MBPP record into an AmbiguousBrief for the elicitation pipeline."""
    test = record["test_list"][0] if record.get("test_list") else ""
    m = re.search(r"assert\s+(\w+)\s*\(", test)
    fn = m.group(1) if m else "f"
    module_name = f"mbpp_{record['task_id']}.py"

    # The NL `text` IS the intent. The test_list goes into existing_tests
    # so the elicitation can see what behavior is expected.
    existing_tests = (
        f"from {module_name[:-3]} import {fn}\n\n"
        + "\n\n".join(
            f"def test_case_{i}():\n    {t.strip()}"
            for i, t in enumerate(record.get("test_list", []))
        )
        + "\n"
    )

    return AmbiguousBrief(
        brief_id=f"mbpp_{record['task_id']}",
        label=f"MBPP-{record['task_id']} · {record.get('text', '')[:60]}",
        description=record.get("text", ""),
        starting_repo=_stub_from_mbpp(record),
        existing_tests=existing_tests,
    )


# ---------------------------------------------------------------------------
# HumanEval
# ---------------------------------------------------------------------------


def _humaneval_records() -> list[dict]:
    out = []
    for line in (_DATA_DIR / "humaneval_sample.jsonl").read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _module_for(record: dict) -> str:
    # HumanEval/0 → he_0.py (avoid the slash and the keyword issue).
    suffix = record["task_id"].split("/")[-1]
    return f"he_{suffix}.py"


def _strip_docstring(prompt: str) -> str:
    """Extract just the docstring text from a HumanEval prompt — the
    natural-language description, separated from the function signature
    and any imports."""
    m = re.search(r'"""(.*?)"""', prompt, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    return prompt.strip()


def humaneval_to_brief(record: dict) -> AmbiguousBrief:
    module = _module_for(record)
    description = _strip_docstring(record["prompt"]).split("\n")[0]
    # The full prompt (signature + docstring + examples) is the starting
    # stub; the model fills in the body.
    stub = record["prompt"] + "    pass  # TODO: implement\n"
    return AmbiguousBrief(
        brief_id=f"humaneval_{record['task_id'].replace('/', '_')}",
        label=f"HumanEval/{record['task_id'].split('/')[-1]} · {record['entry_point']}",
        description=f"Implement {record['entry_point']} — {description}",
        starting_repo={module: stub},
        # HumanEval ships a `test` field that's a `check(candidate)` function
        # rather than pytest-shaped tests. Pass it through verbatim as a
        # hint to the elicitation; we don't try to run it as our positive
        # test (the elicitation produces its own).
        existing_tests=(
            f"# Official HumanEval test ({record['task_id']}):\n\n"
            + record.get("test", "")
        ),
    )


# ---------------------------------------------------------------------------
# BigCodeBench (NeurIPS 2024) — realistic library-integration tasks.
# ---------------------------------------------------------------------------


def _bigcodebench_records() -> list[dict]:
    out = []
    for line in (_DATA_DIR / "bigcodebench_sample.jsonl").read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def bigcodebench_to_brief(record: dict) -> AmbiguousBrief:
    """All BCB tasks share entry_point=task_func; use a per-task module name."""
    task_id = record["task_id"]
    suffix = task_id.split("/")[-1]
    module = f"bcb_{suffix}.py"
    # The instruct_prompt is the cleanest NL description; complete_prompt
    # ships the stub + docstring + examples and goes into starting_repo.
    description = record.get("instruct_prompt", "").strip().split("\n")[0]
    stub = record.get("code_prompt", "") + "    pass  # TODO\n"
    return AmbiguousBrief(
        brief_id=f"bigcodebench_{suffix}",
        label=f"BigCodeBench/{suffix} · {record.get('entry_point', 'task_func')} (libs: {record.get('libs', '')})",
        description=f"Implement {record.get('entry_point', 'task_func')} — {description}",
        starting_repo={module: stub},
        prose_doc=record.get("instruct_prompt", ""),
        existing_tests=(
            f"# Official BigCodeBench test ({task_id}):\n\n"
            + record.get("test", "")
        ),
    )


# ---------------------------------------------------------------------------
# HumanEval Pro (ACL 2025 Findings) — self-invoking variants of HumanEval.
# ---------------------------------------------------------------------------


def _humaneval_pro_records() -> list[dict]:
    out = []
    for line in (_DATA_DIR / "humaneval_pro_sample.jsonl").read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def humaneval_pro_to_brief(record: dict) -> AmbiguousBrief:
    """HumanEval Pro = solve a base problem AND a derived problem that uses it."""
    rid = record["id"]
    module = f"hep_{rid}.py"
    # Stub: include the original (raw) problem first, then space for the
    # extension. We treat both halves as starting context.
    stub = (
        record.get("raw_problem", "")
        + "\n\n# Extension (self-invoking) — implement this on top of the above:\n"
        + record.get("new_problem", "")
        + "    pass  # TODO\n"
    )
    return AmbiguousBrief(
        brief_id=f"humaneval_pro_{rid}",
        label=f"HumanEval Pro/{rid} · self-invoking",
        description=(
            "Implement the base problem AND the more complex problem "
            "that invokes it. See the prose_doc source for the extension's full spec."
        ),
        starting_repo={module: stub},
        prose_doc=(
            "BASE PROBLEM:\n" + record.get("raw_problem", "")
            + "\n\nEXTENSION (self-invoking):\n" + record.get("new_problem", "")
        ),
        existing_tests=(
            f"# Official HumanEval Pro test (id={rid}):\n\n"
            + record.get("test_code", "")
        ),
    )


# ---------------------------------------------------------------------------
# LiveCodeBench (ICLR 2025) — contamination-free contest problems.
# ---------------------------------------------------------------------------


def _livecodebench_records() -> list[dict]:
    out = []
    for line in (_DATA_DIR / "livecodebench_sample.jsonl").read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _lcb_pre_authored_test(record: dict, module: str):
    """Build a real pytest from LCB's official public_test_cases.

    Each LCB record carries a JSON-encoded list of {input, output, testtype}
    dicts. For stdin-shaped problems we test that `solve(input) == output`
    (trailing whitespace stripped to be lenient about newlines). For
    functional-shaped problems we use the entry-point if a starter_code
    is provided.

    This bypasses what would otherwise be the weakest link in the LCB
    pipeline: the LLM inventing its own positive test that doesn't match
    the contest's actual I/O contract. By pre-authoring against the
    official test cases, the codegen step is graded against the same
    oracle the benchmark itself uses.
    """
    from safe_scaffold.task_spec.spec import PositiveTest
    cases = json.loads(record.get("public_test_cases", "[]"))
    if not cases:
        return None
    module_name = module[:-3]  # strip .py
    body_parts = [f"from {module_name} import solve\n"]
    for i, tc in enumerate(cases):
        inp = repr(tc.get("input", ""))
        out = repr(tc.get("output", "").rstrip())
        body_parts.append(
            f"def test_case_{i}():\n"
            f"    # LCB official test case {i} for {record['question_id']}\n"
            f"    expected = {out}\n"
            f"    got = solve({inp})\n"
            f"    # Strip trailing whitespace to allow harmless newline drift.\n"
            f"    assert got.rstrip() == expected, repr(got)\n"
        )
    return PositiveTest(
        path=f"test_{module_name}.py",
        code="\n".join(body_parts),
        name=f"LCB official tests for {record['question_id']}",
    )


def livecodebench_to_brief(record: dict) -> AmbiguousBrief:
    """LCB problems are stdin/stdout-shaped; adapt as `solve(stdin_str) -> str`.

    We pre-author the positive test from LCB's official public_test_cases
    so the codegen step is graded against the contest's actual I/O
    contract instead of whatever test the LLM invents.
    """
    qid = record["question_id"]
    module = f"lcb_{qid}.py"
    title = record.get("question_title", "")
    # Very explicit contract so the LLM doesn't guess: signature, return
    # type, what stdin looks like, what stdout should look like.
    stub = (
        f"# LiveCodeBench problem {qid}: {title}\n"
        f"# Convention: read EVERYTHING from the `stdin` string argument,\n"
        f"# return EVERYTHING that would be printed as a single string.\n"
        f"# Do NOT call input() or print().\n"
        f"#\n"
        f"# The string passed in already contains all input lines\n"
        f"# (with newlines between them). The returned string must end\n"
        f"# with a newline if the expected output ends with one.\n"
        f"def solve(stdin: str) -> str:\n"
        f"    # TODO: implement\n"
        f"    raise NotImplementedError\n"
    )
    pre_test = _lcb_pre_authored_test(record, module)
    return AmbiguousBrief(
        brief_id=f"livecodebench_{qid}",
        label=f"LiveCodeBench/{qid} · {title[:50]} ({record.get('difficulty', '?')})",
        description=(
            f"Solve LCB/{qid} — {title}. "
            f"Implement `solve(stdin: str) -> str`: read input from the "
            f"`stdin` string argument and return what would be printed "
            f"to stdout."
        ),
        starting_repo={module: stub},
        prose_doc=record.get("question_content", ""),
        existing_tests=(
            f"# LCB public test cases (stdin/stdout — JSON-encoded):\n"
            + record.get("public_test_cases", "")
        ),
        override_positive_test=pre_test,
    )


# ---------------------------------------------------------------------------
# Combined accessor
# ---------------------------------------------------------------------------


def all_dataset_briefs() -> list[AmbiguousBrief]:
    """Return all bundled-dataset briefs. Used by the demo brief picker."""
    return (
        [mbpp_to_brief(r) for r in _mbpp_records()]
        + [humaneval_to_brief(r) for r in _humaneval_records()]
        + [bigcodebench_to_brief(r) for r in _bigcodebench_records()]
        + [humaneval_pro_to_brief(r) for r in _humaneval_pro_records()]
        + [livecodebench_to_brief(r) for r in _livecodebench_records()]
    )


__all__ = [
    "all_dataset_briefs",
    "mbpp_to_brief",
    "humaneval_to_brief",
    "bigcodebench_to_brief",
    "humaneval_pro_to_brief",
    "livecodebench_to_brief",
]
