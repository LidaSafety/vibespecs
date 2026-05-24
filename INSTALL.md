# Install & run

This is the full source for the task-spec elicitation + validation work.
Below is the minimum sequence of commands to run the rigorous eval and
regenerate `dashboard.html` on your own machine.

## Requirements

- Python 3.10 or newer (3.12 tested)
- No pip installs are required. The validator + metrics use only the
  Python standard library. Optional extras: `pytest` (for prettier test
  output; a stdlib shim runs without it), `z3-solver` (only for the
  Track 1 universal-property proof demo, irrelevant here).

## Install (just unzip, no `pip install`)

```bash
unzip vibespecs.zip            # or however the archive is named
cd safe_scaffold               # the unzipped folder
```

You now have:

```
safe_scaffold/                 # ← you are here
├── safe_scaffold/             # the Python package
│   ├── __init__.py
│   ├── cli.py                 # the `task-eval` subcommand lives here
│   └── task_spec/             # the new modules
│       ├── ablation.py
│       ├── baselines.py
│       ├── corpus_data/
│       ├── eval.py
│       ├── invariants.py
│       ├── metrics.py
│       ├── spec.py
│       ├── strong_baselines.py
│       └── validator.py
├── examples/
│   ├── demo_task_validation.py
│   └── viz_eval_dashboard.py
├── tests/
├── docs/
│   ├── comparison_methodology.md
│   ├── related_work.md
│   └── track1_task_specs.md
└── INSTALL.md                  # this file
```

## Verify it imports

```bash
PYTHONPATH=. python3 -c "from safe_scaffold.task_spec import validate; print('ok')"
# → ok
```

If you see `ModuleNotFoundError`, you're not at the top of the unzipped
folder, or `PYTHONPATH=.` is missing.

## Run the rigorous eval (no API key needed)

This runs on the 60-pair extended corpus, computes Cohen's κ,
discriminative power, per-invariant precision/recall, the per-invariant
ablation, and writes `dashboard.html`. **Network-free.** Two evaluators
will run (`structured`, `positive_only`); three LLM-based evaluators
(`llm_judge`, `nl2postcond`, `prd_style_judge`) will skip gracefully.

```bash
PYTHONPATH=. python3 -m safe_scaffold.cli task-eval \
    --no-llm --extended --rigorous --ablation \
    --dashboard dashboard.html
```

Expected output ends with a per-invariant ablation table. The headline
numbers should be approximately:

```
structured          98.3%   2.2%   0.0%   ...   κ=0.957   sec/Δ%FAR=31.9
positive_only       50.0%  66.7%   0.0%   ...   κ=0.200   (baseline)
```

## Run with the LLM baselines (needs ANTHROPIC_API_KEY)

```bash
export ANTHROPIC_API_KEY="$(cat ../key-anthropic.txt)"   # or paste the key directly
PYTHONPATH=. python3 -m safe_scaffold.cli task-eval \
    --extended --rigorous --ablation \
    --dashboard dashboard.html
```

This fills in the three LLM columns. Expect ~2 minutes for the full run
(60 pairs × 3 LLM evaluators ≈ 180 API calls, with caching on
`nl2postcond` so it's actually ~75).

## Just the demo (no eval)

If you only want to see what authoring a spec looks like:

```bash
PYTHONPATH=. python3 examples/demo_task_validation.py
```

## Run the unit tests

```bash
PYTHONPATH=. python3 -m unittest discover tests
# → Ran 176 tests in ~30s. OK (skipped=5)
```

The 5 skipped tests need `z3-solver` (Track 1's optional proof feature),
not relevant to the task-spec eval.

## Where to look first

1. `dashboard.html` (root of this folder, or wherever you wrote it) — the
   visual summary. Three confusion matrices, rigorous-metrics table,
   per-invariant ablation, per-task drill-down.
2. `docs/comparison_methodology.md` — head-to-head vs TiCoder,
   nl2postcond, Kiro PBT, PRDJudge with the actual numbers.
3. `docs/related_work.md` — the survey of prior art.
4. `docs/track1_task_specs.md` — the contribution claim writeup.
5. `safe_scaffold/task_spec/` — the implementation; ~1900 LOC total,
   read order: `spec.py` → `invariants.py` → `validator.py` →
   `baselines.py` → `eval.py` → `metrics.py` → `ablation.py`.

## Troubleshooting

**`ModuleNotFoundError: No module named 'safe_scaffold'`** — you forgot
`PYTHONPATH=.` or you're not in the top-level folder.

**`pytest: command not found`** — fine, the validator falls back to a
stdlib test runner. No action needed.

**LLM baselines all show SKIPPED** — `ANTHROPIC_API_KEY` is empty or
wasn't exported in the current shell. Verify with `echo $ANTHROPIC_API_KEY`.

**`urllib.error.HTTPError: HTTP Error 401`** — the API key is set but
invalid. Check the key.
