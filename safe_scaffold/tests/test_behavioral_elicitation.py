"""Schema-validation tests for the behavioral_spec block in elicitation.

These run without calling the Anthropic API by directly exercising the
`_validate_payload` and `_materialize` helpers in `elicitation.py`.
"""

from __future__ import annotations

import unittest

from safe_scaffold.task_spec.elicitation import (
    _materialize,
    _validate_behavioral_spec,
    _validate_payload,
)


_GOOD_BEHAVIORAL = {
    "function_name": "is_not_prime",
    "signature": "is_not_prime(n: int) -> bool",
    "lean_predicate": "def isNotPrime (n : Nat) : Prop := n < 2 ∨ ∃ k, 2 ≤ k ∧ k < n ∧ n % k = 0",
    "python_oracle": (
        "def is_not_prime(n: int) -> bool:\n"
        "    if n < 2:\n        return True\n"
        "    return any(n % k == 0 for k in range(2, n))\n"
    ),
    "input_strategy": "integers(min_value=0, max_value=200)",
}


_GOOD_PAYLOAD = {
    "allowed_files": ["primecheck.py"],
    "forbidden_imports": ["os", "subprocess"],
    "max_diff_lines": 20,
    "check_secrets": True,
    "positive_test": {
        "path": "test_pc.py",
        "name": "primecheck",
        "code": "from primecheck import is_not_prime\n\ndef test_t():\n    assert is_not_prime(4)\n",
    },
    "rationale": {"behavioral_spec": "trial division below sqrt(n)"},
    "behavioral_spec": _GOOD_BEHAVIORAL,
}


class TestBehavioralSchema(unittest.TestCase):
    def test_good_behavioral_passes(self):
        self.assertEqual(_validate_behavioral_spec(_GOOD_BEHAVIORAL), "")

    def test_missing_fields_are_caught(self):
        for missing in ("function_name", "signature", "lean_predicate",
                         "python_oracle", "input_strategy"):
            bad = {k: v for k, v in _GOOD_BEHAVIORAL.items() if k != missing}
            err = _validate_behavioral_spec(bad)
            self.assertIn(missing, err,
                          f"missing {missing} not surfaced: {err!r}")

    def test_function_name_must_be_valid_identifier(self):
        for bad_name in ("123bad", "with-dash", "_private", ""):
            bad = {**_GOOD_BEHAVIORAL, "function_name": bad_name}
            self.assertNotEqual(_validate_behavioral_spec(bad), "")

    def test_signature_must_contain_function_name(self):
        bad = {**_GOOD_BEHAVIORAL, "signature": "different_name(x: int) -> bool"}
        self.assertIn("function_name", _validate_behavioral_spec(bad))

    def test_lean_predicate_must_contain_def(self):
        bad = {**_GOOD_BEHAVIORAL,
                "lean_predicate": "isNotPrime n iff n < 2 or exists k..."}
        self.assertIn("def", _validate_behavioral_spec(bad))

    def test_python_oracle_must_define_the_function(self):
        bad = {**_GOOD_BEHAVIORAL,
                "python_oracle": "def something_else(n): return True\n"}
        self.assertIn("python_oracle", _validate_behavioral_spec(bad))

    def test_input_strategy_must_use_known_strategy(self):
        for bad_strat in ("eval('x')", "rand_int(0,100)", "exec(...)", ""):
            bad = {**_GOOD_BEHAVIORAL, "input_strategy": bad_strat}
            self.assertNotEqual(_validate_behavioral_spec(bad), "")

    def test_known_strategies_accepted(self):
        for strat in ("integers(min_value=0)", "lists(integers())",
                       "text()", "booleans()", "floats(allow_nan=False)",
                       "tuples(integers(), text())",
                       "sampled_from(['a', 'b'])"):
            ok = {**_GOOD_BEHAVIORAL, "input_strategy": strat}
            self.assertEqual(_validate_behavioral_spec(ok), "",
                             f"good strategy {strat!r} rejected")


class TestTopLevelPayloadRequiresBehavioral(unittest.TestCase):
    def test_good_payload_passes(self):
        self.assertEqual(_validate_payload(_GOOD_PAYLOAD), "")

    def test_missing_behavioral_spec_is_caught(self):
        bad = {k: v for k, v in _GOOD_PAYLOAD.items() if k != "behavioral_spec"}
        err = _validate_payload(bad)
        self.assertIn("behavioral_spec", err)

    def test_malformed_behavioral_spec_propagates(self):
        bad = {**_GOOD_PAYLOAD, "behavioral_spec": {"function_name": "x"}}
        # The function name is fine but the other required fields are missing.
        err = _validate_payload(bad)
        self.assertIn("behavioral_spec", err)


class TestMaterializeAttachesBehavioral(unittest.TestCase):
    def test_materialize_sets_behavioral_spec(self):
        draft = _materialize(
            _GOOD_PAYLOAD,
            task_id="t",
            description="add is_not_prime",
            starting_repo={"primecheck.py": "def is_not_prime(n): raise NotImplementedError\n"},
            category="drafted",
        )
        self.assertIsNotNone(draft.spec)
        bs = draft.spec.behavioral_spec
        self.assertIsNotNone(bs)
        self.assertEqual(bs.function_name, "is_not_prime")
        self.assertIn("isNotPrime", bs.lean_predicate)
        self.assertIn("def is_not_prime", bs.python_oracle)
        self.assertEqual(bs.input_strategy,
                         "integers(min_value=0, max_value=200)")


if __name__ == "__main__":
    unittest.main()
