"""Generate a self-contained HTML dashboard from an EvalRun.

Run:

    python -m examples.viz_eval_dashboard
    # writes dashboard.html in the current working directory

Or import:

    from examples.viz_eval_dashboard import render_dashboard
    render_dashboard(run, path="my.html")

Design:
- Single file. No JS framework. No CDN. No external CSS.
- Inline SVG for charts; plain HTML + a small CSS block for layout.
- Browsable: scroll to see the per-task drill-down for each of the 40
  (task, candidate) pairs, with the diff colorized and per-invariant
  trip status shown.
- Honest framing: the page header calls out the corpus size, that it's
  hand-authored, and the one known limitation (t09 eval-based loader).
"""

from __future__ import annotations

import difflib
import html
from pathlib import Path

from safe_scaffold.task_spec.baselines import (
    LLMJudge,
    PositiveTestsOnly,
    StructuredValidator,
)
from safe_scaffold.task_spec.eval import (
    EvalRun,
    EvaluatorReport,
    PairOutcome,
    run_eval,
)
from safe_scaffold.task_spec.spec import CandidateLabel


_PAGE_CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: #f7f7f5;
    color: #1f2024;
    margin: 0;
    padding: 0;
    line-height: 1.5;
}
.container { max-width: 1180px; margin: 0 auto; padding: 32px 24px 80px; }
h1 { font-size: 28px; font-weight: 600; margin: 0 0 6px; }
h2 { font-size: 20px; font-weight: 600; margin: 36px 0 12px; border-bottom: 1px solid #e5e5e0; padding-bottom: 6px; }
h3 { font-size: 16px; font-weight: 600; margin: 18px 0 8px; }
.muted { color: #6c6c70; font-size: 14px; }
.kicker { letter-spacing: 0.08em; text-transform: uppercase; color: #6c6c70; font-size: 11px; font-weight: 600; }

/* Top metric cards */
.cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 22px 0; }
.card { background: #fff; border: 1px solid #e5e5e0; border-radius: 8px; padding: 14px 16px; }
.card .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: #6c6c70; font-weight: 600; }
.card .value { font-size: 26px; font-weight: 600; margin-top: 4px; color: #1f2024; }
.card .unit { font-size: 13px; color: #6c6c70; margin-left: 4px; font-weight: 400; }

/* Confusion matrices row */
.matrices { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 14px 0 8px; }
.matrix { background: #fff; border: 1px solid #e5e5e0; border-radius: 8px; padding: 14px 16px; }
.matrix h3 { margin: 0 0 8px; font-size: 15px; }
.matrix .name { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; color: #1f6feb; }
.matrix table { width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 13px; }
.matrix th, .matrix td { padding: 8px 10px; text-align: center; border: 1px solid #e5e5e0; }
.matrix th { background: #f5f5f0; font-weight: 500; color: #44444a; font-size: 12px; }
.matrix td.label { background: #f5f5f0; text-align: left; font-weight: 500; font-size: 12px; color: #44444a; }
.matrix .count { font-size: 18px; font-weight: 600; }
.matrix .sub { font-size: 11px; color: #6c6c70; margin-top: 2px; }
.tp { background: #ecfdf5; } .tn { background: #ecfdf5; }
.fp { background: #fef2f2; } .fn { background: #fffbeb; }
.skipped-note { font-size: 11px; color: #b08000; margin-top: 8px; font-style: italic; }

/* Headline row inside matrix */
.metrics-row { display: flex; gap: 14px; margin-top: 10px; padding-top: 10px; border-top: 1px solid #e5e5e0; }
.metrics-row .item { flex: 1; }
.metrics-row .num { font-size: 18px; font-weight: 600; }
.metrics-row .num.danger { color: #b91c1c; }
.metrics-row .num.good { color: #047857; }
.metrics-row .sub { font-size: 10px; color: #6c6c70; text-transform: uppercase; letter-spacing: 0.06em; }

/* Bar charts */
.barchart { background: #fff; border: 1px solid #e5e5e0; border-radius: 8px; padding: 16px; margin: 8px 0; }
.bar-row { display: grid; grid-template-columns: 130px 1fr 60px; gap: 10px; align-items: center; margin-bottom: 6px; font-size: 13px; }
.bar-row .row-label { color: #44444a; font-size: 12px; }
.bar-track { background: #f0f0eb; border-radius: 3px; height: 18px; position: relative; overflow: hidden; }
.bar-fill { height: 100%; }
.bar-fill.structured { background: #047857; }
.bar-fill.positive_only { background: #d97706; }
.bar-fill.llm_judge { background: #6366f1; }
.bar-row .val { font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #44444a; text-align: right; }

/* Per-task drill-down */
.taskcard { background: #fff; border: 1px solid #e5e5e0; border-radius: 8px; margin: 16px 0; overflow: hidden; }
.taskcard .header { padding: 12px 16px; background: #f5f5f0; border-bottom: 1px solid #e5e5e0; }
.taskcard .task-id { font-family: ui-monospace, Menlo, monospace; font-size: 13px; color: #1f6feb; font-weight: 600; }
.taskcard .category { display: inline-block; padding: 1px 7px; background: #e0e7ff; color: #3730a3; border-radius: 999px; font-size: 11px; margin-left: 8px; font-weight: 500; }
.taskcard .desc { font-size: 13px; color: #44444a; margin-top: 4px; }
.taskcard .invariants { margin: 6px 16px 0; padding: 8px 12px; background: #fafaf5; border-radius: 6px; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; color: #44444a; }
.taskcard .invariants .inv-name { color: #1f6feb; }

.candidates { padding: 12px 16px; }
.candrow { display: grid; grid-template-columns: 200px 1fr; gap: 16px; padding: 12px 0; border-top: 1px solid #f0f0eb; }
.candrow:first-child { border-top: none; }
.candrow .meta { font-size: 12px; }
.candrow .candidate-id { font-family: ui-monospace, Menlo, monospace; font-size: 12px; font-weight: 600; }
.candrow .truth-pill { display: inline-block; padding: 2px 7px; border-radius: 999px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; margin-top: 4px; }
.truth-pill.correct { background: #dcfce7; color: #14532d; }
.truth-pill.obvious_wrong { background: #fee2e2; color: #7f1d1d; }
.truth-pill.subtle_wrong { background: #fef3c7; color: #78350f; }
.truth-pill.scope_creep { background: #ede9fe; color: #4c1d95; }
.candrow .note { font-size: 11px; color: #6c6c70; margin-top: 6px; font-style: italic; }
.candrow .verdicts { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.verdict-pill { padding: 2px 8px; border-radius: 999px; font-size: 10.5px; font-weight: 600; font-family: ui-monospace, Menlo, monospace; }
.verdict-pill.right { background: #dcfce7; color: #14532d; }
.verdict-pill.wrong { background: #fee2e2; color: #7f1d1d; }
.verdict-pill.skipped { background: #f3f4f6; color: #6b7280; }

.diff { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 11.5px; background: #fafaf5; border-radius: 6px; overflow-x: auto; padding: 8px 10px; line-height: 1.45; max-height: 240px; overflow-y: auto; }
.diff .add { background: #dcfce7; color: #14532d; display: block; }
.diff .rem { background: #fee2e2; color: #7f1d1d; display: block; }
.diff .hunk { color: #6c6c70; display: block; }
.diff .ctx { color: #44444a; display: block; }

.invariant-trace { margin-top: 8px; font-size: 11.5px; font-family: ui-monospace, Menlo, monospace; }
.invariant-trace .inv-line { padding: 2px 4px; border-radius: 3px; margin: 1px 0; }
.invariant-trace .pass { color: #14532d; }
.invariant-trace .fail { color: #7f1d1d; background: #fff5f5; }

/* TOC */
.toc { background: #fff; border: 1px solid #e5e5e0; border-radius: 8px; padding: 14px 18px; margin: 16px 0 24px; font-size: 13px; }
.toc a { color: #1f6feb; text-decoration: none; margin-right: 14px; display: inline-block; padding: 2px 0; }
.toc a:hover { text-decoration: underline; }

/* Footnotes */
.footnote { background: #fffbeb; border: 1px solid #fde68a; border-left: 4px solid #d97706; border-radius: 4px; padding: 10px 14px; margin: 12px 0; font-size: 13px; color: #78350f; }
.footnote strong { color: #78350f; }
.note { font-size: 13px; color: #44444a; }
.note code { background: #f5f5f0; padding: 1px 5px; border-radius: 3px; font-size: 12px; }

table.legend { font-size: 12px; color: #6c6c70; margin-top: 8px; }
table.legend td { padding: 1px 8px 1px 0; }
.legend .sw { display: inline-block; width: 12px; height: 12px; vertical-align: middle; margin-right: 5px; border-radius: 2px; }
"""


def _render_diff_html(before: dict[str, str], after: dict[str, str]) -> str:
    """Render a colorized unified diff as HTML."""
    all_paths = sorted(set(before) | set(after))
    parts: list[str] = []
    for p in all_paths:
        b = before.get(p, "").splitlines()
        a = after.get(p, "").splitlines()
        if b == a:
            continue
        diff_lines = list(
            difflib.unified_diff(
                b, a, fromfile=f"a/{p}", tofile=f"b/{p}", lineterm="", n=2
            )
        )
        for line in diff_lines:
            escaped = html.escape(line)
            if line.startswith("+++") or line.startswith("---"):
                parts.append(f'<span class="hunk">{escaped}</span>')
            elif line.startswith("@@"):
                parts.append(f'<span class="hunk">{escaped}</span>')
            elif line.startswith("+"):
                parts.append(f'<span class="add">{escaped}</span>')
            elif line.startswith("-"):
                parts.append(f'<span class="rem">{escaped}</span>')
            else:
                parts.append(f'<span class="ctx">{escaped}</span>')
    if not parts:
        return '<span class="ctx">(no changes)</span>'
    return "".join(parts)


def _outcome_index(report: EvaluatorReport) -> dict[tuple[str, str], PairOutcome]:
    """Index outcomes by (task_id, candidate_id) for quick lookup."""
    return {(o.spec.task_id, o.candidate.candidate_id): o for o in report.outcomes}


def _render_confusion_matrix(report: EvaluatorReport, title_color: str) -> str:
    """Render one confusion matrix as a small HTML card."""
    skip_note = ""
    if report.skipped_count:
        skip_note = (
            f'<div class="skipped-note">'
            f"{report.skipped_count} pair(s) skipped (e.g. no API key / no network)"
            f"</div>"
        )

    far_class = "danger" if report.false_accept_rate > 0.10 else "good"
    frr_class = "danger" if report.false_reject_rate > 0.10 else "good"

    return f"""
    <div class="matrix">
        <h3><span class="name">{html.escape(report.evaluator_name)}</span> &mdash; {report.total} pairs</h3>
        <table>
            <tr>
                <th></th>
                <th>Truth: ACCEPT<br><span class="sub">(correct candidate)</span></th>
                <th>Truth: REJECT<br><span class="sub">(bad candidate)</span></th>
            </tr>
            <tr>
                <td class="label">Evaluator: ACCEPT</td>
                <td class="tp"><div class="count">{report.true_positive}</div><div class="sub">true positive</div></td>
                <td class="fp"><div class="count">{report.false_positive}</div><div class="sub">false accept ⚠</div></td>
            </tr>
            <tr>
                <td class="label">Evaluator: REJECT</td>
                <td class="fn"><div class="count">{report.false_negative}</div><div class="sub">false reject</div></td>
                <td class="tn"><div class="count">{report.true_negative}</div><div class="sub">true negative</div></td>
            </tr>
        </table>
        <div class="metrics-row">
            <div class="item">
                <div class="num {far_class}">{report.false_accept_rate:.1%}</div>
                <div class="sub">false accept rate</div>
            </div>
            <div class="item">
                <div class="num {frr_class}">{report.false_reject_rate:.1%}</div>
                <div class="sub">false reject rate</div>
            </div>
            <div class="item">
                <div class="num">{report.accuracy:.1%}</div>
                <div class="sub">accuracy</div>
            </div>
        </div>
        {skip_note}
    </div>"""


def _render_rigorous_metrics_panel(run: EvalRun) -> str:
    """Render the discriminative-power / Cohen's-κ / authoring-cost metrics table.

    These metrics provide apples-to-apples comparison points against
    nl2postcond (Endres et al., FSE 2024) and PRDBench (Fu et al., AAMAS
    2026). Discriminative power is the same metric nl2postcond uses.
    Cohen's κ is the standard inter-rater agreement statistic that none of
    the prior work in spec validation reports.
    """
    from safe_scaffold.task_spec.metrics import (
        authoring_cost_per_far_reduction,
        cohen_kappa,
        discriminative_power,
    )

    baseline = run.report_named("positive_only")

    parts = ['<div class="barchart">']
    parts.append('<h3>Rigorous metrics</h3>')
    parts.append(
        '<p class="muted" style="font-size:12px;margin:0 0 12px">'
        '<strong>Discriminative power</strong> (nl2postcond, FSE 2024): fraction of (correct, bad) candidate '
        'pairs distinguished. <strong>Cohen\'s κ</strong>: inter-rater agreement with ground truth; '
        '0.81+ is "almost perfect" (Landis-Koch). <strong>sec/Δ%FAR</strong>: spec-authoring '
        'cost per percentage-point of false-accept-rate reduction over the positive-only baseline. '
        'Lower is better.</p>'
    )

    # Header row
    parts.append(
        '<table style="width:100%;border-collapse:collapse;font-size:13px">'
        '<thead><tr>'
        '<th style="text-align:left;padding:6px 10px;border-bottom:1px solid #e5e5e0">evaluator</th>'
        '<th style="text-align:right;padding:6px 10px;border-bottom:1px solid #e5e5e0">accuracy</th>'
        '<th style="text-align:right;padding:6px 10px;border-bottom:1px solid #e5e5e0">FAR</th>'
        '<th style="text-align:right;padding:6px 10px;border-bottom:1px solid #e5e5e0">disc. power</th>'
        '<th style="text-align:right;padding:6px 10px;border-bottom:1px solid #e5e5e0">Cohen\'s κ</th>'
        '<th style="text-align:right;padding:6px 10px;border-bottom:1px solid #e5e5e0">sec/Δ%FAR</th>'
        '</tr></thead><tbody>'
    )

    import math
    for r in run.reports:
        if r.total == 0:
            continue
        dp = discriminative_power(r)
        k = cohen_kappa(r)
        ratio_str = "(base)"
        if baseline is not None and r.evaluator_name != "positive_only":
            ratio = authoring_cost_per_far_reduction(
                structured=r,
                baseline=baseline,
                total_authoring_seconds=run.total_authoring_seconds,
            )
            ratio_str = f"{ratio:.1f}" if math.isfinite(ratio) else "∞"
        parts.append(
            f'<tr>'
            f'<td style="padding:6px 10px;font-family:ui-monospace,Menlo,monospace;color:#1f6feb">{html.escape(r.evaluator_name)}</td>'
            f'<td style="padding:6px 10px;text-align:right">{r.accuracy:.1%}</td>'
            f'<td style="padding:6px 10px;text-align:right">{r.false_accept_rate:.1%}</td>'
            f'<td style="padding:6px 10px;text-align:right">{dp:.1%}</td>'
            f'<td style="padding:6px 10px;text-align:right">{k:.3f}</td>'
            f'<td style="padding:6px 10px;text-align:right">{ratio_str}</td>'
            f'</tr>'
        )
    parts.append('</tbody></table>')
    parts.append('</div>')
    return "".join(parts)


def _render_ablation_panel() -> str:
    """Render the per-invariant ablation table."""
    from safe_scaffold.task_spec.ablation import run_ablation

    ablation = run_ablation(verbose=False)
    parts = ['<div class="barchart">']
    parts.append('<h3>Ablation: per-invariant contribution to FAR reduction</h3>')
    parts.append(
        '<p class="muted" style="font-size:12px;margin:0 0 12px">'
        'For each invariant type, drop it from every spec and re-run. '
        'A high Δ FAR means the invariant was uniquely catching cases the '
        'others miss. A Δ of 0 means the invariant is redundant given the '
        'others (an honest finding, not a bug to hide).</p>'
    )
    parts.append(
        '<table style="width:100%;border-collapse:collapse;font-size:13px">'
        '<thead><tr>'
        '<th style="text-align:left;padding:6px 10px;border-bottom:1px solid #e5e5e0">ablated invariant</th>'
        '<th style="text-align:right;padding:6px 10px;border-bottom:1px solid #e5e5e0">FAR with</th>'
        '<th style="text-align:right;padding:6px 10px;border-bottom:1px solid #e5e5e0">FAR w/o</th>'
        '<th style="text-align:right;padding:6px 10px;border-bottom:1px solid #e5e5e0">Δ FAR</th>'
        '<th style="text-align:right;padding:6px 10px;border-bottom:1px solid #e5e5e0">unique catches</th>'
        '</tr></thead><tbody>'
    )
    for r in ablation.sorted_by_importance():
        delta_color = "#b91c1c" if r.delta_far > 0.05 else "#6c6c70"
        parts.append(
            f'<tr>'
            f'<td style="padding:6px 10px;font-family:ui-monospace,Menlo,monospace;color:#1f6feb">{html.escape(r.invariant_type)}</td>'
            f'<td style="padding:6px 10px;text-align:right">{r.far_with:.1%}</td>'
            f'<td style="padding:6px 10px;text-align:right">{r.far_without:.1%}</td>'
            f'<td style="padding:6px 10px;text-align:right;color:{delta_color}">+{r.delta_far:.1%}</td>'
            f'<td style="padding:6px 10px;text-align:right">{r.pairs_newly_accepted}</td>'
            f'</tr>'
        )
    parts.append('</tbody></table>')
    parts.append('</div>')
    return "".join(parts)


def _render_per_label_bars(reports: list[EvaluatorReport]) -> str:
    """Side-by-side bars showing each evaluator's reject-rate per ground-truth label.

    For each label, we plot what fraction of candidates with that label the
    evaluator REJECTED. For CORRECT, low is good; for the others, high is good.
    """
    parts: list[str] = ['<div class="barchart">']
    parts.append('<h3>Reject rate by ground-truth label (per evaluator)</h3>')
    parts.append(
        '<p class="muted" style="font-size:12px;margin:0 0 12px">For '
        '<span style="color:#14532d;font-weight:600">CORRECT</span> candidates, lower is better (don\'t reject good code). '
        'For OBVIOUS_WRONG / SUBTLE_WRONG / SCOPE_CREEP, higher is better (catch bad code).</p>'
    )

    for label in CandidateLabel:
        label_name = label.name
        goal = "(should accept these)" if label is CandidateLabel.CORRECT else "(should reject these)"
        parts.append(f'<h4 style="font-size:13px;margin:14px 0 4px;color:#44444a">{label_name} <span style="font-weight:400;color:#6c6c70">{goal}</span></h4>')
        for r in reports:
            br = r.per_label_breakdown()[label]
            total = br["total"]
            rejected = br["reject"]
            pct = (rejected / total) if total else 0.0
            bar_class = r.evaluator_name
            parts.append(f"""
            <div class="bar-row">
                <div class="row-label">{html.escape(r.evaluator_name)}</div>
                <div class="bar-track"><div class="bar-fill {bar_class}" style="width:{pct*100:.1f}%"></div></div>
                <div class="val">{rejected}/{total} ({pct:.0%})</div>
            </div>""")

    parts.append('</div>')
    return "".join(parts)


def _render_per_category_bars(reports: list[EvaluatorReport]) -> str:
    """Per-task-category accuracy."""
    parts: list[str] = ['<div class="barchart">']
    parts.append('<h3>Accuracy by task category</h3>')

    # Collect all categories
    categories: list[str] = []
    for r in reports:
        for cat in r.per_category_breakdown():
            if cat not in categories:
                categories.append(cat)

    for cat in sorted(categories):
        parts.append(f'<h4 style="font-size:13px;margin:14px 0 4px;color:#44444a">{cat}</h4>')
        for r in reports:
            data = r.per_category_breakdown().get(cat, {"correct": 0, "wrong": 0})
            total = data["correct"] + data["wrong"]
            pct = (data["correct"] / total) if total else 0.0
            bar_class = r.evaluator_name
            parts.append(f"""
            <div class="bar-row">
                <div class="row-label">{html.escape(r.evaluator_name)}</div>
                <div class="bar-track"><div class="bar-fill {bar_class}" style="width:{pct*100:.1f}%"></div></div>
                <div class="val">{data['correct']}/{total} ({pct:.0%})</div>
            </div>""")
    parts.append('</div>')
    return "".join(parts)


def _render_per_task_drilldown(run: EvalRun) -> str:
    """One card per spec, with all 4 candidates shown and verdicts from each evaluator."""
    from safe_scaffold.task_spec.corpus_data import CORPUS

    # Index outcomes per evaluator for fast lookup.
    indices = {r.evaluator_name: _outcome_index(r) for r in run.reports}

    parts: list[str] = []
    parts.append('<h2 id="drilldown">Per-task drill-down</h2>')
    parts.append(
        '<p class="muted">Each card shows the spec, the 4 candidate diffs, '
        'and which evaluators correctly classified each. ✓ = matched ground '
        'truth, ✗ = wrong, ⊘ = skipped (no API key).</p>'
    )

    for spec, candidates in CORPUS:
        parts.append(f'<div class="taskcard" id="task-{html.escape(spec.task_id)}">')
        parts.append('<div class="header">')
        parts.append(
            f'<span class="task-id">{html.escape(spec.task_id)}</span>'
            f'<span class="category">{html.escape(spec.category)}</span>'
        )
        parts.append(f'<div class="desc">{html.escape(spec.description)}</div>')
        # Authoring cost annotation
        parts.append(
            f'<div class="muted" style="font-size:11px;margin-top:4px">'
            f'authored in ~{spec.authoring_seconds}s &middot; {spec.authoring_loc} lines of spec'
            f'</div>'
        )
        parts.append('</div>')

        # Invariants summary
        inv_strs = []
        for inv in spec.negative_invariants:
            cls = inv.__class__.__name__
            # Pull a one-line description; the dataclass repr is already short.
            inv_strs.append(f'<span class="inv-name">{cls}</span>')
        parts.append(
            '<div class="invariants"><strong>invariants:</strong> '
            + ', '.join(inv_strs)
            + '</div>'
        )

        parts.append('<div class="candidates">')
        for cand in candidates:
            parts.append('<div class="candrow">')

            # Left column: metadata + verdicts
            parts.append('<div class="meta">')
            parts.append(f'<div class="candidate-id">{html.escape(cand.candidate_id)}</div>')
            label_class = cand.label.value
            parts.append(f'<span class="truth-pill {label_class}">{cand.label.value.replace("_", " ")}</span>')
            if cand.note:
                parts.append(f'<div class="note">{html.escape(cand.note)}</div>')

            # Verdicts row
            parts.append('<div class="verdicts">')
            for ev_name, idx in indices.items():
                outcome = idx.get((spec.task_id, cand.candidate_id))
                if outcome is None:
                    continue
                if outcome.skipped:
                    parts.append(
                        f'<span class="verdict-pill skipped" title="skipped">{html.escape(ev_name)}: ⊘</span>'
                    )
                else:
                    expected = cand.label.should_accept
                    actual = outcome.verdict.accepted
                    right = (expected == actual)
                    cls = "right" if right else "wrong"
                    mark = "✓" if right else "✗"
                    decision = outcome.verdict.decision.value
                    parts.append(
                        f'<span class="verdict-pill {cls}" '
                        f'title="{html.escape(outcome.verdict.reason)}">'
                        f'{html.escape(ev_name)}: {decision} {mark}</span>'
                    )
            parts.append('</div>')

            # Invariant trace from the structured validator (most informative)
            structured_outcome = indices.get("structured", {}).get((spec.task_id, cand.candidate_id))
            if structured_outcome is not None:
                parts.append('<div class="invariant-trace">')
                for ir in structured_outcome.verdict.invariant_results:
                    cls = "pass" if ir.holds else "fail"
                    mark = "✓" if ir.holds else "✗"
                    name = html.escape(ir.invariant_name)
                    details = html.escape(ir.details)[:90]
                    parts.append(
                        f'<div class="inv-line {cls}">{mark} {name}: <span style="color:#6c6c70">{details}</span></div>'
                    )
                parts.append('</div>')

            parts.append('</div>')  # /meta

            # Right column: diff
            parts.append('<div>')
            parts.append('<div class="diff">')
            parts.append(_render_diff_html(spec.starting_repo, cand.modified_repo))
            parts.append('</div>')
            parts.append('</div>')

            parts.append('</div>')  # /candrow

        parts.append('</div>')  # /candidates
        parts.append('</div>')  # /taskcard

    return "".join(parts)


def render_dashboard(run: EvalRun, path: str | Path = "dashboard.html") -> Path:
    """Render the full dashboard to an HTML file. Returns the path written."""
    structured = run.report_named("structured")
    positive_only = run.report_named("positive_only")
    llm_judge = run.report_named("llm_judge")

    # Top-of-page summary cards
    headline_cards = []
    if structured:
        headline_cards.append(("structured FAR",
                               f"{structured.false_accept_rate:.1%}",
                               "false accept rate"))
    if positive_only:
        headline_cards.append(("positive-only FAR",
                               f"{positive_only.false_accept_rate:.1%}",
                               "(≈ what CI catches today)"))
    if llm_judge and llm_judge.total > 0:
        headline_cards.append(("LLM-judge FAR",
                               f"{llm_judge.false_accept_rate:.1%}",
                               "claude-as-judge"))
    headline_cards.append(("authoring cost",
                           f"{run.median_authoring_seconds}s",
                           "median per spec"))

    cards_html = ""
    for label, value, unit in headline_cards:
        cards_html += f"""
        <div class="card">
            <div class="label">{html.escape(label)}</div>
            <div class="value">{html.escape(value)}<span class="unit">{html.escape(unit)}</span></div>
        </div>"""

    # Confusion matrices row
    matrices_html = ""
    reports_present = []
    if structured:
        matrices_html += _render_confusion_matrix(structured, "#047857")
        reports_present.append(structured)
    if positive_only:
        matrices_html += _render_confusion_matrix(positive_only, "#d97706")
        reports_present.append(positive_only)
    if llm_judge:
        matrices_html += _render_confusion_matrix(llm_judge, "#6366f1")
        reports_present.append(llm_judge)

    # Bar charts
    per_label_html = _render_per_label_bars(reports_present)
    per_category_html = _render_per_category_bars(reports_present)
    rigorous_html = _render_rigorous_metrics_panel(run)
    try:
        ablation_html = _render_ablation_panel()
    except Exception as exc:
        # The ablation re-runs the eval; if anything goes wrong, fail closed
        # rather than break the dashboard render.
        ablation_html = f'<div class="footnote">Ablation panel skipped: {html.escape(str(exc))}</div>'
    drilldown_html = _render_per_task_drilldown(run)

    skipped_warning = ""
    if llm_judge and llm_judge.skipped_count > 0:
        skipped_warning = (
            '<div class="footnote">'
            f'<strong>LLM-judge skipped {llm_judge.skipped_count}/{run.corpus_size} pairs</strong> '
            '— typically because no <code>ANTHROPIC_API_KEY</code> was in the environment. '
            'Re-run with the key set to fill in the third column.'
            '</div>'
        )

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Task-spec validator evaluation</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<div class="container">
    <div class="kicker">safe_scaffold &middot; track 1 &middot; task-spec elicitation</div>
    <h1>Task-spec validator evaluation</h1>
    <p class="muted">
        {run.corpus_size} (task, candidate) pairs across {len(reports_present[0].outcomes) // 4 if reports_present else 0} hand-authored tasks.
        Three evaluators compared: <strong>structured</strong> (our positive-tests + negative-invariants combo),
        <strong>positive_only</strong> (≈ what CI catches today), <strong>llm_judge</strong> (Claude as judge baseline).
    </p>

    <div class="toc">
        <a href="#headline">Headline metrics</a>
        <a href="#matrices">Confusion matrices</a>
        <a href="#rigorous">Rigorous metrics</a>
        <a href="#ablation">Ablation</a>
        <a href="#by-label">By candidate label</a>
        <a href="#by-category">By task category</a>
        <a href="#drilldown">Per-task drill-down</a>
        <a href="#caveats">Caveats</a>
    </div>

    {skipped_warning}

    <h2 id="headline">Headline metrics</h2>
    <div class="cards">{cards_html}</div>

    <h2 id="matrices">Confusion matrices</h2>
    <p class="muted">"False accept" is the security-critical number: the evaluator approved code it shouldn't have.</p>
    <div class="matrices">{matrices_html}</div>

    <h2 id="rigorous">Rigorous metrics &amp; prior-work comparison points</h2>
    {rigorous_html}

    <h2 id="ablation">Ablation: which invariant carries the load?</h2>
    {ablation_html}

    <h2 id="by-label">Reject rates by candidate label</h2>
    {per_label_html}

    <h2 id="by-category">Accuracy by task category</h2>
    {per_category_html}

    {drilldown_html}

    <h2 id="caveats">Caveats</h2>
    <div class="note">
        <p>This is a calibration study, not a benchmark. Specific caveats reviewers should weight:</p>
        <ul>
            <li><strong>Hand-authored candidates.</strong> The 40 (task, candidate) pairs were written by hand to exercise each candidate label cleanly. They are not LLM outputs. A follow-up study would replace these with real agent outputs to verify the false-accept rate holds on naturally-occurring failure modes.</li>
            <li><strong>Small corpus.</strong> 10 tasks is enough for the confusion matrix to be meaningfully populated and to compare evaluator approaches; it is not enough to make per-category significance claims.</li>
            <li><strong>Self-reported authoring cost.</strong> The {run.median_authoring_seconds}-second median is the corpus author's own time, not a user study. It is a lower bound on what a developer familiar with the codebase could achieve.</li>
            <li><strong>Known structural limitation.</strong> Task <code>t09_config_loader</code>'s subtle_wrong candidate (eval-based JSON loader with a guard that bypasses the test cases) is not caught by any structural invariant. This is the one false-accept in the structured row of the matrix above. Catching it would require behavioral analysis or symbolic execution; both are out of scope for a structural-invariant validator and represent a natural next research direction.</li>
            <li><strong>LLM-judge is non-deterministic.</strong> Re-running with the same API key will produce slightly different verdicts. The numbers above are one sample, not an expectation.</li>
        </ul>
    </div>

</div>
</body>
</html>
"""

    target = Path(path)
    target.write_text(full_html, encoding="utf-8")
    return target


def main() -> None:
    """CLI entry point: run eval and write dashboard.html."""
    import sys

    print("Running eval across structured / positive_only / llm_judge...")
    evaluators = [StructuredValidator(), PositiveTestsOnly(), LLMJudge()]
    run = run_eval(evaluators=evaluators, verbose=False)

    out = "dashboard.html"
    if len(sys.argv) > 1:
        out = sys.argv[1]
    path = render_dashboard(run, path=out)
    print(f"Wrote dashboard to {path}")

    # Also print summary to stdout
    from safe_scaffold.task_spec.eval import print_summary
    print_summary(run)


if __name__ == "__main__":
    main()
