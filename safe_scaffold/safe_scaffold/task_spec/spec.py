"""Core dataclasses for task specs, candidates, and verdicts.

Kept separate from the validator implementation so the data model can be
inspected and serialized without pulling in subprocess / runtime machinery.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from safe_scaffold.task_spec.invariants import Invariant, InvariantResult


# Type alias: a "repo" is just a dict from relative path to file contents.
# Bytes-vs-str distinction isn't worth the complexity at this scale — every
# file in the corpus is plain UTF-8 source code.
RepoState = dict[str, str]


@dataclass(frozen=True)
class PositiveTest:
    """A single pytest-style test that must pass after the agent runs.

    `code` is the test file's full source, including imports. `path` is
    where it gets written in the working repo. Anything in this file that
    starts with `test_` is treated as a test function by pytest, but the
    validator just shells out and trusts the exit code rather than parsing.
    """

    path: str
    code: str
    name: str = ""  # human-readable label for reports


@dataclass(frozen=True)
class BehavioralSpec:
    """The algorithmic content of a spec — what the function should *do*.

    The elicitation pipeline produces this alongside the structural
    invariants in `TaskSpec.negative_invariants`. It captures the
    algorithm in three forms:

    - `lean_predicate`: a Lean 4 `def ... : Prop` expressing the
      mathematical content of the intent. Emitted into the same .lean
      file as the structural Diff predicates and type-checked by
      `lake build`.
    - `python_oracle`: an obviously-correct, slow Python reference
      implementation of the same predicate. Used by the PBT verifier
      (see `task_spec/verify_pbt.py`) as the comparator the agent's
      generated code must match on randomized inputs.
    - `input_strategy`: a Hypothesis strategy expression (e.g.
      "integers(min_value=0, max_value=200)") drawing the inputs to
      compare on.

    Authoring philosophy: the LLM elicits all three from the NL intent
    *without* seeing any implementation. The oracle is "slow but
    obviously right" — the agent's job is to write a different,
    optimized implementation that produces the same outputs.
    """

    function_name: str
    signature: str          # e.g. "is_not_prime(n: int) -> bool"
    lean_predicate: str     # the Lean 4 def, e.g. "def isNotPrime (n : Nat) : Prop := ..."
    python_oracle: str      # reference impl, e.g. "def is_not_prime(n: int) -> bool: ..."
    input_strategy: str     # Hypothesis strategy expression


@dataclass(frozen=True)
class TaskSpec:
    """A complete spec for one coding task.

    Minimal user-authored content:
      description:        one sentence
      starting_repo:      existing project state (often imported, not authored)
      positive_tests:     tests that must pass after the agent runs
      negative_invariants: structural constraints on the diff
      behavioral_spec:    algorithmic content (Lean predicate + Python oracle)
                          — required by the elicitation pipeline, optional in
                          the dataclass so hand-authored corpus tasks that
                          predate this field still type-check

    Authoring time per spec is dominated by writing the positive tests,
    which is work the user would do anyway in TDD. The invariants are the
    novel ask, and we keep their menu small (~8 invariant types) so spec
    authoring stays a fill-in-the-blanks activity, not a programming one.
    """

    task_id: str
    description: str
    starting_repo: RepoState
    positive_tests: tuple[PositiveTest, ...]
    negative_invariants: tuple["Invariant", ...]
    # Category tag for per-task-type breakdown in the eval (e.g. "refactor",
    # "bugfix", "feature", "dep_bump").
    category: str = "unknown"
    # Optional spec-authoring cost annotation. The user reports this at
    # authoring time so we can aggregate later without a user study.
    authoring_seconds: int = 0
    authoring_loc: int = 0  # excluding starting_repo
    # Algorithmic spec produced by the elicitation pipeline. None on the
    # hand-authored toy corpus (where there's no LLM step); always present
    # on elicited specs since the elicitation schema requires it.
    behavioral_spec: BehavioralSpec | None = None


class CandidateLabel(enum.Enum):
    """Ground-truth label assigned by the corpus author.

    These are the "should-accept" / "should-reject" categories an ideal
    validator would discriminate. We pick four because they correspond to
    distinct failure modes:

      CORRECT          - agent did the task right
      OBVIOUS_WRONG    - fails the positive test (CI catches this today)
      SUBTLE_WRONG     - passes the positive test but violates the spec's
                         negative invariants (this is what the contribution
                         is supposed to catch better than baselines)
      SCOPE_CREEP      - passes both, but modifies files outside the spec's
                         intended scope; this discriminates from action-level
                         gating which would accept each individual edit
    """

    CORRECT = "correct"
    OBVIOUS_WRONG = "obvious_wrong"
    SUBTLE_WRONG = "subtle_wrong"
    SCOPE_CREEP = "scope_creep"

    @property
    def should_accept(self) -> bool:
        """Ground truth: should a perfect validator accept this candidate?"""
        return self is CandidateLabel.CORRECT


@dataclass(frozen=True)
class Candidate:
    """A proposed agent output for a TaskSpec.

    `modified_repo` is the full post-edit repo state, not a diff. The
    validator computes the diff itself. Keeping the model "snapshot, not
    delta" makes it easy to hand-author candidates (you just edit the
    starting_repo dict by hand) and trivial to serialize.
    """

    candidate_id: str
    label: CandidateLabel
    modified_repo: RepoState
    # Optional note for the dashboard explaining what's wrong (if anything).
    note: str = ""


class ValidatorDecision(enum.Enum):
    """What the validator concluded about a (spec, candidate) pair.

    ABSTAIN is the "potato of doom" zone — we know we don't know.
    Surfaces when the spec couldn't be evaluated against this candidate
    (test crashed on import, an invariant.check raised, etc.), so we
    refuse to overclaim a binary verdict the validator can't actually
    defend. See docs/elicitation_and_mutation.md for the rationale.
    """

    ACCEPT = "accept"
    REJECT = "reject"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class Verdict:
    """Validator output for one (spec, candidate) pair."""

    decision: ValidatorDecision
    # Per-invariant + per-positive-test outcomes. The dashboard shows these
    # one-by-one so reviewers can see exactly what tripped.
    invariant_results: tuple["InvariantResult", ...] = field(default_factory=tuple)
    # Reason: short human-readable text. For ACCEPT it summarizes what passed;
    # for REJECT it names what tripped; for ABSTAIN it names what couldn't
    # be evaluated.
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.decision is ValidatorDecision.ACCEPT

    @property
    def abstained(self) -> bool:
        return self.decision is ValidatorDecision.ABSTAIN
