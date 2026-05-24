"""Demo: Z3 proves a universal safety property about the policy.

Run with:
    python examples/demo_z3_proves_invariant.py

This is the killer demo for Track 1. We ask Z3:

    "Is there ANY action of any shape that the safe_default_policy would
     ALLOW, and that also matches a recursive-rm pattern?"

For a sane policy (the one in safe_default_policy), Z3 returns UNSAT — no
such action exists. The policy provably never allows recursive rm,
regardless of what specific argv the agent comes up with.

We then deliberately corrupt the policy by adding an over-broad ALLOW rule
("allow all shell exec"). Re-running the property check, Z3 now returns SAT
with a concrete counterexample (here is a recursive rm that the policy would
allow). The two outcomes together show the formal guarantee in action.

If z3-solver is not installed, this demo prints an explanation and exits 0
— it's not a test failure, just a degraded mode."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from safe_scaffold.conditions import parse_condition  # noqa: E402
from safe_scaffold.policy import Effect, Provenance, Rule, safe_default_policy  # noqa: E402

try:
    from safe_scaffold.properties import (  # noqa: E402
        Z3_AVAILABLE,
        pattern_rm_recursive_anywhere,
        policy_never_allows,
    )
except Exception as exc:
    print(f"Could not import properties module: {exc}")
    sys.exit(1)


def main() -> int:
    if not Z3_AVAILABLE:
        print("z3-solver is not installed in this environment.")
        print("This demo requires Z3 for the universal-property proofs.")
        print()
        print("Install with: pip install safe-scaffold[smt]")
        print()
        print("The DIRECT verifier (safe_scaffold.verify) is still fully")
        print("usable without Z3 — it gates individual actions in O(rules) time.")
        print("Z3 is only needed for the policy-level invariant proofs that")
        print("this demo showcases.")
        return 0

    print("=" * 72)
    print("Demo: Z3 proves universal properties about a policy")
    print("=" * 72)
    print()

    p = safe_default_policy()
    print(f"Policy: {p.name} (v{p.version}), {len(p.rules)} rules.")
    print()

    # ---- 1. The property holds on the default policy ----
    print("Question 1: Does the default policy ever allow recursive rm?")
    print("(Pattern: shell_exec with program=rm and a recursive flag.)")
    print()
    result = policy_never_allows(
        p, pattern_rm_recursive_anywhere(),
        "policy never allows recursive rm",
    )
    print(f"  → {result}")
    if result.holds:
        print()
        print("  Z3 returned UNSAT: no allowed action matches the pattern.")
        print("  The policy PROVABLY denies recursive rm in every form.")
        print("  No matter what argv the agent invents — `rm -rf`, `rm -R`,")
        print("  `rm --recursive`, all argument orders — Z3 has checked them all.")
    print()

    # ---- 2. Corrupt the policy with a too-broad ALLOW rule ----
    print("=" * 72)
    print("Question 2: What if a misguided operator adds an allow-all rule?")
    print("=" * 72)
    print()
    bad_rule = Rule(
        id="oops_allow_all_shell",
        effect=Effect.ALLOW,
        condition=parse_condition({"type": "kind_is", "kind": "shell_exec"}),
        provenance=Provenance(
            created_by="demo",
            source_nl="(misguided) allow any shell command",
            notes="this is the kind of rule a tired operator might add",
        ),
        description="Allow all shell exec — this is too broad and should be caught.",
    )
    p_bad = p.with_rule(bad_rule)
    print(f"Added rule {bad_rule.id!r}: allow all shell exec.")
    print()
    result_bad = policy_never_allows(
        p_bad, pattern_rm_recursive_anywhere(),
        "no recursive rm (with corrupted policy)",
    )
    print(f"  → {result_bad}")
    if not result_bad.holds and result_bad.counterexample is not None:
        print()
        print("  Z3 returned SAT: it FOUND an action that")
        print("  (a) matches the recursive-rm pattern AND")
        print("  (b) would be allowed by the corrupted policy.")
        print()
        print("  Counterexample (concrete action):")
        for k, v in sorted(result_bad.counterexample.items()):
            print(f"    {k:14s} = {v!r}")
        print()
        print("  CI could be configured to fail on this — any policy edit that")
        print("  breaks the invariant is caught BEFORE deployment, with a concrete")
        print("  reproducer attached.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
