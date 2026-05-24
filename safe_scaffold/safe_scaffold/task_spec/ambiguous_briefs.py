"""Hand-crafted ambiguous-intent fixtures for the elicitation demo.

Each fixture is the kind of input Dodds describes as the real shape of
spec-writing inputs: under-specified, contradicting itself across
sources, or written in slide-deck-quality prose. The point of these
fixtures is to show the full pipeline end-to-end:

  muddy brief
    → LLM-drafted spec (with contradictions surfaced where applicable)
    → emitted Lean 4 source
    → `lake build` confirming the structural commitments are well-typed

i.e. even when the source is muddy, the output is *sharp* Lean. The
contradictions panel makes the muddiness visible to the reviewer; the
Lean output makes the structural commitments precise.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from safe_scaffold.task_spec.spec import PositiveTest


@dataclass(frozen=True)
class AmbiguousBrief:
    """A muddy elicitation input.

    `override_positive_test` is optional and, when present, replaces the
    LLM-invented positive test. Useful for benchmarks that ship
    canonical tests (LiveCodeBench, MBPP) where letting the LLM invent
    its own test means the codegen step is graded against a different
    target than the benchmark's own oracle.
    """

    brief_id: str
    label: str                 # short display label
    description: str           # the user-facing one-sentence intent (often vague)
    starting_repo: dict[str, str]
    prose_doc: str = ""        # optional longer prose source
    existing_tests: str = ""   # optional pre-existing tests that hint at intent
    slide_deck: str = ""       # optional bullet-shaped requirements
    override_positive_test: "PositiveTest | None" = None

    def additional_sources(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.prose_doc.strip():
            out["prose_doc"] = self.prose_doc
        if self.existing_tests.strip():
            out["existing_tests"] = self.existing_tests
        if self.slide_deck.strip():
            out["slide_deck"] = self.slide_deck
        return out


# ---------------------------------------------------------------------------
# Brief A — extreme under-specification ("we know it when we see it")
# ---------------------------------------------------------------------------

A_UNDERSPEC = AmbiguousBrief(
    brief_id="a_underspec",
    label="A · 'do auth right'",
    description=(
        "Make the login flow more secure. We've had a few users complain "
        "about something feeling off and security audit is next month, so "
        "just tighten it up."
    ),
    starting_repo={
        "app.py": (
            "from flask import Flask, request, jsonify\n"
            "\n"
            "app = Flask(__name__)\n"
            "USERS = {'alice': 'password123', 'bob': 'hunter2'}\n"
            "\n"
            "@app.route('/login', methods=['POST'])\n"
            "def login():\n"
            "    data = request.get_json() or {}\n"
            "    u, p = data.get('user'), data.get('pass')\n"
            "    if USERS.get(u) == p:\n"
            "        return jsonify({'token': f'tok-{u}'})\n"
            "    return jsonify({'error': 'nope'}), 401\n"
        ),
        "tests/test_login.py": (
            "from app import app\n"
            "def test_alice_logs_in():\n"
            "    with app.test_client() as c:\n"
            "        r = c.post('/login', json={'user': 'alice', 'pass': 'password123'})\n"
            "        assert r.status_code == 200\n"
        ),
    },
    prose_doc="",  # the user gave nothing else
)


# ---------------------------------------------------------------------------
# Brief B — long PRD that contradicts itself
# ---------------------------------------------------------------------------

B_PRD_CONTRADICTS = AmbiguousBrief(
    brief_id="b_prd_contradicts",
    label="B · PRD vs itself",
    description=(
        "Add password hashing to the user signup flow. See attached PRD for "
        "details."
    ),
    starting_repo={
        "users.py": (
            "USERS: dict[str, str] = {}  # username -> stored password\n"
            "\n"
            "def signup(username: str, password: str) -> bool:\n"
            "    if username in USERS:\n"
            "        return False\n"
            "    USERS[username] = password  # TODO: hash\n"
            "    return True\n"
            "\n"
            "def verify(username: str, password: str) -> bool:\n"
            "    return USERS.get(username) == password\n"
        ),
    },
    prose_doc=(
        "PRD — User Signup Hardening (v0.3, ad-hoc)\n"
        "\n"
        "Objective: make the signup flow resistant to credential theft.\n"
        "\n"
        "Requirements:\n"
        "1. Passwords MUST be hashed with a modern KDF before storage.\n"
        "   Use bcrypt or argon2. SHA-1 / MD5 are forbidden.\n"
        "2. Passwords MUST be stored exactly as provided so the support team\n"
        "   can read them back to users who forget their login.\n"
        "3. The change should be backwards compatible — existing plaintext\n"
        "   passwords in USERS must continue to verify successfully.\n"
        "4. No new dependencies are allowed in this sprint.\n"
        "5. Use bcrypt (which is a new dependency) as per requirement 1.\n"
        "\n"
        "Out of scope: rate-limiting, password reset, audit logging.\n"
    ),
)


# ---------------------------------------------------------------------------
# Brief C — slide deck vs the test file that's already in the repo
# ---------------------------------------------------------------------------

C_SLIDES_VS_TESTS = AmbiguousBrief(
    brief_id="c_slides_vs_tests",
    label="C · slides vs tests",
    description=(
        "Implement the discount calculator per the design slides."
    ),
    starting_repo={
        "pricing.py": (
            "def discounted_price(base: float, percent: float) -> float:\n"
            "    # TODO\n"
            "    raise NotImplementedError\n"
        ),
        "tests/test_pricing.py": (
            "from pricing import discounted_price\n"
            "\n"
            "def test_no_discount():\n"
            "    # 0% off a $100 item = $100\n"
            "    assert discounted_price(100.0, 0.0) == 100.0\n"
            "\n"
            "def test_half_off():\n"
            "    # 50% off a $100 item = $50\n"
            "    assert discounted_price(100.0, 50.0) == 50.0\n"
            "\n"
            "def test_full_discount_is_zero():\n"
            "    # 100% off a $100 item = $0\n"
            "    assert discounted_price(100.0, 100.0) == 0.0\n"
            "\n"
            "def test_rejects_negative():\n"
            "    # Negative percent: function returns base unchanged\n"
            "    assert discounted_price(100.0, -10.0) == 100.0\n"
        ),
    },
    slide_deck=(
        "# Discount Calculator — Q2 Spec\n"
        "\n"
        "## Goals\n"
        "- Customer-friendly: when applying a discount, the customer always\n"
        "  pays *strictly less* than the base price (never the same, never more).\n"
        "- Generous default: a 100% discount means the item is free PLUS we\n"
        "  credit the customer $5 as an apology for any inconvenience.\n"
        "- Negative percentages indicate a SURCHARGE: -10% on $100 = $110.\n"
        "\n"
        "## Out of scope\n"
        "- Stacking discounts\n"
        "- Currency conversion\n"
    ),
)


AMBIGUOUS_BRIEFS: tuple[AmbiguousBrief, ...] = (
    A_UNDERSPEC,
    B_PRD_CONTRADICTS,
    C_SLIDES_VS_TESTS,
)

BRIEFS_BY_ID: dict[str, AmbiguousBrief] = {b.brief_id: b for b in AMBIGUOUS_BRIEFS}
