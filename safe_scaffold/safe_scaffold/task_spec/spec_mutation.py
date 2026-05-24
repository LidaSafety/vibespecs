"""Mutation testing for task specs.

Asks: does this spec *actually* capture the intended behavior, or could it
be weakened without changing what it accepts/rejects?

For each spec we generate a family of mutations:

  - drop an invariant entirely
  - weaken a numeric bound (e.g. DiffSmallerThan(20) → DiffSmallerThan(200))
  - shrink a set parameter (e.g. NoNewImports(['os','subprocess']) → (['os']))
  - widen a scope (e.g. OnlyFilesModified(['a.py']) → ([... + every other file]))
  - drop a positive test

Each mutation is re-run against every Candidate the corpus already
attaches to the spec. We then label each mutation by how the verdicts
change:

  - **invisible** — verdicts unchanged. The mutated invariant might be
    redundant on this corpus, or the spec is overconstrained, or the
    candidates simply don't exercise the constraint.
  - **load_bearing** — the mutation newly admits one or more should-reject
    candidates. Direct evidence the original invariant was doing
    safety-relevant work.
  - **brittle** — the mutation newly rejects a should-accept candidate.
    The original spec was barely tolerating CORRECT, so the mutation
    tipped it over. Rare.

This is the analogue of mutation testing for code (PIT, mutmut) but
applied to *specifications*: instead of perturbing code and checking
that tests catch the perturbation, we perturb the spec and check that
the corpus catches the weakening.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from safe_scaffold.task_spec.baselines import StructuredValidator
from safe_scaffold.task_spec.invariants import (
    DiffSmallerThan,
    FilesUnchanged,
    Invariant,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    PositiveTestPasses,
)
from safe_scaffold.task_spec.spec import (
    Candidate,
    CandidateLabel,
    PositiveTest,
    TaskSpec,
)


@dataclass(frozen=True)
class Mutation:
    """Description of one perturbation applied to a spec."""

    kind: str         # "drop_invariant" | "weaken_bound" | "shrink_set" | "widen_scope" | "drop_test"
    target: str       # which invariant/test was mutated, human-readable
    description: str  # what changed, human-readable

    def label(self) -> str:
        return f"{self.kind}({self.target}): {self.description}"


@dataclass(frozen=True)
class MutationResult:
    mutation: Mutation
    spec_id: str
    # candidate_id → (ground_truth_should_accept, original_accepted, mutated_accepted)
    per_candidate: tuple[tuple[str, bool, bool, bool], ...]

    @property
    def newly_accepted(self) -> list[str]:
        """should-reject candidates that newly slipped through."""
        return [cid for cid, should_accept, orig, new in self.per_candidate
                if (not should_accept) and (not orig) and new]

    @property
    def newly_rejected(self) -> list[str]:
        """should-accept candidates that the mutation newly rejects."""
        return [cid for cid, should_accept, orig, new in self.per_candidate
                if should_accept and orig and (not new)]

    @property
    def verdict_changed(self) -> bool:
        return any(orig != new for _, _, orig, new in self.per_candidate)

    @property
    def classification(self) -> str:
        if self.newly_accepted:
            return "load_bearing"
        if self.newly_rejected:
            return "brittle"
        if self.verdict_changed:
            # Verdict moved but neither newly_accepted nor newly_rejected —
            # e.g. the mutated decision flipped from reject→accept on a
            # should-accept candidate (good direction). Treat as "neutral
            # change", classify as invisible-on-safety.
            return "invisible"
        return "invisible"


# ---------------------------------------------------------------------------
# Mutation generators
# ---------------------------------------------------------------------------


def _mutations_for_invariant(
    inv: Invariant,
    candidate_extra_paths: tuple[str, ...] = (),
) -> list[tuple[Mutation, Invariant | None]]:
    """Return (mutation, replacement) pairs. replacement=None means drop the invariant.

    `candidate_extra_paths` is used to make `widen_scope` realistic:
    instead of adding hardcoded filenames the candidates may never
    touch, we widen by files that the candidates actually introduced or
    modified outside the spec's allowed scope. This simulates "a lazy
    reviewer waved the scope to cover everything the agent did" and
    asks whether the spec still rejects safety-relevant cases.
    """
    out: list[tuple[Mutation, Invariant | None]] = []
    inv_name = type(inv).__name__

    out.append((Mutation(
        kind="drop_invariant",
        target=inv_name,
        description=f"remove {inv_name} from the spec",
    ), None))

    if isinstance(inv, DiffSmallerThan):
        for factor in (2, 10):
            new_limit = inv.max_lines * factor
            out.append((Mutation(
                kind="weaken_bound",
                target=inv_name,
                description=f"raise max_lines from {inv.max_lines} to {new_limit}",
            ), DiffSmallerThan(new_limit)))

    elif isinstance(inv, NoNewImports):
        # Drop one forbidden module at a time.
        for mod in inv.forbidden:
            remaining = tuple(m for m in inv.forbidden if m != mod)
            if remaining:
                out.append((Mutation(
                    kind="shrink_set",
                    target=inv_name,
                    description=f"remove '{mod}' from forbidden imports → {list(remaining)}",
                ), NoNewImports(remaining)))

    elif isinstance(inv, OnlyFilesModified):
        # Widen by any path the candidates touched outside the spec's
        # current scope. This is the "permissive reviewer" mutation —
        # would the spec still catch bad behavior if we tolerated
        # exactly the files the agent reached for? Using candidate
        # paths is intentional: it makes widen_scope informative on a
        # corpus where SCOPE_CREEP candidates create arbitrarily-named
        # files, instead of leaving the mutation invisible because
        # hardcoded names never match. (OnlyFilesModified uses
        # exact-path matching, so a literal wildcard isn't expressible
        # without changing the invariant itself.)
        extras = tuple(p for p in candidate_extra_paths if p not in inv.allowed_paths)
        if extras:
            new_paths = tuple(list(inv.allowed_paths) + list(extras))
            out.append((Mutation(
                kind="widen_scope",
                target=inv_name,
                description=f"add {list(extras)} to allowed_paths (files the candidates reach for)",
            ), OnlyFilesModified(new_paths)))

    elif isinstance(inv, FilesUnchanged):
        # Shrink the must-stay-frozen set.
        for p in inv.paths:
            remaining = tuple(q for q in inv.paths if q != p)
            if remaining:
                out.append((Mutation(
                    kind="shrink_set",
                    target=inv_name,
                    description=f"unfreeze '{p}' (remaining {list(remaining)})",
                ), FilesUnchanged(remaining)))

    # NoSecretsInDiff has no parameters to weaken; only "drop" applies.
    return out


def mutate_spec(
    spec: TaskSpec,
    candidates: tuple[Candidate, ...] = (),
) -> list[tuple[Mutation, TaskSpec]]:
    """Generate every mutation of `spec` we know how to make.

    `candidates` is optional; when supplied it informs the `widen_scope`
    mutation (we widen by paths the candidates touched outside the
    current scope, which makes the mutation actually exercise the corpus
    instead of being invisible).
    """
    mutations: list[tuple[Mutation, TaskSpec]] = []

    candidate_extras: set[str] = set()
    for cand in candidates:
        for path in cand.modified_repo:
            if path not in spec.starting_repo or \
               cand.modified_repo[path] != spec.starting_repo[path]:
                candidate_extras.add(path)
    extras_tuple = tuple(sorted(candidate_extras))

    # Structural-invariant mutations.
    for idx, inv in enumerate(spec.negative_invariants):
        if isinstance(inv, PositiveTestPasses):
            continue  # handled with the positive tests below
        for mutation, replacement in _mutations_for_invariant(inv, extras_tuple):
            new_invs = list(spec.negative_invariants)
            if replacement is None:
                new_invs.pop(idx)
            else:
                new_invs[idx] = replacement
            mutations.append((mutation, replace(spec, negative_invariants=tuple(new_invs))))

    # Positive-test mutations: drop each test.
    for idx, test in enumerate(spec.positive_tests):
        remaining_tests = tuple(t for i, t in enumerate(spec.positive_tests) if i != idx)
        remaining_invs = tuple(
            inv for inv in spec.negative_invariants
            if not (isinstance(inv, PositiveTestPasses) and inv.test_path == test.path)
        )
        mutations.append((
            Mutation(
                kind="drop_test",
                target=test.name or test.path,
                description=f"remove positive test '{test.name or test.path}'",
            ),
            replace(spec, positive_tests=remaining_tests, negative_invariants=remaining_invs),
        ))

    return mutations


# ---------------------------------------------------------------------------
# Running mutations
# ---------------------------------------------------------------------------


def run_mutation_analysis(
    spec: TaskSpec,
    candidates: tuple[Candidate, ...],
) -> list[MutationResult]:
    """Apply every mutation to `spec` and report per-candidate verdict deltas."""
    validator = StructuredValidator()

    original_verdicts: dict[str, bool] = {
        cand.candidate_id: validator.evaluate(spec, cand).accepted
        for cand in candidates
    }

    results: list[MutationResult] = []
    for mutation, mutated_spec in mutate_spec(spec, candidates):
        per_cand: list[tuple[str, bool, bool, bool]] = []
        for cand in candidates:
            orig = original_verdicts[cand.candidate_id]
            new = validator.evaluate(mutated_spec, cand).accepted
            per_cand.append((
                cand.candidate_id,
                cand.label.should_accept,
                orig,
                new,
            ))
        results.append(MutationResult(
            mutation=mutation,
            spec_id=spec.task_id,
            per_candidate=tuple(per_cand),
        ))
    return results


@dataclass(frozen=True)
class MutationSummary:
    """Corpus-level rollup."""

    total_mutations: int
    load_bearing: int     # mutations that newly admitted a should-reject candidate
    brittle: int          # mutations that newly rejected a should-accept candidate
    invisible: int        # mutations with no safety-relevant verdict change
    by_kind: dict[str, dict[str, int]] = field(default_factory=dict)

    def fraction_load_bearing(self) -> float:
        return self.load_bearing / self.total_mutations if self.total_mutations else 0.0


def summarize(per_spec_results: dict[str, list[MutationResult]]) -> MutationSummary:
    total = load = brittle = invisible = 0
    by_kind: dict[str, dict[str, int]] = {}
    for results in per_spec_results.values():
        for r in results:
            total += 1
            cls = r.classification
            if cls == "load_bearing":
                load += 1
            elif cls == "brittle":
                brittle += 1
            else:
                invisible += 1
            slot = by_kind.setdefault(r.mutation.kind,
                                       {"load_bearing": 0, "brittle": 0, "invisible": 0})
            slot[cls if cls in slot else "invisible"] += 1
    return MutationSummary(
        total_mutations=total,
        load_bearing=load,
        brittle=brittle,
        invisible=invisible,
        by_kind=by_kind,
    )


# ---------------------------------------------------------------------------
# Coverage — Dodds-shaped honesty about *which* failure dimensions a spec
# actually defends against. A spec where load_bearing=0 for every kind is
# a slide-deck spec — it doesn't constrain anything the corpus exercises.
# ---------------------------------------------------------------------------


# Stable ordering for badge display so the UI is reproducible.
KNOWN_MUTATION_KINDS: tuple[str, ...] = (
    "drop_invariant",
    "widen_scope",
    "shrink_set",
    "weaken_bound",
    "drop_test",
)


def spec_coverage(results: list[MutationResult]) -> dict[str, bool]:
    """For one spec: which mutation kinds produced ≥1 load_bearing result?

    Returns True for each kind that was caught by the candidates, False
    for kinds that are either inapplicable (no such mutation generated)
    or invisible (mutation generated but caught nothing).
    """
    out = {kind: False for kind in KNOWN_MUTATION_KINDS}
    for r in results:
        if r.classification == "load_bearing":
            out[r.mutation.kind] = True
    return out


def coverage_by_kind(
    per_spec_results: dict[str, list[MutationResult]],
) -> dict[str, dict[str, bool]]:
    """For each spec: spec_coverage for that spec. Keyed by task_id."""
    return {tid: spec_coverage(rs) for tid, rs in per_spec_results.items()}


def coverage_score(coverage: dict[str, bool]) -> float:
    """Fraction of mutation kinds with at least one load_bearing case."""
    if not coverage:
        return 0.0
    return sum(1 for v in coverage.values() if v) / len(coverage)


# ---------------------------------------------------------------------------
# Serialization for the demo server
# ---------------------------------------------------------------------------


def result_to_dict(r: MutationResult) -> dict[str, Any]:
    return {
        "kind": r.mutation.kind,
        "target": r.mutation.target,
        "description": r.mutation.description,
        "classification": r.classification,
        "verdict_changed": r.verdict_changed,
        "newly_accepted": r.newly_accepted,
        "newly_rejected": r.newly_rejected,
        "per_candidate": [
            {"candidate_id": cid,
             "should_accept": sa,
             "original": orig,
             "mutated": new,
             "flipped": orig != new}
            for cid, sa, orig, new in r.per_candidate
        ],
    }


def summary_to_dict(s: MutationSummary) -> dict[str, Any]:
    return {
        "total_mutations": s.total_mutations,
        "load_bearing": s.load_bearing,
        "brittle": s.brittle,
        "invisible": s.invisible,
        "fraction_load_bearing": round(s.fraction_load_bearing(), 3),
        "kinds_order": list(KNOWN_MUTATION_KINDS),
        "by_kind": s.by_kind,
    }
