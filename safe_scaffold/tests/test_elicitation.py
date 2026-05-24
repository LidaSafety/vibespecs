"""Unit tests for safe_scaffold.task_spec.elicitation.

Tests the schema-validation and materialization paths without calling
the API. The graceful no-key path is tested by clearing the env var.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from safe_scaffold.task_spec.elicitation import (
    DraftSpec,
    _materialize,
    _validate_payload,
    draft_spec,
)
from safe_scaffold.task_spec.invariants import (
    DiffSmallerThan,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    PositiveTestPasses,
)


_GOOD_PAYLOAD = {
    "allowed_files": ["a.py"],
    "forbidden_imports": ["os", "subprocess"],
    "max_diff_lines": 20,
    "check_secrets": True,
    "positive_test": {
        "path": "test_a.py",
        "name": "a",
        "code": "from a import f\n\ndef test_f():\n    assert f() == 1\n",
    },
    "rationale": {
        "allowed_files": "only one file changes",
        "forbidden_imports": "no need for os/subprocess",
        "max_diff_lines": "small addition",
        "positive_test": "covers the new function",
    },
}


class TestSchemaValidation(unittest.TestCase):
    def test_good_payload_passes(self):
        self.assertEqual(_validate_payload(_GOOD_PAYLOAD), "")

    def test_missing_field(self):
        bad = {k: v for k, v in _GOOD_PAYLOAD.items() if k != "allowed_files"}
        self.assertIn("missing", _validate_payload(bad))

    def test_allowed_files_must_be_nonempty(self):
        bad = {**_GOOD_PAYLOAD, "allowed_files": []}
        self.assertIn("non-empty", _validate_payload(bad))

    def test_forbidden_imports_whitelist(self):
        bad = {**_GOOD_PAYLOAD, "forbidden_imports": ["os", "totally_made_up_module"]}
        err = _validate_payload(bad)
        self.assertIn("totally_made_up_module", err)

    def test_max_diff_lines_must_be_positive(self):
        for bad_value in (0, -5, "twenty"):
            bad = {**_GOOD_PAYLOAD, "max_diff_lines": bad_value}
            self.assertIn("max_diff_lines", _validate_payload(bad))

    def test_check_secrets_must_be_bool(self):
        bad = {**_GOOD_PAYLOAD, "check_secrets": "yes"}
        self.assertIn("check_secrets", _validate_payload(bad))

    def test_positive_test_must_contain_test_function(self):
        bad = {**_GOOD_PAYLOAD,
               "positive_test": {**_GOOD_PAYLOAD["positive_test"],
                                  "code": "import a\n# no test_ here\n"}}
        self.assertIn("test_*", _validate_payload(bad))

    def test_top_level_not_object(self):
        self.assertIn("not an object", _validate_payload(["a", "list"]))


class TestMaterialize(unittest.TestCase):
    def test_materialize_produces_expected_invariants(self):
        draft = _materialize(
            _GOOD_PAYLOAD,
            task_id="t",
            description="add f",
            starting_repo={"a.py": "def f(): return 1\n"},
            category="drafted",
        )
        self.assertIsNotNone(draft.spec)
        types = {type(d.invariant).__name__ for d in draft.drafted_invariants}
        self.assertEqual(types,
                         {"OnlyFilesModified", "NoNewImports",
                          "DiffSmallerThan", "NoSecretsInDiff"})
        # Spec carries the structural invariants + a PositiveTestPasses marker.
        spec_inv_types = {type(i).__name__ for i in draft.spec.negative_invariants}
        self.assertIn("PositiveTestPasses", spec_inv_types)

    def test_check_secrets_false_omits_nosecretsindiff(self):
        payload = {**_GOOD_PAYLOAD, "check_secrets": False}
        draft = _materialize(
            payload, task_id="t", description="x",
            starting_repo={"a.py": ""}, category="drafted",
        )
        types = {type(d.invariant).__name__ for d in draft.drafted_invariants}
        self.assertNotIn("NoSecretsInDiff", types)

    def test_rationales_preserved(self):
        draft = _materialize(
            _GOOD_PAYLOAD, task_id="t", description="x",
            starting_repo={"a.py": ""}, category="drafted",
        )
        rationale_by_type = {
            type(d.invariant).__name__: d.rationale
            for d in draft.drafted_invariants
        }
        self.assertEqual(rationale_by_type["OnlyFilesModified"],
                         "only one file changes")
        self.assertEqual(rationale_by_type["DiffSmallerThan"],
                         "small addition")


class TestNoApiKey(unittest.TestCase):
    def test_draft_spec_returns_error_without_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            draft = draft_spec("intent", {"a.py": "pass\n"})
        self.assertFalse(draft.ok)
        self.assertIsNone(draft.spec)
        self.assertIn("ANTHROPIC_API_KEY", draft.error)


if __name__ == "__main__":
    unittest.main()
