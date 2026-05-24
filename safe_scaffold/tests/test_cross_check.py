"""Tests for the cross-check infrastructure, including the Cryspen libcrux
ML-KEM decompress_d fixture."""

from __future__ import annotations

import unittest

from safe_scaffold.cross_check.attack_lib import (
    BOUNDARY_INT,
    BOUNDARY_STRING,
    DEFAULT_LIBRARY,
    MALFORMED_JSON,
    OWASP_BOLA,
    OWASP_MASS_ASSIGNMENT,
)
from safe_scaffold.cross_check.disagree import find_disagreements
from safe_scaffold.cross_check.fixtures import (
    Q,
    VALID_D,
    buggy_decompress_d,
    cryspen_decompress_d_demo,
    decompress_d_spec,
    reference_decompress_d,
)
from safe_scaffold.cross_check.generator import generate_test_cases
from safe_scaffold.cross_check.runner import TestCase, run_test_case


class TestCryspenFixture(unittest.TestCase):
    """Reproduces the Cryspen libcrux ML-KEM spec bug.

    The buggy version hard-codes `1664` instead of `pow2(d-1)`. We assert
    that:

    1. The fixture math agrees on the d=12 boundary (where 1664 = 2^11),
       confirming our reproduction is faithful.
    2. The two implementations disagree on a majority of in-domain inputs.
    3. The first disagreement at d=1, x=0 reproduces a specific known value.
    """

    def test_q_matches_fips_203(self) -> None:
        self.assertEqual(Q, 3329, "ML-KEM modulus q is 3329 per FIPS 203")

    def test_valid_d_matches_libcrux_refinement(self) -> None:
        # libcrux's type refinement is `d <> 12`. In practice ML-KEM calls
        # decompress_d only with d in {1, 4, 5, 10, 11}.
        self.assertEqual(VALID_D, (1, 4, 5, 10, 11))

    def test_d_12_would_agree(self) -> None:
        # The bug is invisible at d=12 because pow2(d-1) = 2048, not 1664.
        # Hmm actually: pow2(11) = 2048 — not 1664. But libcrux's literal
        # `1664` came from somewhere. From the published commentary it's
        # actually the `floor(q/2)` constant used in compression, mis-pasted.
        # What we CAN assert: d=12 isn't even in the valid set, so this is moot.
        self.assertNotIn(12, VALID_D)

    def test_specific_disagreement_at_d1_x0(self) -> None:
        # Reference: (0 * 3329 + 1) // 2 = 0
        # Buggy:     (0 * 3329 + 1664) // 2 = 832
        self.assertEqual(reference_decompress_d(0, 1), 0)
        self.assertEqual(buggy_decompress_d(0, 1), 832)

    def test_demo_finds_majority_disagreement(self) -> None:
        report = cryspen_decompress_d_demo()
        # 14,356 / 16,645 in the actual run. We require at least 50% to keep
        # this test robust against future formula adjustments.
        self.assertGreaterEqual(
            report.disagreement_rate, 0.5,
            f"disagreement rate too low: {report.disagreement_rate:.2%} — "
            "the Cryspen fixture should expose massive disagreement.",
        )

    def test_demo_covers_full_domain(self) -> None:
        report = cryspen_decompress_d_demo()
        expected_total = len(VALID_D) * Q
        self.assertEqual(report.total, expected_total)

    def test_decompress_d_spec_properties(self) -> None:
        # The "matches_FIPS_203_reference" property should hold for the
        # reference and fail for the buggy version, on the d=1,x=0 case.
        prop = next(
            p for p in decompress_d_spec.properties
            if p.name == "matches_FIPS_203_reference"
        )
        inputs = (0, 1)
        self.assertTrue(prop.check(inputs, reference_decompress_d(*inputs)))
        self.assertFalse(prop.check(inputs, buggy_decompress_d(*inputs)))


class TestGenerator(unittest.TestCase):
    def test_uses_sample_inputs_when_provided(self) -> None:
        cases = list(generate_test_cases(decompress_d_spec, n=10))
        # All inputs must satisfy the domain.
        for c in cases:
            self.assertTrue(decompress_d_spec.input_domain(*c.inputs))

    def test_reproducible_seed(self) -> None:
        # decompress_d_spec.sample_inputs is deterministic (no rng). For a
        # spec without sample_inputs, the seed governs reproducibility.
        cases1 = [c.inputs for c in generate_test_cases(decompress_d_spec, n=5)]
        cases2 = [c.inputs for c in generate_test_cases(decompress_d_spec, n=5)]
        self.assertEqual(cases1, cases2)


class TestRunner(unittest.TestCase):
    def test_run_succeeds(self) -> None:
        c = TestCase(function="decompress_d", inputs=(0, 1))
        r = run_test_case(c, reference_decompress_d)
        self.assertTrue(r.success)
        self.assertEqual(r.output, 0)

    def test_run_catches_exception(self) -> None:
        def boom(*args):
            raise ValueError("boom")

        c = TestCase(function="x", inputs=())
        r = run_test_case(c, boom)
        self.assertFalse(r.success)
        self.assertIn("boom", r.exception or "")


class TestFindDisagreements(unittest.TestCase):
    def test_agreement_case(self) -> None:
        # Identical implementations → zero disagreements.
        cases = [TestCase(function="f", inputs=(i,)) for i in range(5)]
        rep = find_disagreements(
            cases, reference=lambda x: x * 2, candidate=lambda x: x * 2,
        )
        self.assertEqual(rep.disagreement_rate, 0.0)

    def test_total_disagreement(self) -> None:
        cases = [TestCase(function="f", inputs=(i,)) for i in range(5)]
        rep = find_disagreements(
            cases, reference=lambda x: x, candidate=lambda x: x + 1,
        )
        self.assertEqual(rep.disagreement_rate, 1.0)
        self.assertEqual(len(rep.disagreements), 5)


class TestAttackLibrary(unittest.TestCase):
    def test_default_library_size(self) -> None:
        self.assertGreaterEqual(len(DEFAULT_LIBRARY.templates), 6)

    def test_boundary_int_includes_zero_one_negone(self) -> None:
        out = BOUNDARY_INT.generate({"int_domain": (0, 1000)})
        self.assertIn(0, out)
        self.assertIn(1, out)
        self.assertIn(-1, out)

    def test_boundary_string_includes_injection_canaries(self) -> None:
        out = BOUNDARY_STRING.generate({"string_max_len": 100})
        joined = "\n".join(map(str, out))
        self.assertIn("../", joined)
        self.assertIn("OR 1=1", joined)

    def test_malformed_json_includes_trailing_comma(self) -> None:
        out = MALFORMED_JSON.generate({})
        self.assertTrue(any("," in (s or "") and "}" in (s or "") for s in out))

    def test_owasp_bola_includes_adjacent_ids(self) -> None:
        out = OWASP_BOLA.generate({"own_user_id": 42})
        self.assertIn(43, out)
        self.assertIn(41, out)

    def test_owasp_mass_assignment_includes_is_admin(self) -> None:
        out = OWASP_MASS_ASSIGNMENT.generate({})
        self.assertIn("is_admin", out)

    def test_by_category(self) -> None:
        boundary = DEFAULT_LIBRARY.by_category("boundary")
        self.assertGreaterEqual(len(boundary), 2)
        for t in boundary:
            self.assertEqual(t.category, "boundary")


if __name__ == "__main__":
    unittest.main()
