"""
ServerSpec: the world model for server programs.

This is the input to both:
  * the agent that writes the server code (it generates code that
    satisfies the spec),
  * the adversarial test generator that tries to break it.

Scope (intentionally narrow, per the proposal): we model HTTP-style
services with a fixed set of endpoints, optional bearer-token auth,
and JSON request/response bodies. We do NOT try to model long-lived
connections, websockets, or non-HTTP RPC.

Why a structured spec rather than free-form NL?
    The verifier (an adversarial LLM) generates more useful test cases
    when it knows the endpoint surface precisely. Telling it "test the
    server" yields lazy tests; telling it "test that GET
    /users/{id} returns 404 when the caller's token doesn't own user
    `id`, and never returns another user's email" yields targeted
    attacks. The structured spec encodes those targets.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class AuthScheme(str, Enum):
    NONE = "none"
    BEARER = "bearer"      # Authorization: Bearer <token>
    SESSION = "session"    # session cookie


class ParamSource(str, Enum):
    PATH = "path"          # /users/{id}
    QUERY = "query"        # /search?q=...
    BODY = "body"          # JSON body field
    HEADER = "header"


class Param(BaseModel):
    name: str
    source: ParamSource
    type: Literal["string", "integer", "boolean", "number", "object", "array"]
    required: bool = True
    # Free-text constraint description; the adversarial gen uses this
    # to know what valid vs invalid looks like.
    constraints: str | None = None  # e.g. "must be a uuid", "1 <= n <= 100"


class Endpoint(BaseModel):
    method: HttpMethod
    path: str  # OpenAPI-style, e.g. "/users/{id}"
    summary: str
    auth: AuthScheme = AuthScheme.NONE
    params: list[Param] = Field(default_factory=list)
    request_body_schema: dict[str, Any] | None = None  # JSON schema (loose)
    response_body_schema: dict[str, Any] | None = None
    # Behavioral postconditions, in NL. Adversarial gen turns these
    # into test assertions.
    postconditions: list[str] = Field(default_factory=list)
    # Per-endpoint expected status codes
    success_status: int = 200
    error_statuses: list[int] = Field(default_factory=lambda: [400, 401, 403, 404])


class SecurityProperty(BaseModel):
    """Cross-cutting invariants the adversarial gen must try to break.

    Drawn from OWASP API Top 10 categories by default; users can add
    custom properties.
    """
    name: str        # e.g. "broken_object_level_authorization"
    description: str # NL description; fed to the adversarial gen
    severity: Literal["low", "medium", "high", "critical"] = "high"

    @classmethod
    def owasp_defaults(cls) -> list["SecurityProperty"]:
        """Standard OWASP API Top 10 properties (2023 edition). The
        adversarial generator uses these as a baseline test bank."""
        return [
            cls(name="bola",
                description="Broken Object Level Authorization: callers must "
                            "not be able to read/modify objects belonging to "
                            "other users by guessing or substituting IDs.",
                severity="critical"),
            cls(name="broken_auth",
                description="Broken authentication: requests without valid "
                            "tokens, with expired tokens, or with tokens for "
                            "other users must be rejected.",
                severity="critical"),
            cls(name="property_level_auth",
                description="Excessive data exposure: responses must not "
                            "contain sensitive fields (password hashes, "
                            "internal IDs) even if the caller is authorized.",
                severity="high"),
            cls(name="resource_exhaustion",
                description="Endpoints must enforce limits on input size, "
                            "result set size, and request rate.",
                severity="medium"),
            cls(name="injection",
                description="Inputs reaching SQL, OS commands, or template "
                            "engines must be sanitized; classic SQLi, NoSQLi, "
                            "and command-injection payloads must be rejected "
                            "or escaped.",
                severity="critical"),
            cls(name="ssrf",
                description="If the server fetches user-supplied URLs, it "
                            "must refuse internal/loopback/cloud-metadata "
                            "addresses.",
                severity="high"),
            cls(name="path_traversal",
                description="Endpoints handling file paths must reject "
                            "`..`, absolute paths, and symlinked escapes.",
                severity="critical"),
            cls(name="mass_assignment",
                description="Request bodies must not be able to set "
                            "fields beyond those the endpoint documents "
                            "(e.g. setting is_admin via a profile update).",
                severity="high"),
        ]


class ServerSpec(BaseModel):
    """Complete spec of a server program."""
    name: str
    description: str
    base_url: str = "http://localhost:8000"
    auth: AuthScheme = AuthScheme.NONE
    endpoints: list[Endpoint]
    security_properties: list[SecurityProperty] = Field(
        default_factory=SecurityProperty.owasp_defaults
    )
    # Optional: known test fixtures (preexisting users, tokens, etc.) the
    # adversarial gen can reference in tests.
    fixtures: dict[str, Any] = Field(default_factory=dict)
