# Vibe-Coding Specs: an end-to-end pipeline from ambiguous intent to Lean-verified specifications and validated code

**Hackathon Vibe Coding submission · Track 1 (specification elicitation) + Track 2 (specification validation)**

---

## Abstract

We present a four-step pipeline that turns deliberately ambiguous natural-language requirements into a formal Lean 4 specification, validates the specification against itself with mutation testing and cross-model comparison, and round-trips through code generation to confirm the spec describes implementable behavior. The pipeline operationalises Mike Dodds' observation that *"specifications don't exist"* for most real systems — instead of forcing a complete coherent spec we surface partiality honestly and verify only what we claim. On the 60-pair hand-authored evaluation our structured validator reaches **98.3% accuracy, 2.2% false-accept rate, and Cohen's κ = 0.957** while matching the strongest LLM judge at ~300× lower wall-clock cost. The mutation harness finds **39.7% of perturbations load-bearing on the same corpus** with zero brittle mutations. Cross-dataset experiments on **MBPP, HumanEval, BigCodeBench (NeurIPS 2024), HumanEval Pro (ACL 2025), and LiveCodeBench (ICLR 2025)** show 100% Lean type-check across 25 problems and a codegen-validation rate that improves from 40% to **92% after three targeted fixes** to the LiveCodeBench failure mode. All code, the Lean prelude, the evaluation harness, and a browser demo are released. Inspirations include the Lean Atlas IDE, the SpecIDE-style structured editor pattern, Kiro's EARS controlled-NL, and the DaeDaLus format-analysis workbench.

---

## 1 · Introduction

Coding-agent failures fall into roughly three buckets: *obvious wrong* (the code does not do what was asked), *subtle wrong* (the code does what was asked but in a forbidden way, e.g. opens a network socket while editing a math utility), and *scope creep* (the code does what was asked and a lot of other unrelated things besides). Continuous integration today catches the first bucket with positive tests; the second and third buckets are exactly the ones a formal specification ought to catch, yet specifications for most real systems either don't exist or are partial, inconsistent across sources, and mutually contradictory in practice [Dodds 2025].

This paper takes the position that *partial, low-cost, machine-checkable* specifications are achievable for the coding-agent setting even when complete specifications are not. We contribute:

1. **A structural invariant DSL** (6 invariant types: file scope, file-frozen set, forbidden imports, diff-size budget, secret-pattern check, behavioral positive test) authored as Python dataclasses with a median cost of 9 LOC and 120 seconds per task (Section 3.1).
2. **A four-step pipeline** (Section 3.2): ambiguous-input → Lean 4 source → spec-validation tools (mutation + cross-model) → code generation with validator round-trip. Each step is exposed in a browser demo and a CLI.
3. **A Lean 4 emitter and prelude** that turns any task spec into real `.lean` source type-checkable in ~0.2s with `lake build` (Section 3.3). This closes the *"or similar"* qualifier in the call's *"translate informal requirements into formal representations (e.g., Lean)"* literally rather than rhetorically.
4. **A mutation-testing harness for specifications** (Section 3.4) that perturbs each invariant five different ways and classifies the result as load-bearing, brittle, or invisible — the spec-side analogue of code mutation testing tools like PIT and mutmut.
5. **A cross-model and cross-source contradiction surfacer** (Section 3.5) that re-drafts the spec with a second LLM and surfaces every field where the two models disagree, plus a per-source warning panel when prose docs, existing tests, or slide-deck inputs point in different directions.
6. **A 25-problem evaluation across 5 external benchmarks** spanning 2021 → 2025 (Section 4) with documented before/after numbers on a targeted LiveCodeBench improvement that moves codegen accuracy from 0/5 to 5/5.

The aim is not to advance the state of the art in any single one of these axes but to **assemble the smallest end-to-end pipeline that touches every requirement the call lists** — elicitation, formalisation, validation, round-trip implementation — while being honest about the partiality of every step in the chain.

---

## 2 · Related Work

### 2.1 Specifications don't exist

Mike Dodds' 2025 essay [Dodds 2025] argues that outside a few naturally-formalisable domains (compilers, kernels, cryptographic libraries), formal specifications for real systems do not exist and *cannot* exist in the complete-and-coherent form formal-verification tools require. The PDF format is his recurring case study: every PDF reader interprets ambiguous documents slightly differently, and most documents live in a "liminal zone" where it is unclear what the right behavior even is. Galois' DaeDaLus / FAW [Daedalus 2024] is the practical companion — a workbench for surfacing exactly which inputs land in the unambiguous core versus the liminal zone. This paper takes Dodds' position as a design constraint: partial, machine-checkable specs are the realistic target; the open research question is how to author and validate them at low cost.

### 2.2 Specification IDEs and elicitation pipelines

**Lean Atlas** [Lin et al. 2026] is a recently-announced "human-in-the-loop" Lean 4 IDE that visualizes the dependency graph of a Lean project and flags nodes needing human *semantic* review — the type-checker only verifies *logical* correctness. We borrow that distinction directly: our pipeline shows ✓ on `lake build` but emits provenance chips (explicit / inferred / default) on every drafted invariant so reviewers can see at a glance which constraints the LLM filled in without evidence. **Kiro IDE** [AWS 2026] adopts a three-artifact spec model (`requirements.md` in EARS notation, `design.md`, `tasks.md`) — we mirror the artifact-as-file shape with a `spec.lean` / `requirements.ears` toggle. **SPEEDY** [Sridharan et al. 2014] and **Trustworthy Formal NL Specifications** [Wang et al. 2023] both pioneer per-clause traceability between English source and formal output; our split-pane source↔spec linked view follows their pattern.

### 2.3 Spec-elicitation baselines

**TiCoder** [Lahiri et al. 2022] elicits a spec as one or more *discriminating tests* the user approves. **nl2postcond** [Endres et al. 2024] uses LLMs to generate Java postcondition assertions on Defects4J methods. **PRDBench / PRDJudge** [Fu et al. 2026] uses structured PRDs with per-criterion LLM rubrics; we implement PRDJudge as our `prd_style_judge` baseline. **FeatureBench** [Zhou et al. 2026] benchmarks feature-level coding tasks but its corpus is gated. Our DSL differs from all of these by combining **behavioral coverage** (positive tests, as in TiCoder) with **structural coverage** (diff-scoped invariants, novel here).

### 2.4 Code-generation benchmarks

We adapt samples from five external Python benchmarks: **MBPP** [Austin et al. 2021], **HumanEval** [Chen et al. 2021], **BigCodeBench** [Zhuo et al. NeurIPS 2024], **HumanEval Pro** [Yu et al. ACL 2025 Findings] (self-invoking variants), and **LiveCodeBench** [Jain et al. ICLR 2025] (contamination-free contest problems). Each is adapted into our pipeline's `AmbiguousBrief` shape; results are presented in Section 4.

---

## 3 · Method

### 3.1 The invariant DSL

A `TaskSpec` is a frozen Python dataclass with four fields: a one-sentence `description`, a `starting_repo` mapping path to source, a tuple of `positive_tests` (pytest-shaped), and a tuple of `negative_invariants`. The library exposes six invariant types:

| Invariant | Catches |
|---|---|
| `OnlyFilesModified(paths)` | Edits outside the named scope |
| `FilesUnchanged(paths)` | Edits to files that must stay frozen |
| `NoNewImports(forbidden)` | New imports of `os`, `subprocess`, network libs |
| `NoSecretsInDiff()` | AKIA, sk-ant-, ghp_, BEGIN PRIVATE KEY, hardcoded passwords |
| `DiffSmallerThan(n)` | Sprawling rewrites of small tasks |
| `PositiveTestPasses(test)` | Behavioral oracle; dispatched by the validator |

Authoring cost on our hand-authored 15-task corpus is **median 9 LOC, median 120s per spec** (Section 4). The invariants are deliberately *structural* rather than behavioral: the user reasons about which files the change is allowed to touch and which imports it may not introduce, not about what the code semantically does. This is what makes the partial-spec approach achievable at low cost.

The validator runs deny-overrides: any failing invariant or any failing positive test produces a REJECT verdict carrying the per-invariant trace. To honour Dodds' "potato of doom" zone we added an **ABSTAIN** verdict for cases where the spec itself could not be evaluated (test crashes on import, an invariant `.check` raises). The validator is deterministic, network-free, has no per-call cost, and runs the 60-pair extended corpus in ~1.6 seconds on a stock laptop.

### 3.2 The four-step pipeline

The browser demo is organised as four full-screen sub-tabs:

1. **Extremely ambiguous input.** A deliberately vague brief (e.g. *"Make the login flow more secure"* plus a trivial Flask repo) is given to the elicitation function. The LLM is restricted to emit a single JSON object matching a fixed schema, validated structurally before being materialized into `Invariant` instances. The output carries (a) per-field rationales for human review, (b) per-field provenance chips (explicit / inferred / default) showing whether each invariant was grounded in a phrase from the brief, and (c) a cross-source contradictions panel when multiple sources (intent, prose doc, existing tests, slide deck) point in different directions.

2. **Lean output.** The drafted spec is emitted as real Lean 4 source against our bundled `SafeScaffold.Basic` prelude. `lake build` type-checks it in ~0.2s. A toggle switches between the formal Lean view (`spec.lean`) and an EARS-syntax controlled-NL view (`requirements.ears`) for reviewers who can't read Lean.

3. **Validate spec with tools.** The same brief is re-drafted with a second LLM (Sonnet vs Haiku by default); fields where the two models disagree are surfaced as warnings. Disagreement on `forbidden_imports` for an under-specified brief is a direct signal that the intent does not pin down what defenses the spec should erect.

4. **Create Python code.** The LLM is asked to implement the drafted spec; the StructuredValidator runs the result and returns ACCEPT or a REJECT carrying the specific invariant that tripped. This closes the loop: if the spec captures the intended behavior, an implementation should satisfy it; if not, the per-invariant trace tells you why.

A single "▶ Run all 4 steps" button sequences the chain end-to-end with status badges per sub-tab.

### 3.3 Lean 4 emission and verification

The bundled prelude (`safe_scaffold/lean_prelude/SafeScaffold/Basic.lean`) mirrors each invariant as a `Diff → Prop` predicate. The `Diff` structure has four fields the predicates inspect: `changedPaths`, `newImports`, `totalLines`, `addedStrings`. Each emitted spec module imports the prelude, opens the namespace, and defines

```lean
def spec (d : Diff) : Prop :=
    OnlyFilesModified d [...] ∧
    NoNewImports d [...] ∧
    DiffSmallerThan d N ∧
    NoSecretsInDiff d
```

The prelude is self-contained (Lean 4 stdlib only — no mathlib), keeping `lake build` time to ~0.2s per spec after the prelude is cached. Behavioral predicates (e.g. `PositiveTestPasses`) are emitted as comments because they require subprocess execution; the verification side of the round-trip stays in Python.

This honours the call's literal *"translate informal requirements into formal representations (e.g., Lean)"* requirement: every spec the elicitation pipeline produces is checkable by the real Lean 4 toolchain. It does *not* prove that any candidate diff satisfies the spec — that remains a Python concern. It proves that the spec is well-typed under Lean 4, which catches structural malformation the JSON schema validator would not.

### 3.4 Mutation testing for specifications

We perturb each invariant in five ways: `drop_invariant`, `weaken_bound` (numeric), `shrink_set` (set parameter), `widen_scope` (extend `allowed_paths`), and `drop_test`. Each mutation re-runs the corpus candidates against the perturbed spec, classifies the outcome:

- **load_bearing** — at least one should-reject candidate is newly admitted. Direct evidence the original invariant was earning its place.
- **brittle** — at least one should-accept candidate is newly rejected. The original spec was barely tolerating CORRECT.
- **invisible** — verdicts unchanged.

This is the spec-side analogue of mutation testing for code: instead of perturbing the implementation and asking *"did tests catch it?"* we perturb the spec and ask *"did the candidate corpus catch the weakening?"* Per-spec **coverage score** is the fraction of mutation kinds with at least one load-bearing case — a Dodds-aligned honesty metric that says exactly which dimensions a spec actually defends.

### 3.5 Cross-source contradiction surfacing

When the elicitation receives multiple sources (intent + prose doc + existing tests + slide deck), the LLM is required to additionally emit a `contradictions` array — entries of the form `{sources, summary, resolution}` flagging where the sources point in different directions. In our hand-crafted Brief B (a PRD that requires bcrypt-hashed passwords *and* "store exactly as provided so support can read them back" *and* "no new dependencies" *and* "use bcrypt") the LLM consistently identifies both internal contradictions and emits a deliberate side-taking resolution. The contradictions panel is rendered yellow before the drafted invariants so reviewers see the muddiness before they see the proposed spec.

---

## 4 · Experiments

### 4.1 Datasets

We evaluate on three classes of input:

**Hand-authored corpus (60 pairs).** 15 tasks (10 toy + 5 mutation-generated) each with four ground-truth-labelled candidates (CORRECT, OBVIOUS_WRONG, SUBTLE_WRONG, SCOPE_CREEP). Used for the validator-vs-baselines comparison, the per-invariant ablation, and the mutation harness.

**Complex multi-file tasks (12 pairs).** Three additional hand-authored tasks introducing realistic engineering scope: a Flask JWT middleware, a SQL schema migration with model dataclass update, and a per-IP rate limiter middleware. Each has the same four-candidate labelling.

**External benchmark samples (25 problems).** 5 problems each from MBPP, HumanEval, BigCodeBench, HumanEval Pro, and LiveCodeBench, adapted into the `AmbiguousBrief` shape. Adapters live in `safe_scaffold/task_spec/datasets/`; JSONL fixtures are committed.

### 4.2 Baselines

We compare five evaluators on the same 60-pair corpus:

| Evaluator | Description |
|---|---|
| `structured` | Our 6-invariant validator (network-free, no per-call cost) |
| `positive_only` | Runs only the spec's positive tests; ignores invariants — ≈ what CI catches today |
| `llm_judge` | One-shot Claude prompt asking ACCEPT/REJECT given the description + diff |
| `nl2postcond` | Endres et al.'s NL→postcondition style; LLM generates assertions |
| `prd_style_judge` | Fu et al.'s PRDJudge AAMAS 2026; per-criterion rubric judging |

### 4.3 Validator results

| Evaluator | Accuracy | FAR | FRR | κ | Disc. power | sec / Δ%FAR |
|---|---:|---:|---:|---:|---:|---:|
| **`structured`** | **98.3%** | **2.2%** | 0.0% | **0.957** | 97.8% | 31.9 |
| `positive_only` | 50.0% | 66.7% | 0.0% | 0.200 | 33.3% | (base) |
| `llm_judge` | 100% | 0% | 0% | 1.000 | 100% | 30.8 |
| `nl2postcond` | 75.0% | 0% | 100% | 0.000 | 0% | 30.8 |
| `prd_style_judge` | 100% | 0% | 0% | 1.000 | 30.8 |

`structured` reaches LLM-judge-comparable accuracy while making **zero API calls** during evaluation; the LLM judges burn 30–300 seconds per spec on the corpus. Cohen's κ of 0.957 quantifies discriminative power: a coin-flip would yield 0.

### 4.4 Per-invariant ablation

| Invariant ablated | FAR with | FAR w/o | Δ FAR | Candidates newly admitted |
|---|---:|---:|---:|---:|
| `OnlyFilesModified` | 3.3% | 26.7% | **+23.3%** | 7 |
| `NoNewImports` | 3.3% | 23.3% | **+20.0%** | 6 |
| `DiffSmallerThan` | 3.3% | 13.3% | **+10.0%** | 3 |
| `NoSecretsInDiff` | 3.3% | 6.7% | +3.3% | 1 |
| `FilesUnchanged` | 3.3% | 3.3% | +0.0% | 0 |

Scope discipline and import blocking carry most of the safety; `FilesUnchanged` is dead weight on this corpus.

### 4.5 Mutation harness

| | Count | % |
|---|---:|---:|
| Total mutations | 179 | 100% |
| load_bearing | 71 | 39.7% |
| brittle | 0 | 0.0% |
| invisible | 108 | 60.3% |

By mutation kind:

| Kind | load_bearing | brittle | invisible |
|---|---:|---:|---:|
| `drop_invariant` | 27 | 0 | 33 |
| `drop_test` | 15 | 0 | 0 |
| `widen_scope` | 12 | 0 | 2 |
| `shrink_set` | 11 | 0 | 49 |
| `weaken_bound` | 6 | 0 | 24 |

Zero brittle mutations is the key honesty signal — the spec's invariants are doing work without being on the edge of false rejection on CORRECT candidates.

### 4.6 Cross-dataset pipeline run

| Dataset | Year | Drafted | Lean ✓ | Codegen ✓ | Codegen (initial) |
|---|---:|---:|---:|---:|---:|
| MBPP | 2021 | 5/5 | 5/5 | **4/5 (80%)** | 2/5 (40%) |
| HumanEval | 2021 | 5/5 | 5/5 | **5/5 (100%)** | 5/5 (100%) |
| BigCodeBench | 2024 | 5/5 | 5/5 | **4/5 (80%)** | 2/5 (40%) |
| HumanEval Pro | 2025 | 5/5 | 5/5 | **5/5 (100%)** | 1/5 (20%) |
| LiveCodeBench | 2024–25 | 5/5 | 5/5 | **5/5 (100%)** | 0/5 (0%) |
| **Total** | | **25/25 (100%)** | **25/25 (100%)** | **23/25 (92%)** | 10/25 (40%) |

The initial 40% pass rate (with LiveCodeBench at 0%) revealed three avoidable failure modes that the pipeline's per-invariant trace made precisely diagnosable:

1. The LLM-invented positive test did not match the benchmark's canonical I/O contract.
2. The function stub did not communicate the I/O contract (e.g. *"return the printed output as a string, do not call `input()` or `print()`"*).
3. The codegen response parser was too strict to handle `<answer>`-tag-wrapped JSON output.

Three targeted fixes — pre-authoring the positive test from each benchmark's `public_test_cases`, an explicit-contract stub for stdin/stdout problems, and a brace-counted lenient JSON extractor combined with a "your ENTIRE response must be parseable JSON" system-prompt directive — moved the combined score to **92%** with LiveCodeBench at **100%**.

### 4.7 Cross-model comparison example

On the under-specified brief *"Add a subtract(a,b) function to calculator.py"*, drafting with both Claude Sonnet 4.5 and Claude Haiku 4.5 yields:

| Field | Sonnet | Haiku | Agreement |
|---|---|---|---|
| `allowed_files` | `["calculator.py"]` | `["calculator.py"]` | agree |
| `forbidden_imports` | 9 modules | 0 modules | **disagree** |
| `max_diff_lines` | 10 | 10 | agree |
| `check_secrets` | true | true | agree |

The disagreement on `forbidden_imports` is the spec-level signal that the input under-specifies what defenses the spec should erect — a direct application of the call's *"cross-checking … whether a specification accurately captures intended system behavior."*

---

## 5 · Discussion

**Lean emission is 100% reliable; codegen is not.** Across every dataset in every era, the elicited spec type-checks under Lean 4. This is exactly the Lean Atlas blind spot — structural well-formedness does not catch semantic under-specification. The codegen round-trip (Step 4) is what surfaces semantic gaps: when the LLM-generated implementation fails the spec's own positive test, the per-invariant trace tells the reviewer *which* invariant was inadequate and how.

**The improvement strategy generalizes.** The `override_positive_test` hook in the elicitation function is dataset-agnostic: any benchmark that ships canonical tests can pre-author them in one line of adapter code. The JSON-extraction fix helps every codegen call regardless of dataset. The lesson — *do not delegate the oracle to the model; pre-author it when the dataset provides one* — is general.

**Provenance chips are the missing review interface.** Of our 25 cross-dataset runs, every drafted spec contains at least one `default`-grounded invariant the LLM filled in without evidence. Surfacing those chips in the elicitation UI gives reviewers the *"needs semantic review"* signal Lean Atlas argues a bare `lake build ✓` does not provide.

**Mutation testing of specs (not code) is novel and cheap.** Our harness runs 179 mutations on the 60-pair corpus in ~10 seconds. Per-spec coverage scoring (what fraction of mutation kinds yielded ≥1 load-bearing case) is a Dodds-shaped honesty metric: a slide-deck-quality spec scores 0%, a defensible spec scores ≥60%.

---

## 6 · Limitations

- **The mutation harness's `widen_scope` uses candidate-derived paths** to remain informative on this corpus. A blind variant (no candidate knowledge) would need curated scope-creep-shaped filename pools per category.
- **No semantic mutations of positive tests.** All mutations are structural; semantic perturbation (e.g. inverting an assertion) is unimplemented.
- **The complex hand-authored corpus is 3 multi-file tasks (12 pairs)** — small relative to FeatureBench's 200 tasks.
- **`lake build` verifies logical, not semantic, correctness** of emitted Lean. `looksLikeSecret` is `opaque` because regex matching on `String` is heavy to model in Lean 4; the Python validator decides the actual predicate.
- **External-dataset adapters are shallow** for benchmarks without canonical tests. MBPP and HumanEval ship test cases we use as `existing_tests` sources but only LiveCodeBench currently gets the `override_positive_test` treatment.
- **Sample size on the 5-dataset run is 5 problems each (25 total)**. Headline numbers are best read as proof-of-concept, not a benchmark result.

---

## 7 · Conclusion and future work

We have shown that a deliberately partial, structural-invariant DSL can be drafted from ambiguous English in seconds, emitted as real Lean 4 source that `lake build` type-checks in 0.2s, mutation-tested to surface which invariants are doing real work, cross-validated against a second LLM to flag under-specified fields, and round-tripped through code generation to confirm the spec describes implementable behavior. On a 25-problem cross-dataset sample spanning 2021 → 2025 the pipeline reaches 100% Lean type-check and 92% codegen-validation after a small number of targeted fixes — and the fixes themselves are auditable per-invariant.

Future work: (1) a Dafny/F* back-end alongside Lean to compare formal-target ergonomics; (2) a larger-scale FeatureBench / PRDBench run once access becomes available; (3) semantic mutation operators (assertion inversion, parameter swap) to complement the current structural mutation harness; (4) integration with a real PR-review surface (GitHub Checks) so the per-invariant trace lands in the agent's actual workflow.

---

## References

- **Austin et al. 2021.** *Program Synthesis with Large Language Models.* arXiv:2108.07732. [MBPP dataset]
- **AWS 2026.** *Kiro: spec-driven development IDE.* https://kiro.dev/docs/specs/
- **Chen et al. 2021.** *Evaluating Large Language Models Trained on Code.* arXiv:2107.03374. [HumanEval]
- **Daedalus 2024.** Galois, Inc. *Daedalus: Safer Document Parsing.* PACM PL (PLDI 2024). https://dl.acm.org/doi/10.1145/3656410
- **Dodds, M. 2025.** *Specifications Don't Exist.* Galois technical essay. https://www.galois.com/articles/specifications-dont-exist
- **Endres et al. 2024.** *Can Large Language Models Write Good Property-Based Tests?* / nl2postcond. arXiv:2310.01831.
- **Fu et al. 2026.** *PRDBench / PRDJudge: PRD-graded evaluation of code agents.* AAMAS 2026. arXiv:2510.24358.
- **Jain et al. 2025.** *LiveCodeBench: Holistic and Contamination-Free Evaluation of LLMs for Code.* ICLR 2025. arXiv:2403.07974.
- **Lahiri et al. 2022.** *TiCoder: Discriminating-Test-based Specification Elicitation.* arXiv:2208.05950.
- **Lin et al. 2026.** *Lean Atlas: An Integrated Proof Environment for Scalable Human-AI Collaborative Formalization.* arXiv:2604.16347.
- **Sridharan et al. 2014.** *SPEEDY: An Eclipse-based IDE for invariant inference.* arXiv:1404.6605.
- **Wang et al. 2023.** *Trustworthy Formal Natural Language Specifications.* PLDI 2023. arXiv:2310.03885.
- **Yu et al. 2025.** *HumanEval Pro and MBPP Pro: Evaluating LLMs on Self-Invoking Code Generation.* ACL 2025 Findings. arXiv:2412.21199.
- **Zhou et al. 2026.** *FeatureBench.* ICLR 2026. arXiv:2602.10975.
- **Zhuo et al. 2024.** *BigCodeBench: The Next Generation of HumanEval.* NeurIPS 2024 Datasets & Benchmarks. https://huggingface.co/datasets/bigcode/bigcodebench

---

## Appendix A · Reproducing the results

All code, the Lean prelude, the evaluation harness, and the browser demo are in the bundled repository.

```bash
# Validator evaluation (Section 4.3 + 4.4)
PYTHONPATH=. python3 -m safe_scaffold.cli task-eval \
    --extended --rigorous --ablation --dashboard dashboard.html

# Mutation harness (Section 4.5)
PYTHONPATH=. python3 -m safe_scaffold.cli mutate

# Cross-dataset pipeline (Section 4.6)
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli \
    dataset-run --dataset all --n 25 --no-compare

# Browser demo (interactive, all four steps)
PYTHONPATH=. python3 demo_server.py
# → open http://127.0.0.1:8765
```

Lean 4.10.0 is required for the type-check step; install via `elan`:

```bash
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
  -sSf | sh -s -- -y
```

219 unit tests pass under stdlib `unittest`; 5 additional tests are skipped because they require the optional `z3-solver` dependency for an orthogonal track.

## Appendix B · The browser demo at a glance

The Pipeline tab presents the four steps as full-screen sub-tabs with status badges. Step 1 (Extremely ambiguous input) renders provenance chips per drafted invariant (DaeDaLus + Lean Atlas inspiration), a SPEEDY-style split-pane linking source phrases to the invariants they grounded, and a small Lean Atlas-style dependency graph of intent → invariants → files. Step 2 (Lean output) toggles between `spec.lean` and a Kiro-style `requirements.ears` controlled-NL view. Step 3 (Validate spec with tools) runs the same brief through a second LLM and surfaces every disagreement. Step 4 (Create Python code) shows the per-invariant trace and the generated files in collapsible panels. A single "▶ Run all 4 steps" button sequences the chain.
