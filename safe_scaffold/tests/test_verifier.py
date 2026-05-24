"""
Unit tests for the verifier — the safety-critical core.

These tests are the regression net for the decision procedure. If you
change spec.py / verifier.py / condition.py, these MUST keep passing.
"""
from __future__ import annotations

import pytest

pytest.importorskip("z3")  # skip if z3-solver isn't available

from safe_scaffold import (
    Decision, Effect, FileRead, FileWrite, Policy, Rule, RuleProvenance,
    ShellExec, verify, check_policy_property,
)
from safe_scaffold.world_model import ActionKind


def _rule(description, effect, kinds, condition):
    return Rule(
        effect=Effect(effect),
        applies_to=[ActionKind(k) for k in kinds],
        condition=condition,
        description=description,
        provenance=RuleProvenance(source="test"),
    )


def test_unknown_when_policy_empty():
    p = Policy()
    vr = verify(FileRead(path="/tmp/x"), p)
    assert vr.decision == Decision.UNKNOWN
    assert vr.matched_rules == []


def test_allow_matches_path_under():
    p = Policy(rules=[_rule(
        "reads under project", "allow", ["file_read"],
        {"op": "path_under", "field": "path", "value": "/proj"},
    )])
    assert verify(FileRead(path="/proj/a/b.py"), p).decision == Decision.ALLOW
    # Sibling, not under
    assert verify(FileRead(path="/projother/a"), p).decision == Decision.UNKNOWN
    # Exact match also counts as "under"
    assert verify(FileRead(path="/proj"), p).decision == Decision.ALLOW


def test_deny_overrides_allow():
    p = Policy(rules=[
        _rule("broad allow", "allow", ["file_write"],
              {"op": "path_under", "field": "path", "value": "/"}),
        _rule("etc deny", "deny", ["file_write"],
              {"op": "path_under", "field": "path", "value": "/etc"}),
    ])
    fw = FileWrite.from_content(path="/etc/passwd", content=b"")
    vr = verify(fw, p)
    assert vr.decision == Decision.DENY
    assert any("etc" in r.description for r in vr.matched_rules)


def test_shell_rm_rf_blocked():
    p = Policy(rules=[
        _rule("allow rm", "allow", ["shell_exec"],
              {"op": "eq", "field": "command", "value": "rm"}),
        _rule("deny rm -rf", "deny", ["shell_exec"],
              {"op": "and", "args": [
                  {"op": "eq", "field": "command", "value": "rm"},
                  {"op": "contains_arg", "field": "argv_joined", "value": "-rf"},
              ]}),
    ])
    safe_rm = ShellExec(command="rm", argv=["file.txt"], cwd="/tmp", raw="rm file.txt")
    bad_rm = ShellExec(command="rm", argv=["-rf", "/"], cwd="/tmp", raw="rm -rf /")
    assert verify(safe_rm, p).decision == Decision.ALLOW
    assert verify(bad_rm, p).decision == Decision.DENY


def test_in_operator():
    p = Policy(rules=[_rule(
        "allowed devs", "allow", ["shell_exec"],
        {"op": "in", "field": "command", "values": ["git", "pytest", "ruff"]},
    )])
    for cmd in ["git", "pytest", "ruff"]:
        assert verify(ShellExec(command=cmd, argv=[], cwd="/", raw=cmd),
                      p).decision == Decision.ALLOW
    assert verify(ShellExec(command="curl", argv=[], cwd="/", raw="curl"),
                  p).decision == Decision.UNKNOWN


def test_check_policy_property_finds_counterexample():
    # A leaky policy: allows any shell, denies only `sudo`.
    p = Policy(rules=[
        _rule("allow all shell", "allow", ["shell_exec"], {"op": "true"}),
        _rule("deny sudo", "deny", ["shell_exec"],
              {"op": "eq", "field": "command", "value": "sudo"}),
    ])
    safe, ce = check_policy_property(p, {
        "op": "and",
        "args": [
            {"op": "eq", "field": "command", "value": "rm"},
            {"op": "contains_arg", "field": "argv_joined", "value": "-rf"},
        ],
    })
    assert not safe, "policy should be detected as allowing rm -rf"
    assert "rm" in ce


def test_check_policy_property_holds_when_safe():
    p = Policy(rules=[
        _rule("allow git only", "allow", ["shell_exec"],
              {"op": "eq", "field": "command", "value": "git"}),
        _rule("deny rm -rf", "deny", ["shell_exec"],
              {"op": "and", "args": [
                  {"op": "eq", "field": "command", "value": "rm"},
                  {"op": "contains_arg", "field": "argv_joined", "value": "-rf"},
              ]}),
    ])
    safe, ce = check_policy_property(p, {
        "op": "and",
        "args": [
            {"op": "eq", "field": "command", "value": "rm"},
            {"op": "contains_arg", "field": "argv_joined", "value": "-rf"},
        ],
    })
    assert safe, f"policy should not allow rm -rf, got counterexample {ce}"


def test_field_mismatch_silently_does_not_apply():
    """A rule mentioning a field that the action kind doesn't have
    should be inert, not crash."""
    p = Policy(rules=[_rule(
        "talks about hosts but applied to file_read", "allow", ["file_read"],
        {"op": "eq", "field": "host", "value": "localhost"},
    )])
    vr = verify(FileRead(path="/x"), p)
    # No rule fires meaningfully => UNKNOWN.
    assert vr.decision == Decision.UNKNOWN
