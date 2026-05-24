# Paper

Two parallel sources of truth for the same writeup:

| File | Surface | When to use |
|---|---|---|
| `PAPER.md` | Markdown — renders directly on GitHub | Repo-page preview, quick reading, copy into PRs/issues |
| `main.tex` + `references.bib` | LaTeX — compiles to PDF with the NeurIPS 2025 style | Submission, arXiv upload, camera-ready PDF |

Both files are kept in sync; numbers come from real runs captured in `docs/elicitation_and_mutation.md` and `dashboard.html` at the repo root.

## Compiling the PDF

Requires `pdflatex` + `bibtex` (TeXLive distribution) or [Overleaf](https://www.overleaf.com/).

### Local

```bash
cd paper/
pdflatex main.tex
bibtex   main
pdflatex main.tex
pdflatex main.tex      # second pass to resolve cross-refs
open     main.pdf      # macOS; use `xdg-open` on Linux
```

### Overleaf

1. Create a new project, upload `main.tex`, `references.bib`, and `neurips_2025.sty`.
2. Set the compiler to `pdfLaTeX`.
3. Click **Recompile**.

## Files

```
paper/
├── README.md           # this file
├── PAPER.md            # markdown version (no LaTeX required to read)
├── main.tex            # LaTeX source — NeurIPS 2025 style
├── references.bib      # BibTeX references
└── neurips_2025.sty    # official NeurIPS 2025 style file (mirror from gpleiss/latex_template)
```

## Reproducing the experiments in the paper

All numbers in the paper come from these commands (and are captured verbatim in the docs at the repo root):

```bash
# §4.3 + §4.4 (validator vs baselines, per-invariant ablation)
PYTHONPATH=. python3 -m safe_scaffold.cli task-eval --extended --rigorous --ablation

# §4.5 (mutation harness on the 60-pair corpus)
PYTHONPATH=. python3 -m safe_scaffold.cli mutate

# §4.6 (cross-dataset 4-step pipeline)
PATH="$HOME/.elan/bin:$PATH" PYTHONPATH=. python3 -m safe_scaffold.cli \
    dataset-run --dataset all --n 25 --no-compare

# Browser demo (everything together)
PYTHONPATH=. python3 demo_server.py
# → http://127.0.0.1:8765
```

Lean toolchain (for §3.3 + §4.6 `Lean ✓` column):

```bash
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
  -sSf | sh -s -- -y
```

`ANTHROPIC_API_KEY` is required for the LLM judges in §4.3 and for the elicitation / codegen calls in §4.6.

## Style file provenance

`neurips_2025.sty` is mirrored from
<https://github.com/gpleiss/latex_template/blob/main/neurips_2025.sty>
because the official NeurIPS conference site does not currently expose a
direct download URL. The file is identical to the official 2025 template
(checked against the formatting instructions in
<https://arxiv.org/abs/2506.15953>).
