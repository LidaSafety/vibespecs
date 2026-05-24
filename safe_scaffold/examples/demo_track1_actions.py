"""
Track 1 demo: NL policy → action verification.

This script does the full Track 1 loop end-to-end *without* needing an
Anthropic API key, by using a StubLLMClient whose canned response is a
realistic example of what the real translator emits. Replace
`StubLLMClient(...)` with `AnthropicTranslatorClient()` to run against
the live API.

What you should see when you run this:
    Action 1 (read inside project)  -> ALLOW
    Action 2 (write inside project) -> ALLOW
    Action 3 (write to /etc/passwd) -> DENY (matched the path_under deny rule)
    Action 4 (rm -rf /)             -> DENY
    Action 5 (curl example.com)     -> UNKNOWN (policy says nothing about network)

    Then we run check_policy_property against a `rm -rf /` pattern and
    confirm the policy formally cannot allow it.
"""
from __future__ import annotations

import json
import sys

from safe_scaffold import (
    Decision, FileRead, FileWrite, NetworkRequest, ShellExec, Translator,
    check_policy_property, verify,
)
from safe_scaffold.translator import StubLLMClient


# What the LLM-driven translator emits for the description below.
# This was hand-checked against the DSL; in the real loop the LLM
# produces this from the user's NL.
CANNED_TRANSLATOR_OUTPUT = json.dumps([
    {
        "description": "Allow reads anywhere under the project root",
        "effect": "allow",
        "applies_to": ["file_read"],
        "condition": {
            "op": "path_under",
            "field": "path",
            "value": "/home/me/projects/foo",
        },
        "rationale": "User said: read/write under project foo",
    },
    {
        "description": "Allow writes anywhere under the project root",
        "effect": "allow",
        "applies_to": ["file_write"],
        "condition": {
            "op": "path_under",
            "field": "path",
            "value": "/home/me/projects/foo",
        },
        "rationale": "User said: read/write under project foo",
    },
    {
        "description": "Deny writes to /etc, /usr, /var",
        "effect": "deny",
        "applies_to": ["file_write", "file_delete"],
        "condition": {
            "op": "or",
            "args": [
                {"op": "path_under", "field": "path", "value": "/etc"},
                {"op": "path_under", "field": "path", "value": "/usr"},
                {"op": "path_under", "field": "path", "value": "/var"},
            ],
        },
        "rationale": "Never touch system directories",
    },
    {
        "description": "Allow git, pytest, ruff",
        "effect": "allow",
        "applies_to": ["shell_exec"],
        "condition": {
            "op": "in",
            "field": "command",
            "values": ["git", "pytest", "ruff"],
        },
        "rationale": "User-named allowed dev commands",
    },
    {
        "description": "Deny destructive shell: rm -rf, sudo, dd",
        "effect": "deny",
        "applies_to": ["shell_exec"],
        "condition": {
            "op": "or",
            "args": [
                {"op": "eq", "field": "command", "value": "sudo"},
                {"op": "eq", "field": "command", "value": "dd"},
                {"op": "and", "args": [
                    {"op": "eq", "field": "command", "value": "rm"},
                    {"op": "contains_arg", "field": "argv_joined", "value": "-rf"},
                ]},
            ],
        },
        "rationale": "Standard guardrail",
    },
])


def main() -> int:
    nl = (
        "Let the agent read and write files anywhere under "
        "/home/me/projects/foo. Don't let it write to /etc, /usr, /var. "
        "Allow it to run git, pytest, ruff. Block rm -rf, sudo, dd."
    )

    translator = Translator(
        StubLLMClient(CANNED_TRANSLATOR_OUTPUT),
        project_root="/home/me/projects/foo",
    )
    policy = translator.translate(nl)
    print(f"Compiled policy v{policy.version}: {len(policy.rules)} rules")
    for r in policy.rules:
        print(f"  [{r.effect.value}] {r.description}")
    print()

    test_actions = [
        ("read inside project",
            FileRead(path="/home/me/projects/foo/src/main.py")),
        ("write inside project",
            FileWrite.from_content(
                path="/home/me/projects/foo/src/main.py", content=b"x = 1\n")),
        ("write /etc/passwd",
            FileWrite.from_content(path="/etc/passwd", content=b"...")),
        ("rm -rf /",
            ShellExec(command="rm", argv=["-rf", "/"], cwd="/tmp", raw="rm -rf /")),
        ("curl example.com",
            NetworkRequest(url="https://example.com/", method="GET", host="example.com")),
    ]

    for label, action in test_actions:
        vr = verify(action, policy)
        print(f"{label:30s} -> {vr.decision.value.upper()}")
        if vr.matched_rules:
            for r in vr.matched_rules:
                print(f"    matched [{r.id}] {r.description}")

    print()
    # Now prove the policy can never permit `rm -rf /`
    safe, ce = check_policy_property(policy, {
        "op": "and",
        "args": [
            {"op": "eq", "field": "command", "value": "rm"},
            {"op": "contains_arg", "field": "argv_joined", "value": "-rf"},
            {"op": "contains_arg", "field": "argv_joined", "value": "/"},
        ],
    })
    if safe:
        print("Property check: policy provably cannot allow `rm -rf` with `/` arg. ✓")
    else:
        print(f"Property check FAILED. Counterexample: {ce}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
