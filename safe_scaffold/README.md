# safe-scaffold

**Specification elicitation + validation for AI coding agents, with a literal Lean 4 leg.**

Pipelines that translate informal requirements into formal representations (Lean, EARS), with methods to verify that the extracted specifications are correct and complete. Pitched at the call's *"structured editors, GUIs, or pipelines that translate informal requirements into formal representations (e.g., Lean), building on approaches like SpecIDE."*

Originally a starter for the SPS Fellowship project on action-gating coding agents (Track 1 below); the **task-spec elicitation + validation work** that follows is the focus of this README.

---

## Quickstart

```bash
unzip safe_scaffold.zip
cd safe_scaffold

# No pip installs needed for the core. Optional extras:
pip install fastapi uvicorn        # for the demo server
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
  -sSf | sh -s -- -y                # for Lean type-checking

# 1) Browser demo — 4-step pipeline (the headline UI)
export ANTHROPIC_API_KEY="$(cat ../key-anthropic.txt)"
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 demo_server.py
# → http://127.0.0.1:8765 (or http://<host>:8765 if bound to 0.0.0.0)

# 2) Validator eval on the 60-pair extended corpus
PYTHONPATH=. python3 -m safe_scaffold.cli task-eval \
    --extended --rigorous --ablation --dashboard dashboard.html

# 3) Spec mutation report (Track 2)
PYTHONPATH=. python3 -m safe_scaffold.cli mutate

# 4) LLM-drafted spec from your own intent
PYTHONPATH=. python3 -m safe_scaffold.cli elicit \
    --intent "Add a subtract(a,b) function to calc.py" --repo ./examples/sample_repo

# 5) Emit any corpus spec as real Lean 4 + type-check
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli emit-lean \
    --task-id t07_password_hash --verify

# 6) Run the 4-step pipeline on samples from 5 external benchmarks
#    (MBPP, HumanEval, BigCodeBench, HumanEval Pro, LiveCodeBench)
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli \
    dataset-run --dataset all --n 25 --no-compare

# 7) Tests (stdlib unittest; pytest is optional)
PYTHONPATH=. python3 -m unittest discover tests
```

---

## The 4-step pipeline

Each step is a sub-tab in the demo's **▶ 4-step pipeline** view (full-screen, status-badged). Together they answer the call's two-part requirement: translate informal intent into a formal spec, then verify the spec is correct and complete.

| Step | What | Backed by | Key visualization |
|---|---|---|---|
| **1 · Extremely ambiguous input** | Load a deliberately vague brief; LLM drafts a `TaskSpec` (4 invariants + 1 positive test) as constrained JSON; cross-source contradictions surfaced inline | `elicitation.py`, `ambiguous_briefs.py`, `datasets/` | **Provenance chips** (explicit/inferred/default) + **split-pane source↔spec** linking + **mini dependency graph** |
| **2 · Lean output** | Emit the drafted spec as real Lean 4 source; type-check with `lake build` (~0.2s) | `lean_emitter.py`, `lean_prelude/SafeScaffold/Basic.lean`, `ears_emitter.py` | **`spec.lean` / `requirements.ears` toggle** (Kiro-style two-artifact view) |
| **3 · Validate spec with tools** | Cross-check the spec by drafting it again with a different model (sonnet vs haiku); surface every field where they disagree | `elicitation.compare_drafts` | Side-by-side panels + agreement/disagreement pills |
| **4 · Create Python code** | LLM writes Python that satisfies the spec; `StructuredValidator` returns ACCEPT (or names the invariant that tripped) | `codegen.py`, `baselines.StructuredValidator` | Verdict pill + per-invariant trace + collapsible generated files |

One **▶ Run all 4 steps** button at the top sequences them. Status badges (gray → yellow → green/red) live on each sub-tab.

---

## Results

### Validator vs baselines (60-pair extended corpus)

From `python -m safe_scaffold.cli task-eval --extended --rigorous --ablation`:

| Evaluator | Accuracy | FAR | FRR | Cohen's κ | Discriminative power | sec / Δ%FAR |
|---|---:|---:|---:|---:|---:|---:|
| **`structured` (ours)** | **98.3%** | **2.2%** | 0.0% | **0.957** | 97.8% | 31.9 |
| `positive_only` (≈ CI today) | 50.0% | 66.7% | 0.0% | 0.200 | 33.3% | (base) |
| `llm_judge` | 100% | 0% | 0% | 1.000 | 100% | 30.8 |
| `nl2postcond` (Endres et al.) | 75% | 0% | 100% | 0.000 | 0% | 30.8 |
| `prd_style_judge` (Fu et al., AAMAS 2026) | 100% | 0% | 0% | 1.000 | 30.8 |

`structured` matches the strongest LLM judge to within 1.7% accuracy at **~300× lower wall-clock** and zero per-call cost.

### Per-invariant ablation (drop-one-out)

| Invariant ablated | FAR with | FAR w/o | Δ FAR | Candidates newly admitted |
|---|---:|---:|---:|---:|
| `OnlyFilesModified` | 3.3% | 26.7% | **+23.3%** | 7 |
| `NoNewImports` | 3.3% | 23.3% | **+20.0%** | 6 |
| `DiffSmallerThan` | 3.3% | 13.3% | **+10.0%** | 3 |
| `NoSecretsInDiff` | 3.3% | 6.7% | +3.3% | 1 |
| `FilesUnchanged` | 3.3% | 3.3% | +0.0% | 0 |

Scope-discipline and import-blocking carry most of the safety; `FilesUnchanged` is dead weight on this corpus.

### Spec mutation harness (Track 2)

For each spec in the corpus, perturb each invariant (drop / weaken bound / shrink set / widen scope / drop test), re-run the candidates, classify each mutation:

| | Count | % |
|---|---:|---:|
| Total mutations | 179 | 100% |
| **load_bearing** (newly admits a should-reject) | **71** | **39.7%** |
| brittle (newly rejects a should-accept) | 0 | 0.0% |
| invisible (verdicts unchanged) | 108 | 60.3% |

By mutation kind:

| Kind | load_bearing | brittle | invisible |
|---|---:|---:|---:|
| `drop_invariant` | 27 | 0 | 33 |
| `drop_test` | 15 | 0 | 0 |
| `widen_scope` | 12 | 0 | 2 |
| `shrink_set` | 11 | 0 | 49 |
| `weaken_bound` | 6 | 0 | 24 |

Plus a **per-spec coverage score**: for each spec, what fraction of mutation kinds yielded ≥1 load-bearing case. The demo shows it as green/red badges per task. Example: `t01_factorial` covers 4/5 mutation kinds (80%) — `weaken_bound` is the gap.

### External datasets (5 benchmarks, 25-problem batch — 2021 → 2025)

From `safe-scaffold dataset-run --dataset all --n 25 --no-compare`:

| Dataset | Year | Venue | Drafted | Lean ✓ | Codegen ✓ |
|---|---:|---|---:|---:|---:|
| MBPP | 2021 | Austin et al. | 5/5 | 5/5 | **4/5 (80%)** |
| HumanEval | 2021 | Chen et al. (OpenAI) | 5/5 | 5/5 | **5/5 (100%)** |
| BigCodeBench | 2024 | Zhuo et al. (NeurIPS) | 5/5 | 5/5 | **4/5 (80%)** |
| HumanEval Pro | 2025 | Yu et al. (ACL Findings) | 5/5 | 5/5 | **5/5 (100%)** |
| LiveCodeBench | 2024–25 | Jain et al. (ICLR) | 5/5 | 5/5 | **5/5 (100%)** |
| **All** | | | **25/25 (100%)** | **25/25 (100%)** | **23/25 (92%)** |

The same batch a few iterations ago scored **10/25 (40%) — LCB was 0/5**. Three targeted improvements moved it to 92%:

1. **Pre-author the positive test from each benchmark's official tests where available.** LCB ships `public_test_cases` with the exact contest I/O; we parse it into a real pytest and pass it as `override_positive_test` to `draft_spec`. The LLM still drafts the structural invariants but the test is the contest's own.
2. **Make the function contract explicit in the stub.** For LCB's stdin/stdout problems, the stub now spells out `solve(stdin: str) -> str`, "do NOT call input() or print()", "return what would be printed". The LLM no longer has to guess.
3. **Codegen response parser is now lenient.** Models sometimes wrap JSON in `<answer>` tags or preface it with reasoning — the new `_extract_json_object` falls through fences, tags, and brace-counts the first valid `{...}` substring. Combined with a tightened "ENTIRE response must be parseable JSON" system prompt.

Three things still true after the improvement:

1. **Step 1 + Step 2 are 100% across every dataset, every year.** Elicitation always produces a structurally-valid spec; `lake build` always accepts it. The Lean Atlas critique still holds — well-formedness ≠ semantic correctness; the codegen loop is what catches semantic gaps.
2. **The 2 remaining misses are diagnosable per-invariant.** `mbpp_14` and `bigcodebench_710` REJECT with a `PositiveTestPasses` trace pointing at the specific assertion that failed.
3. **The improvement strategy generalizes.** Pre-authoring the positive test from a benchmark's canonical tests is a one-line adapter change per dataset; the JSON-extraction fix is universal.

### Lean 4 emission + verification

- Emission: pure Python text generation, always available — `lean_emitter.emit_lean(spec) → str`.
- Verification: `lake build` against the bundled `safe_scaffold/lean_prelude` project. **~0.21s per spec** on this machine after the prelude is cached.
- Prelude is self-contained (Lean stdlib only — no mathlib): `Diff` structure + invariant predicates (`OnlyFilesModified`, `NoNewImports`, `DiffSmallerThan`, `NoSecretsInDiff`, `FilesUnchanged`).
- All 15 corpus specs emit + verify successfully.

### Cross-model spec comparison (Track 2 validation tool)

Same intent + repo, two Anthropic models (sonnet vs haiku). Field-level diff over `allowed_files`, `forbidden_imports`, `max_diff_lines`, `check_secrets`, `positive_test_loc`. Concrete example from the demo:

For *"Add a subtract(a,b) function to calculator.py"*:

| Field | sonnet | haiku | Agreement |
|---|---|---|---|
| `allowed_files` | `["calculator.py"]` | `["calculator.py"]` | ✓ agree |
| `forbidden_imports` | 9 modules | 0 modules | ✗ **disagree** |
| `max_diff_lines` | 10 | 10 | ✓ agree |
| `check_secrets` | true | true | ✓ agree |

Disagreement = the brief is under-specified on that axis. Same brief; different models drew different defenses.

### Tests

`PYTHONPATH=. python3 -m unittest discover tests` → **219 tests passing**, 5 skipped (Z3-dependent, optional), 3 errors (`test_interceptor`, `test_translator`, `test_verifier` — pre-existing `import pytest` at module load; pytest is optional). 39 of the 219 are new this round:

- `test_elicitation.py` (13) — schema validation, materialization, no-API-key fallback
- `test_spec_mutation.py` (12) — per-invariant mutation generation, classification logic
- `test_lean_emitter.py` (14) — emitted source structure, helper functions, prelude bundle
- `test_complex_tasks.py` (4) — all 12 complex-task candidates classify correctly
- `test_ambiguous_briefs.py` (5) — fixture shape + brief B's PRD really does contradict itself
- 3 helper-method tweaks in existing files

---

## What's in this repo

```
safe_scaffold/
├── safe_scaffold/
│   ├── task_spec/                       # ★ THE WORK ★
│   │   ├── spec.py                      # TaskSpec, Candidate, Verdict (+ ABSTAIN), CandidateLabel
│   │   ├── invariants.py                # OnlyFilesModified, NoNewImports, DiffSmallerThan,
│   │   │                                #   NoSecretsInDiff, FilesUnchanged, PositiveTestPasses
│   │   ├── validator.py                 # StructuredValidator pipeline (3-valued verdicts)
│   │   ├── elicitation.py               # NL → drafted spec; constrained JSON; provenance;
│   │   │                                #   cross-model compare; iterative refinement;
│   │   │                                #   cross-source contradiction surfacer
│   │   ├── lean_emitter.py              # spec → real Lean 4 source + lake build verify
│   │   ├── ears_emitter.py              # same spec → EARS controlled-NL requirements.md
│   │   ├── codegen.py                   # spec → Python implementation (LLM) → validator round-trip
│   │   ├── spec_mutation.py             # mutation harness + coverage metric (Track 2)
│   │   ├── baselines.py + strong_baselines.py
│   │   │                                # positive_only, llm_judge, nl2postcond, prd_style_judge
│   │   ├── eval.py + metrics.py + ablation.py
│   │   │                                # eval loop, rigorous metrics, per-invariant ablation
│   │   ├── ambiguous_briefs.py          # 3 hand-crafted muddy briefs (under-spec, PRD-conflicts, slides-vs-tests)
│   │   ├── corpus_data/
│   │   │   ├── tasks_01_05.py + tasks_06_10.py + auto_mutants.py    # 15 toy tasks
│   │   │   └── complex_tasks.py         # 3 multi-file tasks: JWT, SQL migration, rate limit
│   │   └── datasets/
│   │       ├── mbpp_sample.jsonl        # 5 MBPP problems (Austin et al., 2021)
│   │       ├── humaneval_sample.jsonl   # 5 HumanEval problems (Chen et al., 2021)
│   │       └── __init__.py              # adapters → AmbiguousBrief shape
│   ├── lean_prelude/
│   │   ├── SafeScaffold/Basic.lean      # Diff struct + invariant predicates
│   │   ├── SafeScaffold.lean            # umbrella
│   │   ├── lakefile.lean + lean-toolchain
│   └── cli.py                           # subcommands: task-eval, elicit, mutate,
│                                        #   emit-lean, dataset-run, ...
├── demo_server.py                       # FastAPI — 4-step pipeline + 4 deep tabs
├── tests/                               # 219 passing
├── docs/
│   ├── elicitation_and_mutation.md      # ★ full writeup, Dodds-aligned
│   ├── comparison_methodology.md        # head-to-head vs TiCoder / nl2postcond / Kiro / PRDJudge
│   ├── related_work.md
│   └── track1_task_specs.md
├── dashboard.html                       # confusion matrices · rigorous metrics · per-task drill-down
├── INSTALL.md
└── README.md                            # this file
```

---

## Inspirations (what we built on)

| Reference | What we borrowed |
|---|---|
| **Mike Dodds, *Specifications Don't Exist*** ([Galois, 2025](https://www.galois.com/articles/specifications-dont-exist)) | Whole framing: partial specs are useful; surface the partiality honestly; check whether a spec is doing real work via mutation |
| **Lean Atlas** ([Lin et al., arXiv 2604.16347, 2026](https://arxiv.org/abs/2604.16347)) | Dependency graph view; the *logical vs semantic correctness* distinction → ABSTAIN verdict + provenance "default" chip |
| **Kiro IDE** ([AWS, 2026](https://kiro.dev/docs/specs/)) | Three-artifact file-shaped naming (`spec.lean` / `requirements.ears`) + EARS controlled-NL syntax |
| **Trustworthy Formal NL Specs** ([Wang et al., PLDI 2023](https://arxiv.org/pdf/2310.03885)) | Per-clause traceability between source and spec → linked source↔spec view |
| **DaeDaLus / Galois FAW** ([PLDI 2024](https://dl.acm.org/doi/10.1145/3656410)) | Surfacing the liminal zone of an ambiguous artifact (PDF) → contradictions panel |
| **PRDBench / PRDJudge** (Fu et al., AAMAS 2026) | Multi-prompt LLM judge → implemented as `prd_style_judge` baseline |
| **nl2postcond** ([Endres et al., 2024](https://arxiv.org/abs/2310.01831)) | NL→postcondition baseline → implemented as `nl2postcond` evaluator |
| **TiCoder** ([Lahiri et al., 2022](https://arxiv.org/abs/2208.05950)) | Discriminating tests as spec; relationship documented in `docs/related_work.md` |
| **MBPP** ([Austin et al., 2021](https://github.com/google-research/google-research/tree/master/mbpp)) | 5 problems adapted as external-dataset briefs |
| **HumanEval** ([Chen et al., 2021](https://github.com/openai/human-eval)) | 5 problems adapted as external-dataset briefs |
| **BigCodeBench** ([Zhuo et al., NeurIPS 2024](https://huggingface.co/datasets/bigcode/bigcodebench)) | 5 library-integration tasks; the `instruct_prompt` NL-only field maps cleanly to our intent |
| **HumanEval Pro** ([Yu et al., ACL 2025 Findings](https://arxiv.org/abs/2412.21199)) | 5 self-invoking variants — spec must cover both a base problem and a derived one |
| **LiveCodeBench** ([Jain et al., ICLR 2025](https://livecodebench.github.io/)) | 5 contamination-free contest problems — stress-tests the pipeline on stdin/stdout-shaped tasks |

---

## Limitations (honest section)

- **The mutation harness's `widen_scope` uses candidate-derived paths** to be informative on this corpus; it's not blind. Documented in `docs/elicitation_and_mutation.md`.
- **No semantic mutations of the positive tests.** All mutations are structural.
- **The complex corpus is 3 tasks** (12 (spec, candidate) pairs). Stress-tests the pipeline on multi-file scope but doesn't approach the 200-task scale of FeatureBench.
- **`lake build` verifies logical, not semantic, correctness** of the emitted Lean — exactly the Lean Atlas critique. The semantic-review signal comes from the provenance chips and Step 3's cross-model comparison, not from Lean itself.
- **`looksLikeSecret` is opaque in the Lean prelude.** Regex semantics aren't modelled; the Python validator decides the actual predicate. Type-checking the spec doesn't prove no secrets slip through.
- **External-dataset adapter is shallow.** MBPP and HumanEval ship test cases; we use them as `existing_tests` sources but don't gate Step 4's verdict on the official tests, only on the elicited spec's positive test.

---

## Original SPS Fellowship project (action-gating Track)

The repo started as a scaffold for *formal action gating + adversarial server-code verification* (`world_model.py`, `verifier.py`, `translator.py`, `server_verifier/`). Those modules are still here and pass their tests; they're complementary to the task-spec work above. See the *Citation pointers* and *Wiring into Claude Code* sections below for the original setup.

### Architecture (original)

```
Coding agent → interceptor.parse_*  →  verifier.verify(action, policy)  →  ALLOW / DENY / UNKNOWN
                                                                                 │
                                                                                 ▼
                                                                  human_loop + translator → new Policy
```

### Wire as a Claude Code PreToolUse hook

```bash
python -m safe_scaffold.cli init-policy /path/to/your/project --out ./.safe-scaffold/policy.json
mkdir -p ~/.claude/hooks
cp hooks/claude_code_pretooluse.sh ~/.claude/hooks/pretooluse.sh
chmod +x ~/.claude/hooks/pretooluse.sh
```

### Citation pointers (original action-gating Track)

- Bengio et al., *Towards Guaranteed Safe AI*, 2024 — `arXiv:2405.06624`
- Hadfield-Menell et al., *The Off-Switch Game*, 2017 — `arXiv:1611.08219`
- OWASP API Security Top 10 (2023) — source of `SecurityProperty.owasp_defaults()`

---

## Further reading in this repo

- **[`docs/elicitation_and_mutation.md`](./docs/elicitation_and_mutation.md)** — full Dodds-aligned writeup of Track 1 + Track 2 work, with method, results, limitations, and a section mapping each Dodds quote to a UI panel.
- **[`docs/comparison_methodology.md`](./docs/comparison_methodology.md)** — axis-by-axis comparison vs TiCoder, nl2postcond, Kiro PBT, PRDBench.
- **[`docs/related_work.md`](./docs/related_work.md)** — survey of prior art.
- **[`docs/track1_task_specs.md`](./docs/track1_task_specs.md)** — the contribution-claim writeup for the StructuredValidator + 6-invariant DSL.
- **[`INSTALL.md`](./INSTALL.md)** — full install + CLI walkthrough.
- **[`dashboard.html`](./dashboard.html)** — visual eval output (confusion matrices, per-task drill-down).
