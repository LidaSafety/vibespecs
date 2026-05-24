"""
Interceptor: adapts coding-agent tool calls into our world model and
runs the verify → maybe-ask-human → allow-or-deny loop.

This module is what the proposal calls the "scaffold around current
coding agents". It exposes two surfaces:

  1. `parse_claude_code_hook_payload(stdin_json)` — convert the JSON
     that Claude Code sends a PreToolUse hook into one of our Actions.
     The Claude Code hook protocol is still evolving; this adapter is
     deliberately small and isolated so a single update can track it.
     (As of writing, the relevant docs are at
     https://docs.claude.com/en/docs/claude-code/hooks — the field
     shapes there should be the source of truth.)

  2. `Interceptor.run(envelope)` — the top-level decision loop, useful
     both from a hook script and from a unit test. It returns a simple
     enum (ALLOW / DENY) and, where the policy was updated, the new
     Policy object.

Other agents (Cursor, aider, etc.) would each get their own parser
function. The verify/ask loop downstream is shared.
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .human_loop import HumanLoopOutcome, Journal, Prompter, resolve_unknown, terminal_prompter
from .spec import Policy
from .translator import Translator
from .verifier import Decision, verify
from .world_model import (
    Action, ActionEnvelope, FileDelete, FileRead, FileWrite, NetworkRequest,
    ShellExec,
)


# ---------------------------------------------------------------------------
# Adapters from agent payloads to typed Actions
# ---------------------------------------------------------------------------

class HookParseError(ValueError):
    """Raised when a hook payload can't be mapped to our world model.

    By design we fail closed: if we can't parse it, the interceptor will
    treat it as UNKNOWN and prompt the human, rather than letting it
    through unchecked."""


def parse_claude_code_hook_payload(payload: dict[str, Any], cwd: str | None = None) -> Action:
    """Adapt a Claude Code PreToolUse hook payload to an Action.

    The current Claude Code hook protocol sends, roughly:
        {
          "tool_name": "Bash" | "Edit" | "Write" | "Read" | ...,
          "tool_input": { ...tool-specific fields... },
          "session_id": "...",
          ...
        }

    We map Bash -> ShellExec, Edit/Write -> FileWrite, Read -> FileRead,
    etc. Tool names this adapter doesn't recognize raise HookParseError
    (which the interceptor handles by going straight to UNKNOWN).
    """
    tool = payload.get("tool_name") or payload.get("tool")
    inp = payload.get("tool_input") or payload.get("input") or {}
    cwd = cwd or payload.get("cwd") or os.getcwd()

    if tool == "Bash":
        raw = inp.get("command", "")
        try:
            argv = shlex.split(raw)
        except ValueError:
            argv = raw.split()
        cmd = argv[0] if argv else ""
        return ShellExec(
            command=cmd,
            argv=argv[1:],
            cwd=cwd,
            raw=raw,
            rationale=inp.get("description"),
        )

    if tool in ("Read",):
        return FileRead(path=_abs(inp["file_path"], cwd))

    if tool in ("Write", "Edit", "MultiEdit"):
        path = _abs(inp["file_path"], cwd)
        # `Edit` doesn't send total content; we approximate the size and
        # leave the content hash empty. Spec rules generally don't care
        # about size for edits, only paths.
        content = (inp.get("new_string") or inp.get("content") or "").encode()
        return FileWrite.from_content(
            path=path,
            content=content,
            append=False,
        )

    if tool in ("WebFetch", "WebSearch"):
        url = inp.get("url") or ""
        parsed = urlparse(url)
        return NetworkRequest(
            url=url,
            method="GET",
            host=parsed.hostname or "",
            port=parsed.port,
        )

    raise HookParseError(f"unrecognized Claude Code tool: {tool!r}")


def _abs(p: str, cwd: str) -> str:
    return p if p.startswith("/") else os.path.normpath(os.path.join(cwd, p))


# ---------------------------------------------------------------------------
# The interceptor proper
# ---------------------------------------------------------------------------

@dataclass
class InterceptDecision:
    decision: Decision
    policy: Policy
    abort_session: bool = False
    reason: str = ""


class Interceptor:
    """Wires verifier + human loop into a single callable.

    Lifecycle:
        intc = Interceptor(policy, translator, journal)
        for envelope in incoming_actions:
            result = intc.run(envelope)
            # Forward decision to the agent (allow/deny/abort).
            # If the policy changed (result.policy.version > before),
            # persist it.
    """
    def __init__(
        self,
        policy: Policy,
        translator: Translator,
        journal: Journal | None = None,
        prompter: Prompter = terminal_prompter,
    ):
        self.policy = policy
        self.translator = translator
        self.journal = journal
        self.prompter = prompter

    def run(self, envelope: ActionEnvelope) -> InterceptDecision:
        vr = verify(envelope.action, self.policy)

        if vr.decision == Decision.ALLOW:
            self._journal_silent(envelope, vr, "allow")
            return InterceptDecision(
                Decision.ALLOW, self.policy, reason=vr.explanation
            )

        # DENY and UNKNOWN both consult the human (with denial as the
        # default), per the proposal: the user is asked to confirm or
        # override, and may install a rule.
        outcome: HumanLoopOutcome = resolve_unknown(
            envelope, vr, self.policy, self.translator,
            prompter=self.prompter, journal=self.journal,
        )
        self.policy = outcome.policy
        return InterceptDecision(
            outcome.final_decision,
            self.policy,
            abort_session=outcome.abort_session,
            reason=vr.explanation,
        )

    def _journal_silent(self, envelope: ActionEnvelope, vr, final: str) -> None:
        """Log allows that didn't need human attention. We still record
        them so the audit log shows every decision, not just the
        contentious ones."""
        if self.journal is None:
            return
        from datetime import datetime, timezone
        from .human_loop import JournalEntry
        self.journal.append(JournalEntry(
            timestamp=datetime.now(timezone.utc),
            envelope=envelope,
            verifier_result_decision=vr.decision.value,
            matched_rule_ids=[r.id for r in vr.matched_rules],
            human_response=None,
            policy_version_before=self.policy.version,
            policy_version_after=self.policy.version,
            final_decision=final,
        ))


def wrap_envelope(action: Action, agent_id: str = "claude-code", session_id: str | None = None) -> ActionEnvelope:
    return ActionEnvelope(
        action=action,
        agent_id=agent_id,
        session_id=session_id or uuid4().hex,
    )
