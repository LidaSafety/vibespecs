# Track 1: Task-spec elicitation for AI coding agents

## The problem

When an AI coding agent makes a change, how do you decide whether it did
what you asked? Today the choices are:

1. **Read every diff yourself.** Doesn't scale. The whole reason you used an
   agent is to avoid this.
2. **Trust the agent.** Doesn't work. Agents pass tests by writing code that
   passes the test rather than code that solves the problem; they also
   occasionally introduce credentials, exfil paths, or scope creep that
   tests don't cover.
3. **Have another LLM judge it.** Works some of the time, but is
   non-deterministic, costly, and notoriously bad at noticing what's
   *missing* from a diff vs. what's wrong with it.
4. **Run the tests.** This is what CI does. Catches "obvious wrong"
   (test fails) but accepts everything else.

This track is about option 5: a spec format the user writes *once* per task,
in seconds, that captures enough structural intent for a deterministic
validator to do better than 4 and at least as well as 3 — at zero per-call
cost and full reproducibility.

## The spec format

A `TaskSpec` is four fields:

- `description` — one-sentence natural-language statement of intent
- `starting_repo` — the project before the agent runs (usually existing code)
- `positive_tests` — a few unit tests that must pass after the agent runs
- `negative_invariants` — a few structural constraints on the diff

The novel asks are the negative invariants. The library exposes six types,
each a frozen dataclass:

| Invariant | Catches |
|---|---|
| `OnlyFilesModified(allowed)` | Edits outside the named scope |
| `FilesUnchanged(paths)` | Edits to files that must stay frozen |
| `NoNewImports(forbidden)` | New imports of `os`, `subprocess`, network libs |
| `NoSecretsInDiff()` | AKIA, sk-ant-, ghp_, BEGIN PRIVATE KEY, hardcoded passwords |
| `DiffSmallerThan(n)` | Sprawling rewrites of small tasks |
| `PositiveTestPasses(test)` | (Dispatched by the validator; runs the test) |

These are deliberately structural rather than behavioral. The user doesn't
have to reason about what the code does — they reason about what files the
change is allowed to touch, what dependencies it's allowed to introduce,
how big the diff is allowed to be. These are the same constraints a code
reviewer applies, except encoded once and checked automatically.

## The validator

Given a `TaskSpec` and a `Candidate` (the agent's proposed `modified_repo`),
the validator does the obvious thing: materialize the repo to a temp
directory, run each invariant, run each positive test in a subprocess,
aggregate with deny-overrides. Output is a `Verdict(ACCEPT | REJECT, ...)`
with a per-invariant trace for the dashboard.

The validator is deterministic, network-free, has no per-call cost, and
runs the full 40-pair corpus in ~1.6 seconds on a stock laptop.

## The evaluation

The eval is intentionally small enough to defend at the hackathon scale.
**10 tasks, 4 candidates each, 40 (spec, candidate) pairs.**

Each task has one candidate per ground-truth label:

- **CORRECT** — agent did exactly the right thing
- **OBVIOUS_WRONG** — fails the positive test (CI catches this today)
- **SUBTLE_WRONG** — passes the positive test but violates an invariant
- **SCOPE_CREEP** — passes the test, modifies extra files

Three evaluators are compared:

- **structured** — runs all invariants + positive tests (the contribution)
- **positive_only** — runs only the positive tests; ignores invariants
  (≈ what CI catches today)
- **llm_judge** — sends the task description + diff to Claude with
  instructions to return ACCEPT or REJECT

The headline metric is *false-accept rate* (FAR): fraction of
should-reject candidates the evaluator wrongly approves. This is the
security-critical number — false accepts are how malicious or sloppy
changes ship.

### Results

From `python examples/demo_task_validation.py`:

```
evaluator             acc    FAR    FRR   TP   FN   FP   TN  skip   time(s)
structured          97.5%   3.3%   0.0%   10    0    1   29     0      1.6
positive_only       50.0%  66.7%   0.0%   10    0   20   10     0      1.5
llm_judge           (varies by run; requires ANTHROPIC_API_KEY)
```

The structured validator's 3.3% FAR comes from a single miss: an
eval-based JSON loader (`t09_subtle_wrong`) whose RCE path is guarded by a
condition that doesn't trigger on the positive test inputs. This is a
legitimate limitation of structural invariants — catching it would require
behavioral analysis. The dashboard documents this miss explicitly rather
than hiding it.

The positive-only baseline accepts 20 of 30 bad candidates because, by
design, it doesn't look at imports, file scope, or diff size. This is a
quantification of how much "subtle wrong" + "scope creep" CI misses today.

### Spec authoring cost

Across the 10-task corpus: median 150 seconds per spec, median 10 LOC.
This is the corpus author's own time, not a user study. It's a lower
bound on what an experienced developer could achieve, not an empirical
distribution. A proper user study with developers unfamiliar with the
codebase would produce a slower (and more honest) number.

## Positioning vs prior work

The full survey is in [`related_work.md`](./related_work.md); this is the
short version of where the contribution claim is and isn't.

**Direct academic ancestor: TiCoder** (Lahiri et al., 2022; Fakhoury et
al., 2024). TiCoder uses LLM-generated tests as the elicitation surface
— the user approves/rejects distinguishing tests, and approved tests
prune code candidates. Our positive-tests piece descends directly from
that idea. The novelty here is *not* "tests as a spec"; that's TiCoder.
The novelty is the addition of a diff-scoped negative-invariants layer
on top.

**Direct industrial analog: AWS Kiro PBT** (Nov 2025 GA). Kiro extracts
properties from EARS-formatted requirements and runs property-based
testing (Hypothesis-style shrinking) to find counterexamples. This is a
strictly better behavioral spec mechanism than our hand-authored unit
tests. But PBT has no analog of our negative invariants — there is no
PBT property that fires on "agent imported `subprocess`" or "agent
edited an unrelated file." A productionized version of this work
combines Kiro PBT (behavioral coverage) with our invariants (structural
coverage), not replaces it.

**Discriminative-power metric: nl2postcond** (Endres et al., FSE 2024).
The "discriminative power" metric for postconditions — fraction of
buggy/fixed pairs the postcondition separates — is exactly our
false-accept-rate framing, applied to assertions rather than diffs. Our
four-label corpus (CORRECT / OBVIOUS_WRONG / SUBTLE_WRONG /
SCOPE_CREEP) is a discrete failure-mode-decomposed version of theirs.

**Position-paper framing: Lahiri 2026** (arXiv 2603.17150) names the
field "intent formalization" and lays out a research agenda. This work
sits in agenda items 2 ("change intent and compositionality"), 3
("identifying what to clarify cost-effectively"), and 7 ("integration
into developer workflows").

**Adjacent but orthogonal**: PRDBench/PRDJudge (Fu et al., AAMAS 2026) —
fine-tuned LLM judge, 90% human alignment, but needs 30B inference per
pair; FeatureBench (Zhou et al., ICLR 2026) — 200-task feature-level
benchmark, answers "what tasks?" not "did the agent do it right?".

### What's distinctly novel here

The combination, not any single piece:

1. **Positive-tests AND diff-scoped negative-invariants in one spec.**
   TiCoder has tests; Kiro PBT has properties; nl2postcond has
   postconditions; nobody combines an executable behavioral spec with a
   separate structural-diff invariant layer.

2. **Deterministic, network-free, per-call-free validator.** PRDJudge
   needs GPU inference per pair. LLM-as-judge needs API access. Ours
   runs the 40-pair corpus in ~1.6s with no model calls.

3. **The SCOPE_CREEP label.** None of the prior work has this as a
   labeled failure mode. It exists exactly to discriminate against
   action-level gating (which would accept each edit individually) and
   behavioral-property approaches (which can't see the diff). It's the
   axis that justifies the diff-scoped invariant layer.

## Limitations and what comes next

- **Hand-authored candidates.** The 40 candidates are not LLM outputs.
  A follow-up study would replace them with naturally-occurring agent
  outputs to verify the FAR holds on real failure modes.
- **Structural invariants only.** The validator doesn't reason about
  semantics. The `t09_subtle_wrong` miss is the documented limit of this
  approach; behavioral cross-checking (the Track 2 / Cryspen pattern)
  is the natural next step for catching such cases.
- **Self-reported authoring cost.** Without a user study, the "10 LOC, 2
  minutes" number is the corpus author's own time. A proper measurement
  requires N developers unfamiliar with each codebase.
- **Small corpus.** 10 tasks is enough to populate a confusion matrix and
  compare evaluators; it isn't enough for per-category significance claims.
- **No head-to-head against TiCoder, nl2postcond, Kiro PBT, or
  PRDJudge.** These require infrastructure (Codex/GPT-4 with the
  TiCoder pipeline; per-method postcondition generation; the Kiro IDE;
  a 30B fine-tuned judge model) that the hackathon budget can't
  reproduce. `related_work.md` argues qualitatively which failure modes
  each *would* catch. A future study would re-run those approaches on
  the same 40-pair corpus to make the comparison quantitative.
- **Only one LLM-as-judge baseline.** The built-in `LLMJudge` baseline
  uses Claude Sonnet with a one-shot prompt and no fine-tuning. Numbers
  will be worse than PRDJudge's fine-tuned 30B model. The dashboard
  reports this honestly rather than presenting it as the strongest
  LLM-judge baseline available.
