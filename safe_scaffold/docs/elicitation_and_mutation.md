# Spec elicitation + mutation testing

This document covers two contributions layered on top of the
StructuredValidator described in
[`track1_task_specs.md`](./track1_task_specs.md), addressing the two
research tracks called out in the project brief:

1. **Specification elicitation** — turning ambiguous intent (one-sentence
   description + starting repo) into a candidate spec the user can
   review, with provenance. Extended with **cross-model comparison**,
   **iterative refinement**, and **cross-source contradiction surfacing**.
2. **Specification validation** — checking whether a spec actually
   captures intended behavior, by mutating it and watching what changes.
   Extended with **per-spec coverage scoring** and an **ABSTAIN verdict**
   for the potato-of-doom zone Mike Dodds names in *"Specifications
   Don't Exist"*.

Both are implemented, exposed over an HTTP API and a browser UI
(`demo_server.py`), and exercised against the 60-pair corpus.

## Why this shape — Dodds-aligned design

Mike Dodds, June 2025: *"find ways to specify systems that have these
virtues [partial, immediately useful, low cost], and avoid the trap of
imposing a complete and coherent view that fundamentally does not
exist."* The 6-invariant DSL is exactly that bet — structural, partial,
~120s per spec to author. The features below operationalize the rest of
his essay:

| Dodds essay point | Feature here |
|---|---|
| *"the system might or might not be considered a PDF"* — most artifacts get a shrug, not accept/reject | `ABSTAIN` verdict + `uncertain=True` on `InvariantResult` |
| *"how do you know your partial spec is doing real work?"* | mutation harness — `load_bearing` vs `invisible` |
| *"partial spec → must know the partiality"* | per-spec coverage score (which mutation kinds yield ≥1 load-bearing) |
| *"too many partial specifications, none of which match each other"* | cross-model `compare_drafts` — runs N LLMs, surfaces disagreements |
| *"spec writing is hard — make it iterative and tool-assisted"* | reviewer-rejects-invariant → `refine_draft` loop with iteration timeline |
| *"the slide deck disagrees with the test code disagrees with the prose"* | cross-source contradiction surfacer — LLM cross-checks intent + prose_doc + existing_tests and reports conflicts |

---

## Track 1: Spec elicitation

### What it does

`safe_scaffold/task_spec/elicitation.py:draft_spec(description,
starting_repo) → DraftSpec` calls an LLM with a constrained JSON schema
that maps 1-to-1 onto the invariant dataclasses:

```json
{
  "allowed_files":      ["calculator.py"],
  "forbidden_imports":  ["os", "subprocess", "socket", "requests"],
  "max_diff_lines":     10,
  "check_secrets":      true,
  "positive_test":      {"path": "test_subtract.py", "name": "subtract", "code": "..."},
  "rationale":          { "<per_field>": "one sentence" }
}
```

The LLM **never emits Python** — only JSON in the above schema. Every
field is then validated structurally before being materialized into real
`Invariant` objects:

| Schema field | Validation rule |
|---|---|
| `allowed_files` | non-empty list of strings |
| `forbidden_imports` | subset of a fixed 9-module whitelist (`os, subprocess, socket, requests, urllib, http, shutil, ctypes, pickle`) |
| `max_diff_lines` | positive int |
| `check_secrets` | bool |
| `positive_test.code` | must contain at least one `def test_*` function |

If validation fails, the user sees the error and the verbatim LLM
response (audit trail) rather than a silently-filled-in default.

### Reviewer tooling (the "human in the loop")

Each materialized invariant carries the LLM's **one-sentence rationale**:

```
OnlyFilesModified(['math_utils.py'])
  → "Only math_utils.py needs modification to add the multiply function."

NoNewImports(['os', 'subprocess', 'socket', 'requests', 'urllib', ...])
  → "A simple multiply function requires no external imports; blocking common dangerous modules."

DiffSmallerThan(10)
  → "Adding a two-line function should require at most 10 lines including tests."
```

The "Draft a spec" tab in `demo_server.py` shows these as cards the
reviewer can read top-to-bottom before approving, and a collapsible
`<details>` element exposes the raw JSON for an auditor.

### How this fits the brief

> *"Tools that pull formal specifications out of ambiguous sources …
> Structured editors, GUIs, pipelines that translate informal intent
> into Lean (or similar)."*

The invariant DSL is "similar" rather than literally Lean. The pipeline
is the substantive part — staged JSON-only output, schema validation,
typed materialization, per-field rationales for review. Swapping the
output target from invariant dataclasses to Dafny `requires`/`ensures`
clauses or Lean 4 `Prop`s on diff objects is a localized change in
`_materialize()`; the elicitation/validation infrastructure is target-agnostic.

### What it deliberately is not

- **Not an end-to-end agent.** The reviewer is in the loop on purpose.
  A spec that's silently autofilled by an LLM is worth less than no
  spec at all — it provides false assurance.
- **Not a guarantee of soundness.** The LLM can still propose an
  under-constrained spec the reviewer waves through. The mutation
  harness (below) is what catches that.

---

## External-dataset integration (MBPP + HumanEval)

To check that our pipeline works on the standard benchmarks the
elicitation literature cites, we adapted a 10-problem sample from
**MBPP** (Austin et al., 2021) and **HumanEval** (Chen et al., 2021)
into the same `AmbiguousBrief` shape the demo speaks.

Adapters live in `safe_scaffold/task_spec/datasets/`:

| File | Source | Adapter shape |
|---|---|---|
| `mbpp_sample.jsonl` (5 problems) | [google-research/mbpp](https://github.com/google-research/google-research/tree/master/mbpp) | NL `text` → description · synth stub from test signature → starting_repo · `test_list` → existing_tests |
| `humaneval_sample.jsonl` (5 problems) | [openai/human-eval](https://github.com/openai/human-eval) | docstring → description · function signature + docstring → starting_repo · `test` field → existing_tests |

The briefs show up in the Pipeline tab's brief picker under "MBPP
samples" and "HumanEval samples" optgroups, alongside the hand-crafted
ambiguous demos.

### Batch run results across 5 datasets (25 problems, 2021 → 2025)

```
$ PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli \
    dataset-run --dataset all --n 25 --no-compare
```

Combined results, sorted by dataset publication year (current / after-improvement scores):

| Dataset | Year | Source | Drafted | Lean ✓ | Codegen ✓ | Codegen (initial) |
|---|---:|---|---:|---:|---:|---:|
| MBPP | 2021 | Austin et al. | 5/5 | 5/5 | **4/5 (80%)** | 2/5 (40%) |
| HumanEval | 2021 | Chen et al. (OpenAI) | 5/5 | 5/5 | **5/5 (100%)** | 5/5 (100%) |
| BigCodeBench | 2024 | Zhuo et al. (NeurIPS) | 5/5 | 5/5 | **4/5 (80%)** | 2/5 (40%) |
| HumanEval Pro | 2025 | Yu et al. (ACL Findings) | 5/5 | 5/5 | **5/5 (100%)** | 1/5 (20%) |
| LiveCodeBench | 2024–25 | Jain et al. (ICLR) | 5/5 | 5/5 | **5/5 (100%)** | 0/5 (0%) |
| **Total** | | | **25/25 (100%)** | **25/25 (100%)** | **23/25 (92%)** | 10/25 (40%) |

### What changed between initial and improved runs

The initial 40% (with LCB at 0%) came from three avoidable failure
modes that the pipeline let us diagnose precisely:

1. **The LLM-invented positive test didn't match the benchmark's canonical
   test.** On LCB this was catastrophic — the contests are
   stdin/stdout-shaped and the LLM's invented assertions used different
   I/O conventions.
2. **The stub didn't tell the model the I/O contract.** "Implement
   `solve`" was ambiguous; some models wrote functions that took a list
   of strings, others called `input()`/`print()` directly.
3. **The codegen JSON parser was too strict.** Several models wrapped
   their response in `<answer>` tags or prefaced it with reasoning;
   our parser rejected anything that wasn't `^```json...```$`.

Three targeted fixes brought the combined score to 92%:

1. **Pre-author the positive test from the benchmark's canonical tests
   when available.** LCB's `public_test_cases` is parsed into a real
   pytest and passed as `override_positive_test=` to `draft_spec`. The
   LLM still drafts the structural invariants but the test is the
   contest's own — so the codegen step is graded against the same
   oracle the benchmark uses.
2. **Explicit contract in the stub.** The LCB stub now says verbatim:
   "Convention: read EVERYTHING from the `stdin` string argument, return
   EVERYTHING that would be printed as a single string. Do NOT call
   input() or print()." The LLM no longer has to infer the contract.
3. **Lenient JSON extractor + stricter prompt.** `_extract_json_object`
   tries the whole text → strip code fences → strip `<answer>`/`<result>`/`<output>`
   tags → brace-count the first `{...}` substring. The system prompt
   additionally forbids preamble and tags up front.

Three observations from the (improved) results:

1. **Lean emission is 100% reliable across all 5 datasets and across
   both runs.** Whatever the LLM drafts, the structural schema is
   valid enough to compile under `lake build`. The type-checker's
   blind spot remains — it doesn't catch *semantic* under-specification
   (the Lean Atlas critique). The codegen round-trip is what catches it.
2. **The two remaining misses (`mbpp_14`, `bigcodebench_710`) are
   diagnosable per-invariant.** Both REJECT with a `PositiveTestPasses`
   trace pointing at the specific assertion the model's code failed.
   That's the validator working: the spec is a partial-but-honest
   description, and the model's code is partial-but-honestly wrong.
3. **The fix strategy generalizes.** The `override_positive_test`
   hook is dataset-agnostic; any benchmark that ships canonical tests
   can pre-author them in one line of adapter code. The
   JSON-extraction improvement helps every codegen call regardless of
   dataset.

### Why these five datasets and not others

| Considered | Year | Why we picked / skipped |
|---|---:|---|
| **MBPP** ✓ | 2021 | smallest format · NL + asserts · cited by every elicitation paper · single Python file per problem |
| **HumanEval** ✓ | 2021 | well-known · docstring-shaped · ships a function stub we can use directly as starting_repo · 164 problems for stretching |
| **BigCodeBench** ✓ | 2024 | 1,140 realistic library-integration tasks · clean `instruct_prompt` (NL only) + `complete_prompt` (NL + stub) · NeurIPS 2024 |
| **HumanEval Pro** ✓ | 2025 | self-invoking variants of HumanEval — spec must cover a base problem AND a derived problem that uses it · ACL 2025 Findings |
| **LiveCodeBench** ✓ | 2024–25 | contamination-free contest problems · ICLR 2025 · stress-tests on stdin/stdout-shaped tasks that don't fit our function model |
| **nl2postcond / Defects4J** ✗ | 2024 | Java — our validator is Python-only |
| **SWE-bench / Multi-SWE-bench** ✗ | 2024–25 | repo-level multi-file resolution; out of scope for our single-file invariant DSL |
| **FeatureBench** (Zhou et al., ICLR 2026) ✗ | 2026 | 200-task feature-level benchmark; access gated at time of writing |
| **PRDBench** (Fu et al., AAMAS 2026) ✗ | 2026 | 50 Python projects but PRD-grade prose, requires substantial adapter work — but reused as the `prd_style_judge` LLM evaluator baseline |
| **TiCoder discriminating tests** ✗ | 2022 | sub-method-level "spec" = single test; less natural to frame as elicitation |

## Complex corpus (t11–t13) and the ambiguous-input pipeline

The original 15-task corpus is intentionally toy (one or two files,
single-function changes). To exercise the pipeline on something closer
to real engineering, we added three multi-file tasks
(`safe_scaffold/task_spec/corpus_data/complex_tasks.py`):

| Task | Domain | What makes it complex |
|---|---|---|
| `t11_jwt_middleware` | Flask auth | Requires editing `app.py` while leaving `auth/jwt_utils.py` and `tests/test_jwt_utils.py` frozen. SUBTLE_WRONG slips a forbidden `import os` past CI; SCOPE_CREEP "improves" the JWT utility module. |
| `t12_sql_migration` | DB schema | Add a new SQL migration AND update the model dataclass. SUBTLE_WRONG imports subprocess to auto-apply migrations; SCOPE_CREEP folds the new column back into the original migration file. |
| `t13_rate_limit` | Web middleware | Per-IP rate limiter, new module + edit to existing route. SUBTLE_WRONG uses `socket.gethostbyname` to "normalize" IPs; SCOPE_CREEP also installs a global before-request hook in `app.py`. |

All 12 candidates classify correctly under the StructuredValidator (4/4
per task, all 4 ground-truth labels). The complex tasks live in
`FULL_CORPUS = EXTENDED_CORPUS + COMPLEX_TASKS` (18 tasks, 72 pairs)
and are served by default in the demo.

### Ambiguous-input → Lean pipeline

Three hand-crafted muddy briefs in
`safe_scaffold/task_spec/ambiguous_briefs.py` exercise the full
end-to-end pipeline:

| Brief | Shape | What's muddy |
|---|---|---|
| **A · 'do auth right'** | Extreme under-specification | Intent says "tighten up login"; no PRD, no acceptance criteria. The LLM has to invent the scope. |
| **B · PRD vs itself** | Internal contradiction | PRD requires bcrypt-hashed passwords AND "store exactly as provided so support can read them back". Also: "no new dependencies" AND "use bcrypt". |
| **C · slides vs tests** | Slide deck contradicting existing tests | Slides say negative percent = surcharge, full discount = item is free PLUS $5 credit. Existing tests assert negative percent returns base unchanged, full discount returns 0. |

The demo's **Draft a spec (LLM)** tab has a "load a hand-crafted
ambiguous brief" dropdown. Pick one, click **Draft spec →**, and you'll
see:

1. **Contradictions panel** (yellow) if the brief had multiple
   conflicting sources. The LLM lists which sources disagreed, on what,
   and which side it took.
2. **Drafted invariants** with per-field rationales and ✗ reject buttons
   for iterative refinement.
3. **↓ Lean 4 source emitted from this drafted spec** — even when the
   input was muddy, the output is sharp Lean. The contradictions panel
   makes the muddiness visible; the Lean panel makes the structural
   commitments precise.
4. **Verify with lake build →** — proves the emitted spec is well-typed
   under Lean 4. This is the literal-Lean leg of the pipeline.

That sequence — *muddy brief → drafted spec with contradictions
surfaced → Lean source → lake build success* — is the most Dodds-shaped
end-to-end demo we can offer in a hackathon-scale prototype. It maps
each step of his essay's failure mode to a concrete UI panel:

| Dodds quote | Panel |
|---|---|
| *"informal specifications can be ambiguous, partial, flexible"* | input form (muddy briefs) |
| *"too many partial specifications, none of which match each other"* | contradictions panel |
| *"writing the specification tends to uncover most of the bugs"* | drafted invariants + iterative refinement |
| *"translate informal intent into Lean (or similar)"* | Lean emission panel |
| *"once you have a good-quality formal specification, verifying is not difficult, merely expensive"* | `lake build` verify button (0.2s — not expensive at all, because the spec is small and partial *on purpose*) |

## Lean 4 emission (closing the "or similar" qualifier)

The brief asks for *"pipelines that translate informal intent into Lean
(or similar)."* Everything above was the "or similar" part. This
section is the literal Lean part.

`safe_scaffold/task_spec/lean_emitter.py` produces real Lean 4 source
from any `TaskSpec`. The bundled prelude
(`safe_scaffold/lean_prelude/SafeScaffold/Basic.lean`) mirrors each
invariant dataclass as a `Diff → Prop` predicate; the emitter writes
one module per spec, conjoining the invariants:

```lean
-- emitted from t01_factorial
import SafeScaffold.Basic
namespace Spec_t01_factorial
open SafeScaffold

/-- Auto-emitted from TaskSpec 't01_factorial'.
Description: Add a `factorial(n)` function to mymath.py. … -/
def spec (d : Diff) : Prop :=
    OnlyFilesModified d ["mymath.py"] ∧
    NoNewImports d ["os", "subprocess", "socket", "requests"] ∧
    NoSecretsInDiff d ∧
    DiffSmallerThan d 20
-- positive tests (behavioral, run via subprocess in Python): factorial returns correct values
end Spec_t01_factorial
```

Two strength levels:

1. **Emission** — `emit_lean(spec) → str`. Always available. Pure text
   generation; no Lean install needed.
2. **Verification** — `verify_lean(source) → VerifyResult`. Runs
   `lake build` against the prelude project and reports type-check
   success in ~0.2s per spec on this machine. Requires
   `elan + lean4` (the demo box has Lean 4.10.0).

Both are wired into the CLI and the demo:

```bash
# Emit to a file
PYTHONPATH=. python3 -m safe_scaffold.cli emit-lean --task-id t01_factorial -o t01.lean

# Emit and type-check
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli emit-lean \
    --task-id t07_password_hash --verify
# → ✓ Build completed successfully. (0.21s)
```

In the demo's **Validate** tab, the "Show as Lean →" button beside
**Validate →** drops the emitted source into a panel and offers a
"Verify with lake build →" button if Lean is available.

### What the Lean verification actually checks

`lake build` confirms the emitted spec is **well-typed under Lean 4** —
i.e. the predicates exist, the arguments have the right shapes, the
conjunction parses. It does *not* prove that a particular candidate
diff satisfies the spec (that remains a Python concern, see
`validator.py`). The point of the Lean target is:

- **Closes the brief's literal "Lean" requirement** rather than waving
  it away with "or similar".
- **Catches structural malformation** in LLM-drafted specs at type-check
  time, which is stricter than the JSON schema validator alone.
- **Provides a future hook** for users with mathlib to prove individual
  specs hold of toy diffs interactively in Lean.

### What it deliberately doesn't do

- No interactive proof of behavioral correctness. `looksLikeSecret` is
  declared `opaque` because regex matching on `String` is heavy to
  model and we don't need its semantics to type-check the spec.
- No round-trip from Lean back to Python. The Lean module is a
  *derivative* of the TaskSpec, not the source of truth.
- No mathlib dependency, which keeps `lake build` fast (~0.2s) but
  means we can't lean on mathlib lemmas inside emitted specs.

## Track 2: Spec mutation testing

### Method

`safe_scaffold/task_spec/spec_mutation.py` perturbs a spec and reruns
the corpus candidates against the perturbed version. Five mutation
kinds:

| Kind | Applies to | Example |
|---|---|---|
| `drop_invariant` | any invariant | remove `NoNewImports` |
| `weaken_bound` | `DiffSmallerThan(n)` | `n → 2n`, `n → 10n` |
| `shrink_set` | `NoNewImports(forbidden)`, `FilesUnchanged(paths)` | drop one entry |
| `widen_scope` | `OnlyFilesModified(allowed)` | add common files (`utils.py`, `config.py`, `__init__.py`) |
| `drop_test` | any `PositiveTest` | remove the test from the spec |

For each mutation, every candidate in the spec's ground-truth set is
re-evaluated, and the mutation is classified by what changes:

- **load_bearing** — at least one *should-reject* candidate that was
  caught by the original spec is now accepted. Direct evidence the
  mutated invariant was earning its place.
- **brittle** — at least one *should-accept* candidate that was
  accepted by the original spec is now rejected. The original spec was
  barely tolerating CORRECT; the mutation tipped it over.
- **invisible** — verdicts unchanged. The mutated invariant might be
  redundant on this corpus, the candidates might not exercise it, or
  the spec might be overconstrained without consequence.

### Results on the 60-pair corpus

```
total mutations:  179
load_bearing:     71  (39.7%)
brittle:           0
invisible:       108
```

Breakdown by mutation kind:

| Kind | load_bearing | brittle | invisible |
|---|---:|---:|---:|
| drop_invariant | 27 | 0 | 33 |
| drop_test | 15 | 0 | 0 |
| widen_scope | 12 | 0 | 2 |
| shrink_set | 11 | 0 | 49 |
| weaken_bound | 6 | 0 | 24 |

Five things this immediately tells us:

1. **Every positive test is load-bearing** (15/15 `drop_test` mutations
   admit a new should-reject candidate). Confirms the OBVIOUS_WRONG
   label is doing what it claims — the positive test always catches
   exactly the candidate built to fail it.
2. **Zero brittle mutations.** No invariant in the corpus is barely
   tolerating CORRECT. There's slack between the constraints and the
   correct candidates, which is what you want — specs aren't
   overfit to the exact CORRECT diff.
3. **`widen_scope` is mostly load-bearing** (12/14). When the scope
   check is loosened to include the files the candidates actually
   reach for, SCOPE_CREEP slips through almost every time. This is the
   "lazy reviewer" failure mode — if a human had widened the scope to
   tolerate what the agent did, the spec would no longer protect them.
4. **`shrink_set` for `NoNewImports` is mostly invisible** (11
   load-bearing out of 60 set-shrink mutations). Most forbidden modules
   in the spec aren't actually attempted by any SUBTLE_WRONG candidate
   — they're defense-in-depth, not active filters. The 11 load-bearing
   cases are the modules that are *specifically* the ones a candidate
   tries to import (most commonly `os`).
5. **The original spec is well-tuned, not overcautious.** With ~40% of
   mutations load-bearing and 0% brittle, the spec's invariants are
   doing work without being on the edge of false rejection.

Per-spec example (`t01_factorial`, 12 mutations — from
`python -m safe_scaffold.cli mutate --task-id t01_factorial -v`):

```
class           kind             target               newly accepted
--------------  ---------------  -------------------  -------------------
load_bearing    drop_invariant   OnlyFilesModified    +t01_scope_creep
load_bearing    widen_scope      OnlyFilesModified    +t01_scope_creep
load_bearing    drop_invariant   NoNewImports         +t01_subtle_wrong
load_bearing    shrink_set       NoNewImports (-os)   +t01_subtle_wrong
invisible       shrink_set       NoNewImports (-subprocess)
invisible       shrink_set       NoNewImports (-socket)
invisible       shrink_set       NoNewImports (-requests)
invisible       drop_invariant   NoSecretsInDiff
invisible       drop_invariant   DiffSmallerThan
invisible       weaken_bound     DiffSmallerThan
invisible       weaken_bound     DiffSmallerThan
load_bearing    drop_test        factorial_returns    +t01_obvious_wrong
```

This per-spec view is exactly what an author needs to know which
invariants in *their specific spec* are doing safety work and which are
inert. It's a stronger signal than the corpus-level ablation in
`ablation.py` because it pins down failure to specific (spec,
mutation, candidate) tuples rather than averaged FAR deltas.

### How this fits the brief

> *"Methods that check whether a candidate specification actually
> captures the system's intended behavior. Testing, cross-checking,
> mutation, formal validation."*
>
> *"Property-based fuzzing harness that flags specs which underconstrain
> or overconstrain the system."*

Mapping:

| Brief phrase | Mechanism here |
|---|---|
| "actually captures the intended behavior" | classify each invariant against ground-truth-labeled candidates |
| "mutation" | the literal core technique — perturb the spec, observe |
| "underconstrain" | a spec where most mutations are invisible is under-checking the corpus |
| "overconstrain" | a `brittle` mutation count > 0 flags the spec as overfit |

This is the spec-side analogue of mutation testing for code (PIT,
mutmut): instead of perturbing code and asking *"did tests catch it?"*,
we perturb the spec and ask *"did the candidate corpus catch the
weakening?"*

### Relationship to the per-invariant ablation

`safe_scaffold/task_spec/ablation.py` already does a *corpus-wide*
ablation: remove invariant T from every spec, re-measure FAR. The new
mutation harness is **finer-grained**:

- Ablation reports a single Δ FAR per invariant type, averaged across
  all uses in the corpus.
- Mutation reports per-(spec, mutation, candidate) outcomes, and adds
  parameter-level perturbations (`weaken_bound`, `shrink_set`,
  `widen_scope`) that the ablation cannot express.

You'd use ablation to decide *which invariant types belong in the
library*, and mutation to decide *whether this particular spec is
adequately constraining*.

---

## API surface (for integration tests / external tooling)

```
POST /api/elicit
  body: { "description": str, "starting_repo": {path: code, ...}, "task_id"?: str }
  → { "ok": bool, "error": str, "spec": TaskSpec | null,
      "drafted_invariants": [{"type", "args", "rationale"}],
      "positive_test_rationale": str, "raw_response": str }

POST /api/mutate
  body: { "task_id"?: str | null }   # null → run on whole corpus
  → { "task_id": str | null,
      "mutations" | "per_spec": [ MutationResult, ... ],
      "summary": { "total_mutations", "load_bearing", "brittle",
                   "invisible", "fraction_load_bearing", "by_kind" } }
```

Both endpoints are network-free except for `/api/elicit`, which uses
`urllib` directly against `api.anthropic.com` (stdlib only, no
`anthropic` package required — matches the philosophy of `baselines.py`).

---

## Limitations

- **`widen_scope` uses candidate-derived paths.** To make the mutation
  informative on a corpus where SCOPE_CREEP candidates create
  arbitrarily-named files, `widen_scope` widens by paths the
  candidates actually touched outside scope (the "lazy reviewer"
  failure mode). This is defensible — it measures whether the spec
  protects against a reviewer who tolerates exactly what the agent
  did — but is not blind to the candidates, unlike the other
  mutations. A blind version would need a curated list of
  scope-creep-shaped filenames per category.
- **No semantic mutations.** All mutations are structural
  (drop/weaken/widen). A `weaken_test` mutation that
  changes assertions or drops test functions inside a test file would
  exercise the behavioral side of the spec; not implemented.
- **Single-model elicitation.** The brief specifically calls out
  *cross-model spec comparison* as an example. The elicitation API is
  model-parameterized (`model="claude-sonnet-4-5"` default) and could
  be wrapped with a comparison driver, but no such driver is included
  here.
- **No "underspecification" of a baseline corpus.** Mutation testing
  is only as informative as the candidates it runs against. A spec
  that scores 0 load-bearing on these 4 candidates isn't necessarily
  weak — it might catch attacks not represented in the corpus.

---

## Run it

```bash
# Server (browser UI at all three tabs):
cd safe_scaffold
export ANTHROPIC_API_KEY="$(cat ../key-anthropic.txt)"
PYTHONPATH=. python3 demo_server.py
# → open http://127.0.0.1:8765

# Headless mutation-only run on the whole corpus:
PYTHONPATH=. python3 -c "
from safe_scaffold.task_spec.corpus_data import EXTENDED_CORPUS
from safe_scaffold.task_spec.spec_mutation import run_mutation_analysis, summarize
all_r = {s.task_id: run_mutation_analysis(s, c) for s, c in EXTENDED_CORPUS}
print(summarize(all_r))
"
```
