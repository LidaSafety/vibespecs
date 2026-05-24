# vibespecs

**An iterative pipeline for eliciting, editing, and verifying specifications for AI coding agents — with a literal Lean 4 leg.**

Specifications for real systems do not exist as one-shot artifacts: the user's intent emerges as they discover edge cases, rewrite drafts, and react to failing tests. `vibespecs` takes this seriously — every spec artifact (the Lean predicate, the Python reference oracle, the generated code, the LLM-emitted concrete test cases) is independently editable and individually re-verifiable, and the whole session exports as a single JSON **spec bundle**. We back the iterative pipeline with a sibling **batch four-step pipeline** (elicit → Lean → code → validate) that uses the same infrastructure for benchmarking.

---

## Quickstart

```bash
# No pip installs needed for the core. Optional extras:
pip install fastapi uvicorn        # for the demo server
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
  -sSf | sh -s -- -y                # for Lean type-checking

export ANTHROPIC_API_KEY="$(cat key-anthropic.txt)"
cd safe_scaffold

# 1) Browser demo — iterative pipeline + batch 4-step pipeline (the headline UI)
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 demo_server.py
# → http://127.0.0.1:8765 (or http://<host>:8765 if bound to 0.0.0.0)
#   Click the "Iterative pipeline" tab for the primary workflow; the
#   "4-step pipeline" tab runs the same modules end-to-end without
#   stops, the way the benchmark numbers below are measured.

# 2) Validator eval on the 60-pair extended corpus
PYTHONPATH=. python3 -m safe_scaffold.cli task-eval \
    --extended --rigorous --ablation --dashboard ../dashboard.html

# 3) Spec mutation report
PYTHONPATH=. python3 -m safe_scaffold.cli mutate

# 4) LLM-drafted spec from your own intent
PYTHONPATH=. python3 -m safe_scaffold.cli elicit \
    --intent "Add a subtract(a,b) function to calc.py" --repo ./examples/sample_repo

# 5) Emit any corpus spec as real Lean 4 + type-check
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli emit-lean \
    --task-id t07_password_hash --verify

# 6) Run the batch 4-step pipeline on samples from 5 external benchmarks
#    (MBPP, HumanEval, BigCodeBench, HumanEval Pro, LiveCodeBench)
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli \
    dataset-run --dataset all --n 25 --no-compare

# 7) Tests (stdlib unittest; pytest is optional)
PYTHONPATH=. python3 -m unittest discover tests
```

See [`INSTALL.md`](INSTALL.md) for a longer walkthrough.

---

## The iterative pipeline (primary)

Every artifact is editable; every verification is a button. The browser demo's **Iterative pipeline** tab presents five collapsible sections:

| § | What | Backed by | Re-verify button |
|---|---|---|---|
| **1 · Input** | Free-text English brief (or pick a benchmark fixture); editable starting repo (default seed: empty `main.py`); optional ground-truth spec/code fields | `elicitation.py`, `ambiguous_briefs.py`, `datasets/` | `Elicit spec →` |
| **2 · Lean spec** | Editable `.lean` source carrying both the structural `Diff → Prop` block and the algorithmic predicate | `lean_emitter.py`, `lean_prelude/SafeScaffold/Basic.lean` | `Syntax check (lake build)` |
| **3 · Python code** | Editable per-file Python; lightweight codegen that skips the structural validator/PBT (deferred to §4/§5) | `codegen.generate_code_only`, `syntax_check.py` | `Generate code` · `Syntax check (ast.parse)` |
| **4 · Test cases** | ~8 LLM-emitted `(input, expected, rationale)` triples derived from the spec **without seeing the code**; editable per cell; subprocess runner per row | `test_case_gen.py` | `Generate cases` · `Run against current code` |
| **5 · PBT** | Editable Python reference oracle (pre-filled from elicitation, with a "review before trusting" warning); Hypothesis fuzzing on 200 inputs | `verify_pbt.py` | `Run PBT vs oracle` · `Clear oracle (skip PBT)` |

One `Export bundle` button at the top serialises the entire session — input, drafted invariants, Lean source, code, test cases and run results, oracle, PBT verdict — into a single JSON file. **No automatic chaining**: editing the Lean does not re-run the codegen; the reviewer drives every step. The bundle is the spec artifact.

## The batch four-step pipeline (for benchmarking)

Same modules, sequenced with no human in the loop. Sub-tabs in the demo's **▶ 4-step pipeline** view (full-screen, status-badged); the CLI sibling is `dataset-run`.

| Step | What | Key visualization |
|---|---|---|
| **1 · Extremely ambiguous input** | Load a deliberately vague brief; LLM drafts a `TaskSpec` (4 invariants + 1 positive test) as constrained JSON; cross-source contradictions surfaced inline | **Provenance chips** (explicit/inferred/default) + **split-pane source↔spec** linking + **mini dependency graph** |
| **2 · Lean output** | Emit the drafted spec as real Lean 4 source; type-check with `lake build` (~0.2s) | **`spec.lean` / `requirements.ears` toggle** (Kiro-style two-artifact view) |
| **3 · Create Python code** | LLM writes Python that satisfies the spec; deferred verdict so the reviewer reads the code first | Collapsible generated files |
| **4 · Validate the implementation** | `StructuredValidator` per-invariant trace + PBT-against-oracle (200 examples) | Verdict pill + per-invariant trace + PBT row (verified / falsified+counterexample) |

One **▶ Run all 4 steps** button at the top sequences them. All the cross-benchmark numbers below come from this batch variant.

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

### Spec mutation harness

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

Plus a **per-spec coverage score**: for each spec, what fraction of mutation kinds yielded ≥1 load-bearing case. The demo shows it as green/red badges per task.

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

1. **Pre-author the positive test from each benchmark's official tests where available.** LCB ships `public_test_cases` with the exact contest I/O; we parse it into a real pytest and pass it as `override_positive_test` to `draft_spec`.
2. **Make the function contract explicit in the stub.** For LCB's stdin/stdout problems, the stub now spells out `solve(stdin: str) -> str`, "do NOT call input() or print()".
3. **Codegen response parser is now lenient.** The new `_extract_json_object` falls through fences, `<answer>` tags, and brace-counts the first valid `{...}` substring.

### Lean 4 emission + verification

- Emission: pure Python text generation, always available — `lean_emitter.emit_lean(spec) → str`.
- Verification: `lake build` against the bundled `safe_scaffold/lean_prelude` project. **~0.21s per spec** after the prelude is cached.
- Prelude is self-contained (Lean stdlib only — no mathlib): `Diff` struct + invariant predicates.
- Behavioral block: every elicited spec carries an algorithmic Lean predicate (e.g. `def isNotPrime (n : Nat) : Prop := n < 2 ∨ ∃ k, 2 ≤ k ∧ k < n ∧ n % k = 0`) spliced into the same `.lean` module under the structural `spec` definition.
- All 15 corpus specs emit + verify successfully.

### Cross-model spec comparison

Same intent + repo, two Anthropic models (sonnet vs haiku). Field-level diff over `allowed_files`, `forbidden_imports`, `max_diff_lines`, `check_secrets`, `positive_test_loc`. Disagreement = the brief is under-specified on that axis.

### Tests

`cd safe_scaffold && PYTHONPATH=. python3 -m unittest discover tests` → **219+ tests passing** (10 new this round: `test_syntax_check.py` and `test_test_case_gen.py` for the iterative-pipeline modules).

---

## What's in this repo

```
vibespecs/
├── README.md                              # this file
├── INSTALL.md                             # full install + CLI walkthrough
├── dashboard.html                         # confusion matrices · rigorous metrics · per-task drill-down
└── safe_scaffold/                         # ★ THE CODE ★
    ├── demo_server.py                     # FastAPI — iterative tab + 4-step tab + compare-drafts tab
    ├── safe_scaffold/
    │   ├── task_spec/                     # all task-spec modules
    │   │   ├── spec.py                    # TaskSpec, Candidate, Verdict (+ ABSTAIN), CandidateLabel,
    │   │   │                              #   BehavioralSpec (function_name, lean_predicate, python_oracle)
    │   │   ├── invariants.py              # OnlyFilesModified, NoNewImports, DiffSmallerThan,
    │   │   │                              #   NoSecretsInDiff, FilesUnchanged, PositiveTestPasses
    │   │   ├── validator.py               # StructuredValidator pipeline (3-valued verdicts)
    │   │   ├── elicitation.py             # NL → drafted spec; constrained JSON; provenance;
    │   │   │                              #   cross-model compare; cross-source contradiction surfacer
    │   │   ├── lean_emitter.py            # spec → real Lean 4 source + lake build verify
    │   │   ├── ears_emitter.py            # same spec → EARS controlled-NL requirements.md
    │   │   ├── codegen.py                 # spec → Python (LLM); generate_code_only is the primitive used
    │   │   │                              #   by the iterative tab, generate_code wraps it with validator+PBT
    │   │   ├── syntax_check.py            # ast.parse per-file (used by iterative tab §3)
    │   │   ├── test_case_gen.py           # LLM-emitted concrete test cases + subprocess runner (§4)
    │   │   ├── verify_pbt.py              # Hypothesis-against-oracle PBT runner (§5)
    │   │   ├── spec_mutation.py           # mutation harness + per-spec coverage metric
    │   │   ├── baselines.py + strong_baselines.py
    │   │   │                              # positive_only, llm_judge, nl2postcond, prd_style_judge
    │   │   ├── eval.py + metrics.py + ablation.py
    │   │   │                              # eval loop, rigorous metrics, per-invariant ablation
    │   │   ├── ambiguous_briefs.py        # 3 hand-crafted muddy briefs
    │   │   ├── corpus_data/               # 15 toy tasks + 3 multi-file tasks
    │   │   └── datasets/                  # MBPP / HumanEval / BCB / HEP / LCB adapters
    │   ├── lean_prelude/                  # Diff struct + invariant predicates
    │   └── cli.py                         # task-eval, elicit, mutate, emit-lean, dataset-run, ...
    ├── tests/                             # stdlib unittest; 219+ passing
    ├── docs/                              # writeups; see "Further reading" below
    ├── hooks/                             # Claude Code PreToolUse hook (original action-gating Track)
    └── examples/                          # demo scripts
```

---

## Inspirations (what we built on)

| Reference | What we borrowed |
|---|---|
| **Mike Dodds, *Specifications Don't Exist*** ([Galois, 2025](https://www.galois.com/articles/specifications-dont-exist)) | Whole framing: specs emerge through iteration; surface the partiality honestly; check whether a spec is doing real work via mutation |
| **Lean Atlas** ([Lin et al., arXiv 2604.16347, 2026](https://arxiv.org/abs/2604.16347)) | Dependency graph view; the *logical vs semantic correctness* distinction → ABSTAIN verdict + provenance "default" chip |
| **Kiro IDE** ([AWS, 2026](https://kiro.dev/docs/specs/)) | Three-artifact file-shaped naming (`spec.lean` / `requirements.ears`) + EARS controlled-NL syntax |
| **Trustworthy Formal NL Specs** ([Wang et al., PLDI 2023](https://arxiv.org/pdf/2310.03885)) | Per-clause traceability between source and spec → linked source↔spec view |
| **DaeDaLus / Galois FAW** ([PLDI 2024](https://dl.acm.org/doi/10.1145/3656410)) | Surfacing the liminal zone of an ambiguous artifact → contradictions panel |
| **PRDBench / PRDJudge** (Fu et al., AAMAS 2026) | Multi-prompt LLM judge → implemented as `prd_style_judge` baseline |
| **nl2postcond** ([Endres et al., 2024](https://arxiv.org/abs/2310.01831)) | NL→postcondition baseline → implemented as `nl2postcond` evaluator |
| **TiCoder** ([Lahiri et al., 2022](https://arxiv.org/abs/2208.05950)) | Discriminating tests as spec; relationship documented in `safe_scaffold/docs/related_work.md` |
| **Hypothesis** (David MacIver et al.) | Property-based testing engine — backs the PBT-vs-oracle runner |
| **MBPP / HumanEval / BCB / HEP / LCB** | 5 problems per dataset adapted as external-dataset briefs |

---

## Limitations (honest section)

- **The mutation harness's `widen_scope` uses candidate-derived paths** to be informative on this corpus; it's not blind. Documented in `safe_scaffold/docs/elicitation_and_mutation.md`.
- **No semantic mutations of the positive tests.** All mutations are structural.
- **The complex corpus is 3 tasks** (12 (spec, candidate) pairs). Stress-tests multi-file scope but doesn't approach FeatureBench's 200-task scale.
- **`lake build` verifies logical, not semantic, correctness** of the emitted Lean — exactly the Lean Atlas critique. The semantic-review signal comes from the provenance chips, cross-model comparison, and the editable oracle, not from Lean itself.
- **The iterative pipeline's oracle is LLM-synthesized by default.** A bright warning in §5 of the iterative tab tells the reviewer to read it before trusting any PBT verdict; the `Clear oracle (skip PBT)` button disables PBT entirely when no oracle is appropriate.
- **`looksLikeSecret` is opaque in the Lean prelude.** Regex semantics aren't modelled; the Python validator decides the actual predicate.
- **External-dataset adapter is shallow.** MBPP and HumanEval ship test cases; we use them as `existing_tests` sources but only LiveCodeBench currently gets the `override_positive_test` treatment that pins the canonical contest test as the spec's positive test.

---

## Original SPS Fellowship project (action-gating Track)

The repo started as a scaffold for *formal action gating + adversarial server-code verification* (`world_model.py`, `verifier.py`, `translator.py`, `server_verifier/`). Those modules are still here and pass their tests; they're complementary to the task-spec work above.

### Wire as a Claude Code PreToolUse hook

```bash
cd safe_scaffold
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

- **[`safe_scaffold/docs/elicitation_and_mutation.md`](safe_scaffold/docs/elicitation_and_mutation.md)** — Dodds-aligned writeup of the elicitation + mutation work, with method, results, limitations, and a section mapping each Dodds quote to a UI panel.
- **[`safe_scaffold/docs/comparison_methodology.md`](safe_scaffold/docs/comparison_methodology.md)** — axis-by-axis comparison vs TiCoder, nl2postcond, Kiro, PRDBench.
- **[`safe_scaffold/docs/related_work.md`](safe_scaffold/docs/related_work.md)** — survey of prior art.
- **[`safe_scaffold/docs/track1_task_specs.md`](safe_scaffold/docs/track1_task_specs.md)** — the contribution-claim writeup for the StructuredValidator + 6-invariant DSL.
- **[`INSTALL.md`](INSTALL.md)** — full install + CLI walkthrough.
- **[`dashboard.html`](dashboard.html)** — visual eval output (confusion matrices, per-task drill-down).
