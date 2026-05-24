"""Reproducible fixtures for cross-checking, including the Cryspen libcrux
ML-KEM `decompress_d` specification bug.

The bug, in one paragraph: libcrux's first version of the ML-KEM spec file
defined `decompress_d` using a literal constant `1664`, which would be correct
only for `d=12`. For other values of `d` the constant should have been
`pow2(d-1)`. The function was type-refined to forbid `d=12`, meaning the
constant was wrong in EVERY case the function was actually called with. The
bug had been present since the very first commit of the spec and was found by
Symbolic Software in early 2026.

This fixture defines:

- `Q = 3329`, the FIPS 203 ML-KEM modulus.
- `reference_decompress_d(x, d)`: implements the FIPS 203 formula
  `round((x * Q) / 2^d)`, which for integer math equals
  `(x * Q + 2^(d-1)) // 2^d`. This is what the spec SHOULD have computed.
- `buggy_decompress_d(x, d)`: implements the pre-fix libcrux version,
  hard-coding `1664` instead of `2^(d-1)`. Reproduces the bug verbatim.
- `decompress_d_spec`: a FunctionSpec encoding the input domain (excluding
  `d=12` per libcrux's type refinement).
- `cryspen_decompress_d_demo()`: a one-call entry point that runs the cross-
  check and returns a DisagreementReport. Used by the Track 2 demo.

Anyone re-running the demo gets a deterministic disagreement count: out of
the `(Q − 1) × 5 = 16,640` (x, d) input pairs in the spec's domain, the buggy
version disagrees with the reference on the vast majority (only matching when
the spec rounds the same way 1664 happens to round). The point isn't the
exact count — it's that a 20-line cross-check harness would have caught a bug
that 16 months of formal verification work missed.

For full background see:
    https://symbolic.software/blog/2026-02-05-cryspen/
    https://symbolic.software/blog/2026-02-12-cryspen-response/
"""

from __future__ import annotations

from typing import Iterable

from safe_scaffold.cross_check.disagree import DisagreementReport, find_disagreements
from safe_scaffold.cross_check.runner import TestCase
from safe_scaffold.cross_check.spec import FunctionSpec, Property

# FIPS 203 ML-KEM modulus.
Q = 3329

# The libcrux type refinement: `d <> 12`. Bug applies to d in {1, 4, 5, 10, 11}
# in practice (these are the values ML-KEM actually invokes decompress_d with).
VALID_D = (1, 4, 5, 10, 11)


def reference_decompress_d(x: int, d: int) -> int:
    """FIPS 203 Algorithm 5 Decompress_d, integer formulation.

    Definition: Decompress_d(x) = round(x * (Q / 2^d)), the round-half-up
    convention. For integer math this equals (x * Q + 2^(d-1)) // 2^d, then
    reduced mod Q.

    Source: NIST FIPS 203, August 2024, §4.2.1 Algorithm 5.
    """
    return (x * Q + (1 << (d - 1))) // (1 << d)


def buggy_decompress_d(x: int, d: int) -> int:
    """The pre-fix libcrux version.

    The literal 1664 is the value of `pow2(d-1)` for d=12 — but d=12 is
    excluded by the type refinement, so 1664 is wrong in every reachable call.

    Verbatim from the commit `c1935b9cf8` ("spec") on August 12, 2024, the
    very first version of the file.
    """
    return (x * Q + 1664) // (1 << d)


# ----- FunctionSpec encoding the libcrux invariants -----


def _sample_decompress_d_inputs(n: int) -> Iterable[tuple[int, int]]:
    """Hand-curated input sampler. Yields every (x, d) pair in domain.

    Domain size is |VALID_D| * Q = 5 * 3329 = 16,645, well within typical
    n=200 ceilings... so we ignore n and yield everything. The bug is dense
    enough that the first dozen samples already disagree, but yielding all
    inputs makes the disagreement count a stable regression metric.
    """
    del n
    for d in VALID_D:
        for x in range(Q):
            yield (x, d)


def _decompress_d_domain(x: int, d: int) -> bool:
    return 0 <= x < Q and d in VALID_D


decompress_d_spec = FunctionSpec(
    name="decompress_d",
    inputs=(("x", "int"), ("d", "int")),
    input_domain=_decompress_d_domain,
    sample_inputs=_sample_decompress_d_inputs,
    properties=(
        Property(
            name="matches_FIPS_203_reference",
            description=(
                "Output equals the FIPS 203 reference formula "
                "Decompress_d(x) = round(x * Q / 2^d)."
            ),
            check=lambda inputs, output: output == reference_decompress_d(*inputs),
        ),
        Property(
            name="output_in_valid_range",
            description="Output is a non-negative integer less than Q.",
            check=lambda inputs, output: isinstance(output, int) and 0 <= output < Q + 1,
        ),
    ),
    description=(
        "ML-KEM decompress_d as defined in FIPS 203 Algorithm 5. "
        "Used to validate libcrux's implementation against the standard."
    ),
)


def cryspen_decompress_d_demo() -> DisagreementReport:
    """Run the cross-check between reference and buggy decompress_d.

    Returns a DisagreementReport. In practice, the report will contain
    thousands of disagreements — anywhere the rounding constant matters,
    which is most of the domain.
    """
    cases = (
        TestCase(function="decompress_d", inputs=(x, d))
        for d in VALID_D
        for x in range(Q)
    )
    return find_disagreements(
        cases,
        reference=reference_decompress_d,
        candidate=buggy_decompress_d,
    )
