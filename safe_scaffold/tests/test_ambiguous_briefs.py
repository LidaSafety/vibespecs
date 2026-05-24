"""Tests for the ambiguous-brief fixtures.

These are demo-only fixtures, but we check shape so the demo's brief
picker can rely on the structure.
"""

from __future__ import annotations

import unittest

from safe_scaffold.task_spec.ambiguous_briefs import (
    AMBIGUOUS_BRIEFS,
    BRIEFS_BY_ID,
)


class TestAmbiguousBriefs(unittest.TestCase):
    def test_three_briefs_present(self):
        self.assertEqual(len(AMBIGUOUS_BRIEFS), 3)
        ids = [b.brief_id for b in AMBIGUOUS_BRIEFS]
        self.assertIn("a_underspec", ids)
        self.assertIn("b_prd_contradicts", ids)
        self.assertIn("c_slides_vs_tests", ids)

    def test_briefs_by_id_index(self):
        for b in AMBIGUOUS_BRIEFS:
            self.assertIs(BRIEFS_BY_ID[b.brief_id], b)

    def test_every_brief_has_intent_and_starting_repo(self):
        for b in AMBIGUOUS_BRIEFS:
            with self.subTest(brief_id=b.brief_id):
                self.assertTrue(b.description.strip(), "description empty")
                self.assertTrue(b.starting_repo, "starting_repo empty")
                self.assertTrue(b.label.strip(), "label empty")

    def test_additional_sources_returns_only_nonempty(self):
        # Brief A has no extra sources.
        a = BRIEFS_BY_ID["a_underspec"]
        self.assertEqual(a.additional_sources(), {})
        # Brief B has a prose_doc with contradictions.
        b = BRIEFS_BY_ID["b_prd_contradicts"]
        srcs = b.additional_sources()
        self.assertIn("prose_doc", srcs)
        self.assertNotIn("existing_tests", srcs)
        self.assertNotIn("slide_deck", srcs)
        # Brief C has a slide deck contradicting an existing-tests file.
        c = BRIEFS_BY_ID["c_slides_vs_tests"]
        srcs = c.additional_sources()
        # C uses starting_repo for the tests file, with slide_deck as the
        # extra source contradicting them.
        self.assertIn("slide_deck", srcs)

    def test_brief_b_prose_doc_contains_internal_contradiction(self):
        """Brief B is the PRD-contradicts-itself fixture; sanity check
        that the prose actually has the contradiction we wrote it to have."""
        b = BRIEFS_BY_ID["b_prd_contradicts"]
        prose = b.prose_doc.lower()
        # Says "passwords MUST be hashed" AND "passwords MUST be stored exactly".
        self.assertIn("hashed", prose)
        self.assertIn("stored exactly as provided", prose)
        # Also says "no new dependencies" AND "use bcrypt".
        self.assertIn("no new dependencies", prose)
        self.assertIn("bcrypt", prose)


if __name__ == "__main__":
    unittest.main()
