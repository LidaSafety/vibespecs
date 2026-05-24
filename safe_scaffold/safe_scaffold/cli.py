"""Command-line interface for safe_scaffold.

Subcommands:

    safe-scaffold init-policy [--output FILE]
        Write the safe_default policy to FILE (default: ./policy.json).

    safe-scaffold check --policy FILE --action JSON
        Verify a single action against a policy. Exit 0=ALLOW, 1=DENY, 2=UNKNOWN.

    safe-scaffold hook --policy FILE [--journal FILE] [--interactive]
        Run as a Claude Code PreToolUse hook. Reads JSON payload on stdin,
        writes a decision JSON on stdout, exits 0/1.

    safe-scaffold prove --policy FILE --pattern NAME
        Run a Z3-backed universal-property proof. Requires the `smt` extra.

    safe-scaffold eval [--policy FILE]
        Run the built-in eval corpus against the policy. Prints metrics.

    safe-scaffold cross-check --demo cryspen
        Run the Cryspen libcrux decompress_d cross-check demo.

    safe-scaffold task-eval [--dashboard PATH] [--no-llm]
        Run the 10-task task-spec eval and print confusion matrices for
        the structured validator, positive-only baseline, and LLM-judge
        baseline. With --dashboard, also writes a self-contained HTML
        visualization to PATH.

All subcommands return non-zero on failure. Designed to compose with shell
pipes; output is JSON when `--json` is passed, human-readable otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from safe_scaffold import __version__
from safe_scaffold.adapters import parse_claude_code_hook_payload
from safe_scaffold.policy import Policy, safe_default_policy
from safe_scaffold.verifier import Decision, verify
from safe_scaffold.world import Action


def _cmd_init_policy(args: argparse.Namespace) -> int:
    out = Path(args.output)
    if out.exists() and not args.force:
        sys.stderr.write(
            f"{out} exists. Pass --force to overwrite.\n"
        )
        return 1
    safe_default_policy().save(out)
    print(f"Wrote default policy to {out}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    policy = Policy.load(args.policy)
    action_data = json.loads(args.action) if isinstance(args.action, str) else args.action
    action = Action.from_dict(action_data)
    verdict = verify(action, policy)
    if args.json:
        print(json.dumps({
            "decision": verdict.decision.value,
            "matched_rule_ids": [r.id for r in verdict.matched_rules],
            "explanation": verdict.explain(),
        }, indent=2))
    else:
        print(verdict.explain())
    return {
        Decision.ALLOW: 0,
        Decision.DENY: 1,
        Decision.UNKNOWN: 2,
    }[verdict.decision]


def _cmd_hook(args: argparse.Namespace) -> int:
    policy = Policy.load(args.policy)
    payload = json.loads(sys.stdin.read())
    try:
        action = parse_claude_code_hook_payload(payload)
    except Exception as exc:
        json.dump(
            {"action": "block", "reason": f"adapter error: {exc}"},
            sys.stdout,
        )
        return 1
    verdict = verify(action, policy)
    # Fail closed on UNKNOWN — the policy has no opinion, and the runtime
    # gate must not invent one. Operators extend the policy out-of-band
    # if a blocked action turns out to be benign.
    decision_to_hook = {
        Decision.ALLOW: ("approve", "policy ALLOW"),
        Decision.DENY: ("block", "policy DENY"),
        Decision.UNKNOWN: ("block", "no policy rule matched (fail-closed)"),
    }
    name, reason = decision_to_hook[verdict.decision]
    json.dump({"action": name, "reason": reason, "explanation": verdict.explain()}, sys.stdout)
    return 0 if verdict.decision is Decision.ALLOW else 1


def _cmd_prove(args: argparse.Namespace) -> int:
    try:
        from safe_scaffold.properties import (
            Z3_AVAILABLE,
            pattern_credential_env_read,
            pattern_network_exfil,
            pattern_rm_recursive_anywhere,
            pattern_write_under,
            policy_never_allows,
        )
    except Exception as exc:
        print(f"failed to import properties module: {exc}", file=sys.stderr)
        return 1

    if not Z3_AVAILABLE:
        print(
            "z3-solver is not installed. Install with "
            "`pip install safe-scaffold[smt]` to run universal-property proofs.",
            file=sys.stderr,
        )
        return 1

    policy = Policy.load(args.policy)
    patterns = {
        "rm_recursive": (pattern_rm_recursive_anywhere(), "policy never allows recursive rm"),
        "write_etc": (pattern_write_under("/etc"), "policy never allows writes under /etc"),
        "exfil": (
            pattern_network_exfil(["attacker.example", "evil.example"]),
            "policy never allows network requests to listed evil hosts",
        ),
        "credential_env": (pattern_credential_env_read(), "policy never allows credential env reads"),
    }
    if args.pattern not in patterns:
        print(
            f"unknown pattern {args.pattern!r}. Choices: {sorted(patterns)}",
            file=sys.stderr,
        )
        return 1
    pat, desc = patterns[args.pattern]
    result = policy_never_allows(policy, pat, desc)
    print(str(result))
    return 0 if result.holds else 2


def _cmd_eval(args: argparse.Namespace) -> int:
    from safe_scaffold.eval import BENIGN_CORPUS, REDTEAM_CORPUS, run_eval

    policy = Policy.load(args.policy) if args.policy else safe_default_policy()
    metrics = run_eval(policy, REDTEAM_CORPUS, BENIGN_CORPUS)
    if args.json:
        print(json.dumps({
            "block_rate": metrics.block_rate,
            "false_allow_rate": metrics.false_allow_rate,
            "false_deny_rate": metrics.false_deny_rate,
            "redteam_total": metrics.redteam_total,
            "redteam_denied": metrics.redteam_denied,
            "redteam_allowed": metrics.redteam_allowed,
            "redteam_unknown": metrics.redteam_unknown,
            "benign_total": metrics.benign_total,
            "benign_allowed": metrics.benign_allowed,
            "benign_denied": metrics.benign_denied,
            "benign_unknown": metrics.benign_unknown,
        }, indent=2))
    else:
        print(metrics.report())
    return 0 if metrics.false_allow_rate == 0.0 else 1


def _cmd_cross_check(args: argparse.Namespace) -> int:
    if args.demo != "cryspen":
        print(f"unknown demo {args.demo!r}", file=sys.stderr)
        return 1
    from safe_scaffold.cross_check.fixtures import cryspen_decompress_d_demo

    report = cryspen_decompress_d_demo()
    print(report.summary())
    # A report with disagreements is the EXPECTED outcome of this demo (the
    # bug is real); we exit 0 because the cross-check executed cleanly.
    return 0


def _cmd_task_eval(args: argparse.Namespace) -> int:
    """Run the task-spec eval across the 10-task (or 15-task extended) corpus."""
    from safe_scaffold.task_spec.baselines import (
        LLMJudge,
        PositiveTestsOnly,
        StructuredValidator,
    )
    from safe_scaffold.task_spec.eval import print_summary, run_eval

    evaluators = [StructuredValidator(), PositiveTestsOnly()]
    if not args.no_llm:
        evaluators.append(LLMJudge())
    if args.rigorous:
        # Stronger LLM baselines: nl2postcond-style + PRDJudge-style.
        from safe_scaffold.task_spec.strong_baselines import (
            NL2PostcondJudge, PRDStyleJudge,
        )
        evaluators.extend([NL2PostcondJudge(), PRDStyleJudge()])

    corpus = None
    if args.extended:
        from safe_scaffold.task_spec.corpus_data import EXTENDED_CORPUS
        corpus = list(EXTENDED_CORPUS)

    run = run_eval(evaluators=evaluators, corpus=corpus, verbose=False)
    print_summary(run)

    if args.rigorous:
        from safe_scaffold.task_spec.metrics import print_rigorous_summary
        print()
        print_rigorous_summary(run)

    if args.ablation:
        from safe_scaffold.task_spec.ablation import (
            print_ablation_summary, run_ablation,
        )
        print()
        ablation = run_ablation(verbose=False)
        print_ablation_summary(ablation)

    if args.dashboard:
        from examples.viz_eval_dashboard import render_dashboard
        out = render_dashboard(run, path=args.dashboard)
        print(f"\nDashboard written to {out}")

    return 0


def _cmd_elicit(args: argparse.Namespace) -> int:
    """Draft a TaskSpec from an NL intent + a directory of starting files."""
    import json as _json
    from pathlib import Path
    from safe_scaffold.task_spec.elicitation import draft_spec

    repo_root = Path(args.repo)
    if not repo_root.is_dir():
        print(f"error: --repo must be a directory: {repo_root}")
        return 2
    starting_repo: dict[str, str] = {}
    for path in sorted(repo_root.rglob("*")):
        if path.is_file() and not any(p.startswith(".") for p in path.relative_to(repo_root).parts):
            try:
                starting_repo[str(path.relative_to(repo_root))] = path.read_text()
            except UnicodeDecodeError:
                pass  # skip binary files
    if not starting_repo:
        print(f"error: no readable text files under {repo_root}")
        return 2

    draft = draft_spec(args.intent, starting_repo, task_id=args.task_id)

    if args.json:
        out: dict = {"ok": draft.ok, "error": draft.error}
        if draft.spec:
            out["invariants"] = [
                {"type": type(d.invariant).__name__, "rationale": d.rationale}
                for d in draft.drafted_invariants
            ]
            pt = draft.spec.positive_tests[0]
            out["positive_test"] = {"path": pt.path, "name": pt.name, "code": pt.code}
        print(_json.dumps(out, indent=2))
        return 0 if draft.ok else 1

    if not draft.ok:
        print(f"DRAFT FAILED: {draft.error}")
        if draft.raw_response:
            print("\n--- raw LLM response ---")
            print(draft.raw_response)
        return 1

    print(f"Drafted spec for task_id={args.task_id!r}\n")
    print("Invariants:")
    for d in draft.drafted_invariants:
        print(f"  {type(d.invariant).__name__}")
        print(f"    why: {d.rationale}")
    pt = draft.spec.positive_tests[0]
    print(f"\nPositive test ({pt.path}):")
    print(f"  why: {draft.positive_test_rationale}")
    print()
    for line in pt.code.splitlines():
        print(f"  {line}")
    return 0


def _cmd_mutate(args: argparse.Namespace) -> int:
    """Run the spec-mutation harness on one task or the whole corpus."""
    import json as _json
    from safe_scaffold.task_spec.corpus_data import EXTENDED_CORPUS
    from safe_scaffold.task_spec.spec_mutation import (
        result_to_dict, run_mutation_analysis, summarize, summary_to_dict,
    )

    if args.task_id:
        match = [(s, c) for s, c in EXTENDED_CORPUS if s.task_id == args.task_id]
        if not match:
            print(f"error: unknown --task-id {args.task_id!r}")
            print(f"available: {[s.task_id for s, _ in EXTENDED_CORPUS]}")
            return 2
        spec, candidates = match[0]
        per_spec = {spec.task_id: run_mutation_analysis(spec, candidates)}
    else:
        per_spec = {s.task_id: run_mutation_analysis(s, c) for s, c in EXTENDED_CORPUS}

    summary = summarize(per_spec)

    if args.json:
        out = {
            "summary": summary_to_dict(summary),
            "per_spec": {tid: [result_to_dict(r) for r in rs]
                         for tid, rs in per_spec.items()},
        }
        print(_json.dumps(out, indent=2))
        return 0

    print(f"Spec mutation report — {len(per_spec)} spec(s), "
          f"{summary.total_mutations} mutations")
    print(f"  load_bearing: {summary.load_bearing} "
          f"({100 * summary.fraction_load_bearing():.1f}%)")
    print(f"  brittle:      {summary.brittle}")
    print(f"  invisible:    {summary.invisible}")
    print()
    print(f"  {'kind':<18}{'load_bearing':>14}{'brittle':>10}{'invisible':>12}")
    for kind, v in summary.by_kind.items():
        print(f"  {kind:<18}{v.get('load_bearing',0):>14}"
              f"{v.get('brittle',0):>10}{v.get('invisible',0):>12}")

    if args.verbose:
        print()
        for tid, rs in per_spec.items():
            print(f"=== {tid} ===")
            for r in rs:
                marker = {"load_bearing": "!!", "brittle": "??", "invisible": "  "}[r.classification]
                print(f"  {marker} {r.classification:<14}{r.mutation.kind:<16}"
                      f"{r.mutation.target}: {r.mutation.description}")
                if r.newly_accepted:
                    print(f"        + newly accepted: {r.newly_accepted}")
                if r.newly_rejected:
                    print(f"        - newly rejected: {r.newly_rejected}")
    return 0


def _cmd_dataset_run(args: argparse.Namespace) -> int:
    """Run the 4-step pipeline on a small sample from MBPP / HumanEval.

    Per problem: elicit a spec, emit Lean (verify if toolchain present),
    cross-check across models (skipped if --no-compare to keep API calls
    down), generate code, validate. Print one row per problem with
    aggregate stats at the end.
    """
    import time as _time
    from safe_scaffold.task_spec.codegen import generate_code
    from safe_scaffold.task_spec.datasets import (
        _bigcodebench_records, _humaneval_pro_records, _humaneval_records,
        _livecodebench_records, _mbpp_records,
        bigcodebench_to_brief, humaneval_pro_to_brief, humaneval_to_brief,
        livecodebench_to_brief, mbpp_to_brief,
    )
    from safe_scaffold.task_spec.elicitation import compare_drafts, draft_spec
    from safe_scaffold.task_spec.lean_emitter import (
        emit_lean, lean_available, verify_lean,
    )

    if args.dataset == "mbpp":
        briefs = [mbpp_to_brief(r) for r in _mbpp_records()]
    elif args.dataset == "humaneval":
        briefs = [humaneval_to_brief(r) for r in _humaneval_records()]
    elif args.dataset == "bigcodebench":
        briefs = [bigcodebench_to_brief(r) for r in _bigcodebench_records()]
    elif args.dataset == "humaneval_pro":
        briefs = [humaneval_pro_to_brief(r) for r in _humaneval_pro_records()]
    elif args.dataset == "livecodebench":
        briefs = [livecodebench_to_brief(r) for r in _livecodebench_records()]
    else:  # both / all
        briefs = (
            [mbpp_to_brief(r) for r in _mbpp_records()]
            + [humaneval_to_brief(r) for r in _humaneval_records()]
            + [bigcodebench_to_brief(r) for r in _bigcodebench_records()]
            + [humaneval_pro_to_brief(r) for r in _humaneval_pro_records()]
            + [livecodebench_to_brief(r) for r in _livecodebench_records()]
        )

    briefs = briefs[: args.n] if args.n else briefs
    have_lean = lean_available()

    print(f"Running 4-step pipeline on {len(briefs)} problem(s) "
          f"from {args.dataset}. Lean toolchain: {'present' if have_lean else 'missing'}.\n")

    cols = ("id", "drafted", "lean_ok", "disagrees", "codegen")
    print(f"{cols[0]:<28} {cols[1]:<8} {cols[2]:<8} {cols[3]:<10} {cols[4]:<8}")
    print("-" * 64)

    n_drafted = n_lean_ok = n_codegen_ok = 0
    n_disagree_total = 0
    for b in briefs:
        row_start = _time.monotonic()
        extra = b.additional_sources()
        # Step 1: elicit. Pass override_positive_test through if the
        # brief carries one (e.g. LCB pre-authored tests from the
        # contest's official public_test_cases).
        draft = draft_spec(b.description, b.starting_repo,
                            task_id=b.brief_id,
                            additional_sources=extra or None,
                            override_positive_test=b.override_positive_test)
        drafted_ok = draft.ok
        n_drafted += 1 if drafted_ok else 0

        # Step 2: emit + verify Lean.
        lean_ok = None
        if drafted_ok and have_lean:
            src = emit_lean(draft.spec)
            r = verify_lean(src)
            lean_ok = r.ok
            n_lean_ok += 1 if lean_ok else 0

        # Step 3: cross-model comparison (optional, slow).
        n_disagree = None
        if drafted_ok and not args.no_compare:
            comp = compare_drafts(b.description, b.starting_repo,
                                   task_id=b.brief_id + "_cmp")
            n_disagree = len(comp.disagreements)
            n_disagree_total += n_disagree

        # Step 4: generate code, validate.
        codegen_ok = None
        if drafted_ok:
            res = generate_code(draft.spec)
            codegen_ok = res.ok
            n_codegen_ok += 1 if codegen_ok else 0

        def fmt(v):
            if v is None:
                return "n/a"
            if isinstance(v, bool):
                return "✓" if v else "✗"
            return str(v)

        elapsed = _time.monotonic() - row_start
        print(f"{b.brief_id:<28} {fmt(drafted_ok):<8} {fmt(lean_ok):<8} "
              f"{fmt(n_disagree):<10} {fmt(codegen_ok):<8} ({elapsed:.1f}s)")

    n = len(briefs)
    print()
    print(f"Aggregate ({n} problems):")
    print(f"  drafted ok:        {n_drafted}/{n}  ({100*n_drafted/n:.0f}%)" if n else "")
    if have_lean:
        print(f"  Lean type-checks:  {n_lean_ok}/{n_drafted}  ({100*n_lean_ok/max(n_drafted,1):.0f}% of drafted)")
    if not args.no_compare:
        print(f"  total disagreements across all problems: {n_disagree_total}")
    print(f"  codegen validates: {n_codegen_ok}/{n_drafted}  ({100*n_codegen_ok/max(n_drafted,1):.0f}% of drafted)")
    return 0


def _cmd_emit_lean(args: argparse.Namespace) -> int:
    """Emit a TaskSpec from the corpus as Lean 4 source; optionally verify."""
    from safe_scaffold.task_spec.corpus_data import EXTENDED_CORPUS
    from safe_scaffold.task_spec.lean_emitter import (
        emit_lean, lean_available, verify_lean,
    )

    match = [(s, c) for s, c in EXTENDED_CORPUS if s.task_id == args.task_id]
    if not match:
        print(f"error: unknown --task-id {args.task_id!r}")
        print(f"available: {[s.task_id for s, _ in EXTENDED_CORPUS]}")
        return 2

    spec, _ = match[0]
    source = emit_lean(spec)

    if args.output:
        from pathlib import Path
        out = Path(args.output)
        out.write_text(source, encoding="utf-8")
        print(f"Wrote Lean source to {out}")
    else:
        print(source)

    if args.verify:
        if not lean_available():
            print("\nERROR: --verify requested but Lean toolchain not found "
                  "(checked PATH and ~/.elan/bin)")
            return 3
        print("\nVerifying with `lake build`...")
        result = verify_lean(source)
        print(f"  ok:       {result.ok}")
        print(f"  duration: {result.duration_seconds:.2f}s")
        if result.lean_version:
            print(f"  lean:     {result.lean_version}")
        if result.stdout.strip():
            print(f"--- stdout ---\n{result.stdout.strip()}")
        if result.stderr.strip():
            print(f"--- stderr ---\n{result.stderr.strip()}")
        return 0 if result.ok else 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safe-scaffold",
        description="Formal action gating and spec validation for AI coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init-policy", help="Write the default policy to a file")
    p_init.add_argument("--output", "-o", default="policy.json")
    p_init.add_argument("--force", action="store_true", help="Overwrite if exists")
    p_init.set_defaults(func=_cmd_init_policy)

    p_check = sub.add_parser("check", help="Verify one action against a policy")
    p_check.add_argument("--policy", required=True)
    p_check.add_argument("--action", required=True, help="JSON action dict")
    p_check.add_argument("--json", action="store_true")
    p_check.set_defaults(func=_cmd_check)

    p_hook = sub.add_parser("hook", help="Run as a Claude Code PreToolUse hook")
    p_hook.add_argument("--policy", required=True)
    p_hook.set_defaults(func=_cmd_hook)

    p_prove = sub.add_parser("prove", help="Prove a universal safety property via Z3")
    p_prove.add_argument("--policy", required=True)
    p_prove.add_argument(
        "--pattern",
        required=True,
        choices=["rm_recursive", "write_etc", "exfil", "credential_env"],
    )
    p_prove.set_defaults(func=_cmd_prove)

    p_eval = sub.add_parser("eval", help="Evaluate policy against the built-in corpus")
    p_eval.add_argument("--policy", help="Path to a policy (default: safe_defaults)")
    p_eval.add_argument("--json", action="store_true")
    p_eval.set_defaults(func=_cmd_eval)

    p_cc = sub.add_parser("cross-check", help="Run a cross-check demo")
    p_cc.add_argument("--demo", required=True, choices=["cryspen"])
    p_cc.set_defaults(func=_cmd_cross_check)

    p_te = sub.add_parser(
        "task-eval",
        help="Run the 10-task task-spec eval (structured vs positive-only vs LLM-judge)",
    )
    p_te.add_argument(
        "--dashboard",
        metavar="PATH",
        help="Also write the HTML dashboard to PATH",
    )
    p_te.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the LLM-judge baseline (avoids API calls)",
    )
    p_te.add_argument(
        "--extended",
        action="store_true",
        help="Use the 15-task / 60-pair extended corpus (with mutation-generated tasks)",
    )
    p_te.add_argument(
        "--rigorous",
        action="store_true",
        help="Add nl2postcond-style + PRDJudge-style baselines AND print Cohen's kappa, discriminative power, per-invariant precision/recall",
    )
    p_te.add_argument(
        "--ablation",
        action="store_true",
        help="Also run the per-invariant ablation study",
    )
    p_te.set_defaults(func=_cmd_task_eval)

    p_el = sub.add_parser(
        "elicit",
        help="Draft a TaskSpec from an NL intent + a directory of files (needs ANTHROPIC_API_KEY)",
    )
    p_el.add_argument("--intent", required=True, help="One-sentence description of the task")
    p_el.add_argument("--repo", required=True, help="Directory whose files become the starting_repo")
    p_el.add_argument("--task-id", default="draft", help="Identifier for the drafted spec")
    p_el.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text")
    p_el.set_defaults(func=_cmd_elicit)

    p_mut = sub.add_parser(
        "mutate",
        help="Run the spec-mutation harness (Track 2: spec validation)",
    )
    p_mut.add_argument("--task-id", help="Mutate only this task (default: whole corpus)")
    p_mut.add_argument("--json", action="store_true", help="Emit JSON instead of summary text")
    p_mut.add_argument("-v", "--verbose", action="store_true",
                        help="Print every mutation with newly-accepted / -rejected candidates")
    p_mut.set_defaults(func=_cmd_mutate)

    p_ds = sub.add_parser(
        "dataset-run",
        help="Run the 4-step pipeline on a sample from MBPP / HumanEval (needs ANTHROPIC_API_KEY)",
    )
    p_ds.add_argument(
        "--dataset",
        choices=["mbpp", "humaneval", "bigcodebench", "humaneval_pro",
                  "livecodebench", "all", "both"],
        default="all",
        help="'all' covers all 5 bundled samples; 'both' kept as alias for mbpp+humaneval",
    )
    p_ds.add_argument("--n", type=int, default=5, help="Cap number of problems")
    p_ds.add_argument("--no-compare", action="store_true",
                      help="Skip the cross-model comparison step (faster, cheaper)")
    p_ds.set_defaults(func=_cmd_dataset_run)

    p_lean = sub.add_parser(
        "emit-lean",
        help="Emit a corpus TaskSpec as Lean 4 source (optionally type-check with `lake build`)",
    )
    p_lean.add_argument("--task-id", required=True, help="Which corpus spec to emit")
    p_lean.add_argument("--output", "-o", help="Write to this file (default: stdout)")
    p_lean.add_argument("--verify", action="store_true",
                         help="Run `lake build` on the emitted source to type-check")
    p_lean.set_defaults(func=_cmd_emit_lean)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
