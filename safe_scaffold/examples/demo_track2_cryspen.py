"""Demo: Track 2 — reproduce the Cryspen libcrux ML-KEM `decompress_d` bug.

Run with:
    python examples/demo_track2_cryspen.py

In early 2026, Symbolic Software published a series of findings about
sixteen vulnerabilities in Cryspen's "formally verified" libcrux library.
One was a specification-level bug in ML-KEM: the mathematical spec file
that all the correctness proofs were verified against was computing a
DIFFERENT FUNCTION than FIPS 203. The fix was a single-line edit:

    let r = (x * v v_FIELD_MODULUS + 1664) / pow2 d
becomes
    let r = (x * v v_FIELD_MODULUS + pow2 (d - 1)) / pow2 d

The wrong literal `1664` had been there since the very first commit.
Because the function was type-refined to `d <> 12`, the substitution was
wrong in every case the function was actually called with.

This demo runs cross_check.fixtures.cryspen_decompress_d_demo(), which
implements both versions in Python and finds disagreements over the full
domain `{1, 4, 5, 10, 11} × {0, ..., Q-1}`. Expected output: a disagreement
rate of ~86%, showing that the bug is mechanically detectable with a small
cross-check harness.

The point isn't that cross-checking would have caught THIS specific bug
(Symbolic Software's audit did, via manual review). The point is that a
research artifact of ~100 LOC reproduces the finding in seconds — and the
same machinery generalizes to any spec where you can write down a reference
formula and a candidate implementation.

References:
    https://symbolic.software/blog/2026-02-05-cryspen/
    https://symbolic.software/blog/2026-04-07-cryspen-vulnerabilities/
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from safe_scaffold.cross_check.fixtures import (  # noqa: E402
    Q,
    VALID_D,
    buggy_decompress_d,
    cryspen_decompress_d_demo,
    reference_decompress_d,
)


def main() -> int:
    print("=" * 72)
    print("Cryspen libcrux ML-KEM `decompress_d` cross-check")
    print("=" * 72)
    print()
    print("FIPS 203 ML-KEM modulus Q = 3329.")
    print(f"Valid d values per libcrux type refinement (d <> 12): {VALID_D}")
    print()
    print("Reference (FIPS 203):  (x * Q + 2**(d-1))   //   2**d")
    print("Buggy (libcrux pre-fix): (x * Q + 1664)    //    2**d")
    print()
    print("Cross-checking both implementations across the full input domain...")
    print()

    report = cryspen_decompress_d_demo()
    print(report.summary())
    print()
    print(f"Domain explored:      {report.total:,} (x, d) pairs")
    print(f"Disagreements found:  {len(report.disagreements):,}")
    print(f"Disagreement rate:    {report.disagreement_rate:.2%}")
    print()
    print("First disagreement (smallest x, d):")
    if report.disagreements:
        d = report.disagreements[0]
        x_arg, d_arg = d.case.inputs
        print(f"  decompress_d(x={x_arg}, d={d_arg})")
        print(f"  reference: {reference_decompress_d(x_arg, d_arg)}")
        print(f"  buggy:     {buggy_decompress_d(x_arg, d_arg)}")
    print()
    print("Conclusion: the bug is dense across the domain; any first-order")
    print("cross-check between the FIPS 203 reference and the implementation")
    print("would have flagged this on the very first invocation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
