# Install & run

This is the full source for the `vibespecs` task-spec elicitation +
validation work. Below is the minimum sequence of commands to bring
the demo up, run the rigorous eval, and regenerate `dashboard.html`
on your own machine.

The full writeup is in
[`Hackathon-Vibe-Coding/PAPER.md`](Hackathon-Vibe-Coding/PAPER.md);
[`README.md`](README.md) is the short tour.

## Requirements

- Python 3.10 or newer (3.12 tested)
- No pip installs are required for the core. The validator + metrics
  use only the Python standard library. Optional extras:
  - `fastapi` + `uvicorn` for the browser demo,
  - `pytest` (prettier test output; a stdlib shim runs without it),
  - `z3-solver` (only for the legacy SPS-Fellowship universal-property
    proof demo, irrelevant for the spec work),
  - `elan` / Lean 4 for the `lake build` type-check step.

## Layout

You're looking at the `vibespecs` checkout:

```
vibespecs/                       # ← repo root, you start here
├── README.md                    # short tour
├── INSTALL.md                   # this file
├── dashboard.html               # visual eval output (regenerable)
├── key-anthropic.txt            # API key (gitignored in your fork)
├── Hackathon-Vibe-Coding/       # the paper (LaTeX + markdown twin)
└── safe_scaffold/               # all code lives here
    ├── demo_server.py           # FastAPI — iterative tab + 4-step tab
    ├── safe_scaffold/           # the Python package
    │   ├── cli.py               # task-eval, elicit, mutate, emit-lean, ...
    │   └── task_spec/           # spec / invariants / validator / elicitation /
    │                            # lean_emitter / codegen / verify_pbt /
    │                            # syntax_check / test_case_gen / spec_mutation /
    │                            # baselines / strong_baselines / eval / metrics /
    │                            # ablation / ambiguous_briefs / corpus_data / datasets
    ├── lean_prelude/            # Diff struct + invariant predicates
    ├── examples/                # standalone demo scripts
    ├── docs/                    # writeups
    └── tests/                   # stdlib unittest; 219+ passing
```

Most commands run from inside `safe_scaffold/`:

```bash
cd safe_scaffold
```

## Verify it imports

```bash
PYTHONPATH=. python3 -c "from safe_scaffold.task_spec import validate; print('ok')"
# → ok
```

If you see `ModuleNotFoundError`, you're not in `safe_scaffold/`, or
you forgot `PYTHONPATH=.`.

## Browser demo (iterative + batch pipeline)

```bash
export ANTHROPIC_API_KEY="$(cat ../key-anthropic.txt)"   # or paste the key directly
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 demo_server.py
# → http://127.0.0.1:8765 (or http://<host>:8765 if you've bound 0.0.0.0)
```

Two top-level tabs:

- **Iterative pipeline** (primary; recommended starting point). Five
  collapsible sections: input + starting repo (seeds with empty
  `main.py`), editable Lean (`lake build` button), editable per-file
  Python (`ast.parse` syntax check), editable LLM-emitted concrete test
  cases (run-row-by-row), and editable Python reference oracle (PBT
  fuzzing on 200 Hypothesis-drawn examples). One `Export bundle`
  button serialises everything to a single JSON file.
- **▶ 4-step pipeline**. The same modules sequenced batch-style with
  no human in the loop. Use this to reproduce the cross-dataset
  numbers and as the comparison baseline.

## Run the rigorous eval (no API key needed)

This runs on the 60-pair extended corpus, computes Cohen's κ,
discriminative power, per-invariant precision/recall, the per-invariant
ablation, and writes `dashboard.html`. **Network-free.** Two evaluators
will run (`structured`, `positive_only`); three LLM-based evaluators
(`llm_judge`, `nl2postcond`, `prd_style_judge`) will skip gracefully.

```bash
PYTHONPATH=. python3 -m safe_scaffold.cli task-eval \
    --no-llm --extended --rigorous --ablation \
    --dashboard ../dashboard.html
```

Expected headline numbers:

```
structured          98.3%   2.2%   0.0%   ...   κ=0.957   sec/Δ%FAR=31.9
positive_only       50.0%  66.7%   0.0%   ...   κ=0.200   (baseline)
```

## Run with the LLM baselines (needs ANTHROPIC_API_KEY)

```bash
export ANTHROPIC_API_KEY="$(cat ../key-anthropic.txt)"   # or paste the key directly
PYTHONPATH=. python3 -m safe_scaffold.cli task-eval \
    --extended --rigorous --ablation \
    --dashboard ../dashboard.html
```

This fills in the three LLM columns. Expect ~2 minutes for the full run
(60 pairs × 3 LLM evaluators ≈ 180 API calls, with caching on
`nl2postcond` so it's actually ~75).

## Try the elicitation + mutation pieces

```bash
# Spec mutation testing on the whole corpus (no API key needed):
PYTHONPATH=. python3 -m safe_scaffold.cli mutate

# LLM-drafted spec from an NL intent + a directory of files:
export ANTHROPIC_API_KEY="$(cat ../key-anthropic.txt)"
PYTHONPATH=. python3 -m safe_scaffold.cli elicit \
    --intent "Add a subtract(a, b) function to calculator.py" \
    --repo ./examples/sample_repo

# Iterative pipeline session through the CLI is in the demo browser;
# the CLI sibling for batch runs is dataset-run (below).
```

## Lean 4 emission (literal "into Lean")

Any corpus spec can be emitted as real Lean 4 source and type-checked:

```bash
# Emission only (no Lean toolchain needed):
PYTHONPATH=. python3 -m safe_scaffold.cli emit-lean --task-id t01_factorial

# Emit AND verify with `lake build` (~0.2s per spec):
# Requires Lean 4 / elan installed. Install with:
#   curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh -s -- -y
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli emit-lean \
    --task-id t07_password_hash --verify
```

The bundled prelude is in `safe_scaffold/lean_prelude/` — a tiny Lean
4 project (stdlib only, no mathlib) defining a `Diff` structure and
the invariant predicates. The Python emitter generates one
`Spec_<task_id>.lean` per TaskSpec; behavioral specs additionally
splice an algorithmic predicate (e.g. `def isNotPrime ...`) into the
same module.

## Cross-dataset 4-step pipeline (5 benchmarks, 25 problems)

```bash
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli \
    dataset-run --dataset all --n 25 --no-compare
```

Runs the batch 4-step pipeline on 5 problems each from MBPP, HumanEval,
BigCodeBench, HumanEval Pro, and LiveCodeBench. Expected: 25/25 Lean
type-check, 23/25 codegen verdict (see results table in `README.md`).

## Just the demo (no eval, no API)

If you only want to see what authoring a spec looks like without
hitting the network:

```bash
PYTHONPATH=. python3 examples/demo_task_validation.py
```

## Run the unit tests

```bash
PYTHONPATH=. python3 -m unittest discover tests
# → Ran 219+ tests in ~35s. OK (skipped=5)
```

The 5 skipped tests need `z3-solver` (legacy proof feature), not
relevant to the spec work. Newest additions:
`test_syntax_check.py` and `test_test_case_gen.py` cover the
iterative pipeline's per-button runners.

## Where to look first

1. **[`README.md`](README.md)** at the repo root — short tour, headline
   numbers, directory map.
2. **[`Hackathon-Vibe-Coding/PAPER.md`](Hackathon-Vibe-Coding/PAPER.md)**
   — the paper writeup (markdown twin of `neurips_2026.tex`).
3. **`dashboard.html`** at the repo root — visual eval summary: three
   confusion matrices, rigorous-metrics table, per-invariant ablation,
   per-task drill-down.
4. **`safe_scaffold/docs/elicitation_and_mutation.md`** — the
   Dodds-aligned writeup of the elicitation + mutation work, with
   method, numbers, and limitations.
5. **`safe_scaffold/docs/comparison_methodology.md`** — head-to-head
   vs TiCoder, nl2postcond, Kiro PBT, PRDJudge with the actual
   numbers.
6. **`safe_scaffold/docs/related_work.md`** — the survey of prior art.
7. **`safe_scaffold/docs/track1_task_specs.md`** — the original
   contribution claim writeup (validator + 6-invariant DSL).
8. **`safe_scaffold/safe_scaffold/task_spec/`** — the implementation;
   read order: `spec.py` → `invariants.py` → `validator.py` →
   `baselines.py` → `eval.py` → `metrics.py` → `ablation.py` →
   `elicitation.py` → `lean_emitter.py` → `codegen.py` →
   `verify_pbt.py` → `syntax_check.py` → `test_case_gen.py` →
   `spec_mutation.py`.

## Troubleshooting

**`ModuleNotFoundError: No module named 'safe_scaffold'`** — you
forgot `PYTHONPATH=.` or you're not inside `safe_scaffold/`.

**`pytest: command not found`** — fine, the validator falls back to a
stdlib test runner. No action needed.

**LLM baselines all show SKIPPED** — `ANTHROPIC_API_KEY` is empty or
wasn't exported in the current shell. Verify with
`echo $ANTHROPIC_API_KEY`.

**`urllib.error.HTTPError: HTTP Error 401`** — the API key is set but
invalid. Check the key.

**`elicit` or `dataset-run` reports `JSON parse: no JSON object found`**
— rare, but if the LLM returns a non-JSON response the error now
includes the first 300 chars of the raw response. Retry, or open an
issue with the surfaced snippet.

**Iterative-tab PBT button is disabled** — Section 5 disables it when
the reference oracle is empty. Paste an oracle into the textarea or
click `Clear oracle (skip PBT)` to keep working without one.
