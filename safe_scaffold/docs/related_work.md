# Related work

This document surveys the prior work most relevant to task-spec elicitation
for AI coding agents, ordered by closeness to what this repo builds. It
exists so the contribution claim in `track1_task_specs.md` can be
defended on a per-line basis: every novelty claim there has a corresponding
"who already did this" entry here.

## The Microsoft Research line on intent formalization

This is the closest academic lineage. All three works are by overlapping
authors out of Microsoft Research (Lahiri, Fakhoury, Chakraborty, Endres).

### TiCoder — Lahiri et al., arXiv 2208.05950 (Aug 2022), extended user study Fakhoury et al. arXiv 2404.10100 (Apr 2024)

*"Interactive Code Generation via Test-Driven User-Intent Formalization"*

Workflow: the LLM produces candidate code AND candidate distinguishing
tests. The user is asked to approve or reject tests. Approved tests then
prune the code candidate list. Evaluated on MBPP and HumanEval with
simulated user feedback from a reference solution; the published numbers
report 22–54% absolute improvement in pass@1 with 1–5 simulated user
queries.

**Relationship to this work.** TiCoder is the direct intellectual
ancestor of the positive-tests half of our spec format. The shared idea
is "tests as the elicitation surface": the user writes (or in TiCoder's
case, approves) executable predicates that the candidate must satisfy.

**Difference.** TiCoder's tests are LLM-generated and curated *during*
generation; ours are user-authored once per task and then re-used across
many agent outputs. TiCoder also has nothing analogous to our
negative-invariant layer — no concept of "scope creep," "forbidden
imports," or "diff size budget." TiCoder's eval is pass@1 on pre-built
benchmarks; ours is a 2×2 confusion matrix per evaluator on a corpus
with four ground-truth labels.

### nl2postcond — Endres, Fakhoury, Chakraborty, Lahiri, FSE 2024 (arXiv 2310.01831)

*"Can Large Language Models Transform Natural Language Intent into Formal Method Postconditions?"*

Workflow: ask the LLM to generate method-level postconditions from a
function's natural-language docstring. Use the postconditions as
assertion-shaped specifications. Evaluated by *discriminative power* —
the fraction of buggy/fixed pairs from Defects4J where the postcondition
fires on the buggy version but passes on the fix. Reports catching 64
real-world historical bugs that prior detection methods missed.

**Relationship to this work.** The discriminative-power framing is
exactly our false-accept-rate framing, applied at the assertion level
rather than the diff level. A postcondition is valuable iff it
distinguishes correct from incorrect code; an invariant in our system is
valuable iff it distinguishes CORRECT candidates from SUBTLE_WRONG /
SCOPE_CREEP / OBVIOUS_WRONG ones. Same metric, different artifact.

**Difference.** Postconditions are method-level and behavioral; our
invariants are diff-level and structural. The two are complementary —
you could imagine pairing nl2postcond-generated postconditions with our
diff-scoped invariants. A `PositiveTest` in our system can in principle
host an LLM-generated postcondition; we use vanilla pytest-style
assertions in the corpus because they're cheaper to author by hand.

### Intent Formalization — Lahiri, arXiv 2603.17150 (Mar 2026)

*"Intent Formalization: A Grand Challenge for Reliable Coding in the Age of AI Agents"*

This is a position paper, not an empirical contribution. Lahiri names
the field, articulates the *intent gap* (semantic distance between user
intent and program behavior), and lays out a research agenda. The
research agenda's items 2 ("change intent and compositionality"), 3
("identifying what to clarify cost-effectively"), and 7 ("integration
into developer workflows") are the items this work sits in.

**Relationship.** This is the framing we adopt. Our four-label corpus is
a concrete instance of "what to clarify cost-effectively"; our spec
format with ~10 LOC and ~150 seconds median authoring time is one
operating point on Lahiri's tradeoff spectrum.

## The industrial analog

### AWS Kiro Property-Based Testing — Nov 2025 GA release

*"Spec correctness via property-based testing"*

Workflow: the user writes requirements in EARS format ("THE System SHALL
allow authenticated users to view active car listings"). Kiro extracts
properties from the requirements and generates hundreds of random test
cases via Hypothesis-style shrinking to search for counterexamples.
Marketed under "spec correctness" — does the code match what the spec
said? Co-developed with the AWS Automated Reasoning Group.

**Relationship.** Kiro PBT is the production-grade version of our
positive-tests layer. Property-based testing with shrinking is strictly
more powerful than hand-authored unit tests at catching unanticipated
inputs; this is what a mature implementation of our positive-tests
piece should look like.

**Difference.** Kiro PBT has no analog of our negative-invariant layer.
PBT properties are *behavioral* — they assert what the code should do
on inputs. There is no PBT property that says "the agent shouldn't have
introduced an `import subprocess` here" or "the diff should not touch
`auth.py`." These are diff-scoped, not input-scoped. The contribution
of this repo, relative to Kiro PBT, is exactly that layer.

(A productionized version of this work would integrate with Kiro's PBT
rather than replace it: Kiro PBT covers behavioral spec coverage, our
invariants cover structural spec coverage, deny-overrides aggregation
across both.)

## Adjacent benchmarks (orthogonal contribution)

### PRDBench / PRDJudge — Fu et al., AAMAS 2026 (arXiv 2510.24358)

50 real-world Python projects across 20 domains, each with a structured
Product Requirements Document and per-task criteria. Evaluation is via
**PRDJudge**, a fine-tuned Qwen3-Coder-30B reaching ~90% human alignment
in fixed-interface scenarios.

**Relationship.** This is the LLM-as-judge route done seriously. Where
the in-this-repo `LLMJudge` baseline uses prompt-only Claude Sonnet,
PRDJudge fine-tunes a 30B model on the judging task. Their 90% human
alignment is the realistic ceiling for LLM-as-judge approaches.

**Difference.** PRDJudge needs GPU inference per (spec, candidate)
pair; ours needs a subprocess and a regex match. PRDJudge measures human
alignment; ours measures false-accept rate against ground-truth labels.
The two are complementary: a production system might use our cheap
deterministic validator as a fast first pass, and escalate uncertain
cases to PRDJudge.

### FeatureBench — Zhou et al., ICLR 2026 (arXiv 2602.10975)

200 challenging feature-level coding tasks automatically derived from
unit-test/PR pairs in 24 open-source repositories, with execution-based
evaluation. Reports Claude Opus 4.5 at 11.0% resolved rate vs 74.4% on
SWE-bench.

**Relationship.** Orthogonal contribution. FeatureBench answers "what
tasks should we benchmark coding agents on?"; this work answers "given
one agent output for one task, did it do what was asked?" The two
compose: FeatureBench gives the task distribution, our validator gives
the per-output verdict.

### TAI3 — arXiv 2506.07524

Tests agent intent integrity by mutating realistic task descriptions to
expose subtle agent errors. Focused on tool-using agents (80 toolkit
APIs across 5 domains), not coding agents per se. Different domain,
same intellectual neighborhood.

### Spec Kit Agents — arXiv 2604.05278

Augments GitHub Spec-Kit with context-grounding hooks; reports a +1.7%
improvement to 58.2% Pass@1 on SWE-bench Lite. About delivering
spec-driven generation, not about validating agent output.

## Spec-driven development tools (industry, not benchmarked)

The 2025–2026 SDD tool landscape — GitHub Spec-Kit, AWS Kiro, Tessl,
OpenSpec, BMAD, Spec Kitty, Codeplain — all share a workflow:

  Specify → Plan → Tasks → Implement

with a markdown-formatted spec at the top. None of these tools, with
the exception of Kiro PBT (above), publishes a deterministic validator
that produces a per-(spec, candidate) accept/reject verdict measurable
against ground truth. They're delivery tools, not validation tools.

A February 2026 independent evaluation across 13 scoring categories on
a medium-sized serverless Python backend ranked OpenSpec highest
overall (Sjogren cameronsjo/spec-compare), but the comparison was
multi-dimensional rather than against a single accuracy metric. There
is no public benchmark on which to directly position our 97.5%
accuracy / 3.3% FAR numbers; the closest comparable is Kiro PBT's
self-reported coverage which AWS describes qualitatively rather than
quantitatively.

## What's distinctly novel about this work

Putting the survey together:

| Component | Already exists in | Novelty here |
|---|---|---|
| Tests as elicitation surface | TiCoder (2022), Kiro PBT (2025) | Combined with structural diff layer |
| Discriminating correct from incorrect via spec | nl2postcond (2024) | Discrete four-label corpus, FAR metric |
| Property-based testing on natural-language specs | Kiro PBT (2025) | (we don't do this; orthogonal) |
| LLM-as-judge for coding agent output | PRDJudge (2026) | Deterministic alternative, zero per-call cost |
| Diff-scoped negative invariants | (none found) | **The core contribution** |
| Four-label CORRECT/OBVIOUS/SUBTLE/SCOPE_CREEP | (none found) | **Failure-mode decomposition of FAR** |
| Spec authoring cost as a reported metric | (none found systematically) | **Authoring-time + LOC per spec, defended as the elicitation cost claim** |

The combination — structured spec format = positive tests + diff-scoped
invariants; deterministic, no-per-call-cost validator; FAR decomposed
across four failure-mode labels — is what's new. None of these pieces
in isolation is novel.

## What this would mean for a real comparison

We could not actually re-run TiCoder, nl2postcond, PRDJudge, or Kiro PBT
on our 40-pair corpus inside the hackathon. TiCoder requires a
distinguishing-test-generation pipeline against Codex/GPT-4; nl2postcond
requires per-method LLM-generated postconditions; Kiro PBT requires the
Kiro IDE; PRDJudge requires a 30B fine-tuned judge model. What we
*can* do honestly is:

1. State the prior-work coverage gap precisely (this document).
2. Run our own deterministic validator + a same-prompt LLM-as-judge
   baseline against the corpus.
3. Report FAR per evaluator, broken out per failure label.
4. Identify which failure modes prior work *would* have caught
   (positive tests are TiCoder-like; PBT properties would be Kiro-like)
   and which it would *not* (the structural-diff and scope-creep
   categories, which need diff-level reasoning none of the prior work
   does).

That positioning is more defensible than claiming a quantitative
comparison we didn't actually run.
