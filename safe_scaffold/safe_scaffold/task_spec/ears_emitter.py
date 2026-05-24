"""TaskSpec → EARS-syntax controlled natural language emitter.

EARS (Easy Approach to Requirements Syntax) is the controlled-NL pattern
adopted by AWS's Kiro IDE for `requirements.md`. Each requirement
follows one of five patterns:

  - Ubiquitous:   "The system SHALL <action>."
  - Event-driven: "WHEN <trigger>, the system SHALL <action>."
  - Unwanted:     "IF <unwanted condition>, THEN the system SHALL <response>."
  - State-driven: "WHILE <state>, the system SHALL <action>."
  - Optional:     "WHERE <feature>, the system SHALL <action>."

We use the event-driven and unwanted forms for our invariants, because
that's the natural English shape ("when the agent modifies a file…",
"if a new import is introduced…").

The emitted EARS text is the same spec the Lean emitter produces — same
invariants, same parameters — but rendered as controlled English for
reviewers who can't read Lean. Two artifacts, one source of truth.
"""

from __future__ import annotations

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


def _ears_for(inv: Invariant, index: int) -> str | None:
    if isinstance(inv, OnlyFilesModified):
        files = ", ".join(f"`{p}`" for p in inv.allowed_paths)
        return (
            f"R{index}. **WHEN** the agent modifies a file, **THE SYSTEM SHALL** "
            f"only allow modifications to {files}."
        )
    if isinstance(inv, FilesUnchanged):
        files = ", ".join(f"`{p}`" for p in inv.paths)
        return (
            f"R{index}. **IF** the agent's diff modifies {files}, "
            f"**THEN THE SYSTEM SHALL** reject the diff."
        )
    if isinstance(inv, NoNewImports):
        mods = ", ".join(f"`{m}`" for m in inv.forbidden)
        return (
            f"R{index}. **IF** the diff introduces a top-level import of "
            f"{mods}, **THEN THE SYSTEM SHALL** reject the diff."
        )
    if isinstance(inv, NoSecretsInDiff):
        return (
            f"R{index}. **IF** the diff contains a string matching a "
            f"credential pattern (AWS key, API token, private key block, "
            f"or hardcoded password literal), **THEN THE SYSTEM SHALL** "
            f"reject the diff."
        )
    if isinstance(inv, DiffSmallerThan):
        return (
            f"R{index}. **THE SYSTEM SHALL** reject the diff **IF** "
            f"the total added+removed lines exceed {inv.max_lines}."
        )
    if isinstance(inv, PositiveTestPasses):
        return (
            f"R{index}. **WHEN** the agent's diff is materialized, "
            f"**THE SYSTEM SHALL** require `{inv.test_path}` to pass."
        )
    return None


def emit_ears(spec: TaskSpec) -> str:
    """Render `spec` as an EARS-shaped requirements.md."""
    lines: list[str] = [
        f"# Requirements — {spec.task_id}",
        "",
        "Auto-emitted from the drafted TaskSpec. Same source of truth as",
        "the Lean predicates; this view is the controlled-English alternative",
        "for reviewers who can't read Lean.",
        "",
        f"**Intent.** {spec.description}",
        "",
        "## Negative invariants (EARS)",
        "",
    ]
    n = 1
    skipped: list[str] = []
    for inv in spec.negative_invariants:
        sentence = _ears_for(inv, n)
        if sentence is None:
            skipped.append(type(inv).__name__)
            continue
        lines.append(sentence)
        lines.append("")
        n += 1

    if skipped:
        lines.append(f"_Skipped (no EARS template): {', '.join(skipped)}_")
        lines.append("")

    lines.append("## Positive tests (behavioral)")
    lines.append("")
    if spec.positive_tests:
        for t in spec.positive_tests:
            label = t.name or t.path
            lines.append(f"- `{label}` (in `{t.path}`)")
    else:
        lines.append("_(none)_")

    return "\n".join(lines) + "\n"
