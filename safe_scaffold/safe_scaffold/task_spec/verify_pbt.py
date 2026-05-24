"""PBT verification: does the agent's code satisfy the elicited algorithmic spec?

This is the executable shadow of the Lean predicate. The elicitation
pipeline emits a `BehavioralSpec` carrying *both* a Lean predicate (the
formal artifact, type-checked by `lake build`) and an obviously-correct
Python reference oracle. We run Hypothesis to fuzz the agent's
optimized implementation against the oracle on randomized inputs drawn
from `behavioral_spec.input_strategy`.

Why not formal proof? For arbitrary Python the question "does this
implementation satisfy this predicate" is undecidable. Tools like
CrossHair handle a useful subset via SMT but require the predicate to
be re-expressed as Python contracts. We chose PBT-against-oracle as the
v1 because: (a) Hypothesis is the de-facto standard for Python property
checking; (b) the oracle is itself elicited from the same intent, so
it's an independent check on the agent's code rather than a tautology;
(c) failures come back as shrunken counterexamples — a reviewer can act
on them immediately.

Honest framing: a `verified` verdict means "no counterexample in
N runs" (default 200, configurable). It is strong evidence, not a
formal proof. Where the LLM's behavioral_spec is wrong, both the
oracle and the agent's code may agree-to-be-wrong; cross-model
elicitation (Step 3 of the pipeline) is the safeguard against that.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from safe_scaffold.task_spec.spec import RepoState, TaskSpec


@dataclass(frozen=True)
class PBTResult:
    """Outcome of one PBT-against-oracle run."""

    outcome: str            # "verified" | "falsified" | "error"
    detail: str             # human-readable summary
    counterexample: str = ""  # shrunken CE if falsified, else empty
    duration_seconds: float = 0.0
    n_runs: int = 0         # actual runs Hypothesis performed (best-effort parse)

    @property
    def ok(self) -> bool:
        """`True` only when Hypothesis ran and produced no counterexample."""
        return self.outcome == "verified"


# Hypothesis defaults. 200 random examples per spec is fast (~2s for
# tiny functions) and gives ~99.5% coverage of small input domains.
# `deadline=None` because the agent's optimized impl may be slow on
# adversarial inputs and we'd rather not flake on deadline timeouts.
_DEFAULT_MAX_EXAMPLES = 200


def _safe_module_name(fn_name: str) -> str:
    """Module name for the oracle file. Distinct from the agent's module."""
    return f"_oracle_{re.sub(r'[^a-zA-Z0-9_]', '_', fn_name)}"


def _build_driver(spec: TaskSpec, oracle_module: str) -> tuple[str, str]:
    """Return (driver_filename, driver_source).

    The driver imports the agent's function and the oracle's function,
    declares one Hypothesis @given with the spec's input_strategy, and
    asserts equality. Hypothesis handles shrinking on failure.
    """
    bs = spec.behavioral_spec
    assert bs is not None, "verify_pbt requires spec.behavioral_spec to be set"
    fn = bs.function_name
    strategy = bs.input_strategy

    # Try to figure out which module the agent's function will live in.
    # The drafted positive_test imports `from <module> import <function_name>`
    # — reuse that module name so the agent's code is found on disk.
    impl_module = None
    for pt in spec.positive_tests:
        m = re.search(rf"from\s+(\S+)\s+import\s+(\w+\s*,\s*)*{re.escape(fn)}\b",
                       pt.code)
        if m:
            impl_module = m.group(1)
            break
    if impl_module is None:
        # Fall back: take the first .py file in starting_repo whose stem
        # could plausibly host the function.
        for path in spec.starting_repo:
            if path.endswith(".py"):
                impl_module = path[:-3].replace("/", ".")
                break
    if impl_module is None:
        impl_module = fn  # last-ditch

    driver_filename = f"test_pbt_{fn}.py"
    # We `import *` from hypothesis.strategies so the elicited
    # input_strategy can use NESTED strategies as bare names — e.g.
    # `tuples(lists(integers(min_value=0, max_value=100)), integers(...))`.
    # If we only imported `strategies as st` and prefixed the top-level
    # call, nested `lists(...)` and `integers(...)` would NameError.
    driver_source = f'''"""Auto-generated PBT driver: compares the agent's `{fn}` to the elicited oracle."""
from hypothesis import given, settings
from hypothesis.strategies import *  # noqa: F401,F403 — for nested strategies in input_strategy
from {impl_module} import {fn} as _impl
from {oracle_module} import {fn} as _oracle


@settings(max_examples={_DEFAULT_MAX_EXAMPLES}, deadline=None)
@given({strategy})
def test_impl_matches_oracle(x):
    expected = _oracle(x)
    got = _impl(x)
    assert got == expected, f"PBT counterexample at x={{x!r}}: impl={{got!r}}, oracle={{expected!r}}"
'''
    return driver_filename, driver_source


def _resolve_runner() -> list[str] | None:
    """Use pytest if installed (better failure messages, shrinks reported);
    otherwise we can't run Hypothesis at all — return None and let the caller
    report `error`."""
    try:
        import pytest  # noqa: F401
    except ImportError:
        return None
    # --hypothesis-show-statistics dumps a per-test block we parse
    # afterwards to know how many examples Hypothesis actually ran.
    # Without it we'd have to take @settings(max_examples=N) on faith.
    return [sys.executable, "-m", "pytest", "-x", "-q", "--no-header",
            "--tb=short", "--hypothesis-show-statistics"]


def _materialize(repo: RepoState, target: Path) -> None:
    """Write every file from `repo` into `target`. Same shape as
    validator._materialize but local copy to avoid cross-import."""
    for relative_path, contents in repo.items():
        p = target / relative_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")


def _parse_hypothesis_example_count(combined_output: str) -> int | None:
    """Return the number of examples Hypothesis actually ran, or None.

    With `--hypothesis-show-statistics` (added in `_resolve_runner`),
    Hypothesis prints a per-test stats block whose lines look like:

      - during generate phase (~0.05 seconds):
        - Typical runtimes: 0-1 ms, of which 0-0 ms in data generation
        - 200 passing examples, 0 failing examples, 0 invalid examples

    We sum every `N passing examples` we see (Hypothesis emits one per
    phase: `reuse`, `generate`, `shrink` — only the first two when
    verified). Returns the total, or None if no `passing examples` line
    matched (e.g. Hypothesis version without that wording, or stats
    flag dropped).
    """
    matches = re.findall(r"(\d+)\s+passing\s+examples?",
                          combined_output, flags=re.IGNORECASE)
    if not matches:
        return None
    try:
        return sum(int(m) for m in matches)
    except ValueError:
        return None


def verify_against_oracle(
    spec: TaskSpec,
    generated_repo: RepoState,
    *,
    timeout_seconds: float = 60.0,
) -> PBTResult:
    """Fuzz the agent's implementation against the LLM-emitted reference oracle.

    Workflow:
      1. Materialize `generated_repo` and a sibling file with the oracle
         (`<oracle_module>.py`) into a temp directory.
      2. Generate a self-contained pytest+Hypothesis driver that imports
         both and asserts equality on inputs drawn from
         `spec.behavioral_spec.input_strategy`.
      3. Run pytest in a subprocess; parse the result.

    Returns a PBTResult carrying one of:
      - outcome="verified" — no counterexample in N runs
      - outcome="falsified" — Hypothesis returned a (shrunken) CE
      - outcome="error" — toolchain missing, oracle threw, etc.
    """
    import time as _time

    if spec.behavioral_spec is None:
        return PBTResult(
            outcome="error",
            detail="spec has no behavioral_spec; cannot run PBT",
        )

    runner = _resolve_runner()
    if runner is None:
        return PBTResult(
            outcome="error",
            detail="pytest not installed in this environment; "
                   "install with `pip install pytest hypothesis`",
        )
    try:
        import hypothesis  # noqa: F401
    except ImportError:
        return PBTResult(
            outcome="error",
            detail="hypothesis not installed; install with `pip install hypothesis`",
        )

    bs = spec.behavioral_spec
    oracle_module = _safe_module_name(bs.function_name)
    driver_filename, driver_source = _build_driver(spec, oracle_module)

    start = _time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ssc_pbt_") as td:
        td_path = Path(td)
        # Drop the agent's code in.
        _materialize(generated_repo, td_path)
        # Drop the oracle module beside it. The oracle source already
        # contains `def {function_name}(...)` per elicitation validation.
        (td_path / f"{oracle_module}.py").write_text(
            bs.python_oracle, encoding="utf-8"
        )
        # Drop the driver.
        (td_path / driver_filename).write_text(driver_source, encoding="utf-8")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(td_path) + os.pathsep + env.get("PYTHONPATH", "")
        # Make Hypothesis less chatty in CI; ride the verbose output of pytest.
        env.setdefault("HYPOTHESIS_PROFILE", "default")

        try:
            proc = subprocess.run(
                runner + [str(td_path / driver_filename)],
                cwd=td_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return PBTResult(
                outcome="error",
                detail=f"PBT timed out after {timeout_seconds}s",
                counterexample=(exc.stdout.decode() if exc.stdout
                                 else "")[:400],
                duration_seconds=_time.monotonic() - start,
            )

    dur = _time.monotonic() - start
    combined = (proc.stdout + "\n" + proc.stderr).strip()

    if proc.returncode == 0:
        parsed = _parse_hypothesis_example_count(combined)
        if parsed is not None and parsed > 0:
            detail = (f"no counterexample in {parsed} examples vs reference "
                       f"oracle (Hypothesis @settings(max_examples="
                       f"{_DEFAULT_MAX_EXAMPLES}))")
            n_runs = parsed
        else:
            # Hypothesis output didn't expose a stats block we could parse.
            # Be honest: don't claim a specific count we can't substantiate.
            detail = (f"no counterexample in up to {_DEFAULT_MAX_EXAMPLES} "
                       f"examples vs reference oracle (Hypothesis "
                       f"@settings(max_examples={_DEFAULT_MAX_EXAMPLES}); "
                       f"actual count not parsed from stdout)")
            n_runs = 0  # signal that the count was not confirmed
        return PBTResult(
            outcome="verified",
            detail=detail,
            duration_seconds=dur,
            n_runs=n_runs,
        )

    # Hypothesis-shrunken counterexample tends to appear after a
    # `Falsifying example:` line. Capture from there to the end (clipped).
    ce_match = re.search(r"Falsifying example:.*?(?=\n_+\s*$|\Z)",
                          combined, flags=re.DOTALL)
    counterexample = ce_match.group(0).strip() if ce_match else ""
    if not counterexample:
        # Fall back to the last few non-empty lines so the user has something.
        lines = [l for l in combined.splitlines() if l.strip()]
        counterexample = "\n".join(lines[-6:])

    # pytest returncode 1 = test failed (i.e. counterexample found).
    # Any other non-zero is an environment/oracle problem.
    if proc.returncode == 1:
        return PBTResult(
            outcome="falsified",
            detail=f"Hypothesis found a counterexample after fuzzing vs oracle",
            counterexample=counterexample[:600],
            duration_seconds=dur,
        )
    return PBTResult(
        outcome="error",
        detail=f"PBT driver exited {proc.returncode} (likely oracle or import error)",
        counterexample=combined[-800:],
        duration_seconds=dur,
    )
