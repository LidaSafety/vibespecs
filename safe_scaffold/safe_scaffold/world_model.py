"""
World model for agent actions.

A "world model" in the Guaranteed Safe AI sense (Bengio et al., 2024,
https://arxiv.org/abs/2405.06624) is an explicit, machine-checkable
description of the environment the agent acts in. Here we model a
Linux host as a set of typed actions an agent might propose to take.

Every concrete capability of a coding agent (running a shell command,
editing a file, etc.) must be representable as one of these Action
subclasses. The verifier and the NL→spec translator both consume this
type system, so adding a new action type is a single point of change.

Design notes
------------
* Actions are pydantic models — they serialize cleanly to JSON, which is
  the wire format used by Claude Code's hook protocol and by the human-
  in-the-loop UI.
* Each Action carries enough structured fields that the verifier can
  reason about it symbolically (paths, command names, hosts, etc.)
  without having to re-parse free-form strings at check time.
* The `ActionEnvelope` wraps an Action with provenance (which agent
  proposed it, which session, when) so audit logs and human prompts
  can render it faithfully.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ActionKind(str, Enum):
    """Tag for the action variants. Keeps spec rules readable."""
    SHELL_EXEC = "shell_exec"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    FILE_DELETE = "file_delete"
    NETWORK_REQUEST = "network_request"
    PROCESS_SIGNAL = "process_signal"
    ENV_READ = "env_read"


class _BaseAction(BaseModel):
    """Common fields. Subclasses set `kind` as a Literal for discrimination."""
    kind: ActionKind
    # Free-text rationale from the agent — useful context for the verifier
    # and for the human, but NEVER trusted as a security signal.
    rationale: str | None = None

    class Config:
        frozen = True  # actions are immutable once proposed


class ShellExec(_BaseAction):
    """Run a shell command.

    `command` is the canonical executable name (argv[0] resolved if possible),
    kept separate from `argv` so the verifier can reason about *what* program
    is being invoked independently of its arguments.
    """
    kind: Literal[ActionKind.SHELL_EXEC] = ActionKind.SHELL_EXEC
    command: str
    argv: list[str] = Field(default_factory=list)
    cwd: str
    # We capture the raw command line too for human display and for
    # pattern-matching rules that look at the full string (e.g. detecting
    # `curl ... | sh`).
    raw: str | None = None


class FileRead(_BaseAction):
    kind: Literal[ActionKind.FILE_READ] = ActionKind.FILE_READ
    path: str  # absolute, resolved (no `..`, no symlinks the verifier hasn't seen)


class FileWrite(_BaseAction):
    kind: Literal[ActionKind.FILE_WRITE] = ActionKind.FILE_WRITE
    path: str
    append: bool = False
    # We don't pass the full content through the spec layer — for large
    # diffs that's wasteful and the spec doesn't need it. We pass a hash
    # plus a short summary the agent provided.
    content_sha256: str
    content_summary: str | None = None
    size_bytes: int

    @classmethod
    def from_content(cls, path: str, content: bytes, **kw) -> "FileWrite":
        return cls(
            path=path,
            content_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            **kw,
        )


class FileDelete(_BaseAction):
    kind: Literal[ActionKind.FILE_DELETE] = ActionKind.FILE_DELETE
    path: str
    recursive: bool = False


class NetworkRequest(_BaseAction):
    kind: Literal[ActionKind.NETWORK_REQUEST] = ActionKind.NETWORK_REQUEST
    url: str
    method: str = "GET"
    host: str  # extracted for spec convenience; verifier should not re-parse url
    port: int | None = None


class ProcessSignal(_BaseAction):
    kind: Literal[ActionKind.PROCESS_SIGNAL] = ActionKind.PROCESS_SIGNAL
    pid: int
    signal_name: str  # "SIGTERM", "SIGKILL", ...


class EnvRead(_BaseAction):
    """Reading an environment variable. Often sensitive (API keys, tokens)."""
    kind: Literal[ActionKind.ENV_READ] = ActionKind.ENV_READ
    var_name: str


# Discriminated union — pydantic uses `kind` to pick the right subclass on
# deserialization. This is what lets a Claude Code hook payload decode
# straight into a typed Action.
Action = Annotated[
    Union[
        ShellExec,
        FileRead,
        FileWrite,
        FileDelete,
        NetworkRequest,
        ProcessSignal,
        EnvRead,
    ],
    Field(discriminator="kind"),
]


class ActionEnvelope(BaseModel):
    """An Action wrapped with provenance for audit + human display."""
    action: Action
    agent_id: str  # e.g. "claude-code", "cursor", "aider"
    session_id: str
    proposed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def short_summary(self) -> str:
        """One-line description for the human prompt."""
        a = self.action
        if isinstance(a, ShellExec):
            return f"run shell: {a.raw or (a.command + ' ' + ' '.join(a.argv))}"
        if isinstance(a, FileRead):
            return f"read file: {a.path}"
        if isinstance(a, FileWrite):
            verb = "append to" if a.append else "write"
            return f"{verb} file: {a.path} ({a.size_bytes} bytes)"
        if isinstance(a, FileDelete):
            rec = " -r" if a.recursive else ""
            return f"delete{rec}: {a.path}"
        if isinstance(a, NetworkRequest):
            return f"{a.method} {a.url}"
        if isinstance(a, ProcessSignal):
            return f"send {a.signal_name} to pid {a.pid}"
        if isinstance(a, EnvRead):
            return f"read env var: {a.var_name}"
        return repr(a)
