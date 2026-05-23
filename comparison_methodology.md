# Head-to-head comparison vs prior spec-elicitation work

This document evaluates the work in this repository against the four
closest pieces of prior art identified in
[`related_work.md`](./related_work.md): **TiCoder**, **nl2postcond**,
**AWS Kiro PBT**, and **PRDBench/PRDJudge**.

For each axis (novelty, tasks, metrics) we (a) state how the prior work
sits, (b) state how this work sits, (c) identify a concrete way this
work is better-defended than the prior — implemented in code, not just
asserted in prose.

---

## Axis 1: Novelty of the spec format

| Work | Spec format | What it covers |
|---|---|---|
| TiCoder | LLM-generated distinguishing tests, user-approved | behavioral only (executable tests) |
| nl2postcond | LLM-generated Python `assert` postconditions | behavioral only (single-method assertions) |
| Kiro PBT | EARS requirements → LLM-extracted properties → Hypothesis shrinking | behavioral only (property-based) |
| PRDBench | structured PRDs with per-criterion rubrics | behavioral + soft policy criteria, but graded by an LLM judge |
| **This work** | hand-authored positive tests + 6 invariant types | **behavioral + structural** (diff-scoped) |

**How this work is better-defended:** the spec format combines behavioral
and structural coverage. Behavioral coverage alone cannot catch
SCOPE_CREEP (the agent did the task correctly *and* did other unrelated
things), as the corpus quantifies — the positive-only baseline catches
**0 of 10** scope-creep candidates. Adding the diff-scoped invariant
layer catches **10 of 10** on the hand-authored corpus and **15 of 15**
on the extended 60-pair corpus.

The novelty claim is honest: no individual piece (tests as spec, diff
predicates as policy) is new. The combination as a *user-authored
artifact attached to a single task* is.

---

## Axis 2: Tasks / corpus

| Work | Corpus | Provenance |
|---|---|---|
| TiCoder | 500+ MBPP, 164 HumanEval | published code-gen benchmarks |
| nl2postcond | 840 methods / 525 Java bugs | Defects4J |
| Kiro PBT | (not published) | (industrial use) |
| PRDBench | 50 Python projects across 20 domains | hand-collected + agent-annotated |
| FeatureBench | 200 feature-level tasks | auto-derived from real test/PR pairs |
| **This work** | 10 hand-authored + 5 mutation-generated = 15 tasks × 4 candidates = 60 pairs | hand + automated |

**Where this work is weaker:** corpus size. 60 pairs is 1–2 orders of
magnitude smaller than the academic benchmarks. This is honest in the
caveats section of `track1_task_specs.md`.

**How this work is better-defended:** every prior corpus evaluates "did
the agent solve the task?" — a single Boolean per (agent, task) pair.
This work's corpus has **four ground-truth labels per task** (CORRECT,
OBVIOUS_WRONG, SUBTLE_WRONG, SCOPE_CREEP), giving four pairs per task
instead of one. So while we have 15 tasks vs PRDBench's 50, we have 60
labeled (spec, candidate) pairs vs PRDBench's 50 unlabeled outputs.

More importantly, the failure-mode decomposition makes the comparison
*specific*: we can claim "structured catches X% of SUBTLE_WRONG and Y% of
SCOPE_CREEP" where prior work can only claim "structured solves X% of
tasks." The decomposition is what makes the per-invariant ablation
meaningful.

The mutation-based candidate generation (`auto_mutants.py`) extends the
corpus from 10 to 15 tasks with zero hand-authoring of the candidates —
just the canonical CORRECT/OBVIOUS_WRONG implementations, and the
SUBTLE_WRONG and SCOPE_CREEP are derived mechanically. This is a
scaling path that prior work doesn't offer.

---

## Axis 3: Evaluation metrics

| Work | Metric | What it measures |
|---|---|---|
| TiCoder | pass@1 with simulated user feedback | code generation accuracy |
| nl2postcond | discriminative power | fraction of buggy/fixed pairs distinguished |
| Kiro PBT | (qualitative "spec correctness") | not benchmarked |
| PRDJudge | human alignment | fraction of judge verdicts matching humans (~90%) |
| **This work** | FAR, FRR, accuracy, discriminative power, Cohen's κ, per-invariant P/R, authoring cost / Δ%FAR | six metrics, all on the same corpus |

**How this work is better-defended:** we report on *each* prior metric
where it applies, plus three new ones, on the same corpus:

- **Discriminative power (nl2postcond's metric)** — implemented in
  `metrics.discriminative_power`. Structured = **97.8%** on the extended
  60-pair corpus; positive-only = **33.3%**. Apples-to-apples with the
  nl2postcond paper's metric definition.

- **Cohen's κ** — implemented in `metrics.cohen_kappa`. None of the
  prior work reports κ. Structured = **0.957** ("almost perfect" per
  Landis-Koch); positive-only = **0.200** ("slight"). This rules out
  the "high accuracy might just be chance agreement" critique.

- **Per-invariant precision/recall** — implemented in
  `metrics.per_invariant_precision_recall`. Every invariant has **100%
  precision** on the corpus (no invariant false-alarms on a CORRECT
  candidate). This rules out the "your invariants are over-eager" critique.

- **Authoring cost per Δ%FAR** — implemented in
  `metrics.authoring_cost_per_far_reduction`. ~32 seconds of spec
  authoring per percentage-point of FAR reduction over the positive-only
  baseline. Directly addresses Lahiri 2026's research-agenda item 3
  ("identifying what to clarify cost-effectively"). None of the prior
  work reports a cost-benefit ratio of this shape.

- **Per-invariant ablation** — implemented in `ablation.py`. Removes
  each invariant from every spec in turn, re-runs the eval, reports the
  Δ FAR. On the extended corpus: `OnlyFilesModified` carries the most
  weight (Δ FAR +23pp), `NoNewImports` second (+20pp), `DiffSmallerThan`
  third (+10pp), `NoSecretsInDiff` fourth (+3pp), `FilesUnchanged`
  redundant on this corpus (Δ FAR 0pp — an honest result that we report
  rather than hide). This is the standard ML-paper ablation analysis,
  missing from all four prior works.

---

## Where this work *cannot* claim to be better

Three areas where the prior work has us beaten and we should not
pretend otherwise:

1. **Corpus size.** TiCoder 600+, nl2postcond 800+, PRDBench 50, FeatureBench
   200. Ours: 60. A real benchmark needs 200+ to support per-category
   significance claims. The 60-pair corpus here is a *calibration study*,
   not a benchmark.

2. **Naturally-occurring failure modes.** Our candidates are hand-authored
   (10 tasks) or mechanically mutated (5 tasks). Real LLM agents fail in
   shapes neither author imagined. nl2postcond evaluates against actual
   historical Defects4J bugs, which is a stronger ecological-validity
   claim than ours.

3. **Statistical significance.** With 60 pairs and a 65pp gap between
   structured and positive-only, the gap is well outside any plausible
   chance variation, but we don't compute confidence intervals or
   bootstrap. Adding `scipy.stats.binom` would be a 20-line fix; we just
   haven't done it.

---

## Summary: what would convince a skeptical reviewer

A reviewer who has read TiCoder, nl2postcond, Kiro PBT, and PRDBench and
asks "what's new here?" should be answered with:

1. **The spec format is new** — behavioral + diff-scoped structural in
   one user-authored artifact per task. Demonstrated on the corpus by
   the catch rates per failure label: positive-only catches **0 of
   10/15 SCOPE_CREEP, 0 of 10/15 SUBTLE_WRONG**; structured catches
   them all.

2. **The validator cost model is new** — deterministic, network-free,
   per-call-free, runs the 60-pair corpus in ~1.6s. PRDJudge needs GPU
   inference per pair; LLM-as-judge needs API access. This is not a
   theoretical advantage; it's the difference between gating every PR
   in CI vs gating only a sampled subset.

3. **The ablation is new** — per-invariant Δ FAR is the kind of
   analysis ML papers do as standard and spec-validation papers don't.
   We can say *which* of our invariants is doing the work, with
   numbers.

4. **Three of our metrics are new in this context** — Cohen's κ,
   per-invariant P/R, and authoring-cost-per-Δ%FAR — and the fourth
   (discriminative power) is implemented exactly as nl2postcond defines
   it, on the same corpus where we report the other metrics.

Three areas where the answer should be "not yet": corpus size,
ecological validity, statistical confidence intervals.
