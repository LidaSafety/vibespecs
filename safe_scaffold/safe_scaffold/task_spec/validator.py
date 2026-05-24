"""Validator: given (TaskSpec, Candidate), produce a Verdict.

Workflow:

1. Materialize the candidate's modified_repo to a temp directory.
2. Run each structural invariant against (before, after, temp_dir).
3. For each positive test in the spec, write the test file into the temp dir,
   shell out to `python -m pytest`, and record pass/fail.
4. Aggregate: ACCEPT iff every invariant holds AND every positive test
   passes; otherwise REJECT, naming the first failure.

The validator is deny-overrides at the test level — any single failing
invariant or positive test means REJECT — but reports ALL outcomes for the
dashboard so reviewers can see what would have caught each candidate.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from safe_scaffold.task_spec.invariants import (
    Invariant,
    InvariantResult,
    PositiveTestPasses,
)
from safe_scaffold.task_spec.spec import (
    Candidate,
    PositiveTest,
    RepoState,
    TaskSpec,
    ValidatorDecision,
    Verdict,
)


def validate(spec: TaskSpec, candidate: Candidate) -> Verdict:
    """Run the full validation pipeline on one (spec, candidate) pair."""
    with tempfile.TemporaryDirectory(prefix="safe_scaffold_task_") as td:
        repo_dir = Path(td)
        _materialize(candidate.modified_repo, repo_dir)

        results: list[InvariantResult] = []

        # Structural invariants first (fast, no subprocess).
        for inv in spec.negative_invariants:
            if isinstance(inv, PositiveTestPasses):
                # Skip in this pass; handled below where we have the
                # PositiveTest object the path refers to.
                continue
            try:
                result = inv.check(
                    before=spec.starting_repo,
                    after=candidate.modified_repo,
                    repo_dir=str(repo_dir),
                )
            except Exception as exc:
                # An invariant that raises is in the potato-of-doom zone
                # — we don't know whether the constraint holds. Record
                # as uncertain so the verdict becomes ABSTAIN rather
                # than silently passing the candidate.
                result = InvariantResult(
                    invariant_name=type(inv).__name__,
                    holds=False,
                    details=f"check raised {type(exc).__name__}: {exc}",
                    uncertain=True,
                )
            results.append(result)

        # Positive tests: write each into the temp repo and run pytest.
        # We run them one at a time so per-test outcomes are clean.
        for test in spec.positive_tests:
            result = _run_positive_test(test, repo_dir)
            results.append(result)

        # Aggregate. ABSTAIN takes precedence over REJECT: if we couldn't
        # evaluate something, we don't pretend we could. Only after all
        # structural checks evaluate cleanly do we collapse to ACCEPT/REJECT.
        first_uncertain = next((r for r in results if r.uncertain), None)
        if first_uncertain is not None:
            return Verdict(
                decision=ValidatorDecision.ABSTAIN,
                invariant_results=tuple(results),
                reason=(
                    f"abstain: {first_uncertain.invariant_name}: "
                    f"{first_uncertain.details}"
                ),
            )
        first_failure = next((r for r in results if not r.holds), None)
        if first_failure is None:
            return Verdict(
                decision=ValidatorDecision.ACCEPT,
                invariant_results=tuple(results),
                reason="all invariants held and all positive tests passed",
            )
        return Verdict(
            decision=ValidatorDecision.REJECT,
            invariant_results=tuple(results),
            reason=f"failed: {first_failure.invariant_name}: {first_failure.details}",
        )


def _materialize(repo: RepoState, target: Path) -> None:
    for relative_path, contents in repo.items():
        p = target / relative_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents, encoding="utf-8")


def _run_positive_test(test: PositiveTest, repo_dir: Path) -> InvariantResult:
    """Write the test file into the repo and run it in a subprocess.

    Prefers pytest (most user-written tests use the pytest assert idiom).
    Falls back to a small in-process shim that finds top-level `test_*`
    functions, calls each one, and reports pass/fail. Mirrors pytest's
    collection rule closely enough for the hand-authored corpus, which
    uses no fixtures or parametrize.
    """
    test_path = repo_dir / test.path
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(test.code, encoding="utf-8")

    try:
        import pytest  # noqa: F401
        cmd = [sys.executable, "-m", "pytest", "-x", "-q", str(test_path)]
    except ImportError:
        # Build a shim file that discovers test_ functions and runs them.
        shim_path = repo_dir / "__sscaf_run_tests.py"
        shim_path.write_text(
            "import importlib.util, sys, traceback\n"
            f"spec = importlib.util.spec_from_file_location('ct', r'{test_path}')\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "try:\n"
            "    spec.loader.exec_module(mod)\n"
            "except Exception:\n"
            "    traceback.print_exc()\n"
            "    sys.exit(2)\n"
            "tests = [(n, getattr(mod, n)) for n in dir(mod)\n"
            "         if n.startswith('test_') and callable(getattr(mod, n))]\n"
            "if not tests:\n"
            "    print('no test_ functions found')\n"
            "    sys.exit(3)\n"
            "failed = []\n"
            "for name, fn in tests:\n"
            "    try:\n"
            "        fn()\n"
            "    except Exception as e:\n"
            "        failed.append((name, repr(e)))\n"
            "if failed:\n"
            "    for n, msg in failed:\n"
            "        print(f'FAIL {n}: {msg}')\n"
            "    sys.exit(1)\n"
            "print(f'OK {len(tests)} tests')\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        cmd = [sys.executable, str(shim_path)]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_dir) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return InvariantResult(
            invariant_name=f"PositiveTestPasses({test.path})",
            holds=False,
            details="test timed out after 30s",
        )

    name = f"PositiveTestPasses({test.path})"
    if proc.returncode == 0:
        return InvariantResult(invariant_name=name, holds=True, details="passed")

    snippet_lines = (proc.stdout + proc.stderr).strip().splitlines()
    snippet = snippet_lines[-3:] if snippet_lines else ["(no output)"]

    # Distinguish "test asserted false" (returncode 1, a REJECT signal) from
    # "test couldn't run" (anything else: ImportError, collection error,
    # pytest internal error, shim couldn't find functions). The latter is
    # the potato-of-doom zone — surface as uncertain so the verdict
    # becomes ABSTAIN rather than REJECT.
    if proc.returncode == 1:
        return InvariantResult(
            invariant_name=name,
            holds=False,
            details="test failed: " + " | ".join(snippet),
        )
    return InvariantResult(
        invariant_name=name,
        holds=False,
        details=(
            f"could not evaluate (exit code {proc.returncode}): "
            + " | ".join(snippet)
        ),
        uncertain=True,
    )
