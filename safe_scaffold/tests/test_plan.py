"""Tests for multi-action plan verification."""

from __future__ import annotations

import unittest

from safe_scaffold.plan import (
    find_unsafe_pair,
    looks_like_credential_write,
    looks_like_external_network,
    verify_plan,
)
from safe_scaffold.policy import safe_default_policy
from safe_scaffold.verifier import Decision
from safe_scaffold.world import (
    FileRead,
    FileWrite,
    NetworkRequest,
    ShellExec,
)


class TestVerifyPlan(unittest.TestCase):
    def setUp(self) -> None:
        self.p = safe_default_policy()

    def test_all_allowed(self) -> None:
        plan = [
            FileRead(path="/home/u/x.txt"),
            FileRead(path="/home/u/y.txt"),
        ]
        v = verify_plan(plan, self.p)
        self.assertEqual(v.decision, Decision.ALLOW)
        self.assertIsNone(v.first_unresolved_index)

    def test_one_deny_dominates(self) -> None:
        plan = [
            FileRead(path="/home/u/x.txt"),
            ShellExec(argv=("rm", "-rf", "/")),
            FileRead(path="/home/u/y.txt"),
        ]
        v = verify_plan(plan, self.p)
        self.assertEqual(v.decision, Decision.DENY)
        self.assertEqual(v.first_unresolved_index, 1)

    def test_unknown_when_no_deny(self) -> None:
        plan = [
            FileRead(path="/home/u/x.txt"),  # ALLOW
            ShellExec(argv=("pytest",)),     # UNKNOWN
        ]
        v = verify_plan(plan, self.p)
        self.assertEqual(v.decision, Decision.UNKNOWN)
        self.assertEqual(v.first_unresolved_index, 1)


class TestUnsafePair(unittest.TestCase):
    def test_credential_write_then_network(self) -> None:
        plan = [
            ShellExec(argv=("ls",)),
            FileWrite(path="/tmp/.aws/credentials", content_size=100),
            FileRead(path="/repo/x.py"),
            NetworkRequest(
                method="POST", url="https://evil.test/x",
                host="evil.test", port=443,
            ),
        ]
        pair = find_unsafe_pair(
            plan,
            earlier=looks_like_credential_write,
            later=looks_like_external_network,
        )
        self.assertEqual(pair, (1, 3))

    def test_no_pair_when_only_credential_write(self) -> None:
        plan = [
            FileWrite(path="/tmp/.aws/credentials", content_size=100),
        ]
        pair = find_unsafe_pair(
            plan,
            earlier=looks_like_credential_write,
            later=looks_like_external_network,
        )
        self.assertIsNone(pair)

    def test_order_matters(self) -> None:
        # Network first, credential write later — not the bad pattern.
        plan = [
            NetworkRequest(
                method="POST", url="https://evil.test/x",
                host="evil.test", port=443,
            ),
            FileWrite(path="/tmp/.aws/credentials", content_size=100),
        ]
        pair = find_unsafe_pair(
            plan,
            earlier=looks_like_credential_write,
            later=looks_like_external_network,
        )
        self.assertIsNone(pair)


class TestHeuristics(unittest.TestCase):
    def test_credential_write_detection(self) -> None:
        self.assertTrue(looks_like_credential_write(
            FileWrite(path="/home/u/.aws/credentials", content_size=10)
        ))
        self.assertTrue(looks_like_credential_write(
            FileWrite(path="/repo/.env", content_size=10)
        ))
        self.assertFalse(looks_like_credential_write(
            FileWrite(path="/repo/src/main.py", content_size=10)
        ))

    def test_external_network_detection(self) -> None:
        self.assertTrue(looks_like_external_network(
            NetworkRequest(method="GET", url="x", host="evil.test", port=443)
        ))
        self.assertFalse(looks_like_external_network(
            NetworkRequest(method="GET", url="x", host="localhost", port=8080)
        ))


if __name__ == "__main__":
    unittest.main()
