"""safe_scaffold — Formal action gating and spec validation for AI coding agents.

Two related research tracks:

1. **Action gating with provable invariants (Track 1).** Sit a small formal
   verification layer between an AI coding agent and the world. Maintain a
   policy in a restricted DSL with formal semantics. Each proposed action is
   evaluated against the policy with deny-overrides aggregation; UNKNOWN
   verdicts block at the gate (fail-closed).

   The headline contribution relative to prior work on agent permission
   systems (Progent, Conseca, VeriGuard) is that because the policy is a
   DSL expression rather than synthesized code, we can prove *universal
   properties about the policy itself* via Z3:

       "No matter which subset of rules ends up matching, this policy
        provably cannot allow any action of shape <pattern>."

   Per-action gating is the runtime defense. Policy-level invariant proofs
   are the CI defense — they catch a too-broad ALLOW rule the moment it is
   added, with a concrete counterexample, before the policy is ever shipped.

2. **Spec cross-checking (Track 2).** Apply the same "structured spec +
   adversarial testing" pattern to server code, motivated by Symbolic
   Software's Feb-Apr 2026 documentation of 16 vulnerabilities in Cryspen's
   formally-verified libcrux library — including a specification-level bug
   in ML-KEM `decompress_d` that had been present since the very first commit
   of the spec file. The cross_check submodule reproduces that bug as a
   fixture in ~100 LOC.

Both tracks are motivated by the Galois article "Specifications Don't Exist"
(Mike Dodds, Jun 2025): the bottleneck in formal verification isn't proof
cost, it's writing specs that match what humans actually want. Track 1 makes
narrow specs cheap to write; Track 2 validates that specs we already have
actually match the implementations they describe.
"""

from safe_scaffold.conditions import (
    And,
    Condition,
    ContainsArg,
    Eq,
    InSet,
    KindIs,
    MatchesGlob,
    MatchesRegex,
    Not,
    Or,
    PathEquals,
    PathUnder,
    Reference,
    StartsWith,
    ValidationError,
    parse_condition,
)
from safe_scaffold.plan import (
    PlanVerdict,
    find_unsafe_pair,
    looks_like_credential_write,
    looks_like_external_network,
    verify_plan,
)
from safe_scaffold.policy import (
    Effect,
    Policy,
    Provenance,
    Rule,
    safe_default_policy,
)
from safe_scaffold.task_spec import (
    Candidate as TaskCandidate,
    CandidateLabel,
    DiffSmallerThan,
    FilesUnchanged,
    Invariant,
    InvariantResult,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    PositiveTestPasses,
    TaskSpec,
    Verdict as TaskVerdict,
    ValidatorDecision,
    validate as validate_task,
)
from safe_scaffold.verifier import Decision, Verdict, verify
from safe_scaffold.world import (
    Action,
    ActionId,
    EnvRead,
    FileDelete,
    FileRead,
    FileWrite,
    NetworkRequest,
    ProcessSignal,
    ShellExec,
)

__version__ = "0.2.0"

__all__ = [
    # Actions
    "Action", "ActionId",
    "ShellExec", "FileRead", "FileWrite", "FileDelete",
    "NetworkRequest", "ProcessSignal", "EnvRead",
    # Policy
    "Policy", "Rule", "Effect", "Provenance", "safe_default_policy",
    # Conditions DSL
    "Condition", "Reference", "ValidationError", "parse_condition",
    "And", "Or", "Not", "KindIs", "Eq", "InSet", "ContainsArg",
    "PathUnder", "PathEquals", "MatchesGlob", "MatchesRegex", "StartsWith",
    # Verification
    "verify", "Decision", "Verdict",
    # Plan verification
    "verify_plan", "PlanVerdict", "find_unsafe_pair",
    "looks_like_credential_write", "looks_like_external_network",
    # Task-spec elicitation & validation (Track 1)
    "TaskSpec", "TaskCandidate", "CandidateLabel",
    "TaskVerdict", "ValidatorDecision", "validate_task",
    "Invariant", "InvariantResult",
    "FilesUnchanged", "OnlyFilesModified",
    "NoNewImports", "NoSecretsInDiff",
    "DiffSmallerThan", "PositiveTestPasses",
    # Metadata
    "__version__",
]
