"""TaskSpec → Lean 4 source emitter.

Closes the "or similar" qualifier in the project brief's
*"translate informal intent into Lean (or similar)"* — every TaskSpec
the elicitation pipeline produces can be emitted as real Lean 4 syntax
checkable by `lake build`. No mathlib dependency; uses only stdlib
predicates over the `Diff` structure defined in
`safe_scaffold/lean_prelude/SafeScaffold/Basic.lean`.

Two levels of strength:

1. `emit_lean(spec)` — pure text generation, always available.
2. `verify_lean(lean_path)` — invokes `lake build` against the prelude
   project. Returns success/failure + compiler output. Requires the
   Lean 4 toolchain (elan + lean4 stable) on PATH or under ~/.elan/bin.

The verification check answers *"is the spec well-typed under Lean 4?"*,
not *"is this candidate diff accepted by the spec?"*. The latter remains
a Python concern — see validator.py.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from safe_scaffold.task_spec.invariants import (
    DiffSmallerThan,
    FilesUnchanged,
    Invariant,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    PositiveTestPasses,
)
from safe_scaffold.task_spec.spec import TaskSpec


# Path to the bundled Lean prelude project (the lakefile.lean lives here).
PRELUDE_DIR = Path(__file__).resolve().parents[1] / "lean_prelude"


_LEAN_KEYWORDS = {
    "if", "then", "else", "fun", "do", "let", "match", "with", "have",
    "show", "by", "exact", "def", "theorem", "example", "namespace", "end",
    "import", "open", "section", "variable", "instance", "class",
    "structure", "inductive", "where", "in", "and", "or", "not",
}


def _safe_ns(task_id: str) -> str:
    """Coerce a task_id into a Lean-legal namespace name."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", task_id) or "Anon"
    if cleaned[0].isdigit() or cleaned in _LEAN_KEYWORDS:
        cleaned = "S_" + cleaned
    return f"Spec_{cleaned}"


def _quote_str(s: str) -> str:
    """Render a Python string as a Lean string literal."""
    # Lean's string syntax is "..." with backslash escapes; same shape as Python.
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _emit_str_list(items: Iterable[str]) -> str:
    parts = ", ".join(_quote_str(s) for s in items)
    return "[" + parts + "]"


def _invariant_to_prop(inv: Invariant) -> str | None:
    """Render one invariant as a Lean Prop expression applied to `d`.

    Returns None for invariants that don't have a `Diff`-shaped encoding
    (e.g. PositiveTestPasses, which is a runtime/behavioral check).
    """
    if isinstance(inv, OnlyFilesModified):
        return f"OnlyFilesModified d {_emit_str_list(inv.allowed_paths)}"
    if isinstance(inv, NoNewImports):
        return f"NoNewImports d {_emit_str_list(inv.forbidden)}"
    if isinstance(inv, DiffSmallerThan):
        return f"DiffSmallerThan d {inv.max_lines}"
    if isinstance(inv, NoSecretsInDiff):
        return "NoSecretsInDiff d"
    if isinstance(inv, FilesUnchanged):
        return f"FilesUnchanged d {_emit_str_list(inv.paths)}"
    if isinstance(inv, PositiveTestPasses):
        return None  # behavioral, handled outside the Prop
    return None  # unknown invariant type; emit a comment instead


def _format_block_comment(text: str) -> str:
    """Wrap a multi-line description in a Lean docstring block."""
    safe = text.replace("/-", "/—").replace("-/", "—/")
    return f"/-- {safe} -/"


def emit_lean(spec: TaskSpec, *, module_name: str | None = None) -> str:
    """Produce a single .lean source string for `spec`.

    The output:
      - imports SafeScaffold.Basic
      - opens the SafeScaffold namespace
      - defines `spec : Diff → Prop` as the conjunction of every
        Diff-shaped invariant in `spec.negative_invariants`
      - emits any non-Diff invariant (e.g. PositiveTestPasses) as a
        comment so the human reader sees what was excluded and why
      - records the description and task_id as docstrings
    """
    ns = module_name or _safe_ns(spec.task_id)

    prop_lines: list[str] = []
    skipped: list[tuple[str, str]] = []
    for inv in spec.negative_invariants:
        rendered = _invariant_to_prop(inv)
        if rendered is None:
            inv_type = type(inv).__name__
            reason = ("runtime/behavioral predicate — kept in the Python "
                      "validator only" if isinstance(inv, PositiveTestPasses)
                      else "no Lean encoding available for this invariant type")
            skipped.append((inv_type, reason))
        else:
            prop_lines.append(rendered)

    if not prop_lines:
        body = "True  -- no Diff-shaped invariants in this spec"
    else:
        joiner = " ∧\n    "
        body = joiner.join(prop_lines)

    skipped_block = ""
    if skipped:
        lines = "\n".join(f"-- skipped: {n} ({why})" for n, why in skipped)
        skipped_block = f"\n{lines}\n"

    test_names = ", ".join(t.name or t.path for t in spec.positive_tests)
    if test_names:
        skipped_block += (
            f"-- positive tests (behavioral, run via subprocess in Python): "
            f"{test_names}\n"
        )

    docstring = _format_block_comment(
        f"Auto-emitted from TaskSpec {spec.task_id!r}.\n"
        f"Description: {spec.description}"
    )
    return f"""import SafeScaffold.Basic

namespace {ns}

open SafeScaffold

{docstring}
def spec (d : Diff) : Prop :=
    {body}
{skipped_block}
end {ns}
"""


# ---------------------------------------------------------------------------
# Lean toolchain integration (optional — only used when --verify-lean is on)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    stdout: str
    stderr: str
    duration_seconds: float
    lean_version: str = ""


def _resolve_lean_bin(name: str) -> str | None:
    """Find a Lean toolchain binary. Checks PATH then ~/.elan/bin."""
    via_path = shutil.which(name)
    if via_path:
        return via_path
    fallback = Path.home() / ".elan" / "bin" / name
    return str(fallback) if fallback.exists() else None


def lean_available() -> bool:
    return _resolve_lean_bin("lake") is not None


def verify_lean(
    spec_lean_source: str,
    *,
    timeout_seconds: float = 120.0,
) -> VerifyResult:
    """Type-check `spec_lean_source` against the bundled SafeScaffold prelude.

    Copies the prelude project to a temp dir, drops the emitted source
    into `Specs/Generated.lean`, runs `lake build`. Returns success
    plus the lake stdout/stderr so the UI can show what tripped.
    """
    import time as _time

    lake = _resolve_lean_bin("lake")
    lean = _resolve_lean_bin("lean")
    if lake is None or lean is None:
        return VerifyResult(
            ok=False,
            stdout="",
            stderr=(
                "Lean toolchain not found. Install with:\n"
                "  curl https://raw.githubusercontent.com/leanprover/elan/"
                "master/elan-init.sh -sSf | sh -s -- -y"
            ),
            duration_seconds=0.0,
        )

    # Pin PATH so `lake` finds `lean` next to it.
    env = os.environ.copy()
    env["PATH"] = str(Path(lake).parent) + os.pathsep + env.get("PATH", "")

    start = _time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ssc_lean_") as td:
        td_path = Path(td)
        # Copy the entire prelude project so writes don't pollute the
        # repo's checked-in tree.
        shutil.copytree(PRELUDE_DIR, td_path / "proj", dirs_exist_ok=False)
        proj = td_path / "proj"
        specs_dir = proj / "Specs"
        specs_dir.mkdir(exist_ok=True)
        # Wrap with module declaration so lake picks it up as `Specs.Generated`.
        (specs_dir / "Generated.lean").write_text(spec_lean_source, encoding="utf-8")

        try:
            proc = subprocess.run(
                [lake, "build"],
                cwd=proj,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return VerifyResult(
                ok=False,
                stdout=exc.stdout.decode() if exc.stdout else "",
                stderr=f"timed out after {timeout_seconds}s",
                duration_seconds=_time.monotonic() - start,
            )

    dur = _time.monotonic() - start
    version = ""
    try:
        vproc = subprocess.run(
            [lean, "--version"], capture_output=True, text=True, timeout=5,
        )
        version = vproc.stdout.strip()
    except Exception:
        pass

    return VerifyResult(
        ok=(proc.returncode == 0),
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_seconds=dur,
        lean_version=version,
    )
