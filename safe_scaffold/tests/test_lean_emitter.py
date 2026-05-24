"""Unit tests for safe_scaffold.task_spec.lean_emitter.

Tests the text-generation path without invoking Lean. Verification via
`lake build` is exercised by smoke runs from CLI / demo, not unit tests
(it requires the Lean toolchain on the test machine).
"""

from __future__ import annotations

import unittest

from safe_scaffold.task_spec.invariants import (
    DiffSmallerThan,
    FilesUnchanged,
    NoNewImports,
    NoSecretsInDiff,
    OnlyFilesModified,
    PositiveTestPasses,
)
from safe_scaffold.task_spec.lean_emitter import (
    PRELUDE_DIR,
    _invariant_to_prop,
    _quote_str,
    _safe_ns,
    emit_lean,
)
from safe_scaffold.task_spec.spec import PositiveTest, TaskSpec


def _spec(*invariants, task_id: str = "syn", tests=()) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        description="add f to a.py",
        starting_repo={"a.py": "pass\n"},
        positive_tests=tests,
        negative_invariants=tuple(invariants),
    )


class TestPreludeBundle(unittest.TestCase):
    def test_prelude_dir_exists(self):
        self.assertTrue(PRELUDE_DIR.is_dir(),
                         f"prelude dir {PRELUDE_DIR} not found")
        self.assertTrue((PRELUDE_DIR / "SafeScaffold" / "Basic.lean").is_file())
        self.assertTrue((PRELUDE_DIR / "lakefile.lean").is_file())


class TestHelpers(unittest.TestCase):
    def test_safe_ns_replaces_invalid_chars(self):
        self.assertEqual(_safe_ns("t01_factorial"), "Spec_t01_factorial")
        # Starts with a digit → prepended with S_ to avoid an invalid
        # Lean identifier, then wrapped with the Spec_ prefix.
        self.assertEqual(_safe_ns("01-bad start"), "Spec_S_01_bad_start")

    def test_safe_ns_avoids_keyword_collision(self):
        ns = _safe_ns("end")
        self.assertNotEqual(ns, "end")
        self.assertTrue(ns.startswith("Spec_"))

    def test_quote_str_escapes(self):
        self.assertEqual(_quote_str("hello"), '"hello"')
        self.assertEqual(_quote_str('say "hi"'), '"say \\"hi\\""')
        self.assertEqual(_quote_str("a\\b"), '"a\\\\b"')


class TestInvariantRendering(unittest.TestCase):
    def test_only_files_modified(self):
        out = _invariant_to_prop(OnlyFilesModified(("a.py", "b.py")))
        self.assertEqual(out, 'OnlyFilesModified d ["a.py", "b.py"]')

    def test_no_new_imports(self):
        out = _invariant_to_prop(NoNewImports(("os", "subprocess")))
        self.assertEqual(out, 'NoNewImports d ["os", "subprocess"]')

    def test_diff_smaller_than(self):
        self.assertEqual(_invariant_to_prop(DiffSmallerThan(20)),
                         "DiffSmallerThan d 20")

    def test_no_secrets(self):
        self.assertEqual(_invariant_to_prop(NoSecretsInDiff()),
                         "NoSecretsInDiff d")

    def test_files_unchanged(self):
        self.assertEqual(_invariant_to_prop(FilesUnchanged(("config.py",))),
                         'FilesUnchanged d ["config.py"]')

    def test_positive_test_passes_returns_none(self):
        # Behavioral, kept in Python; emitter should signal "skip me".
        self.assertIsNone(_invariant_to_prop(PositiveTestPasses("test_a.py")))


class TestEmitLean(unittest.TestCase):
    def test_full_spec_emission(self):
        spec = _spec(
            OnlyFilesModified(("a.py",)),
            NoNewImports(("os",)),
            DiffSmallerThan(15),
            NoSecretsInDiff(),
            PositiveTestPasses("test_a.py"),
            tests=(PositiveTest(path="test_a.py", code="def test_a(): pass\n",
                                 name="a_works"),),
        )
        src = emit_lean(spec)
        # Imports + namespace + open + spec def + end namespace.
        self.assertIn("import SafeScaffold.Basic", src)
        self.assertIn("namespace Spec_syn", src)
        self.assertIn("open SafeScaffold", src)
        self.assertIn("def spec (d : Diff) : Prop :=", src)
        self.assertIn("end Spec_syn", src)
        # Each Diff-shaped invariant present.
        self.assertIn('OnlyFilesModified d ["a.py"]', src)
        self.assertIn('NoNewImports d ["os"]', src)
        self.assertIn("DiffSmallerThan d 15", src)
        self.assertIn("NoSecretsInDiff d", src)
        # Skipped invariants surface as a comment.
        self.assertIn("skipped: PositiveTestPasses", src)
        # Positive tests recorded as a trailing comment.
        self.assertIn("positive tests", src)
        self.assertIn("a_works", src)

    def test_spec_with_no_diff_invariants_emits_true(self):
        spec = _spec(PositiveTestPasses("test_a.py"))
        src = emit_lean(spec)
        self.assertIn("True", src)
        self.assertIn("no Diff-shaped invariants", src)

    def test_description_in_docstring(self):
        src = emit_lean(_spec(OnlyFilesModified(("a.py",))))
        self.assertIn("add f to a.py", src)
        # Block comment, not line.
        self.assertIn("/--", src)
        self.assertIn("-/", src)

    def test_docstring_escapes_nested_comment_markers(self):
        spec = TaskSpec(
            task_id="x",
            description="weird /- nested -/ markers",
            starting_repo={},
            positive_tests=(),
            negative_invariants=(OnlyFilesModified(("a.py",)),),
        )
        src = emit_lean(spec)
        # Verbatim "/- ... -/" sequences would unbalance the docstring.
        # Verify the emitter sanitized them so the only `/-` and `-/`
        # tokens left are the docstring delimiters themselves.
        body_only = src.split("namespace")[1]
        self.assertEqual(body_only.count("/-"), 1)  # the docstring open
        self.assertEqual(body_only.count("-/"), 1)  # the docstring close


if __name__ == "__main__":
    unittest.main()
