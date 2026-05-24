"""Tests for path normalization and glob matching."""

from __future__ import annotations

import unittest

from safe_scaffold.paths import (
    is_under,
    looks_like_destructive_root,
    matches_glob,
    normalize,
)


class TestNormalize(unittest.TestCase):
    def test_collapse_double_slash(self) -> None:
        self.assertEqual(normalize("/etc//passwd"), "/etc/passwd")

    def test_resolve_dotdot(self) -> None:
        self.assertEqual(
            normalize("/tmp/a/../../etc/passwd"), "/etc/passwd"
        )

    def test_strip_trailing_slash(self) -> None:
        self.assertEqual(normalize("/etc/"), "/etc")

    def test_root_preserved(self) -> None:
        self.assertEqual(normalize("/"), "/")

    def test_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize("")


class TestIsUnder(unittest.TestCase):
    def test_simple_under(self) -> None:
        self.assertTrue(is_under("/etc/passwd", "/etc"))

    def test_equality(self) -> None:
        self.assertTrue(is_under("/etc", "/etc"))

    def test_prefix_not_under(self) -> None:
        # /etcetera should NOT be under /etc
        self.assertFalse(is_under("/etcetera", "/etc"))

    def test_dotdot_escape(self) -> None:
        self.assertFalse(
            is_under("/tmp/foo/../../etc/passwd", "/tmp")
        )
        self.assertTrue(
            is_under("/tmp/foo/../../etc/passwd", "/etc")
        )

    def test_relative_parent_rejected(self) -> None:
        with self.assertRaises(ValueError):
            is_under("/etc", "etc")

    def test_relative_child_not_under(self) -> None:
        # Defensive: relative child paths shouldn't be considered "under" anything.
        self.assertFalse(is_under("etc/passwd", "/etc"))


class TestMatchesGlob(unittest.TestCase):
    def test_single_segment(self) -> None:
        self.assertTrue(matches_glob("/tmp/foo.txt", "/tmp/*.txt"))

    def test_no_segment_leak(self) -> None:
        # `*` must not leak across `/` boundaries.
        self.assertFalse(matches_glob("/tmp/a/b.txt", "/tmp/*.txt"))

    def test_recursive(self) -> None:
        self.assertTrue(matches_glob("/tmp/a/b/c.txt", "/tmp/**"))
        self.assertTrue(matches_glob("/tmp/x.txt", "/tmp/**"))
        self.assertFalse(matches_glob("/etc/passwd", "/tmp/**"))

    def test_bare_double_star_rejected(self) -> None:
        with self.assertRaises(ValueError):
            matches_glob("/x", "**/foo")

    def test_empty_pattern_rejected(self) -> None:
        with self.assertRaises(ValueError):
            matches_glob("/x", "")


class TestDestructiveRoot(unittest.TestCase):
    def test_catches_root(self) -> None:
        self.assertTrue(looks_like_destructive_root("/"))

    def test_catches_etc(self) -> None:
        self.assertTrue(looks_like_destructive_root("/etc"))

    def test_catches_tilde(self) -> None:
        self.assertTrue(looks_like_destructive_root("~"))

    def test_catches_unexpanded_home(self) -> None:
        self.assertTrue(looks_like_destructive_root("$HOME"))
        self.assertTrue(looks_like_destructive_root("${HOME}"))

    def test_passes_scratch(self) -> None:
        self.assertFalse(looks_like_destructive_root("/tmp/scratch"))


if __name__ == "__main__":
    unittest.main()
