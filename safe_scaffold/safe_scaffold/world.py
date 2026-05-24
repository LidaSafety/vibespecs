"""Typed world model: every action an agent might take, represented as a dataclass.

This module defines the universe of actions we know how to reason about. Any
agent integration (Claude Code hook, generic shell wrapper, etc.) is responsible
for translating its native tool-call format into one of these dataclasses.

Design decisions:

1. **Closed action universe.** We intentionally enumerate the action types we
   support rather than accepting an open string field. Unknown action types
   cause the parser to raise; the verifier never sees something it does not
   understand. This is the inverse of the "skip permissions" failure mode where
   an unrecognized action defaults to allowed.

2. **Stdlib dataclasses, no pydantic.** A research artifact should be runnable
   on bare Python. Validation is explicit (in `__post_init__` and in
   `from_dict`) so reviewers can read the type rules without leaving the file.

3. **Discriminated union via class identity.** Each subclass declares `kind` as
   a class variable. `Action.from_dict` dispatches on this. This is cleaner than
   pydantic's Literal-field discriminator and works with mypy's structural
   exhaustiveness checking on `isinstance`.

4. **Identity for audit.** Every action gets a stable `id` so the journal can
   record provenance: "this rule was added in response to action X".

References:
    Bengio et al., "Towards Guaranteed Safe AI", arXiv:2405.06624 — argues for
    a small, well-typed world model as a precondition for formal verification
    of AI systems.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar


ActionId = str
"""Opaque identifier for an action. Stable across journal and prompts."""


def _fresh_id(prefix: str) -> ActionId:
    """Return a journal-safe unique id with a human-readable prefix."""
    # 64 bits of randomness is plenty; we are not relying on uniqueness across
    # universes, only within a session's journal.
    return f"{prefix}_{int(time.time())}_{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    """Base class for all actions the agent might propose.

    Concrete subclasses set `kind` as a ClassVar and add their own fields. The
    base provides identity, a creation timestamp (for the journal), and an
    optional `agent` tag so we can tell `claude-code` apart from `openhands`
    when reading audit logs later.
    """

    id: ActionId = field(default_factory=lambda: _fresh_id("act"))
    created_at: float = field(default_factory=time.time)
    agent: str = "unknown"

    # Subclasses override.
    kind: ClassVar[str] = "_base"

    # Populated by __init_subclass__.
    _registry: ClassVar[dict[str, type["Action"]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.kind == "_base":
            raise TypeError(f"Action subclass {cls.__name__} must override `kind`")
        if cls.kind in Action._registry:
            existing = Action._registry[cls.kind]
            if existing is not cls:
                raise TypeError(
                    f"Duplicate action kind {cls.kind!r}: "
                    f"already registered to {existing.__name__}"
                )
        Action._registry[cls.kind] = cls

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for the journal or for inter-process transport."""
        out: dict[str, Any] = {
            "kind": self.kind,
            "id": self.id,
            "created_at": self.created_at,
            "agent": self.agent,
        }
        # Concrete subclass-specific fields. We iterate over dataclass fields
        # rather than using asdict() so we can preserve frozen dataclass tuples
        # cleanly and avoid dragging in private fields if subclasses add any.
        from dataclasses import fields as dc_fields

        for f in dc_fields(self):
            if f.name in out:
                continue
            value = getattr(self, f.name)
            if isinstance(value, tuple):
                out[f.name] = list(value)
            else:
                out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        """Construct the appropriate Action subclass from a dict.

        Raises:
            ValueError: if `kind` is missing or unknown.
            TypeError: if required fields for the subclass are missing.
        """
        if not isinstance(data, dict):
            raise ValueError(f"Action must be a dict, got {type(data).__name__}")
        kind = data.get("kind")
        if kind is None:
            raise ValueError("Action dict must include a `kind` field")
        if kind not in cls._registry:
            raise ValueError(
                f"Unknown action kind {kind!r}. "
                f"Known: {sorted(cls._registry)}"
            )
        target = cls._registry[kind]
        # Strip kind; pass everything else through. Unknown fields are an error
        # — fail closed.
        payload = {k: v for k, v in data.items() if k != "kind"}
        # Tuple-ify list fields where the dataclass expects tuple.
        from dataclasses import fields as dc_fields

        field_types = {f.name: f.type for f in dc_fields(target)}
        for k in list(payload):
            if k not in field_types:
                raise ValueError(
                    f"Unknown field {k!r} for action kind {kind!r}. "
                    f"Known fields: {sorted(field_types)}"
                )
            # Best-effort tuple coercion for fields annotated with tuple[...]
            ftype = field_types[k]
            ftype_str = ftype if isinstance(ftype, str) else getattr(ftype, "__name__", str(ftype))
            if isinstance(payload[k], list) and "tuple" in ftype_str:
                payload[k] = tuple(payload[k])
        try:
            return target(**payload)
        except TypeError as exc:
            raise TypeError(f"Cannot construct {target.__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# Concrete actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShellExec(Action):
    """Run a shell command. `argv` is the parsed argument list (NOT a string).

    We require pre-parsed argv to dodge an entire category of shell-injection
    ambiguity. The agent adapter is responsible for parsing; if it cannot parse
    safely, it should raise rather than passing a raw string.
    """

    argv: tuple[str, ...] = ()
    cwd: str = ""
    env_overrides: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float | None = None

    kind: ClassVar[str] = "shell_exec"

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("ShellExec.argv must be non-empty")
        if any(not isinstance(a, str) for a in self.argv):
            raise TypeError("ShellExec.argv elements must be strings")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("ShellExec.timeout_seconds must be positive if set")

    @property
    def program(self) -> str:
        """The executable, i.e. argv[0]. Useful in conditions."""
        return self.argv[0]


@dataclass(frozen=True)
class FileRead(Action):
    """Read the contents of a file."""

    path: str = ""

    kind: ClassVar[str] = "file_read"

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("FileRead.path is required")


@dataclass(frozen=True)
class FileWrite(Action):
    """Write content to a file (creates or replaces).

    `content_size` is the size in bytes the agent intends to write. This lets
    policies cap output (e.g. block writes >100MB to /tmp) without needing to
    pass the full content through verification.
    """

    path: str = ""
    content_size: int = 0
    create_new: bool = False

    kind: ClassVar[str] = "file_write"

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("FileWrite.path is required")
        if self.content_size < 0:
            raise ValueError("FileWrite.content_size must be non-negative")


@dataclass(frozen=True)
class FileDelete(Action):
    """Delete a file or empty directory."""

    path: str = ""
    recursive: bool = False

    kind: ClassVar[str] = "file_delete"

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("FileDelete.path is required")


@dataclass(frozen=True)
class NetworkRequest(Action):
    """Outbound network request."""

    method: str = "GET"
    url: str = ""
    host: str = ""
    port: int = 443

    kind: ClassVar[str] = "network_request"

    _ALLOWED_METHODS: ClassVar[frozenset[str]] = frozenset(
        {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
    )

    def __post_init__(self) -> None:
        if self.method not in self._ALLOWED_METHODS:
            raise ValueError(
                f"NetworkRequest.method must be one of {sorted(self._ALLOWED_METHODS)}, "
                f"got {self.method!r}"
            )
        if not self.url:
            raise ValueError("NetworkRequest.url is required")
        if not self.host:
            raise ValueError("NetworkRequest.host is required")
        if not (0 < self.port < 65536):
            raise ValueError(f"NetworkRequest.port must be 1-65535, got {self.port}")


@dataclass(frozen=True)
class ProcessSignal(Action):
    """Send a signal to a process (e.g. SIGKILL)."""

    pid: int = 0
    signal: str = "SIGTERM"

    kind: ClassVar[str] = "process_signal"

    def __post_init__(self) -> None:
        if self.pid <= 0:
            raise ValueError(f"ProcessSignal.pid must be positive, got {self.pid}")
        if not self.signal:
            raise ValueError("ProcessSignal.signal is required")


@dataclass(frozen=True)
class EnvRead(Action):
    """Read an environment variable. Listed separately so we can deny credential
    reads (`*_TOKEN`, `*_KEY`) by default without blocking benign reads."""

    name: str = ""

    kind: ClassVar[str] = "env_read"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("EnvRead.name is required")
