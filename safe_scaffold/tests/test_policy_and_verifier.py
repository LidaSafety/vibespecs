"""Tests for the Policy/Rule/Effect model and the verifier."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from safe_scaffold.conditions import parse_condition
from safe_scaffold.policy import (
    Effect,
    Policy,
    Provenance,
    Rule,
    safe_default_policy,
)
from safe_scaffold.verifier import Decision, verify
from safe_scaffold.world import (
    EnvRead,
    FileDelete,
    FileRead,
    FileWrite,
    NetworkRequest,
    ShellExec,
)


def _rule(rid: str, effect: Effect, cond_dict: dict) -> Rule:
    return Rule(
        id=rid,
        effect=effect,
        condition=parse_condition(cond_dict),
        description="",
    )


class TestPolicyConstruction(unittest.TestCase):
    def test_empty_policy(self) -> None:
        p = Policy()
        self.assertEqual(p.rules, ())

    def test_with_rule_immutable(self) -> None:
        p = Policy()
        r = _rule("r1", Effect.DENY, {"type": "kind_is", "kind": "shell_exec"})
        p2 = p.with_rule(r)
        self.assertIsNot(p, p2)
        self.assertEqual(p.rules, ())
        self.assertEqual(len(p2.rules), 1)
        self.assertEqual(p2.version, p.version + 1)

    def test_duplicate_rule_id_rejected(self) -> None:
        p = Policy()
        r = _rule("r1", Effect.DENY, {"type": "kind_is", "kind": "shell_exec"})
        p2 = p.with_rule(r)
        with self.assertRaises(ValueError):
            p2.with_rule(r)

    def test_without_rule(self) -> None:
        p = Policy()
        r = _rule("r1", Effect.DENY, {"type": "kind_is", "kind": "shell_exec"})
        p2 = p.with_rule(r)
        p3 = p2.without_rule("r1")
        self.assertEqual(p3.rules, ())

    def test_without_missing_rule_noop(self) -> None:
        p = Policy()
        self.assertIs(p.without_rule("nope"), p)


class TestSerialization(unittest.TestCase):
    def test_json_round_trip(self) -> None:
        p = safe_default_policy()
        js = p.to_json()
        p2 = Policy.from_json(js)
        self.assertEqual(p, p2)

    def test_save_load(self) -> None:
        p = safe_default_policy()
        with TemporaryDirectory() as td:
            f = Path(td) / "policy.json"
            p.save(f)
            self.assertTrue(f.exists())
            data = json.loads(f.read_text())
            self.assertEqual(data["name"], "safe_defaults")
            p2 = Policy.load(f)
            self.assertEqual(p, p2)


class TestVerifyDenyOverrides(unittest.TestCase):
    """Deny-overrides: any matching DENY wins, even with matching ALLOW rules."""

    def test_deny_beats_allow(self) -> None:
        p = Policy()
        p = p.with_rule(_rule("allow_all_shell", Effect.ALLOW,
                              {"type": "kind_is", "kind": "shell_exec"}))
        p = p.with_rule(_rule("deny_rm", Effect.DENY, {
            "type": "and", "of": [
                {"type": "kind_is", "kind": "shell_exec"},
                {"type": "eq", "ref": "program", "value": "rm"},
            ],
        }))
        v = verify(ShellExec(argv=("rm", "-rf", "/")), p)
        self.assertEqual(v.decision, Decision.DENY)

    def test_no_match_is_unknown(self) -> None:
        p = Policy()
        v = verify(ShellExec(argv=("ls",)), p)
        self.assertEqual(v.decision, Decision.UNKNOWN)


class TestSafeDefaults(unittest.TestCase):
    def setUp(self) -> None:
        self.p = safe_default_policy()

    def test_rm_rf_root_denied(self) -> None:
        v = verify(ShellExec(argv=("rm", "-rf", "/")), self.p)
        self.assertEqual(v.decision, Decision.DENY)

    def test_rm_rf_home_denied(self) -> None:
        v = verify(ShellExec(argv=("rm", "-rf", "$HOME")), self.p)
        self.assertEqual(v.decision, Decision.DENY)

    def test_rm_rf_tmp_unknown(self) -> None:
        # Recursive rm but target isn't root/home → UNKNOWN (no rule matched).
        v = verify(ShellExec(argv=("rm", "-rf", "/tmp/scratch")), self.p)
        self.assertEqual(v.decision, Decision.UNKNOWN)

    def test_write_etc_denied(self) -> None:
        v = verify(FileWrite(path="/etc/passwd", content_size=10), self.p)
        self.assertEqual(v.decision, Decision.DENY)

    def test_delete_etc_denied(self) -> None:
        v = verify(FileDelete(path="/etc/hosts"), self.p)
        self.assertEqual(v.decision, Decision.DENY)

    def test_credential_env_denied(self) -> None:
        v = verify(EnvRead(name="AWS_SECRET_ACCESS_KEY"), self.p)
        self.assertEqual(v.decision, Decision.DENY)

    def test_regular_env_unknown(self) -> None:
        v = verify(EnvRead(name="PATH"), self.p)
        self.assertEqual(v.decision, Decision.UNKNOWN)

    def test_read_home_allowed(self) -> None:
        v = verify(FileRead(path="/home/user/notes.txt"), self.p)
        self.assertEqual(v.decision, Decision.ALLOW)

    def test_read_etc_not_allowed_by_default(self) -> None:
        v = verify(FileRead(path="/etc/passwd"), self.p)
        # The allow_read_in_repo rule excludes /etc, so this falls through.
        self.assertEqual(v.decision, Decision.UNKNOWN)


class TestVerdictExplain(unittest.TestCase):
    def test_explain_deny(self) -> None:
        p = safe_default_policy()
        v = verify(ShellExec(argv=("rm", "-rf", "/")), p)
        text = v.explain()
        self.assertIn("DENY", text)
        self.assertIn("deny_rm_recursive_root", text)


class TestProvenance(unittest.TestCase):
    def test_provenance_preserved(self) -> None:
        prov = Provenance(created_by="test", source_nl="block sudo", notes="from spec")
        r = Rule(
            id="r1",
            effect=Effect.DENY,
            condition=parse_condition({"type": "kind_is", "kind": "shell_exec"}),
            provenance=prov,
        )
        p = Policy().with_rule(r)
        loaded = Policy.from_json(p.to_json())
        self.assertEqual(loaded.rules[0].provenance.created_by, "test")
        self.assertEqual(loaded.rules[0].provenance.source_nl, "block sudo")


if __name__ == "__main__":
    unittest.main()
