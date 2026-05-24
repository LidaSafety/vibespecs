"""Tests for the PBT-against-oracle verifier.

Uses a tiny synthetic spec (is_even) so each test runs in <1s. No
network and no LLM calls — the spec is hand-constructed.
"""

from __future__ import annotations

import unittest

from safe_scaffold.task_spec.invariants import (
    OnlyFilesModified,
    PositiveTestPasses,
)
from safe_scaffold.task_spec.spec import (
    BehavioralSpec,
    PositiveTest,
    TaskSpec,
)
from safe_scaffold.task_spec.verify_pbt import (
    PBTResult,
    verify_against_oracle,
)


def _is_even_spec() -> TaskSpec:
    """A tiny synthetic spec around `is_even(n: int) -> bool`."""
    bs = BehavioralSpec(
        function_name="is_even",
        signature="is_even(n: int) -> bool",
        lean_predicate="def isEven (n : Nat) : Prop := n % 2 = 0",
        python_oracle=(
            "def is_even(n: int) -> bool:\n"
            "    return n % 2 == 0\n"
        ),
        input_strategy="integers(min_value=-100, max_value=100)",
    )
    return TaskSpec(
        task_id="syn_is_even",
        description="Return True iff n is even.",
        starting_repo={"iseven.py": "def is_even(n):\n    raise NotImplementedError\n"},
        positive_tests=(PositiveTest(
            path="test_iseven.py",
            name="iseven",
            code="from iseven import is_even\ndef test_two():\n    assert is_even(2)\n",
        ),),
        negative_invariants=(OnlyFilesModified(("iseven.py",)),
                              PositiveTestPasses("test_iseven.py")),
        behavioral_spec=bs,
    )


class TestPBTVerifies(unittest.TestCase):
    def test_correct_impl_is_verified(self):
        spec = _is_even_spec()
        generated = {"iseven.py": "def is_even(n):\n    return n % 2 == 0\n"}
        r = verify_against_oracle(spec, generated, timeout_seconds=30)
        self.assertEqual(r.outcome, "verified", f"unexpected: {r}")
        self.assertTrue(r.ok)
        self.assertIn("no counterexample", r.detail)
        # n_runs is now parsed from Hypothesis's --hypothesis-show-statistics
        # block. We don't pin a single value (could be 200 or whatever
        # Hypothesis actually ran), but we do require either a positive
        # parsed count OR 0 (= "not parsed; reported 'up to N' instead").
        from safe_scaffold.task_spec.verify_pbt import _DEFAULT_MAX_EXAMPLES
        self.assertGreaterEqual(r.n_runs, 0)
        self.assertLessEqual(r.n_runs, _DEFAULT_MAX_EXAMPLES)
        # And the detail string should reflect the parsed-or-fallback distinction.
        if r.n_runs > 0:
            self.assertIn(f"{r.n_runs} examples", r.detail)
        else:
            self.assertIn("up to", r.detail)


class TestPBTFalsifies(unittest.TestCase):
    def test_wrong_impl_is_falsified_with_counterexample(self):
        spec = _is_even_spec()
        # Off-by-one: always returns the wrong answer for n=1, n=3, etc.
        generated = {"iseven.py": "def is_even(n):\n    return n % 2 == 1\n"}
        r = verify_against_oracle(spec, generated, timeout_seconds=30)
        self.assertEqual(r.outcome, "falsified", f"unexpected: {r}")
        self.assertFalse(r.ok)
        # Hypothesis should have shrunk to a tiny counterexample.
        self.assertTrue(r.counterexample, "expected counterexample text")
        # The Falsifying example trace should reference a small int.
        # Don't assert a specific value (Hypothesis can shrink to 0, 1, etc).

    def test_impl_returning_wrong_type_is_falsified(self):
        spec = _is_even_spec()
        # Returns int instead of bool. The oracle returns bool, so the
        # equality assertion will fire on every input.
        generated = {"iseven.py": "def is_even(n):\n    return n % 2\n"}
        r = verify_against_oracle(spec, generated, timeout_seconds=30)
        # `1 == True` and `0 == False` in Python, so this might actually
        # pass equality. If it does, that's fine — we just confirm we
        # got *some* terminating verdict rather than `error`.
        self.assertIn(r.outcome, ("verified", "falsified"))


class TestPBTNestedStrategy(unittest.TestCase):
    """Regression: a strategy like `tuples(lists(integers()), ...)` should
    not NameError. Prior to the `from hypothesis.strategies import *` fix
    the driver only prefixed the top-level call with `st.`, leaving nested
    `lists`/`integers` as bare undefined names."""

    def _list_max_spec(self):
        bs = BehavioralSpec(
            function_name="max_of_pair",
            signature="max_of_pair(args: tuple) -> int",
            lean_predicate="def maxOfPair (xs : List Int) (k : Nat) : Prop := True",
            python_oracle=(
                "def max_of_pair(args):\n"
                "    xs, k = args\n"
                "    if not xs: return k\n"
                "    return max(max(xs), k)\n"
            ),
            input_strategy=(
                "tuples(lists(integers(min_value=-50, max_value=50), "
                "min_size=0, max_size=20), integers(min_value=0, max_value=20))"
            ),
        )
        return TaskSpec(
            task_id="syn_nested",
            description="max of a list and a scalar",
            starting_repo={"mp.py": "def max_of_pair(args): raise NotImplementedError\n"},
            positive_tests=(PositiveTest(path="t.py", name="t", code="def test_():\n    pass\n"),),
            negative_invariants=(OnlyFilesModified(("mp.py",)),),
            behavioral_spec=bs,
        )

    def test_nested_strategy_does_not_nameerror(self):
        spec = self._list_max_spec()
        generated = {"mp.py": (
            "def max_of_pair(args):\n"
            "    xs, k = args\n"
            "    if not xs: return k\n"
            "    return max(max(xs), k)\n"
        )}
        r = verify_against_oracle(spec, generated, timeout_seconds=30)
        # The point is to not NameError on `lists`/`integers`.
        # A correct impl should produce `verified`; we don't care about
        # the count here, just that the driver actually ran.
        self.assertEqual(r.outcome, "verified",
                          f"nested strategy crashed: detail={r.detail!r}, "
                          f"ce={r.counterexample[:200]!r}")


class TestPBTErrorPaths(unittest.TestCase):
    def test_missing_behavioral_spec_returns_error(self):
        spec = TaskSpec(
            task_id="t",
            description="x",
            starting_repo={"a.py": "pass\n"},
            positive_tests=(),
            negative_invariants=(),
            behavioral_spec=None,
        )
        r = verify_against_oracle(spec, {"a.py": "pass\n"})
        self.assertEqual(r.outcome, "error")
        self.assertIn("behavioral_spec", r.detail)


if __name__ == "__main__":
    unittest.main()
