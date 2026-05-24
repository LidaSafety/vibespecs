"""Task-spec elicitation and validation.

This subpackage implements the Track 1 contribution: a way to specify what
an AI coding agent is supposed to do for a given task, in a format cheap
enough that users will actually write it, and a validator that checks whether
the agent's output satisfies the spec.

The shape of a spec is deliberately minimal:

    TaskSpec
      description:           one-sentence natural-language statement of intent
      starting_repo:         dict[path, contents]  -- the world before the agent runs
      positive_tests:        small list of unit tests that must PASS after
      negative_invariants:   small list of invariants that must HOLD throughout

The user authors `positive_tests` and `negative_invariants`; everything else
either already exists (starting_repo) or is one line of prose (description).
The validator does the rest: apply the candidate diff, run the tests, check
the invariants, emit a Verdict.

This is the "specs by example" elicitation pattern. The user doesn't write
formal logic; they write one example of correctness (the positive test) and
a handful of structural constraints (don't touch unrelated files, don't
introduce new dependencies). Both are forms the user already speaks fluently
— unit tests are bread-and-butter for any developer, and structural
constraints are how code review already works.

The validator is then a deterministic Python function. No LLM judging, no
fuzzy semantics. The eval module compares this against (a) a "positive
tests only" baseline, mirroring most CI setups today, and (b) an
"LLM-as-judge" baseline that asks Claude to score the agent output. The
hypothesis defended by the eval is that the structured positive/negative
form has a lower false-accept rate than either baseline on a corpus of
hand-curated tasks.
"""

from safe_scaffold.task_spec.invariants import (
    DiffSmallerThan,
    FilesUnchanged,
    Invariant,
    InvariantResult,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    PositiveTestPasses,
)
from safe_scaffold.task_spec.spec import (
    Candidate,
    CandidateLabel,
    TaskSpec,
    Verdict,
    ValidatorDecision,
)
from safe_scaffold.task_spec.validator import validate

__all__ = [
    "TaskSpec",
    "Candidate",
    "CandidateLabel",
    "Verdict",
    "ValidatorDecision",
    "validate",
    "Invariant",
    "InvariantResult",
    "FilesUnchanged",
    "OnlyFilesModified",
    "PositiveTestPasses",
    "NoNewImports",
    "NoSecretsInDiff",
    "DiffSmallerThan",
]
