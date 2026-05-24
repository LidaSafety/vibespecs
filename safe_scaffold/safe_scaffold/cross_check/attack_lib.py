"""Library of attack templates for adversarial spec validation.

Each `AttackTemplate` encodes a class of inputs that has historically broken
implementations or revealed spec gaps. Templates fall into a few buckets:

1. Boundary values (zero, modulus-minus-one, INT_MAX). These trip
   off-by-one bugs and overflow-handling errors, including the kind of
   constant-substitution error that bit libcrux.
2. Malformed inputs (truncated, padded, with embedded special characters).
   These reveal parser disagreements between spec text and implementation —
   the PDF "potato of doom" pattern from the Galois article applied to APIs.
3. OWASP API Security Top 10 patterns: BOLA-style ID enumeration, mass
   assignment, broken authentication, injection.
4. Cross-encoding: same payload in JSON, x-www-form-urlencoded, multipart —
   reveals charset and parser disagreements.

This module is a STARTING POINT, not exhaustive. Users add domain-specific
templates. The shape of each template is uniform so the generator can iterate
mechanically.

References:
    * Symbolic Software, "On the Promises of 'High-Assurance' Cryptography",
      Feb 5, 2026 — motivating example of bug class (1).
    * OWASP API Security Top 10 (2023), https://owasp.org/API-Security/ — for
      bucket (3).
    * Galois Inc., "Specifications Don't Exist", Jun 16 2025 — for bucket (2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class AttackTemplate:
    """A reusable template for generating adversarial inputs."""

    name: str
    category: str  # "boundary" | "malformed" | "owasp" | "encoding"
    description: str
    # A generator: takes a context dict (e.g. {"int_domain": (0, 3329)}) and
    # yields concrete input values. Stateless; called fresh per use.
    generate: Callable[[dict[str, Any]], list[Any]]


# ---------------------------------------------------------------------------
# Boundary-value templates
# ---------------------------------------------------------------------------


def _int_boundaries(ctx: dict[str, Any]) -> list[Any]:
    lo, hi = ctx.get("int_domain", (0, 1 << 31))
    candidates = {
        lo, lo + 1, lo - 1,
        hi - 1, hi, hi + 1,
        0, 1, -1,
        1 << 16, (1 << 16) - 1, (1 << 16) + 1,
        1 << 31, (1 << 31) - 1, (1 << 32) - 1,
    }
    return sorted(c for c in candidates if isinstance(c, int))


def _string_boundaries(ctx: dict[str, Any]) -> list[Any]:
    max_len = ctx.get("string_max_len", 1024)
    return [
        "",
        " ",
        "\x00",
        "a" * (max_len - 1),
        "a" * max_len,
        "a" * (max_len + 1),
        "🦀",  # multibyte
        "../" * 8 + "etc/passwd",  # path traversal
        "<script>alert(1)</script>",  # XSS canary
        "' OR 1=1 --",  # SQLi canary
    ]


BOUNDARY_INT = AttackTemplate(
    name="boundary_int",
    category="boundary",
    description=(
        "Integer values at the edges of expected domains: zero, modulus, "
        "powers of two, and just past common 32-bit limits. "
        "Catches off-by-one and constant-substitution bugs in the style of "
        "the Cryspen libcrux ML-KEM finding."
    ),
    generate=_int_boundaries,
)

BOUNDARY_STRING = AttackTemplate(
    name="boundary_string",
    category="boundary",
    description=(
        "Strings at boundary lengths, with multibyte content, and with "
        "common injection canaries."
    ),
    generate=_string_boundaries,
)


# ---------------------------------------------------------------------------
# Malformed-input templates
# ---------------------------------------------------------------------------


def _malformed_json(ctx: dict[str, Any]) -> list[Any]:
    return [
        "",
        "null",
        "{",
        '{"a": }',
        '{"a": 1, }',  # trailing comma
        '{"a": 1, "a": 2}',  # duplicate keys (RFC 8259 says implementations vary)
        '{"a": NaN}',  # not valid JSON; many parsers accept
        '{"\\u0000": 1}',  # embedded NUL in key
    ]


def _malformed_pdf_signal(ctx: dict[str, Any]) -> list[Any]:
    # Per Galois's DARPA SafeDocs work: PDFs that sit in the liminal region
    # where readers disagree. We model two: truncated header, ambiguous xref.
    return [
        b"%PDF-1.4\n%trailer wrong",
        b"%PDF-1.4\n1 0 obj <</Type /Page>> endobj\n%trailer no xref",
        b"%PDF-2.0\n",  # technically valid, but most readers reject
    ]


MALFORMED_JSON = AttackTemplate(
    name="malformed_json",
    category="malformed",
    description=(
        "JSON payloads that sit in the gray zone where RFC 8259 permits "
        "implementations to disagree. Useful for catching parser-divergence "
        "bugs between spec language and implementation."
    ),
    generate=_malformed_json,
)

MALFORMED_PDF = AttackTemplate(
    name="malformed_pdf",
    category="malformed",
    description=(
        "PDF payloads in the 'potato of doom' liminal region. Tests whether "
        "your PDF processor agrees with itself across versions."
    ),
    generate=_malformed_pdf_signal,
)


# ---------------------------------------------------------------------------
# OWASP API Top 10 templates
# ---------------------------------------------------------------------------


def _bola_ids(ctx: dict[str, Any]) -> list[Any]:
    own_id = ctx.get("own_user_id", 1)
    return [
        own_id,                # control
        own_id + 1,            # adjacent
        own_id - 1,            # adjacent backward
        0,                     # often an admin row
        -1,                    # sign error
        2 ** 31 - 1,           # max int32
        99999999999999999,     # past 64-bit
        f"{own_id}/../{own_id + 1}",  # path traversal
        f"{own_id}; DROP TABLE users",  # ASCII canary
    ]


def _mass_assignment_keys(ctx: dict[str, Any]) -> list[Any]:
    """Field names that should be unsettable by clients (admin, role, etc.)"""
    return [
        "is_admin",
        "isAdmin",
        "role",
        "permissions",
        "verified",
        "is_verified",
        "balance",
        "credit",
        "id",
        "user_id",
    ]


OWASP_BOLA = AttackTemplate(
    name="owasp_bola",
    category="owasp",
    description=(
        "Broken Object Level Authorization (OWASP API1:2023). Probes ID "
        "fields with adjacent and out-of-domain values to find missing "
        "authorization checks."
    ),
    generate=_bola_ids,
)

OWASP_MASS_ASSIGNMENT = AttackTemplate(
    name="owasp_mass_assignment",
    category="owasp",
    description=(
        "OWASP API6:2023. Tests whether the server accepts client-supplied "
        "values for sensitive fields like is_admin, role, balance."
    ),
    generate=_mass_assignment_keys,
)


# ---------------------------------------------------------------------------
# All templates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttackLibrary:
    """A collection of templates."""

    templates: tuple[AttackTemplate, ...] = field(default_factory=tuple)

    def by_category(self, category: str) -> tuple[AttackTemplate, ...]:
        return tuple(t for t in self.templates if t.category == category)


DEFAULT_LIBRARY = AttackLibrary(
    templates=(
        BOUNDARY_INT,
        BOUNDARY_STRING,
        MALFORMED_JSON,
        MALFORMED_PDF,
        OWASP_BOLA,
        OWASP_MASS_ASSIGNMENT,
    )
)
