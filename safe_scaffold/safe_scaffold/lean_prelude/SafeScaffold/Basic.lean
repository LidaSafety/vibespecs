/-
SafeScaffold.Basic — Lean 4 prelude for the task-spec invariant DSL.

Mirrors the dataclasses in `safe_scaffold/task_spec/invariants.py` as
propositions over a `Diff` value. The emitter in
`safe_scaffold/task_spec/lean_emitter.py` produces one .lean file per
`TaskSpec`, importing this module and instantiating each predicate with
the spec's concrete arguments.

Self-contained: depends only on Lean 4 stdlib (no mathlib). Lake will
build it in seconds.

The intent of this file is *not* to enable interactive theorem proving
about candidate diffs — we keep the verification side (does THIS
modified_repo satisfy the spec?) in Python, where it can subprocess
out to pytest. What this file buys us:

1. Honest "translate informal intent into Lean (or similar)" — the
   call's literal requirement. The emitter produces real Lean 4
   syntax checkable by `lake build`.
2. A typed witness that the spec is well-formed before the user
   accepts it. If a drafted spec doesn't type-check here, the
   elicitation produced garbage.
3. A target for future work: pluggable Prop instances mean a
   user with mathlib can prove individual specs hold of toy diffs
   directly in Lean.
-/

namespace SafeScaffold

/-- A diff between two repo states, normalized to the four fields our
    invariants actually inspect. The Python emitter computes these from
    `before` and `after`; here we treat them as a given. -/
structure Diff where
  changedPaths : List String  -- relative paths the candidate modified
  newImports   : List String  -- top-level module names newly imported
  totalLines   : Nat          -- added + removed lines, ndiff-style
  addedStrings : List String  -- lines starting with "+ " in the diff
  deriving Repr

/-- The agent may only modify files in `allowed`. -/
def OnlyFilesModified (d : Diff) (allowed : List String) : Prop :=
  ∀ p ∈ d.changedPaths, p ∈ allowed

/-- The diff must not introduce a top-level import of any module in
    `forbidden`. Mirrors `NoNewImports` in invariants.py. -/
def NoNewImports (d : Diff) (forbidden : List String) : Prop :=
  ∀ m ∈ d.newImports, m ∉ forbidden

/-- The total churn must not exceed `n` lines. -/
def DiffSmallerThan (d : Diff) (n : Nat) : Prop :=
  d.totalLines ≤ n

/-- `looksLikeSecret s` holds when `s` matches one of our credential
    regexes (AKIA, sk-ant-, ghp_, BEGIN PRIVATE KEY, hardcoded
    passwords). Kept opaque here because regex matching on `String`
    is heavy to model and we don't need the semantics to type-check
    the spec. The Python validator decides the actual predicate. -/
opaque looksLikeSecret : String → Prop

/-- No added line in the diff matches any credential-shaped pattern. -/
def NoSecretsInDiff (d : Diff) : Prop :=
  ∀ s ∈ d.addedStrings, ¬ looksLikeSecret s

/-- The named files must be byte-identical before and after. The
    Python check operates on RepoStates; the Lean form here just
    requires that none of them appear in the changed-paths list. -/
def FilesUnchanged (d : Diff) (frozen : List String) : Prop :=
  ∀ p ∈ frozen, p ∉ d.changedPaths

end SafeScaffold
