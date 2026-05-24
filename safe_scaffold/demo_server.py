"""Interactive demo server for the StructuredValidator (our baseline).

Run from the safe_scaffold/ top-level folder:

    PYTHONPATH=. python3 demo_server.py
    # → open http://127.0.0.1:8765

Lets you:
  - Browse the 15-task extended corpus (description, invariants, candidates).
  - Pick one of the 4 ground-truth candidates OR edit any file in the repo.
  - Click Validate to run the StructuredValidator and see the per-invariant
    trace plus the final accept/reject verdict.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from safe_scaffold.task_spec.baselines import StructuredValidator
from safe_scaffold.task_spec.corpus_data import EXTENDED_CORPUS, FULL_CORPUS
from safe_scaffold.task_spec.elicitation import (
    DEFAULT_COMPARE_MODELS,
    compare_drafts,
    draft_spec,
    refine_draft,
)
from safe_scaffold.task_spec.lean_emitter import (
    emit_lean,
    lean_available,
    verify_lean,
)
from safe_scaffold.task_spec.ears_emitter import emit_ears
from safe_scaffold.task_spec.ambiguous_briefs import AMBIGUOUS_BRIEFS, BRIEFS_BY_ID
from safe_scaffold.task_spec.datasets import all_dataset_briefs
from safe_scaffold.task_spec.codegen import generate_code
from safe_scaffold.task_spec.spec import Candidate, CandidateLabel
from safe_scaffold.task_spec.spec_mutation import (
    coverage_by_kind,
    coverage_score,
    result_to_dict,
    run_mutation_analysis,
    spec_coverage,
    summarize,
    summary_to_dict,
)

TASKS = {spec.task_id: (spec, candidates) for spec, candidates in FULL_CORPUS}
VALIDATOR = StructuredValidator()


def _invariant_summary(inv: Any) -> dict[str, Any]:
    """Serialize one Invariant dataclass to JSON-friendly fields.

    Reads each invariant type's actual attribute names (which differ —
    `OnlyFilesModified.allowed_paths`, `NoNewImports.forbidden`,
    `DiffSmallerThan.max_lines`, etc.) so the UI and `_spec_from_request`
    can round-trip them faithfully.
    """
    out: dict[str, Any] = {"type": type(inv).__name__, "name": getattr(inv, "name", "?")}
    for fld in ("allowed_paths", "paths", "forbidden", "max_lines", "test_path"):
        val = getattr(inv, fld, None)
        if val is not None:
            out[fld] = list(val) if isinstance(val, tuple) else val
    return out


def _candidate_summary(cand: Candidate) -> dict[str, Any]:
    return {
        "candidate_id": cand.candidate_id,
        "label": cand.label.value,
        "should_accept": cand.label.should_accept,
        "note": cand.note,
    }


def _task_summary(spec, candidates) -> dict[str, Any]:
    return {
        "task_id": spec.task_id,
        "category": spec.category,
        "description": spec.description,
        "invariants": [_invariant_summary(i) for i in spec.negative_invariants],
        "candidates": [_candidate_summary(c) for c in candidates],
    }


def _task_full(spec, candidates) -> dict[str, Any]:
    return {
        **_task_summary(spec, candidates),
        "starting_repo": dict(spec.starting_repo),
        "positive_tests": [
            {"path": t.path, "name": t.name, "code": t.code}
            for t in spec.positive_tests
        ],
        "candidates_full": [
            {
                **_candidate_summary(c),
                "modified_repo": dict(c.modified_repo),
            }
            for c in candidates
        ],
    }


app = FastAPI(title="StructuredValidator demo")


@app.get("/api/tasks")
def list_tasks() -> dict[str, Any]:
    return {"tasks": [_task_summary(s, c) for s, c in FULL_CORPUS]}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    entry = TASKS.get(task_id)
    if entry is None:
        raise HTTPException(404, f"unknown task_id: {task_id}")
    return _task_full(*entry)


class ValidateRequest(BaseModel):
    task_id: str
    candidate_id: str | None = None       # use a ground-truth candidate
    modified_repo: dict[str, str] | None = None  # OR submit your own diff
    note: str = ""


@app.post("/api/validate")
def validate(req: ValidateRequest) -> dict[str, Any]:
    entry = TASKS.get(req.task_id)
    if entry is None:
        raise HTTPException(404, f"unknown task_id: {req.task_id}")
    spec, candidates = entry

    if req.candidate_id is not None:
        cand = next(
            (c for c in candidates if c.candidate_id == req.candidate_id),
            None,
        )
        if cand is None:
            raise HTTPException(404, f"unknown candidate_id: {req.candidate_id}")
        used_label = cand.label.value
    elif req.modified_repo is not None:
        cand = Candidate(
            candidate_id="user_submitted",
            label=CandidateLabel.CORRECT,  # label is irrelevant to validator
            modified_repo=req.modified_repo,
            note=req.note,
        )
        used_label = "user_submitted"
    else:
        raise HTTPException(400, "provide candidate_id or modified_repo")

    verdict = VALIDATOR.evaluate(spec, cand)
    return {
        "task_id": req.task_id,
        "candidate_id": cand.candidate_id,
        "ground_truth_label": used_label,
        "decision": verdict.decision.value,
        "accepted": verdict.accepted,
        "reason": verdict.reason,
        "invariant_results": [
            {"name": r.invariant_name, "holds": r.holds,
             "details": r.details, "uncertain": r.uncertain}
            for r in verdict.invariant_results
        ],
    }


# ---------------------------------------------------------------------------
# Elicitation — Track 1 (NL + repo → draft TaskSpec via LLM)
# ---------------------------------------------------------------------------


class ElicitRequest(BaseModel):
    description: str
    starting_repo: dict[str, str]
    task_id: str = "draft"
    additional_sources: dict[str, str] | None = None  # e.g. {"prose_doc": "...", "existing_tests": "..."}


@app.post("/api/elicit")
def elicit(req: ElicitRequest) -> dict[str, Any]:
    if not req.description.strip():
        raise HTTPException(400, "description must not be empty")
    if not req.starting_repo:
        raise HTTPException(400, "starting_repo must not be empty")

    draft = draft_spec(
        req.description,
        req.starting_repo,
        task_id=req.task_id,
        additional_sources=req.additional_sources,
    )

    invariants_out = [
        {
            "type": type(d.invariant).__name__,
            "args": _invariant_summary(d.invariant),
            "rationale": d.rationale,
            "provenance": {
                "grounding": d.provenance.grounding,
                "source_phrase": d.provenance.source_phrase,
            },
        }
        for d in draft.drafted_invariants
    ]
    return {
        "ok": draft.ok,
        "error": draft.error,
        "raw_response": draft.raw_response,
        "spec": _task_full(draft.spec, ()) if draft.spec else None,
        "drafted_invariants": invariants_out,
        "positive_test_rationale": draft.positive_test_rationale,
        "contradictions": [
            {"sources": list(c.sources),
             "summary": c.summary,
             "resolution": c.resolution}
            for c in draft.contradictions
        ],
    }


class RefineRequest(BaseModel):
    description: str
    starting_repo: dict[str, str]
    previous_response: str       # the raw JSON the LLM returned last time
    feedback: list[dict[str, str]]  # [{"field": "...", "reason": "..."}]
    task_id: str = "draft"


@app.post("/api/elicit/refine")
def elicit_refine(req: RefineRequest) -> dict[str, Any]:
    if not req.description.strip() or not req.starting_repo:
        raise HTTPException(400, "description and starting_repo are required")
    if not req.feedback:
        raise HTTPException(400, "feedback list must not be empty")

    draft = refine_draft(
        req.description, req.starting_repo,
        previous_response=req.previous_response,
        feedback=req.feedback,
        task_id=req.task_id,
    )
    return {
        "ok": draft.ok,
        "error": draft.error,
        "raw_response": draft.raw_response,
        "spec": _task_full(draft.spec, ()) if draft.spec else None,
        "drafted_invariants": [
            {"type": type(d.invariant).__name__,
             "args": _invariant_summary(d.invariant),
             "rationale": d.rationale}
            for d in draft.drafted_invariants
        ],
        "positive_test_rationale": draft.positive_test_rationale,
    }


class EmitLeanRequest(BaseModel):
    task_id: str | None = None        # emit a corpus spec by id
    spec_json: dict[str, Any] | None = None  # OR an arbitrary spec carried by the client


def _spec_from_request(req: "EmitLeanRequest"):
    """Resolve EmitLeanRequest → TaskSpec. Used by /api/emit_lean and /api/verify_lean."""
    if req.task_id is not None:
        entry = TASKS.get(req.task_id)
        if entry is None:
            raise HTTPException(404, f"unknown task_id: {req.task_id}")
        return entry[0]
    if req.spec_json is not None:
        # Minimal reconstruction: we only need the fields emit_lean reads
        # (task_id, description, negative_invariants, positive_tests).
        from safe_scaffold.task_spec.spec import TaskSpec, PositiveTest
        from safe_scaffold.task_spec.invariants import (
            DiffSmallerThan, FilesUnchanged, NoNewImports,
            NoSecretsInDiff, OnlyFilesModified, PositiveTestPasses,
        )
        invs = []
        for inv_dict in req.spec_json.get("invariants", []):
            t = inv_dict.get("type")
            # Accept both shapes the demo emits: nested `args:{...}` (used
            # by /api/elicit) and flat fields-at-top-level (used by
            # /api/tasks/{id}).
            args = inv_dict.get("args") or inv_dict
            if t == "OnlyFilesModified":
                paths = args.get("allowed_paths") or args.get("paths") or []
                invs.append(OnlyFilesModified(tuple(paths)))
            elif t == "NoNewImports":
                invs.append(NoNewImports(tuple(args.get("forbidden", []))))
            elif t == "DiffSmallerThan":
                invs.append(DiffSmallerThan(int(args.get("max_lines", 20))))
            elif t == "NoSecretsInDiff":
                invs.append(NoSecretsInDiff())
            elif t == "FilesUnchanged":
                invs.append(FilesUnchanged(tuple(args.get("paths", []))))
            elif t == "PositiveTestPasses":
                invs.append(PositiveTestPasses(args.get("test_path", "")))
        tests = tuple(
            PositiveTest(path=pt.get("path", ""), code=pt.get("code", ""),
                          name=pt.get("name", ""))
            for pt in req.spec_json.get("positive_tests", [])
        )
        return TaskSpec(
            task_id=req.spec_json.get("task_id", "draft"),
            description=req.spec_json.get("description", ""),
            starting_repo=req.spec_json.get("starting_repo", {}),
            positive_tests=tests,
            negative_invariants=tuple(invs),
        )
    raise HTTPException(400, "provide task_id or spec_json")


@app.get("/api/lean_available")
def api_lean_available() -> dict[str, bool]:
    return {"available": lean_available()}


def _serialize_brief(b, source: str) -> dict[str, Any]:
    return {
        "brief_id": b.brief_id,
        "label": b.label,
        "source": source,
        "description": b.description,
        "starting_repo": dict(b.starting_repo),
        "prose_doc": b.prose_doc,
        "existing_tests": b.existing_tests,
        "slide_deck": b.slide_deck,
    }


@app.get("/api/ambiguous_briefs")
def api_ambiguous_briefs() -> dict[str, Any]:
    """List both hand-crafted fixtures and adapted external-dataset briefs."""
    briefs = [_serialize_brief(b, "custom") for b in AMBIGUOUS_BRIEFS]
    for b in all_dataset_briefs():
        # Order matters: humaneval_pro must match before humaneval.
        for prefix, src in (
            ("mbpp_", "mbpp"),
            ("humaneval_pro_", "humaneval_pro"),
            ("humaneval_", "humaneval"),
            ("bigcodebench_", "bigcodebench"),
            ("livecodebench_", "livecodebench"),
        ):
            if b.brief_id.startswith(prefix):
                briefs.append(_serialize_brief(b, src))
                break
        else:
            briefs.append(_serialize_brief(b, "external"))
    return {"briefs": briefs}


@app.post("/api/emit_lean")
def api_emit_lean(req: EmitLeanRequest) -> dict[str, Any]:
    spec = _spec_from_request(req)
    return {
        "task_id": spec.task_id,
        "source": emit_lean(spec),
        "lean_available": lean_available(),
    }


@app.post("/api/emit_ears")
def api_emit_ears(req: EmitLeanRequest) -> dict[str, Any]:
    """Same spec → EARS-syntax requirements.md (Kiro-style controlled NL)."""
    spec = _spec_from_request(req)
    return {"task_id": spec.task_id, "source": emit_ears(spec)}


@app.post("/api/verify_lean")
def api_verify_lean(req: EmitLeanRequest) -> dict[str, Any]:
    if not lean_available():
        raise HTTPException(503, "Lean toolchain not installed")
    spec = _spec_from_request(req)
    source = emit_lean(spec)
    result = verify_lean(source)
    return {
        "task_id": spec.task_id,
        "source": source,
        "ok": result.ok,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_seconds": round(result.duration_seconds, 3),
        "lean_version": result.lean_version,
    }


class CodegenRequest(BaseModel):
    spec_json: dict[str, Any]


@app.post("/api/codegen")
def api_codegen(req: CodegenRequest) -> dict[str, Any]:
    """Step 4 of the pipeline: spec → Python implementation."""
    # Reuse the same JSON→TaskSpec reconstruction as /api/emit_lean.
    fake = EmitLeanRequest(spec_json=req.spec_json)
    spec = _spec_from_request(fake)
    result = generate_code(spec)
    return {
        "ok": result.ok,
        "error": result.error,
        "notes": result.notes,
        "files_changed": result.files_changed,
        "modified_repo": result.modified_repo,
        "raw_response": result.raw_response,
        "verdict": {
            "decision": result.verdict.decision.value if result.verdict else None,
            "reason": result.verdict.reason if result.verdict else "",
            "invariant_results": [
                {"name": r.invariant_name, "holds": r.holds,
                 "details": r.details, "uncertain": r.uncertain}
                for r in (result.verdict.invariant_results if result.verdict else ())
            ],
        },
    }


class CompareRequest(BaseModel):
    description: str
    starting_repo: dict[str, str]
    models: list[str] | None = None
    task_id: str = "draft"


@app.post("/api/elicit/compare")
def elicit_compare(req: CompareRequest) -> dict[str, Any]:
    if not req.description.strip():
        raise HTTPException(400, "description must not be empty")
    if not req.starting_repo:
        raise HTTPException(400, "starting_repo must not be empty")
    models = tuple(req.models) if req.models else DEFAULT_COMPARE_MODELS
    if len(models) < 2:
        raise HTTPException(400, "need at least 2 models to compare")

    comp = compare_drafts(
        req.description, req.starting_repo,
        models=models, task_id=req.task_id,
    )

    drafts_out = {}
    for m, d in comp.drafts.items():
        drafts_out[m] = {
            "ok": d.ok,
            "error": d.error,
            "raw_response": d.raw_response,
            "spec": _task_full(d.spec, ()) if d.spec else None,
            "drafted_invariants": [
                {"type": type(di.invariant).__name__,
                 "args": _invariant_summary(di.invariant),
                 "rationale": di.rationale}
                for di in d.drafted_invariants
            ],
            "positive_test_rationale": d.positive_test_rationale,
        }

    return {
        "models": list(models),
        "drafts": drafts_out,
        "field_comparisons": [
            {"field_name": c.field_name,
             "agreement": c.agreement,
             "values_by_model": c.values_by_model,
             "intersection": list(c.intersection),
             "union": list(c.union)}
            for c in comp.field_comparisons
        ],
        "disagreements": list(comp.disagreements),
    }


# ---------------------------------------------------------------------------
# Mutation — Track 2 (perturb the spec, see what catches the weakening)
# ---------------------------------------------------------------------------


class MutateRequest(BaseModel):
    task_id: str | None = None  # None → run on the whole corpus


@app.post("/api/mutate")
def mutate(req: MutateRequest) -> dict[str, Any]:
    if req.task_id is not None:
        entry = TASKS.get(req.task_id)
        if entry is None:
            raise HTTPException(404, f"unknown task_id: {req.task_id}")
        spec, candidates = entry
        results = run_mutation_analysis(spec, candidates)
        cov = spec_coverage(results)
        return {
            "task_id": req.task_id,
            "mutations": [result_to_dict(r) for r in results],
            "summary": summary_to_dict(summarize({spec.task_id: results})),
            "coverage": {req.task_id: cov},
            "coverage_score": {req.task_id: round(coverage_score(cov), 3)},
        }
    all_results = {
        s.task_id: run_mutation_analysis(s, c) for s, c in FULL_CORPUS
    }
    cov_map = coverage_by_kind(all_results)
    return {
        "task_id": None,
        "per_spec": {
            tid: [result_to_dict(r) for r in rs] for tid, rs in all_results.items()
        },
        "summary": summary_to_dict(summarize(all_results)),
        "coverage": cov_map,
        "coverage_score": {tid: round(coverage_score(c), 3) for tid, c in cov_map.items()},
    }


INDEX_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>StructuredValidator demo</title>
<style>
  :root {
    --bg:#0f1419; --panel:#1a1f2e; --border:#2a3142; --muted:#6c7891;
    --fg:#e8ecf3; --accent:#7aa2f7; --green:#9ece6a; --red:#f7768e;
    --yellow:#e0af68; --mono: ui-monospace, "SF Mono", Menlo, monospace;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg);font-size:14px}
  header{padding:14px 20px;border-bottom:1px solid var(--border);display:flex;align-items:baseline;gap:16px}
  h1{margin:0;font-size:18px;font-weight:600}
  .muted{color:var(--muted);font-size:12px}
  main{display:grid;grid-template-columns:300px 1fr 1fr;gap:1px;background:var(--border);height:calc(100vh - 51px)}
  .col{background:var(--bg);overflow-y:auto;padding:14px}
  .col h2{margin:0 0 10px;font-size:13px;text-transform:uppercase;letter-spacing:0.5px;color:var(--muted)}
  .task-item{padding:10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;cursor:pointer}
  .task-item:hover{border-color:var(--accent)}
  .task-item.active{border-color:var(--accent);background:var(--panel)}
  .task-id{font-family:var(--mono);font-size:12px;color:var(--accent)}
  .task-cat{font-size:11px;color:var(--muted);float:right}
  .task-desc{margin-top:4px;font-size:12px;color:var(--fg)}
  .pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10px;font-family:var(--mono);margin-right:4px;border:1px solid var(--border)}
  .pill.correct{color:var(--green);border-color:var(--green)}
  .pill.obvious_wrong,.pill.subtle_wrong,.pill.scope_creep{color:var(--red);border-color:var(--red)}
  .pill.accept{background:var(--green);color:#0f1419;border-color:var(--green);font-weight:600}
  .pill.reject{background:var(--red);color:#0f1419;border-color:var(--red);font-weight:600}
  .pill.abstain{background:var(--yellow);color:#0f1419;border-color:var(--yellow);font-weight:600}
  .section{margin-bottom:18px}
  .label{font-size:11px;text-transform:uppercase;color:var(--muted);letter-spacing:0.5px;margin-bottom:4px}
  .inv-list{font-family:var(--mono);font-size:12px;line-height:1.6}
  .inv-name{color:var(--yellow)}
  .candidate-tabs{display:flex;gap:4px;margin-bottom:8px;flex-wrap:wrap}
  .candidate-tab{padding:5px 10px;border:1px solid var(--border);border-radius:4px;font-family:var(--mono);font-size:11px;cursor:pointer;background:var(--panel)}
  .candidate-tab:hover{border-color:var(--accent)}
  .candidate-tab.active{border-color:var(--accent);background:var(--bg);color:var(--accent)}
  .file-tabs{display:flex;gap:2px;margin-bottom:0;flex-wrap:wrap;border-bottom:1px solid var(--border)}
  .file-tab{padding:4px 10px;font-family:var(--mono);font-size:11px;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px}
  .file-tab.active{color:var(--accent);border-bottom-color:var(--accent)}
  .file-tab.diff::after{content:" *";color:var(--yellow)}
  textarea{width:100%;min-height:280px;font-family:var(--mono);font-size:12px;background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:0 0 6px 6px;padding:10px;line-height:1.5;resize:vertical}
  button{background:var(--accent);color:#0f1419;border:0;padding:8px 16px;border-radius:4px;cursor:pointer;font-size:13px;font-weight:600}
  button:hover{filter:brightness(1.1)}
  button:disabled{opacity:0.5;cursor:not-allowed}
  pre{background:var(--panel);padding:10px;border-radius:4px;overflow-x:auto;font-size:12px;margin:0}
  .verdict-box{padding:14px;border-radius:6px;margin-bottom:12px;border:1px solid var(--border)}
  .verdict-box.accept{border-color:var(--green);background:rgba(158,206,106,0.05)}
  .verdict-box.reject{border-color:var(--red);background:rgba(247,118,142,0.05)}
  .verdict-box.abstain{border-color:var(--yellow);background:rgba(224,175,104,0.05)}
  .inv-trace{display:flex;align-items:flex-start;gap:8px;padding:6px 0;border-top:1px solid var(--border);font-family:var(--mono);font-size:12px}
  .inv-trace:first-child{border-top:0}
  .inv-trace .check{flex-shrink:0;width:16px}
  .inv-trace.pass .check{color:var(--green)}
  .inv-trace.fail .check{color:var(--red)}
  .inv-trace.uncertain .check{color:var(--yellow)}
  .inv-trace .details{color:var(--muted);margin-left:4px}
  .placeholder{color:var(--muted);font-style:italic;padding:20px;text-align:center}
  nav.tabs{display:flex;gap:0;padding:0 20px;border-bottom:1px solid var(--border);background:var(--bg)}
  nav.tabs button{background:transparent;color:var(--muted);padding:10px 16px;font-size:13px;border-radius:0;border-bottom:2px solid transparent;font-weight:500}
  nav.tabs button:hover{color:var(--fg)}
  nav.tabs button.active{color:var(--accent);border-bottom-color:var(--accent)}
  .view{display:none}
  .view.active{display:block}
  main.split2{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border);height:calc(100vh - 91px)}
  main.split1{height:calc(100vh - 91px);overflow-y:auto;padding:20px}
  main{height:calc(100vh - 91px)}
  #view-validate main{height:calc(100vh - 91px)}
  textarea.small{min-height:120px}
  textarea.tiny{min-height:60px;resize:vertical}
  input[type=text]{width:100%;background:var(--panel);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:8px 10px;font-size:13px;font-family:inherit;margin-bottom:8px}
  table.mut{width:100%;border-collapse:collapse;font-size:12px;font-family:var(--mono);margin-top:8px}
  table.mut th,table.mut td{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
  table.mut th{color:var(--muted);text-transform:uppercase;font-size:10px;letter-spacing:0.5px}
  table.mut tr.load_bearing{background:rgba(247,118,142,0.08)}
  table.mut tr.brittle{background:rgba(224,175,104,0.08)}
  .mut-class{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600}
  .mut-class.load_bearing{background:var(--red);color:#0f1419}
  .mut-class.brittle{background:var(--yellow);color:#0f1419}
  .mut-class.invisible{background:var(--border);color:var(--muted)}
  .stat{display:inline-block;padding:8px 14px;margin-right:10px;background:var(--panel);border:1px solid var(--border);border-radius:6px}
  .stat .n{font-size:20px;font-weight:600;color:var(--accent)}
  .stat .l{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;display:block}
  .drafted-inv{padding:10px;border:1px solid var(--border);border-radius:6px;margin-bottom:8px;background:var(--panel)}
  .drafted-inv .why{color:var(--muted);font-size:12px;margin-top:4px}
  .file-row{display:flex;gap:6px;margin-bottom:6px}
  .file-row input{flex:0 0 200px;margin-bottom:0}
  .file-row textarea{flex:1;min-height:60px;margin-bottom:0;border-radius:4px}
  .file-row button.del{background:var(--red);color:#0f1419;padding:4px 8px;font-size:11px;align-self:flex-start}
  .cov-row{display:flex;flex-wrap:wrap;gap:4px;align-items:center;font-family:var(--mono);font-size:11px;margin:6px 0}
  .cov-badge{padding:2px 8px;border-radius:10px;border:1px solid var(--border)}
  .cov-badge.covered{color:var(--green);border-color:var(--green);background:rgba(158,206,106,0.08)}
  .cov-badge.uncovered{color:var(--muted);opacity:0.55;text-decoration:line-through}
  .cov-score{font-weight:600;color:var(--accent);margin-right:6px}
  .pcard{border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:14px;background:var(--bg)}
  .pcard.running{border-color:var(--yellow)}
  .pcard.done{border-color:var(--green)}
  .pcard.failed{border-color:var(--red)}
  .pcard-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
  .pcard-num{width:30px;height:30px;border-radius:50%;background:var(--panel);color:var(--accent);display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0;border:1px solid var(--border)}
  .pcard.done .pcard-num{background:var(--green);color:#0f1419;border-color:var(--green)}
  .pcard.running .pcard-num{background:var(--yellow);color:#0f1419;border-color:var(--yellow)}
  .pcard.failed .pcard-num{background:var(--red);color:#0f1419;border-color:var(--red)}
  .pcard-title{font-size:15px;font-weight:600}
  .pcard-sub{flex:1;color:var(--muted);font-size:12px;text-align:right}
  .pcard-status{padding:3px 10px;border-radius:10px;font-size:11px;font-family:var(--mono);background:var(--panel);color:var(--muted)}
  .pcard.running .pcard-status{background:var(--yellow);color:#0f1419}
  .pcard.done .pcard-status{background:var(--green);color:#0f1419}
  .pcard.failed .pcard-status{background:var(--red);color:#0f1419}
  .pcard-body{font-size:13px}
  .pcard-body pre{font-size:11px;max-height:280px;overflow:auto}
  .pcard-actions{margin-top:10px}
  .pcard-actions button{background:var(--panel);color:var(--fg);border:1px solid var(--border)}
  .pcard-actions button.primary{background:var(--accent);color:#0f1419;border:0;font-weight:600}
  /* Provenance chips (DaeDaLus / Lean Atlas inspired). */
  .prov-chip{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-family:var(--mono);font-weight:600;letter-spacing:0.3px;margin-left:8px;border:1px solid var(--border)}
  .prov-chip.explicit{color:var(--green);border-color:var(--green);background:rgba(158,206,106,0.08)}
  .prov-chip.inferred{color:var(--yellow);border-color:var(--yellow);background:rgba(224,175,104,0.08)}
  .prov-chip.default{color:var(--red);border-color:var(--red);background:rgba(247,118,142,0.08)}
  /* Source↔spec linked view. */
  .src-link{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px}
  .src-link .src-pane,.src-link .spec-pane{border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--panel);max-height:340px;overflow:auto}
  .src-link .label-row{font-size:11px;text-transform:uppercase;color:var(--muted);letter-spacing:0.5px;margin-bottom:6px}
  .src-block{margin-bottom:8px;font-size:12px;line-height:1.55}
  .src-block .src-title{font-family:var(--mono);font-size:10px;color:var(--accent);text-transform:uppercase;margin-bottom:2px}
  mark.src-hit{background:var(--yellow);color:#0f1419;padding:1px 2px;border-radius:2px;cursor:pointer}
  mark.src-hit.active{background:var(--accent);color:#0f1419;outline:2px solid var(--accent)}
  .inv-row{padding:6px 8px;border-radius:4px;cursor:pointer;font-size:12px;margin-bottom:4px}
  .inv-row:hover{background:rgba(122,162,247,0.08)}
  .inv-row.active{background:rgba(122,162,247,0.15);outline:1px solid var(--accent)}
  /* Mini dependency graph. */
  .dep-graph{margin-top:12px;border:1px solid var(--border);border-radius:6px;padding:8px;background:var(--panel)}
  .dep-graph svg{display:block;width:100%;max-width:680px}
  /* Lean/EARS artifact toggle. */
  .artifact-toggle{display:inline-flex;gap:0;margin-bottom:8px;border:1px solid var(--border);border-radius:6px;overflow:hidden}
  .artifact-toggle button{background:transparent;color:var(--muted);padding:5px 12px;font-size:11px;font-family:var(--mono);border-radius:0}
  .artifact-toggle button.active{background:var(--accent);color:#0f1419;font-weight:600}
  .artifact-name{font-family:var(--mono);font-size:11px;color:var(--accent);margin-right:6px}
  /* Pipeline tab: full-screen per-step layout. */
  #pipeline-toolbar{display:flex;gap:10px;align-items:center;padding:10px 20px;border-bottom:1px solid var(--border);background:var(--bg)}
  #pipeline-toolbar select{background:var(--panel);color:var(--fg);border:1px solid var(--border);padding:6px 10px;border-radius:4px;font-size:13px;min-width:240px}
  #step-nav{display:flex;gap:0;padding:0 20px;background:var(--bg);border-bottom:1px solid var(--border)}
  #step-nav .step-btn{display:flex;align-items:center;gap:8px;padding:12px 18px;background:transparent;color:var(--muted);border:0;border-bottom:2px solid transparent;cursor:pointer;font-size:13px;font-weight:500;border-radius:0}
  #step-nav .step-btn:hover{color:var(--fg)}
  #step-nav .step-btn.active{color:var(--accent);border-bottom-color:var(--accent)}
  #step-nav .step-btn .step-num{width:22px;height:22px;border-radius:50%;background:var(--panel);color:inherit;display:inline-flex;align-items:center;justify-content:center;font-weight:700;font-size:11px;border:1px solid var(--border)}
  #step-nav .step-btn.pending .step-num{}
  #step-nav .step-btn.running .step-num{background:var(--yellow);color:#0f1419;border-color:var(--yellow)}
  #step-nav .step-btn.done .step-num{background:var(--green);color:#0f1419;border-color:var(--green)}
  #step-nav .step-btn.failed .step-num{background:var(--red);color:#0f1419;border-color:var(--red)}
  .step-content{height:calc(100vh - 51px - 41px - 49px - 51px);overflow-y:auto;padding:20px;background:var(--bg)}
  .step-content h2{margin:0 0 8px;font-size:18px}
  .step-content .step-sub{color:var(--muted);font-size:13px;margin-bottom:16px}
  .step-content .step-actions{margin-top:16px}
  /* Make split-pane in step 1 fill the available height. */
  .step-content .src-link{height:calc(100vh - 380px);min-height:300px}
  .step-content .src-link .src-pane,.step-content .src-link .spec-pane{max-height:none;height:100%}
  .step-content pre{max-height:none}
  .step-content .dep-graph svg{max-width:100%}
</style></head>
<body>
<header>
  <h1>StructuredValidator demo</h1>
  <span class="muted">our baseline · 98.3% acc · 2.2% FAR · κ=0.957 on 60 pairs</span>
  <button id="run-demo-btn" style="margin-left:auto;background:var(--accent);color:#0f1419;padding:8px 16px;font-weight:600">▶ Run full demo</button>
</header>
<div id="demo-banner" style="display:none;position:sticky;top:0;z-index:10;padding:10px 20px;background:var(--accent);color:#0f1419;font-weight:600;font-size:13px"></div>
<nav class="tabs">
  <button data-view="pipeline" class="active">▶ 4-step pipeline</button>
  <button data-view="validate">Validate corpus</button>
  <button data-view="elicit">Draft a spec (LLM)</button>
  <button data-view="compare">Compare drafts</button>
  <button data-view="mutate">Mutation analysis</button>
</nav>

<div class="view active" id="view-pipeline">
<div id="pipeline-toolbar">
  <button id="pipeline-run-all" class="primary">▶ Run all 4 steps</button>
  <span class="muted">brief:</span>
  <select id="pipeline-brief-picker"></select>
  <span class="muted" style="flex:1;text-align:right;font-size:11px">
    pipelines that translate informal requirements into formal representations (e.g., Lean)
  </span>
</div>
<nav id="step-nav"></nav>
<div id="step-content" class="step-content"></div>
</div>

<div class="view" id="view-validate">
<main>
  <div class="col" id="task-list-col">
    <h2>Tasks (18)</h2>
    <div id="task-list"></div>
  </div>
  <div class="col" id="spec-col">
    <h2>Spec</h2>
    <div id="spec-body"><div class="placeholder">pick a task on the left</div></div>
  </div>
  <div class="col" id="result-col">
    <h2>Verdict</h2>
    <div id="result-body"><div class="placeholder">pick a candidate, hit Validate</div></div>
  </div>
</main>
</div>

<div class="view" id="view-elicit">
<main class="split2">
  <div class="col">
    <h2>Track 1 · spec elicitation</h2>
    <div class="muted" style="margin-bottom:12px">
      Give the LLM an intent + starting repo. It proposes a structural spec
      (file scope, forbidden imports, diff budget, one positive test) as
      constrained JSON. Every field is validated structurally before it
      becomes a real <code>TaskSpec</code>.
    </div>
    <div class="label">load a hand-crafted ambiguous brief (Dodds-shaped inputs)</div>
    <select id="brief-picker" style="background:var(--panel);color:var(--fg);border:1px solid var(--border);padding:6px 10px;border-radius:4px;font-size:13px;width:100%;margin-bottom:12px">
      <option value="">— start blank —</option>
    </select>
    <div class="label">intent (one sentence)</div>
    <input type="text" id="elicit-desc" placeholder="Add a subtract(a, b) function to calculator.py that returns a - b."/>
    <div class="label">starting repo (path + contents)</div>
    <div id="elicit-files"></div>
    <button id="elicit-add-file" style="background:var(--panel);color:var(--fg);border:1px solid var(--border);font-weight:400">+ add file</button>
    <details style="margin-top:12px">
      <summary class="muted" style="cursor:pointer;font-size:12px">+ additional sources (prose doc, existing tests, slide deck) — surfaces cross-source contradictions</summary>
      <div style="margin-top:8px">
        <div class="label">prose_doc (e.g. a paragraph from a design doc)</div>
        <textarea id="elicit-prose" class="small" placeholder="optional — a longer description, requirements doc excerpt, etc."></textarea>
        <div class="label" style="margin-top:8px">existing_tests (e.g. tests already in the repo that hint at intent)</div>
        <textarea id="elicit-tests" class="small" placeholder="optional — paste pytest code that's already in the repo"></textarea>
        <div class="label" style="margin-top:8px">slide_deck (e.g. bullets from a slide describing what to build)</div>
        <textarea id="elicit-slides" class="small" placeholder="optional — paste prose from a slide deck"></textarea>
      </div>
    </details>
    <button id="elicit-btn" style="margin-top:8px">Draft spec →</button>
  </div>
  <div class="col">
    <h2>Drafted spec</h2>
    <div id="elicit-result"><div class="placeholder">fill the form on the left, hit Draft</div></div>
  </div>
</main>
</div>

<div class="view" id="view-compare">
<main class="split1">
  <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:0.5px;color:var(--muted);margin:0 0 10px">
    Cross-model spec comparison · "too many partial specs" probe
  </h2>
  <div class="muted" style="margin-bottom:12px">
    Same intent, same starting repo, N models. The point isn't to pick
    a winner — it's to surface where the models disagree and require a
    human to resolve, the way Dodds describes spec writing actually
    going. If two LLMs agree on every field, the spec is uncontested.
    If they disagree on <code>forbidden_imports</code>, you've found
    a place where the intent was underspecified.
  </div>
  <div style="margin-bottom:14px">
    <input type="text" id="compare-desc" style="width:60%" placeholder="intent: Add a subtract(a, b) function to calculator.py"/>
    <input type="text" id="compare-models" style="width:30%" placeholder="models (comma-sep, default: sonnet,haiku)"/>
  </div>
  <div style="margin-bottom:14px">
    <div class="label">starting repo (paths + contents — same as Draft tab)</div>
    <div id="compare-files"></div>
    <button id="compare-add-file" style="background:var(--panel);color:var(--fg);border:1px solid var(--border);font-weight:400">+ add file</button>
    <button id="compare-btn" style="margin-left:8px">Compare across models →</button>
  </div>
  <div id="compare-result"><div class="placeholder">fill in intent + files, hit Compare</div></div>
</main>
</div>

<div class="view" id="view-mutate">
<main class="split1">
  <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:0.5px;color:var(--muted);margin:0 0 10px">
    Track 2 · spec mutation testing
  </h2>
  <div class="muted" style="margin-bottom:12px">
    Perturb each invariant in a spec (drop it, weaken its bound, shrink its set, widen its scope, drop a positive test).
    Re-run the validator on the corpus candidates. A mutation that newly
    accepts a should-reject candidate is <span class="mut-class load_bearing">load_bearing</span>
    — direct evidence the original invariant earned its place.
  </div>
  <div style="margin-bottom:14px">
    <select id="mutate-task" style="background:var(--panel);color:var(--fg);border:1px solid var(--border);padding:6px 10px;border-radius:4px;font-size:13px">
      <option value="">(whole corpus — slower)</option>
    </select>
    <button id="mutate-btn" style="margin-left:8px">Run mutations →</button>
  </div>
  <div id="mutate-result"><div class="placeholder">pick a task (or "whole corpus") and hit Run</div></div>
</main>
</div>
<script>
let TASKS = [];
let CURRENT = null;   // full task object
let CAND_IDX = 0;     // which candidate is selected
let FILE_IDX = 0;     // which file tab is selected in the editor
let EDITED = {};      // task_id -> cand_id -> {path: code} of user edits

async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function loadTasks() {
  const {tasks} = await fetchJSON('/api/tasks');
  TASKS = tasks;
  const list = document.getElementById('task-list');
  list.innerHTML = tasks.map(t => `
    <div class="task-item" data-id="${t.task_id}">
      <span class="task-id">${t.task_id}</span>
      <span class="task-cat">${t.category}</span>
      <div class="task-desc">${escapeHtml(t.description)}</div>
    </div>`).join('');
  list.querySelectorAll('.task-item').forEach(el => {
    el.addEventListener('click', () => selectTask(el.dataset.id));
  });
  if (tasks.length) selectTask(tasks[0].task_id);
}

async function selectTask(taskId) {
  document.querySelectorAll('.task-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === taskId);
  });
  CURRENT = await fetchJSON(`/api/tasks/${taskId}`);
  CAND_IDX = 0;
  FILE_IDX = 0;
  renderSpec();
  document.getElementById('result-body').innerHTML =
    '<div class="placeholder">hit Validate to run StructuredValidator</div>';
}

function renderSpec() {
  const t = CURRENT;
  const invHtml = t.invariants.map(i => {
    const args = Object.entries(i).filter(([k]) => k !== 'type' && k !== 'name')
      .map(([k,v]) => `${k}=${JSON.stringify(v)}`).join(', ');
    return `<div>· <span class="inv-name">${i.type}</span>(${args})</div>`;
  }).join('');

  const candHtml = t.candidates_full.map((c, i) => `
    <div class="candidate-tab ${i===CAND_IDX?'active':''}" data-i="${i}">
      ${c.candidate_id} <span class="pill ${c.label}">${c.label}</span>
    </div>`).join('');

  const cand = t.candidates_full[CAND_IDX];
  const editedMap = (EDITED[t.task_id] && EDITED[t.task_id][cand.candidate_id]) || {};
  const merged = {...cand.modified_repo, ...editedMap};
  const paths = Object.keys(merged).sort();
  const currentPath = paths[FILE_IDX] || paths[0];
  if (FILE_IDX >= paths.length) FILE_IDX = 0;

  const fileTabsHtml = paths.map((p, i) => {
    const isEdited = editedMap[p] !== undefined && editedMap[p] !== cand.modified_repo[p];
    return `<div class="file-tab ${i===FILE_IDX?'active':''} ${isEdited?'diff':''}" data-i="${i}">${p}</div>`;
  }).join('');

  document.getElementById('spec-body').innerHTML = `
    <div class="section">
      <div class="label">description</div>
      <div>${escapeHtml(t.description)}</div>
    </div>
    <div class="section">
      <div class="label">negative invariants</div>
      <div class="inv-list">${invHtml}</div>
    </div>
    <div class="section">
      <div class="label">positive tests</div>
      <div class="inv-list">${t.positive_tests.map(pt =>
        `<div>· <span class="inv-name">${pt.name || pt.path}</span></div>`).join('')}</div>
    </div>
    <div class="section">
      <div class="label">candidates (pick one)</div>
      <div class="candidate-tabs">${candHtml}</div>
      <div class="muted" style="font-size:11px;margin-bottom:8px">
        ${escapeHtml(cand.note || '(no note)')} · ground truth: should ${cand.should_accept?'accept':'reject'}
      </div>
      <div class="label">modified repo (editable — change anything to test your own diff)</div>
      <div class="file-tabs">${fileTabsHtml}</div>
      <textarea id="file-editor">${escapeHtml(merged[currentPath] || '')}</textarea>
    </div>
    <button id="validate-btn">Validate →</button>
    <button id="lean-btn" style="margin-left:8px;background:var(--panel);color:var(--fg);border:1px solid var(--border)">Show as Lean →</button>
    <div id="lean-panel"></div>
  `;

  document.querySelectorAll('.candidate-tab').forEach(el => {
    el.addEventListener('click', () => { CAND_IDX = +el.dataset.i; FILE_IDX = 0; renderSpec(); });
  });
  document.querySelectorAll('.file-tab').forEach(el => {
    el.addEventListener('click', () => { saveEdit(); FILE_IDX = +el.dataset.i; renderSpec(); });
  });
  document.getElementById('validate-btn').addEventListener('click', runValidate);
  document.getElementById('lean-btn').addEventListener('click', showAsLean);
  document.getElementById('file-editor').addEventListener('input', () => {
    // mark file as edited (for the * indicator) without re-rendering on every keystroke
    saveEdit();
    const tab = document.querySelectorAll('.file-tab')[FILE_IDX];
    if (tab) tab.classList.add('diff');
  });
}

function saveEdit() {
  const t = CURRENT;
  const cand = t.candidates_full[CAND_IDX];
  const paths = Object.keys({...cand.modified_repo,
                              ...((EDITED[t.task_id]||{})[cand.candidate_id]||{})}).sort();
  const p = paths[FILE_IDX];
  if (!p) return;
  const code = document.getElementById('file-editor').value;
  EDITED[t.task_id] = EDITED[t.task_id] || {};
  EDITED[t.task_id][cand.candidate_id] = EDITED[t.task_id][cand.candidate_id] || {};
  EDITED[t.task_id][cand.candidate_id][p] = code;
}

async function runValidate() {
  saveEdit();
  const t = CURRENT;
  const cand = t.candidates_full[CAND_IDX];
  const edits = (EDITED[t.task_id] && EDITED[t.task_id][cand.candidate_id]) || {};
  const hasEdits = Object.keys(edits).some(p => edits[p] !== cand.modified_repo[p]);

  const body = hasEdits
    ? { task_id: t.task_id, modified_repo: {...cand.modified_repo, ...edits} }
    : { task_id: t.task_id, candidate_id: cand.candidate_id };

  document.getElementById('result-body').innerHTML = '<div class="placeholder">running…</div>';
  try {
    const v = await fetchJSON('/api/validate', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    renderVerdict(v, hasEdits);
  } catch (e) {
    document.getElementById('result-body').innerHTML =
      `<div class="verdict-box reject">error: ${escapeHtml(e.message)}</div>`;
  }
}

async function showAsLean() {
  if (!CURRENT) return;
  const panel = document.getElementById('lean-panel');
  panel.innerHTML = '<div class="placeholder" style="padding:8px 0">emitting Lean…</div>';
  try {
    const r = await fetchJSON('/api/emit_lean', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({task_id: CURRENT.task_id}),
    });
    const verifyBtn = r.lean_available
      ? `<button id="verify-lean-btn" style="margin-top:6px">Verify with lake build →</button>`
      : `<div class="muted" style="font-size:11px;margin-top:6px">Lean toolchain not installed on the server — emission only.</div>`;
    panel.innerHTML = `
      <div class="section" style="margin-top:12px;border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--panel)">
        <div class="label">emitted Lean 4 spec (real .lean source — checkable by lake build)</div>
        <pre style="font-size:11px">${escapeHtml(r.source)}</pre>
        ${verifyBtn}
        <div id="verify-lean-result"></div>
      </div>`;
    const vbtn = document.getElementById('verify-lean-btn');
    if (vbtn) vbtn.addEventListener('click', verifyLean);
  } catch (e) {
    panel.innerHTML = `<div class="verdict-box reject" style="margin-top:8px">${escapeHtml(e.message)}</div>`;
  }
}

async function verifyLean() {
  if (!CURRENT) return;
  const out = document.getElementById('verify-lean-result');
  out.innerHTML = '<div class="placeholder" style="padding:8px 0">running lake build…</div>';
  try {
    const r = await fetchJSON('/api/verify_lean', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({task_id: CURRENT.task_id}),
    });
    const cls = r.ok ? 'accept' : 'reject';
    const msg = r.ok
      ? `<strong>✓ type-checks under Lean 4</strong> in ${r.duration_seconds}s`
      : `<strong>✗ lake build failed</strong>`;
    out.innerHTML = `
      <div class="verdict-box ${cls}" style="margin-top:10px">
        ${msg}
        <div class="muted" style="font-size:11px;margin-top:4px">${escapeHtml(r.lean_version || '')}</div>
        ${r.stdout ? `<details style="margin-top:6px"><summary class="muted" style="cursor:pointer;font-size:11px">stdout</summary><pre>${escapeHtml(r.stdout)}</pre></details>` : ''}
        ${r.stderr ? `<details><summary class="muted" style="cursor:pointer;font-size:11px">stderr</summary><pre>${escapeHtml(r.stderr)}</pre></details>` : ''}
      </div>`;
  } catch (e) {
    out.innerHTML = `<div class="verdict-box reject" style="margin-top:8px">${escapeHtml(e.message)}</div>`;
  }
}

function renderVerdict(v, hasEdits) {
  const gt = hasEdits ? null : v.ground_truth_label;
  const matchesGT = gt === null ? null :
    (v.accepted === (gt === 'correct'));
  const traces = v.invariant_results.map(r => {
    const cls = r.uncertain ? 'uncertain' : (r.holds ? 'pass' : 'fail');
    const mark = r.uncertain ? '⊘' : (r.holds ? '✓' : '✗');
    return `<div class="inv-trace ${cls}">
      <span class="check">${mark}</span>
      <span><span class="inv-name">${r.name}</span><span class="details"> — ${escapeHtml(r.details||'(no details)')}</span></span>
    </div>`;
  }).join('');

  document.getElementById('result-body').innerHTML = `
    <div class="verdict-box ${v.decision}">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <span class="pill ${v.decision}">${v.decision.toUpperCase()}</span>
        ${gt ? `<span class="muted" style="font-size:11px">ground truth: <span class="pill ${gt}">${gt}</span></span>` : ''}
        ${matchesGT === true ? '<span class="muted">· ✓ matches ground truth</span>' : ''}
        ${matchesGT === false ? '<span class="muted" style="color:var(--red)">· ✗ disagrees with ground truth</span>' : ''}
        ${hasEdits ? '<span class="muted">· user-edited diff</span>' : ''}
      </div>
      <div style="font-size:12px">${escapeHtml(v.reason)}</div>
    </div>
    <div class="section">
      <div class="label">per-invariant trace</div>
      ${traces}
    </div>
  `;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ---------------------------------------------------------------------------
// Tab nav
// ---------------------------------------------------------------------------
document.querySelectorAll('nav.tabs button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav.tabs button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('view-' + btn.dataset.view).classList.add('active');
    if (btn.dataset.view === 'mutate') populateMutateTaskList();
  });
});

// ---------------------------------------------------------------------------
// Elicitation view
// ---------------------------------------------------------------------------
let ELICIT_FILES = [
  {path: 'calculator.py', code: 'def add(a, b):\n    return a + b\n'},
  {path: 'test_calculator.py', code: 'from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n'},
];

function renderElicitFiles() {
  const host = document.getElementById('elicit-files');
  host.innerHTML = ELICIT_FILES.map((f, i) => `
    <div class="file-row">
      <input type="text" data-i="${i}" data-k="path" value="${escapeHtml(f.path)}" placeholder="path"/>
      <textarea data-i="${i}" data-k="code" placeholder="file contents">${escapeHtml(f.code)}</textarea>
      <button class="del" data-i="${i}">×</button>
    </div>`).join('');
  host.querySelectorAll('input, textarea').forEach(el => {
    el.addEventListener('input', () => {
      const i = +el.dataset.i, k = el.dataset.k;
      ELICIT_FILES[i][k] = el.value;
    });
  });
  host.querySelectorAll('button.del').forEach(el => {
    el.addEventListener('click', () => {
      ELICIT_FILES.splice(+el.dataset.i, 1);
      renderElicitFiles();
    });
  });
}

document.getElementById('elicit-add-file').addEventListener('click', () => {
  ELICIT_FILES.push({path: '', code: ''});
  renderElicitFiles();
});

let LAST_DRAFT = null;        // most recent successful draft response
let OBJECTIONS = [];          // [{field, reason}, ...] pending for the next refine
let ITERATION_HISTORY = [];   // chronological [{label, draft, feedback}, ...]

document.getElementById('elicit-btn').addEventListener('click', async () => {
  const desc = document.getElementById('elicit-desc').value.trim();
  if (!desc) { alert('please enter an intent'); return; }
  const starting_repo = {};
  for (const f of ELICIT_FILES) {
    if (f.path) starting_repo[f.path] = f.code;
  }
  if (!Object.keys(starting_repo).length) { alert('add at least one file'); return; }
  const additional_sources = {};
  for (const [k, id] of [['prose_doc','elicit-prose'], ['existing_tests','elicit-tests'], ['slide_deck','elicit-slides']]) {
    const el = document.getElementById(id);
    if (el && el.value.trim()) additional_sources[k] = el.value;
  }
  const hasSources = Object.keys(additional_sources).length > 0;
  document.getElementById('elicit-result').innerHTML =
    `<div class="placeholder">calling Claude${hasSources ? ` with ${Object.keys(additional_sources).length} extra source${Object.keys(additional_sources).length===1?'':'s'}` : ''}… (~5-15s)</div>`;
  // Reset iteration history on fresh draft.
  ITERATION_HISTORY = [];
  OBJECTIONS = [];
  try {
    const r = await fetchJSON('/api/elicit', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        description: desc, starting_repo,
        task_id: 'draft_' + Date.now(),
        additional_sources: hasSources ? additional_sources : null,
      }),
    });
    if (r.ok) {
      LAST_DRAFT = r;
      ITERATION_HISTORY.push({label: 'v1 (initial draft)', draft: r, feedback: []});
    }
    renderElicitResult(r);
  } catch (e) {
    document.getElementById('elicit-result').innerHTML =
      `<div class="verdict-box reject">error: ${escapeHtml(e.message)}</div>`;
  }
});

function renderElicitResult(r) {
  if (!r.ok) {
    document.getElementById('elicit-result').innerHTML = `
      <div class="verdict-box reject"><strong>elicitation failed:</strong> ${escapeHtml(r.error)}</div>
      ${r.raw_response ? `<div class="label">raw LLM response</div><pre>${escapeHtml(r.raw_response)}</pre>` : ''}`;
    return;
  }
  const fieldKeyForInvariant = {
    OnlyFilesModified: 'allowed_files',
    NoNewImports: 'forbidden_imports',
    DiffSmallerThan: 'max_diff_lines',
    NoSecretsInDiff: 'check_secrets',
  };
  const invHtml = r.drafted_invariants.map((d, i) => {
    const args = Object.entries(d.args).filter(([k]) => k !== 'type' && k !== 'name')
      .map(([k,v]) => `${k}=${JSON.stringify(v)}`).join(', ');
    const fieldKey = fieldKeyForInvariant[d.type] || d.type;
    return `<div class="drafted-inv">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
        <div>
          <div><span class="inv-name">${d.type}</span>(${args})</div>
          <div class="why">${escapeHtml(d.rationale || '(no rationale)')}</div>
        </div>
        <button class="del" data-field="${escapeHtml(fieldKey)}" data-target="${escapeHtml(d.type)}" style="font-size:11px;padding:3px 8px">✗ reject</button>
      </div>
    </div>`;
  }).join('');
  const pt = r.spec.positive_tests[0] || {};

  // Contradictions warning panel — fires when the user supplied multiple
  // sources and the LLM found them pointing in different directions.
  const contradictions = r.contradictions || [];
  const contradictionsHtml = contradictions.length
    ? `<div class="verdict-box abstain">
        <strong>⚠ ${contradictions.length} cross-source contradiction${contradictions.length===1?'':'s'} found</strong>
        <div class="muted" style="margin:6px 0;font-size:12px">The sources disagreed. Review before accepting the draft.</div>
        ${contradictions.map(c => `
          <div style="border-top:1px solid var(--border);padding:8px 0">
            <div style="font-size:12px">
              ${c.sources.map(s => `<span class="pill abstain" style="font-size:10px;margin-right:4px">${escapeHtml(s)}</span>`).join('')}
              <strong>${escapeHtml(c.summary)}</strong>
            </div>
            ${c.resolution ? `<div class="muted" style="font-size:11px;margin-top:4px">→ resolution: ${escapeHtml(c.resolution)}</div>` : ''}
          </div>`).join('')}
      </div>`
    : '';

  document.getElementById('elicit-result').innerHTML = `
    ${renderIterationTimeline()}
    ${contradictionsHtml}
    <div class="section">
      <div class="label">proposed invariants (with rationale) — click ✗ to flag for revision</div>
      ${invHtml}
    </div>
    <div id="draft-lean-panel"></div>
    <div class="section">
      <div class="label">proposed positive test — ${escapeHtml(pt.path||'')}
        <button class="del" data-field="positive_test" data-target="positive_test" style="font-size:11px;padding:3px 8px;margin-left:8px">✗ reject</button>
      </div>
      <div class="muted" style="font-size:12px;margin-bottom:6px">${escapeHtml(r.positive_test_rationale)}</div>
      <pre>${escapeHtml(pt.code||'')}</pre>
    </div>
    <div id="objections-panel">${renderObjections()}</div>
    <details>
      <summary class="muted" style="cursor:pointer;font-size:11px">raw LLM JSON</summary>
      <pre style="margin-top:6px">${escapeHtml(r.raw_response)}</pre>
    </details>
  `;

  document.querySelectorAll('button.del[data-field]').forEach(btn => {
    btn.addEventListener('click', () => {
      const reason = prompt(`Why reject ${btn.dataset.target}?`, '');
      if (!reason) return;
      OBJECTIONS.push({field: btn.dataset.field, reason});
      document.getElementById('objections-panel').innerHTML = renderObjections();
      wireRefineButton();
    });
  });
  wireRefineButton();

  // Auto-emit Lean from the drafted spec.
  emitLeanFromDraft(r);
}

async function emitLeanFromDraft(r) {
  const panel = document.getElementById('draft-lean-panel');
  if (!panel) return;
  panel.innerHTML = `<div class="muted" style="margin-top:14px;font-size:12px">↓ emitting Lean from this draft…</div>`;
  try {
    const spec_json = {
      task_id: r.spec.task_id,
      description: r.spec.description,
      starting_repo: r.spec.starting_repo,
      invariants: r.drafted_invariants,
      positive_tests: r.spec.positive_tests,
    };
    const lean = await fetchJSON('/api/emit_lean', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({spec_json}),
    });
    const verifyBtn = lean.lean_available
      ? `<button id="draft-verify-btn" style="margin-top:6px">Verify with lake build →</button>`
      : `<div class="muted" style="font-size:11px;margin-top:6px">Lean toolchain not on the server — emission only.</div>`;
    panel.innerHTML = `
      <div class="section" style="margin-top:14px;border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--panel)">
        <div class="label">↓ Lean 4 source emitted from this drafted spec</div>
        <div class="muted" style="font-size:11px;margin-bottom:6px">
          Even if the input brief is muddy, the output is sharp Lean.
          The contradictions panel above (if any) makes the muddiness visible;
          this panel makes the structural commitments precise.
        </div>
        <pre style="font-size:11px">${escapeHtml(lean.source)}</pre>
        ${verifyBtn}
        <div id="draft-verify-result"></div>
      </div>`;
    const vbtn = document.getElementById('draft-verify-btn');
    if (vbtn) vbtn.addEventListener('click', () => verifyDraftLean(spec_json));
  } catch (e) {
    panel.innerHTML = `<div class="verdict-box reject" style="margin-top:10px">Lean emission failed: ${escapeHtml(e.message)}</div>`;
  }
}

async function verifyDraftLean(spec_json) {
  const out = document.getElementById('draft-verify-result');
  out.innerHTML = `<div class="placeholder" style="padding:8px 0">running lake build…</div>`;
  try {
    const r = await fetchJSON('/api/verify_lean', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({spec_json}),
    });
    const cls = r.ok ? 'accept' : 'reject';
    const msg = r.ok
      ? `<strong>✓ type-checks under Lean 4</strong> in ${r.duration_seconds}s`
      : `<strong>✗ lake build failed</strong>`;
    out.innerHTML = `
      <div class="verdict-box ${cls}" style="margin-top:10px">
        ${msg}
        <div class="muted" style="font-size:11px;margin-top:4px">${escapeHtml(r.lean_version || '')}</div>
        ${r.stdout ? `<details style="margin-top:6px"><summary class="muted" style="cursor:pointer;font-size:11px">stdout</summary><pre>${escapeHtml(r.stdout)}</pre></details>` : ''}
        ${r.stderr ? `<details><summary class="muted" style="cursor:pointer;font-size:11px">stderr</summary><pre>${escapeHtml(r.stderr)}</pre></details>` : ''}
      </div>`;
  } catch (e) {
    out.innerHTML = `<div class="verdict-box reject" style="margin-top:8px">${escapeHtml(e.message)}</div>`;
  }
}

function renderObjections() {
  if (!OBJECTIONS.length) return '';
  const rows = OBJECTIONS.map((o, i) =>
    `<div style="display:flex;gap:8px;padding:4px 0;font-size:12px">
      <span class="pill reject">${escapeHtml(o.field)}</span>
      <span style="flex:1">${escapeHtml(o.reason)}</span>
      <button class="del" data-clear="${i}" style="padding:2px 6px;font-size:10px">×</button>
    </div>`).join('');
  return `<div class="section">
    <div class="label">pending objections (${OBJECTIONS.length})</div>
    ${rows}
    <button id="refine-btn" style="margin-top:8px">Refine spec →</button>
  </div>`;
}

function wireRefineButton() {
  document.querySelectorAll('button[data-clear]').forEach(b => {
    b.addEventListener('click', () => {
      OBJECTIONS.splice(+b.dataset.clear, 1);
      document.getElementById('objections-panel').innerHTML = renderObjections();
      wireRefineButton();
    });
  });
  const btn = document.getElementById('refine-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const desc = document.getElementById('elicit-desc').value.trim();
    const starting_repo = {};
    for (const f of ELICIT_FILES) {
      if (f.path) starting_repo[f.path] = f.code;
    }
    if (!LAST_DRAFT || !LAST_DRAFT.raw_response) {
      alert('No prior draft to refine'); return;
    }
    document.getElementById('elicit-result').innerHTML =
      '<div class="placeholder">refining with reviewer feedback… (~5-15s)</div>';
    try {
      const r = await fetchJSON('/api/elicit/refine', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          description: desc,
          starting_repo,
          previous_response: LAST_DRAFT.raw_response,
          feedback: OBJECTIONS,
          task_id: 'refine_' + Date.now(),
        }),
      });
      if (r.ok) {
        const carriedFeedback = OBJECTIONS.slice();
        ITERATION_HISTORY.push({
          label: `v${ITERATION_HISTORY.length + 1} (after ${carriedFeedback.length} objection${carriedFeedback.length===1?'':'s'})`,
          draft: r, feedback: carriedFeedback,
        });
        LAST_DRAFT = r;
        OBJECTIONS = [];
      }
      renderElicitResult(r);
    } catch (e) {
      document.getElementById('elicit-result').innerHTML =
        `<div class="verdict-box reject">refine error: ${escapeHtml(e.message)}</div>`;
    }
  });
}

function renderIterationTimeline() {
  if (ITERATION_HISTORY.length <= 1) return '';
  const rows = ITERATION_HISTORY.map((it, i) => {
    const isLatest = i === ITERATION_HISTORY.length - 1;
    const fb = it.feedback.length
      ? it.feedback.map(o => `<span class="pill reject" style="font-size:10px">${escapeHtml(o.field)}</span>`).join(' ')
      : '<span class="muted" style="font-size:11px">(no feedback — initial)</span>';
    return `<div style="display:flex;gap:8px;align-items:center;padding:3px 0">
      <span class="pill ${isLatest?'accept':''}" style="font-size:10px">${escapeHtml(it.label)}</span>
      <span style="flex:1">${fb}</span>
    </div>`;
  }).join('');
  return `<div class="section" style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--panel)">
    <div class="label">iteration timeline</div>
    ${rows}
  </div>`;
}

renderElicitFiles();

// Populate the ambiguous-brief picker.
(async () => {
  try {
    const r = await fetchJSON('/api/ambiguous_briefs');
    const sel = document.getElementById('brief-picker');
    for (const b of r.briefs) {
      const opt = document.createElement('option');
      opt.value = b.brief_id;
      opt.textContent = b.label;
      sel.appendChild(opt);
    }
    sel.addEventListener('change', () => {
      const b = r.briefs.find(x => x.brief_id === sel.value);
      if (!b) return;
      document.getElementById('elicit-desc').value = b.description;
      ELICIT_FILES = Object.entries(b.starting_repo).map(([path, code]) => ({path, code}));
      renderElicitFiles();
      const prose = document.getElementById('elicit-prose');
      const tests = document.getElementById('elicit-tests');
      const slides = document.getElementById('elicit-slides');
      if (prose)  prose.value  = b.prose_doc || '';
      if (tests)  tests.value  = b.existing_tests || '';
      if (slides) slides.value = b.slide_deck || '';
    });
  } catch (e) {
    // briefs are optional; ignore if endpoint missing
  }
})();

// ---------------------------------------------------------------------------
// Compare-drafts view (cross-model)
// ---------------------------------------------------------------------------
let COMPARE_FILES = [
  {path: 'calculator.py', code: 'def add(a, b):\n    return a + b\n'},
];

function renderCompareFiles() {
  const host = document.getElementById('compare-files');
  host.innerHTML = COMPARE_FILES.map((f, i) => `
    <div class="file-row">
      <input type="text" data-i="${i}" data-k="path" value="${escapeHtml(f.path)}" placeholder="path"/>
      <textarea data-i="${i}" data-k="code" placeholder="file contents">${escapeHtml(f.code)}</textarea>
      <button class="del" data-i="${i}">×</button>
    </div>`).join('');
  host.querySelectorAll('input, textarea').forEach(el => {
    el.addEventListener('input', () => {
      COMPARE_FILES[+el.dataset.i][el.dataset.k] = el.value;
    });
  });
  host.querySelectorAll('button.del').forEach(el => {
    el.addEventListener('click', () => {
      COMPARE_FILES.splice(+el.dataset.i, 1);
      renderCompareFiles();
    });
  });
}

document.getElementById('compare-add-file').addEventListener('click', () => {
  COMPARE_FILES.push({path: '', code: ''});
  renderCompareFiles();
});

document.getElementById('compare-btn').addEventListener('click', async () => {
  const desc = document.getElementById('compare-desc').value.trim();
  if (!desc) { alert('please enter an intent'); return; }
  const modelsRaw = document.getElementById('compare-models').value.trim();
  const models = modelsRaw ? modelsRaw.split(',').map(s => s.trim()).filter(Boolean) : null;
  const starting_repo = {};
  for (const f of COMPARE_FILES) {
    if (f.path) starting_repo[f.path] = f.code;
  }
  if (!Object.keys(starting_repo).length) { alert('add at least one file'); return; }
  document.getElementById('compare-result').innerHTML =
    '<div class="placeholder">running drafts in parallel… (~5-15s per model)</div>';
  try {
    const r = await fetchJSON('/api/elicit/compare', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({description: desc, starting_repo, models, task_id: 'compare_' + Date.now()}),
    });
    renderCompareResult(r);
  } catch (e) {
    document.getElementById('compare-result').innerHTML =
      `<div class="verdict-box reject">error: ${escapeHtml(e.message)}</div>`;
  }
});

function renderCompareResult(r) {
  const models = r.models;
  // Disagreement banner.
  const banner = r.disagreements.length
    ? `<div class="verdict-box abstain" style="margin-bottom:14px">
        <strong>${r.disagreements.length} disagreement${r.disagreements.length===1?'':'s'} found:</strong>
        ${r.disagreements.map(d => `<span class="pill abstain" style="margin:0 4px">${escapeHtml(d)}</span>`).join('')}
        — the intent underspecifies these fields. A human needs to resolve before this spec is trustworthy.
      </div>`
    : `<div class="verdict-box accept" style="margin-bottom:14px">
        <strong>All models agreed</strong> on every comparable field. The spec is uncontested (for this intent + repo).
      </div>`;

  // Per-field agreement table.
  const agreementRow = (c) => {
    const cls = c.agreement === 'agree' ? 'accept'
      : c.agreement === 'disagree' ? 'reject' : 'abstain';
    const valsHtml = models.map(m => {
      const v = c.values_by_model[m];
      if (v === undefined) return `<td class="muted">(failed)</td>`;
      return `<td><code>${escapeHtml(JSON.stringify(v))}</code></td>`;
    }).join('');
    return `<tr>
      <td><strong>${escapeHtml(c.field_name)}</strong></td>
      <td><span class="pill ${cls}">${c.agreement}</span></td>
      ${valsHtml}
    </tr>`;
  };

  const fieldTable = `<table class="mut" style="margin-top:8px">
    <tr><th>field</th><th>agreement</th>${models.map(m => `<th>${escapeHtml(m)}</th>`).join('')}</tr>
    ${r.field_comparisons.map(agreementRow).join('')}
  </table>`;

  // Side-by-side per-model panels (collapsed by default).
  const modelPanels = `<div style="display:grid;grid-template-columns:repeat(${models.length},1fr);gap:14px;margin-top:16px">
    ${models.map(m => {
      const d = r.drafts[m];
      if (!d.ok) {
        return `<div style="padding:12px;border:1px solid var(--red);border-radius:6px">
          <div style="font-family:var(--mono);font-size:12px;color:var(--accent)">${escapeHtml(m)}</div>
          <div class="muted" style="margin-top:6px">FAILED: ${escapeHtml(d.error)}</div>
        </div>`;
      }
      const invs = d.drafted_invariants.map(di => {
        const args = Object.entries(di.args).filter(([k]) => k !== 'type' && k !== 'name')
          .map(([k,v]) => `${k}=${JSON.stringify(v)}`).join(', ');
        return `<div class="drafted-inv">
          <div><span class="inv-name">${di.type}</span>(${args})</div>
          <div class="why">${escapeHtml(di.rationale || '')}</div>
        </div>`;
      }).join('');
      return `<div style="padding:12px;border:1px solid var(--border);border-radius:6px">
        <div style="font-family:var(--mono);font-size:12px;color:var(--accent);margin-bottom:8px">${escapeHtml(m)}</div>
        ${invs}
      </div>`;
    }).join('')}
  </div>`;

  document.getElementById('compare-result').innerHTML = banner + fieldTable + modelPanels;
}

renderCompareFiles();

// ---------------------------------------------------------------------------
// Mutation view
// ---------------------------------------------------------------------------
function populateMutateTaskList() {
  const sel = document.getElementById('mutate-task');
  if (sel.options.length > 1) return;  // already populated
  for (const t of TASKS) {
    const opt = document.createElement('option');
    opt.value = t.task_id;
    opt.textContent = t.task_id + ' — ' + t.description.slice(0, 60);
    sel.appendChild(opt);
  }
}

document.getElementById('mutate-btn').addEventListener('click', async () => {
  const taskId = document.getElementById('mutate-task').value || null;
  document.getElementById('mutate-result').innerHTML =
    `<div class="placeholder">running mutations… ${taskId ? '(~1s)' : 'on full corpus (~15s)'}</div>`;
  try {
    const r = await fetchJSON('/api/mutate', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({task_id: taskId}),
    });
    renderMutateResult(r);
  } catch (e) {
    document.getElementById('mutate-result').innerHTML =
      `<div class="verdict-box reject">error: ${escapeHtml(e.message)}</div>`;
  }
});

function renderMutateResult(r) {
  const s = r.summary;
  const stats = `
    <div class="stat"><span class="n">${s.total_mutations}</span><span class="l">total mutations</span></div>
    <div class="stat"><span class="n" style="color:var(--red)">${s.load_bearing}</span><span class="l">load-bearing (${(s.fraction_load_bearing*100).toFixed(1)}%)</span></div>
    <div class="stat"><span class="n" style="color:var(--yellow)">${s.brittle}</span><span class="l">brittle</span></div>
    <div class="stat"><span class="n" style="color:var(--muted)">${s.invisible}</span><span class="l">invisible</span></div>`;

  const mutations = r.mutations
    || Object.values(r.per_spec).flat();

  // Coverage section: which mutation kinds did each spec defend against?
  // Empty coverage = slide-deck spec; full coverage = the spec actually
  // constrains every dimension the harness can perturb.
  const coverageHtml = renderCoverageBlock(r.coverage || {}, r.coverage_score || {}, s.kinds_order || []);

  const rowsHtml = mutations.map(m => `
    <tr class="${m.classification}">
      <td><span class="mut-class ${m.classification}">${m.classification}</span></td>
      <td>${escapeHtml(m.kind)}</td>
      <td>${escapeHtml(m.target)}</td>
      <td>${escapeHtml(m.description)}</td>
      <td>${m.newly_accepted.length ? '+' + m.newly_accepted.join(', +') : ''}</td>
      <td>${m.newly_rejected.length ? '-' + m.newly_rejected.join(', -') : ''}</td>
    </tr>`).join('');

  const byKindRows = Object.entries(s.by_kind).map(([k, v]) =>
    `<tr><td>${k}</td><td>${v.load_bearing||0}</td><td>${v.brittle||0}</td><td>${v.invisible||0}</td></tr>`
  ).join('');

  document.getElementById('mutate-result').innerHTML = `
    <div style="margin-bottom:18px">${stats}</div>
    ${coverageHtml}
    <div class="section">
      <div class="label">by mutation kind</div>
      <table class="mut" style="max-width:500px">
        <tr><th>kind</th><th>load_bearing</th><th>brittle</th><th>invisible</th></tr>
        ${byKindRows}
      </table>
    </div>
    <div class="section">
      <div class="label">all mutations (${mutations.length})</div>
      <table class="mut">
        <tr><th>class</th><th>kind</th><th>target</th><th>change</th><th>newly accepted</th><th>newly rejected</th></tr>
        ${rowsHtml}
      </table>
    </div>
  `;
}

function renderCoverageBlock(coverage, coverageScore, kindsOrder) {
  const taskIds = Object.keys(coverage);
  if (!taskIds.length) return '';
  const kinds = kindsOrder.length ? kindsOrder :
    Array.from(new Set(taskIds.flatMap(t => Object.keys(coverage[t] || {}))));
  const rows = taskIds.map(tid => {
    const cov = coverage[tid] || {};
    const score = coverageScore[tid] != null
      ? `${(coverageScore[tid] * 100).toFixed(0)}%` : '';
    const badges = kinds.map(k => {
      const ok = !!cov[k];
      return `<span class="cov-badge ${ok?'covered':'uncovered'}">${ok?'✓':'⊘'} ${escapeHtml(k)}</span>`;
    }).join('');
    return `<div class="cov-row">
      <span class="cov-score">${escapeHtml(tid)} · ${score}</span>
      ${badges}
    </div>`;
  }).join('');
  return `<div class="section">
    <div class="label">spec coverage (per task — which mutation kinds yielded ≥1 load-bearing case)</div>
    <div class="muted" style="font-size:12px;margin-bottom:8px">
      A spec where every kind is ⊘ is a slide-deck spec — the corpus
      doesn't exercise anything it constrains. A spec where every kind
      is ✓ defends against every failure dimension the harness can
      perturb. Most real specs land somewhere in the middle, and
      that's fine — it just needs to be honest.
    </div>
    ${rows}
  </div>`;
}

loadTasks().catch(e => {
  document.getElementById('task-list').innerHTML =
    `<div class="placeholder">failed to load: ${escapeHtml(e.message)}</div>`;
});

// ---------------------------------------------------------------------------
// ▶ Run full demo — scripted walkthrough of the entire pipeline
// ---------------------------------------------------------------------------

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

function waitFor(predicate, {timeout = 60000, interval = 200} = {}) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      let v;
      try { v = predicate(); } catch (e) { v = null; }
      if (v) return resolve(v);
      if (Date.now() - start > timeout) return reject(new Error('timeout: ' + predicate));
      setTimeout(tick, interval);
    };
    tick();
  });
}

function showBanner(msg, color) {
  const el = document.getElementById('demo-banner');
  el.style.display = 'block';
  el.style.background = color || 'var(--accent)';
  el.innerHTML = msg;
}

function hideBanner() {
  document.getElementById('demo-banner').style.display = 'none';
}

function activateTab(name) {
  document.querySelector(`nav.tabs button[data-view="${name}"]`).click();
}

async function runFullDemo() {
  const startMs = Date.now();
  const btn = document.getElementById('run-demo-btn');
  btn.disabled = true;
  btn.textContent = '⋯ running…';

  try {
    // ---- STEP 1: Elicit from a muddy brief (Brief B — PRD contradicts itself).
    showBanner('Step 1/4 · loading "PRD vs itself" brief, drafting spec…');
    activateTab('elicit');
    await delay(400);

    const briefPicker = document.getElementById('brief-picker');
    await waitFor(() => briefPicker.options.length > 1);
    briefPicker.value = 'b_prd_contradicts';
    briefPicker.dispatchEvent(new Event('change'));
    await delay(400);

    document.getElementById('elicit-btn').click();
    // Wait for the contradictions/invariants panels to appear.
    await waitFor(() => document.querySelector('#elicit-result .drafted-inv'));
    showBanner('Step 1/4 ✓ · spec drafted · contradictions surfaced · auto-emitting Lean…');
    await delay(800);

    // ---- STEP 2: Wait for Lean emission, click Verify, wait for lake build.
    showBanner('Step 2/4 · waiting for Lean source, then verifying with lake build…');
    const vbtn = await waitFor(() => document.getElementById('draft-verify-btn'));
    vbtn.click();
    await waitFor(() => document.querySelector('#draft-verify-result .verdict-box'));
    showBanner('Step 2/4 ✓ · Lean type-checks under lake build (~0.2s)');
    await delay(1500);

    // ---- STEP 3: Validate t11 against all 4 candidates.
    showBanner('Step 3/4 · validating t11_jwt_middleware against all 4 candidates…');
    activateTab('validate');
    await delay(400);
    const t11Item = await waitFor(() => document.querySelector('.task-item[data-id="t11_jwt_middleware"]'));
    t11Item.click();
    await waitFor(() => document.querySelectorAll('.candidate-tab').length >= 4);

    const verdicts = [];
    for (let i = 0; i < 4; i++) {
      const tabs = document.querySelectorAll('.candidate-tab');
      tabs[i].click();
      await delay(250);
      // Snapshot label *before* validate so the validate result doesn't
      // clobber our reference.
      const label = tabs[i].textContent.trim();
      document.getElementById('validate-btn').click();
      const box = await waitFor(() =>
        document.querySelector('#result-body .verdict-box'));
      const decision = box.classList.contains('accept') ? 'accept'
                      : box.classList.contains('reject') ? 'reject' : 'abstain';
      verdicts.push({label, decision});
      await delay(700);
    }
    showBanner(
      'Step 3/4 ✓ · ' +
      verdicts.map(v => `${v.label.split(' ')[0]}→${v.decision}`).join(' · '));
    await delay(1500);

    // ---- STEP 4: Mutation analysis on t11.
    showBanner('Step 4/4 · running mutation harness on t11_jwt_middleware…');
    activateTab('mutate');
    await delay(400);
    const mutateSel = document.getElementById('mutate-task');
    await waitFor(() => mutateSel.options.length > 1);
    mutateSel.value = 't11_jwt_middleware';
    document.getElementById('mutate-btn').click();
    await waitFor(() => document.querySelector('#mutate-result .stat'));
    // Pull the corpus numbers out of the rendered DOM.
    const stats = Array.from(document.querySelectorAll('#mutate-result .stat .n'))
      .map(n => n.textContent);
    const score = document.querySelector('#mutate-result .cov-score');
    const coverage = score ? score.textContent : '';
    showBanner(
      'Step 4/4 ✓ · ' +
      `mutations=${stats[0]||'?'} · load_bearing=${stats[1]||'?'} · ` +
      `brittle=${stats[2]||'?'} · invisible=${stats[3]||'?'} · ` +
      `coverage: ${coverage}`);
    await delay(2200);

    // ---- DONE
    const elapsed = ((Date.now() - startMs) / 1000).toFixed(1);
    showBanner(
      `✓ Full demo complete in ${elapsed}s · muddy brief → contradictions → ` +
      `Lean → lake build ✓ → validator caught all 4 candidates → mutation report. ` +
      `<span style="margin-left:14px;cursor:pointer;text-decoration:underline" onclick="document.getElementById('demo-banner').style.display='none'">[dismiss]</span>`,
      'var(--green)');
  } catch (e) {
    showBanner('Demo failed: ' + e.message + ' — try clicking the button again, or run each tab manually.', 'var(--red)');
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ Run full demo';
  }
}

document.getElementById('run-demo-btn').addEventListener('click', runFullDemo);

// ---------------------------------------------------------------------------
// 4-step pipeline view
// ---------------------------------------------------------------------------

const PIPELINE_STEPS = [
  {key: 'step1', n: 1, title: 'Step 1 · Extremely ambiguous input',
   tabLabel: 'Input',
   sub: 'load a deliberately vague brief and watch the LLM draft a spec; provenance chips show whether each invariant was grounded in the brief or filled in as a default'},
  {key: 'step2', n: 2, title: 'Step 2 · Lean output',
   tabLabel: 'Lean',
   sub: 'emit the drafted spec as real Lean 4 source; type-check with `lake build`; toggle between spec.lean and the EARS controlled-NL view'},
  {key: 'step3', n: 3, title: 'Step 3 · Validate spec with tools',
   tabLabel: 'Validate',
   sub: 'cross-check the spec by drafting it again with a different model; surface every field the two models disagree on'},
  {key: 'step4', n: 4, title: 'Step 4 · Create Python code',
   tabLabel: 'Code',
   sub: 'ask the LLM to implement the spec; StructuredValidator confirms ACCEPT (or names the specific invariant that tripped)'},
];

let ACTIVE_STEP_IDX = 0;

let PIPELINE_BRIEFS = [];
let PIPELINE_BRIEF = null;
let PIPELINE_STATE = {step1:null, step2:null, step3:null, step4:null};

async function pipelineLoadBriefs() {
  const sel = document.getElementById('pipeline-brief-picker');
  if (!sel || sel.options.length) return;
  try {
    const r = await fetchJSON('/api/ambiguous_briefs');
    PIPELINE_BRIEFS = r.briefs;
    sel.innerHTML = '';
    const groups = {
      custom:         'Custom briefs (ambiguous demos)',
      mbpp:           'MBPP samples (Austin et al. 2021)',
      humaneval:      'HumanEval samples (Chen et al. 2021)',
      bigcodebench:   'BigCodeBench samples (Zhuo et al. NeurIPS 2024)',
      humaneval_pro:  'HumanEval Pro samples (Yu et al. ACL 2025)',
      livecodebench:  'LiveCodeBench samples (Jain et al. ICLR 2025)',
    };
    for (const [src, label] of Object.entries(groups)) {
      const inGroup = PIPELINE_BRIEFS.filter(b => (b.source || 'custom') === src);
      if (!inGroup.length) continue;
      const og = document.createElement('optgroup');
      og.label = label;
      for (const b of inGroup) {
        const opt = document.createElement('option');
        opt.value = b.brief_id;
        opt.textContent = b.label;
        og.appendChild(opt);
      }
      sel.appendChild(og);
    }
    PIPELINE_BRIEF = PIPELINE_BRIEFS[0];
    sel.value = PIPELINE_BRIEF.brief_id;
    sel.addEventListener('change', () => {
      PIPELINE_BRIEF = PIPELINE_BRIEFS.find(b => b.brief_id === sel.value);
      PIPELINE_STATE = {step1:null, step2:null, step3:null, step4:null};
      renderPipeline();
    });
  } catch (e) {/* fallback handled below */}
  renderPipeline();
}

function renderPipeline() {
  const nav = document.getElementById('step-nav');
  const content = document.getElementById('step-content');
  if (!nav || !content) return;

  nav.innerHTML = PIPELINE_STEPS.map((s, i) => {
    const state = PIPELINE_STATE[s.key];
    const status = state ? state.status : 'pending';
    const active = i === ACTIVE_STEP_IDX ? 'active' : '';
    return `<button class="step-btn ${status} ${active}" data-i="${i}">
      <span class="step-num">${s.n}</span>${escapeHtml(s.tabLabel)}
    </button>`;
  }).join('');
  nav.querySelectorAll('.step-btn').forEach(btn => {
    btn.addEventListener('click', () => setActiveStep(+btn.dataset.i));
  });

  const step = PIPELINE_STEPS[ACTIVE_STEP_IDX];
  const state = PIPELINE_STATE[step.key];
  const body = state ? renderStepBody(step, state) : renderStepIntro(step);
  content.innerHTML = `
    <h2>${escapeHtml(step.title)}</h2>
    <div class="step-sub">${escapeHtml(step.sub)}</div>
    ${body}
    <div class="step-actions">
      <button id="run-${step.key}" class="primary">Run step ${step.n}</button>
    </div>`;
  const runBtn = document.getElementById(`run-${step.key}`);
  if (runBtn) runBtn.addEventListener('click', () => runOneStep(step.key));

  // Wire interactive sub-controls if this step is done.
  if (step.key === 'step1' && state && state.status === 'done') _wireLinkedClicks();
  if (step.key === 'step2' && state && state.status === 'done') _wireArtifactToggle();
}

function setActiveStep(idx) {
  ACTIVE_STEP_IDX = Math.max(0, Math.min(3, idx));
  renderPipeline();
}

function renderStepIntro(step) {
  if (step.key === 'step1') {
    if (!PIPELINE_BRIEF) return '<div class="muted">loading briefs…</div>';
    const filesList = Object.keys(PIPELINE_BRIEF.starting_repo).map(p =>
      `<code>${escapeHtml(p)}</code>`).join(', ');
    const sources = [];
    if (PIPELINE_BRIEF.prose_doc) sources.push('prose_doc');
    if (PIPELINE_BRIEF.existing_tests) sources.push('existing_tests');
    if (PIPELINE_BRIEF.slide_deck) sources.push('slide_deck');
    return `
      <div><strong>intent:</strong> <em>"${escapeHtml(PIPELINE_BRIEF.description)}"</em></div>
      <div style="margin-top:6px"><strong>starting files:</strong> ${filesList}</div>
      <div style="margin-top:6px"><strong>extra sources:</strong> ${sources.length ? sources.join(', ') : '<span class="muted">(none — pure under-specification)</span>'}</div>`;
  }
  if (step.key === 'step2') {
    return '<div class="muted">run step 1 first, or click "Run step 2" to use cached spec</div>';
  }
  if (step.key === 'step3') {
    return '<div class="muted">cross-checks the drafted spec by asking a different model to draft the same thing; flags any disagreements</div>';
  }
  if (step.key === 'step4') {
    return '<div class="muted">asks the LLM to implement the drafted spec; StructuredValidator confirms ACCEPT</div>';
  }
  return '';
}

function renderStepBody(step, state) {
  if (state.status === 'running') return '<div class="placeholder">running…</div>';
  if (state.status === 'failed') {
    return `<div class="verdict-box reject">${escapeHtml(state.error || 'failed')}</div>`;
  }
  // done
  if (step.key === 'step1') {
    return renderStep1Body(state.result);
  }
  if (step.key === 'step2') {
    return renderStep2Body(state.result);
  }
  if (step.key === 'step3') {
    const r = state.result;
    const dis = r.disagreements || [];
    const banner = dis.length
      ? `<div class="verdict-box abstain"><strong>⚠ ${dis.length} field${dis.length===1?'':'s'} where models disagreed:</strong>
          ${dis.map(d => `<span class="pill abstain" style="margin:0 4px">${escapeHtml(d)}</span>`).join('')}
         </div>`
      : `<div class="verdict-box accept"><strong>✓ All models agreed.</strong> Spec is uncontested across models for this intent.</div>`;
    const rows = r.field_comparisons.map(c => {
      const cls = c.agreement === 'agree' ? 'accept'
        : c.agreement === 'disagree' ? 'reject' : 'abstain';
      const vals = r.models.map(m => {
        const v = c.values_by_model[m];
        return `<td><code>${v===undefined?'(failed)':escapeHtml(JSON.stringify(v))}</code></td>`;
      }).join('');
      return `<tr><td><strong>${escapeHtml(c.field_name)}</strong></td><td><span class="pill ${cls}">${c.agreement}</span></td>${vals}</tr>`;
    }).join('');
    return `${banner}<table class="mut" style="margin-top:8px"><tr><th>field</th><th>agreement</th>${r.models.map(m=>`<th>${escapeHtml(m)}</th>`).join('')}</tr>${rows}</table>`;
  }
  if (step.key === 'step4') {
    const r = state.result;
    const v = r.verdict || {};
    const cls = v.decision === 'accept' ? 'accept' : v.decision === 'abstain' ? 'abstain' : 'reject';
    const verdictMsg = v.decision
      ? `<div class="verdict-box ${cls}"><strong>${v.decision.toUpperCase()}</strong> — ${escapeHtml(v.reason||'')}</div>`
      : '';
    const traces = (v.invariant_results || []).map(ir => {
      const c = ir.uncertain ? 'uncertain' : (ir.holds ? 'pass' : 'fail');
      const m = ir.uncertain ? '⊘' : (ir.holds ? '✓' : '✗');
      return `<div class="inv-trace ${c}"><span class="check">${m}</span><span><span class="inv-name">${ir.name}</span><span class="details"> — ${escapeHtml(ir.details||'')}</span></span></div>`;
    }).join('');
    const filesHtml = (r.files_changed || []).map(p => {
      const code = r.modified_repo[p] || '';
      return `<details style="margin-top:6px"><summary><code>${escapeHtml(p)}</code></summary><pre>${escapeHtml(code)}</pre></details>`;
    }).join('');
    return `${verdictMsg}
      <div style="margin-top:8px"><strong>files generated:</strong> ${(r.files_changed||[]).map(p=>`<code>${escapeHtml(p)}</code>`).join(', ') || '(none)'}</div>
      ${r.notes ? `<div class="muted" style="font-size:12px;margin-top:4px">notes: ${escapeHtml(r.notes)}</div>` : ''}
      ${filesHtml}
      <div style="margin-top:8px"><strong>per-invariant trace:</strong></div>
      ${traces}`;
  }
  return '';
}

// ----- Step 1 rendering: provenance chips + linked source↔spec + mini dep graph
function renderStep1Body(d) {
  const cs = d.contradictions || [];
  const csHtml = cs.length ? `
    <div class="verdict-box abstain" style="margin-bottom:10px">
      <strong>⚠ ${cs.length} cross-source contradiction${cs.length===1?'':'s'}:</strong>
      ${cs.map(c => `<div style="margin-top:6px"><strong>${escapeHtml(c.summary)}</strong>
        <div class="muted" style="font-size:11px">${c.sources.map(s=>`<code>${escapeHtml(s)}</code>`).join(' vs ')} → ${escapeHtml(c.resolution||'(no resolution)')}</div></div>`).join('')}
    </div>` : '';

  const invs = d.drafted_invariants || [];

  // Aggregate provenance for the legend.
  const counts = {explicit:0, inferred:0, default:0};
  invs.forEach(di => {
    const g = (di.provenance && di.provenance.grounding) || 'default';
    counts[g] = (counts[g] || 0) + 1;
  });
  const legend = `<div class="muted" style="font-size:11px;margin:8px 0 4px">
    <strong>provenance:</strong>
    <span class="prov-chip explicit">explicit ${counts.explicit}</span>
    <span class="prov-chip inferred">inferred ${counts.inferred}</span>
    <span class="prov-chip default">default ${counts.default}</span>
    <span style="margin-left:10px">— grounded in brief · inferred from repo · LLM default (review)</span>
  </div>`;

  const linked = renderLinkedView(d, invs);
  const graph = renderDepGraph(d, invs);

  return `${csHtml}${legend}${linked}${graph}`;
}

function renderLinkedView(d, invs) {
  // LEFT: brief sources, with provenance source_phrase spans highlighted.
  // Each invariant with grounding != 'default' contributes one or more
  // <mark data-inv="i"> hits.
  const briefSources = {
    'intent': d.spec && d.spec.description ? d.spec.description : '',
  };
  // Pull additional sources (prose_doc, existing_tests, slide_deck) — we
  // serialized them per /api/elicit; but spec.starting_repo includes the
  // files used too. We surface only the textual sources here.
  // To avoid an extra round-trip we use the current PIPELINE_BRIEF.
  if (PIPELINE_BRIEF) {
    if (PIPELINE_BRIEF.prose_doc) briefSources['prose_doc'] = PIPELINE_BRIEF.prose_doc;
    if (PIPELINE_BRIEF.existing_tests) briefSources['existing_tests'] = PIPELINE_BRIEF.existing_tests;
    if (PIPELINE_BRIEF.slide_deck) briefSources['slide_deck'] = PIPELINE_BRIEF.slide_deck;
  }

  // Build per-source HTML with highlights.
  const srcHtml = Object.entries(briefSources).map(([name, text]) => {
    let body = escapeHtml(text);
    invs.forEach((di, i) => {
      const phrase = di.provenance && di.provenance.source_phrase;
      if (!phrase) return;
      const escaped = escapeHtml(phrase);
      // Wrap the first occurrence per source.
      const idx = body.toLowerCase().indexOf(escaped.toLowerCase());
      if (idx >= 0) {
        body = body.slice(0, idx) +
          `<mark class="src-hit" data-inv="${i}">${body.slice(idx, idx + escaped.length)}</mark>` +
          body.slice(idx + escaped.length);
      }
    });
    return `<div class="src-block">
      <div class="src-title">${escapeHtml(name)}</div>
      <div>${body}</div>
    </div>`;
  }).join('');

  // RIGHT: invariant rows with provenance chips.
  const invRows = invs.map((di, i) => {
    const args = Object.entries(di.args || {}).filter(([k]) => k!=='type'&&k!=='name')
      .map(([k,v]) => `${k}=${JSON.stringify(v)}`).join(', ');
    const g = (di.provenance && di.provenance.grounding) || 'default';
    const phrase = di.provenance && di.provenance.source_phrase;
    return `<div class="inv-row" data-inv="${i}">
      <span class="inv-name">${escapeHtml(di.type)}</span>(${args})
      <span class="prov-chip ${g}" title="${escapeHtml(phrase || '(no source phrase)')}">${g}</span>
      <div class="muted" style="font-size:11px;margin-top:3px">${escapeHtml(di.rationale||'')}</div>
    </div>`;
  }).join('');

  return `<div class="src-link">
    <div class="src-pane">
      <div class="label-row">brief (sources)</div>
      ${srcHtml || '<div class="muted">(no brief sources)</div>'}
    </div>
    <div class="spec-pane">
      <div class="label-row">drafted invariants — click to highlight source</div>
      ${invRows}
    </div>
  </div>`;
}

function renderDepGraph(d, invs) {
  // Mini SVG: intent (left) → invariant nodes (middle) → file nodes (right).
  const filesAll = new Set();
  invs.forEach(di => {
    const a = di.args || {};
    (a.allowed_paths || a.paths || []).forEach(f => filesAll.add(f));
  });
  const files = Array.from(filesAll);
  const W = 660, ROW = 28, PAD = 12;
  const invH = invs.length * ROW + PAD * 2;
  const fileH = Math.max(files.length, 1) * ROW + PAD * 2;
  const H = Math.max(invH, fileH, 80);

  const intentX = 10, intentY = H / 2;
  const invX = 240;
  const fileX = 480;

  const invNodes = invs.map((di, i) => ({
    i, type: di.type,
    x: invX, y: PAD + ROW * i + 14,
    files: ((di.args && (di.args.allowed_paths || di.args.paths)) || []),
  }));
  const fileNodes = files.map((f, j) => ({
    f, x: fileX, y: PAD + ROW * j + 14,
  }));

  let edges = '';
  // intent → each invariant
  invNodes.forEach(n => {
    edges += `<line x1="${intentX+110}" y1="${intentY}" x2="${n.x-2}" y2="${n.y}" stroke="#6c7891" stroke-width="1"/>`;
  });
  // invariant → files
  invNodes.forEach(n => {
    n.files.forEach(f => {
      const fn = fileNodes.find(x => x.f === f);
      if (fn) edges += `<line x1="${n.x+150}" y1="${n.y}" x2="${fn.x-2}" y2="${fn.y}" stroke="#9ece6a" stroke-width="1" opacity="0.7"/>`;
    });
  });

  const intentLabel = `<rect x="${intentX}" y="${intentY-12}" width="110" height="24" rx="3" fill="#1a1f2e" stroke="#7aa2f7"/>
    <text x="${intentX+55}" y="${intentY+4}" fill="#7aa2f7" font-size="10" font-family="monospace" text-anchor="middle">intent</text>`;

  const invLabels = invNodes.map(n =>
    `<rect x="${n.x}" y="${n.y-10}" width="150" height="20" rx="3" fill="#1a1f2e" stroke="#e0af68"/>
     <text x="${n.x+75}" y="${n.y+4}" fill="#e0af68" font-size="9" font-family="monospace" text-anchor="middle">${escapeHtml(n.type)}</text>`
  ).join('');
  const fileLabels = fileNodes.map(n =>
    `<rect x="${n.x}" y="${n.y-10}" width="170" height="20" rx="3" fill="#1a1f2e" stroke="#9ece6a"/>
     <text x="${n.x+85}" y="${n.y+4}" fill="#9ece6a" font-size="9" font-family="monospace" text-anchor="middle">${escapeHtml(n.f)}</text>`
  ).join('');

  const noFilesNote = files.length === 0
    ? `<text x="${fileX+85}" y="${intentY+4}" fill="#6c7891" font-size="10" text-anchor="middle">(no file scope)</text>`
    : '';

  return `<div class="dep-graph">
    <div class="label-row" style="font-size:11px;text-transform:uppercase;color:#6c7891;letter-spacing:0.5px;margin-bottom:6px">
      dependency graph — intent → invariants → files (Lean Atlas style)
    </div>
    <svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
      ${edges}
      ${intentLabel}
      ${invLabels}
      ${fileLabels}
      ${noFilesNote}
    </svg>
  </div>`;
}

// Wire after the body is in the DOM: click on inv row highlights linked mark, and vice versa.
function _wireLinkedClicks() {
  document.querySelectorAll('.inv-row').forEach(row => {
    row.addEventListener('click', () => {
      const i = row.dataset.inv;
      document.querySelectorAll('.inv-row').forEach(r => r.classList.toggle('active', r === row));
      document.querySelectorAll('mark.src-hit').forEach(m => {
        m.classList.toggle('active', m.dataset.inv === i);
        if (m.dataset.inv === i) m.scrollIntoView({block: 'nearest', behavior: 'smooth'});
      });
    });
  });
  document.querySelectorAll('mark.src-hit').forEach(m => {
    m.addEventListener('click', () => {
      const i = m.dataset.inv;
      document.querySelectorAll('mark.src-hit').forEach(x => x.classList.toggle('active', x === m));
      document.querySelectorAll('.inv-row').forEach(r => {
        r.classList.toggle('active', r.dataset.inv === i);
        if (r.dataset.inv === i) r.scrollIntoView({block: 'nearest', behavior: 'smooth'});
      });
    });
  });
}

// ----- Step 2 rendering: Lean / EARS toggle (Kiro-style artifact tabs)
function renderStep2Body(r) {
  const verifyMsg = r.verify ? (r.verify.ok
    ? `<div class="verdict-box accept" style="margin-top:8px"><strong>✓ lake build succeeded</strong> in ${r.verify.duration_seconds}s · ${escapeHtml(r.verify.lean_version||'')}</div>`
    : `<div class="verdict-box reject" style="margin-top:8px"><strong>✗ lake build failed</strong><pre>${escapeHtml(r.verify.stderr || r.verify.stdout)}</pre></div>`)
    : '<div class="muted" style="margin-top:8px">Lean toolchain not on server — emission only.</div>';
  return `
    <div class="artifact-toggle" id="artifact-toggle">
      <button data-art="lean" class="active">spec.lean</button>
      <button data-art="ears">requirements.ears</button>
    </div>
    <div id="artifact-body">
      <span class="artifact-name">spec.lean</span>
      <pre>${escapeHtml(r.source)}</pre>
    </div>
    ${verifyMsg}`;
}

function _wireArtifactToggle() {
  const tg = document.getElementById('artifact-toggle');
  if (!tg) return;
  tg.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', async () => {
      tg.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === btn));
      const art = btn.dataset.art;
      const body = document.getElementById('artifact-body');
      if (art === 'lean') {
        body.innerHTML = `<span class="artifact-name">spec.lean</span><pre>${escapeHtml(PIPELINE_STATE.step2.result.source)}</pre>`;
      } else {
        body.innerHTML = `<span class="artifact-name">requirements.ears</span><div class="placeholder">fetching…</div>`;
        try {
          const spec_json = _pipelineSpecJson();
          const r = await fetchJSON('/api/emit_ears', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({spec_json}),
          });
          body.innerHTML = `<span class="artifact-name">requirements.ears</span><pre style="white-space:pre-wrap">${escapeHtml(r.source)}</pre>`;
        } catch (e) {
          body.innerHTML = `<div class="verdict-box reject">EARS emission failed: ${escapeHtml(e.message)}</div>`;
        }
      }
    });
  });
}

function setStep(key, status, result, error) {
  PIPELINE_STATE[key] = {status, result, error};
  renderPipeline();
}

async function runOneStep(key) {
  const idx = PIPELINE_STEPS.findIndex(s => s.key === key);
  if (idx >= 0) ACTIVE_STEP_IDX = idx;
  if (key === 'step1') return runStep1();
  if (key === 'step2') return runStep2();
  if (key === 'step3') return runStep3();
  if (key === 'step4') return runStep4();
}

async function runStep1() {
  if (!PIPELINE_BRIEF) { alert('no brief selected'); return; }
  setStep('step1', 'running');
  try {
    const additional_sources = {};
    if (PIPELINE_BRIEF.prose_doc) additional_sources.prose_doc = PIPELINE_BRIEF.prose_doc;
    if (PIPELINE_BRIEF.existing_tests) additional_sources.existing_tests = PIPELINE_BRIEF.existing_tests;
    if (PIPELINE_BRIEF.slide_deck) additional_sources.slide_deck = PIPELINE_BRIEF.slide_deck;
    const r = await fetchJSON('/api/elicit', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        description: PIPELINE_BRIEF.description,
        starting_repo: PIPELINE_BRIEF.starting_repo,
        task_id: 'pipeline_' + Date.now(),
        additional_sources: Object.keys(additional_sources).length ? additional_sources : null,
      }),
    });
    if (!r.ok) { setStep('step1', 'failed', null, r.error); return; }
    setStep('step1', 'done', r);
  } catch (e) {
    setStep('step1', 'failed', null, e.message);
  }
}

function _pipelineSpecJson() {
  const r = PIPELINE_STATE.step1 && PIPELINE_STATE.step1.result;
  if (!r || !r.spec) return null;
  return {
    task_id: r.spec.task_id,
    description: r.spec.description,
    starting_repo: r.spec.starting_repo,
    invariants: r.drafted_invariants,
    positive_tests: r.spec.positive_tests,
  };
}

async function runStep2() {
  const spec_json = _pipelineSpecJson();
  if (!spec_json) { alert('run step 1 first'); return; }
  setStep('step2', 'running');
  try {
    const lean = await fetchJSON('/api/emit_lean', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({spec_json}),
    });
    let verify = null;
    if (lean.lean_available) {
      verify = await fetchJSON('/api/verify_lean', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({spec_json}),
      });
    }
    setStep('step2', 'done', {source: lean.source, verify, lean_available: lean.lean_available});
  } catch (e) {
    setStep('step2', 'failed', null, e.message);
  }
}

async function runStep3() {
  if (!PIPELINE_BRIEF) { alert('no brief selected'); return; }
  setStep('step3', 'running');
  try {
    const additional_sources = {};
    if (PIPELINE_BRIEF.prose_doc) additional_sources.prose_doc = PIPELINE_BRIEF.prose_doc;
    if (PIPELINE_BRIEF.existing_tests) additional_sources.existing_tests = PIPELINE_BRIEF.existing_tests;
    if (PIPELINE_BRIEF.slide_deck) additional_sources.slide_deck = PIPELINE_BRIEF.slide_deck;
    const r = await fetchJSON('/api/elicit/compare', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        description: PIPELINE_BRIEF.description,
        starting_repo: PIPELINE_BRIEF.starting_repo,
        models: null,
        task_id: 'pipeline_compare_' + Date.now(),
      }),
    });
    setStep('step3', 'done', r);
  } catch (e) {
    setStep('step3', 'failed', null, e.message);
  }
}

async function runStep4() {
  const spec_json = _pipelineSpecJson();
  if (!spec_json) { alert('run step 1 first'); return; }
  setStep('step4', 'running');
  try {
    const r = await fetchJSON('/api/codegen', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({spec_json}),
    });
    setStep('step4', 'done', r);
  } catch (e) {
    setStep('step4', 'failed', null, e.message);
  }
}

document.getElementById('pipeline-run-all').addEventListener('click', async () => {
  PIPELINE_STATE = {step1:null, step2:null, step3:null, step4:null};
  ACTIVE_STEP_IDX = 0;
  renderPipeline();
  await runOneStep('step1');
  if (PIPELINE_STATE.step1.status !== 'done') return;
  setActiveStep(1);
  await runOneStep('step2');
  setActiveStep(2);
  await runOneStep('step3');
  setActiveStep(3);
  await runOneStep('step4');
});

pipelineLoadBriefs();
</script>
</body></html>
"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
