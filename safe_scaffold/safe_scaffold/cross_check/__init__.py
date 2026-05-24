"""Track 2: adversarial spec validation via cross-checking.

This package implements the Track 2 ("Specification Validation") research
direction. The motivation, in one paragraph:

In early 2026 Symbolic Software documented sixteen vulnerabilities across six
Cryspen projects, including the ML-KEM mathematical specification itself — the
formal reference that all the correctness proofs were verified against was
computing a different function than FIPS 203. Even with full proof coverage,
the implementation would have been "verifiably correct against the wrong
specification". The fix was a single-line change: `1664` → `pow2(d-1)`.

The lesson: writing a formal spec by hand, even when you're an expert, has the
same human-error failure modes as writing the implementation. We need
ADVERSARIAL VALIDATION of specs — automated test generation that pits a spec
against (a) the original informal spec text (RFC, standard) and (b) the
implementation it's supposed to describe, and surfaces disagreements.

Our pipeline:

    structured spec  ───┐
                        ├──> adversarial generator ──> test cases
    attack templates ───┘                                 │
                                                          ▼
    reference impl ◄──── runner ────► candidate impl
                          │
                          ▼
                       disagreements report

The headline reproduction (`fixtures.cryspen_decompress_d_demo`) reproduces
the libcrux bug end-to-end: ~20 lines of code synthesize a few hundred test
inputs and find the disagreement between the buggy and FIPS-203-faithful
versions of decompress_d, demonstrating that this class of bug is catchable
with very modest tooling.
"""

from safe_scaffold.cross_check.disagree import (
    Disagreement,
    DisagreementReport,
    find_disagreements,
)
from safe_scaffold.cross_check.generator import generate_test_cases
from safe_scaffold.cross_check.runner import TestCase, TestResult, run_test_case
from safe_scaffold.cross_check.spec import (
    FunctionSpec,
    Property,
    ServerSpec,
)

__all__ = [
    "ServerSpec",
    "FunctionSpec",
    "Property",
    "TestCase",
    "TestResult",
    "run_test_case",
    "generate_test_cases",
    "Disagreement",
    "DisagreementReport",
    "find_disagreements",
]
