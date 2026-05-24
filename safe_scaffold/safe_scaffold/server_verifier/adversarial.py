"""
Adversarial test-case generator.

Given a `ServerSpec`, this module produces a pytest test suite designed
to find security and behavioral defects in any server purporting to
implement the spec.

The generator is itself an LLM (we use Claude by default), prompted with:
    * The server spec, machine-readable.
    * A library of few-shot vulnerability exemplars, one or more per
      OWASP API security category. Each exemplar shows an attack
      pattern and a corresponding pytest assertion that catches it.
    * The instruction to produce tests that are minimal, self-
      contained, and that *would fail* on a naive implementation.

Why "white-box" adversarial generation?
    The proposal calls for white-box gen because the adversary has the
    spec (it's the same artifact the agent saw when writing the code).
    That parallels real-world red-teamers reading the API docs before
    attacking. In practice this gives the generator enough structure
    to make targeted tests, not just fuzzy ones.

Important honesty note
----------------------
We are NOT claiming the generated tests are exhaustive. They are
*evidence*: passing them is a necessary but not sufficient condition
for the server being secure. This is the empirical-verifier
weakness that the proposal flags ("we are not overly concerned with
being complete"). In the fellowship, the research question is: how
much defect-finding power do we get from few-shot adversarial tests vs
random fuzzing vs static analysis? That's the experimental contribution
of this track.
"""
from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass

from ..translator import LLMClient
from .spec import SecurityProperty, ServerSpec


# Few-shot exemplars: one per OWASP category. Each exemplar shows
# (a) what a vulnerable handler might look like and (b) the test that
# catches it. The generator is shown all of these in-context so it
# learns the *shape* of effective tests, not just the categories.
#
# These are intentionally short. The fellowship plan should expand this
# bank — both with more exemplars per category and with retrieval
# (pick the K most-relevant exemplars per endpoint) for a finer-grained
# prompt.

_FEW_SHOT_EXEMPLARS: dict[str, str] = {
    "bola": textwrap.dedent("""\
        Vulnerability: endpoint GET /users/{id} returns the user record
        for any id, including users that are not the caller.

        Test (uses fixtures alice_token, bob_user_id):
            def test_bola_users(client, alice_token, bob_user_id):
                r = client.get(f"/users/{bob_user_id}",
                               headers={"Authorization": f"Bearer {alice_token}"})
                # Alice must not be able to read Bob's record
                assert r.status_code in (403, 404), r.text
    """),
    "injection": textwrap.dedent("""\
        Vulnerability: GET /search?q= interpolates `q` into a raw SQL
        query, allowing a `' OR 1=1 --` payload to dump rows.

        Test:
            def test_sql_injection_search(client, alice_token):
                payload = "' OR 1=1 --"
                r = client.get("/search", params={"q": payload},
                               headers={"Authorization": f"Bearer {alice_token}"})
                # Must not return more than the caller is normally allowed to see.
                if r.status_code == 200:
                    rows = r.json()
                    # A naive impl returns ALL rows; that's the failure mode.
                    assert len(rows) <= EXPECTED_MAX_ROWS_FOR_ALICE
    """),
    "path_traversal": textwrap.dedent("""\
        Vulnerability: GET /files?name=foo reads from disk without
        rejecting `..` components, so `name=../../etc/passwd` leaks
        system files.

        Test:
            @pytest.mark.parametrize("payload", [
                "../../etc/passwd", "..\\\\..\\\\windows\\\\win.ini",
                "/etc/passwd", "....//....//etc/passwd",
            ])
            def test_path_traversal(client, alice_token, payload):
                r = client.get("/files", params={"name": payload},
                               headers={"Authorization": f"Bearer {alice_token}"})
                assert r.status_code in (400, 403, 404), r.text
                # And the response body should never look like /etc/passwd
                assert "root:" not in r.text
    """),
    "mass_assignment": textwrap.dedent("""\
        Vulnerability: PATCH /users/{id} blindly merges the JSON body
        into the user record, so a caller can set `is_admin: true`.

        Test:
            def test_mass_assignment_no_admin(client, alice_token, alice_user_id):
                r = client.patch(f"/users/{alice_user_id}",
                                 json={"display_name": "Alice", "is_admin": True},
                                 headers={"Authorization": f"Bearer {alice_token}"})
                # Whether 200 or 400, the resulting record must not be admin.
                me = client.get(f"/users/{alice_user_id}",
                                headers={"Authorization": f"Bearer {alice_token}"}).json()
                assert me.get("is_admin") is not True
    """),
    "broken_auth": textwrap.dedent("""\
        Vulnerability: endpoints accept any non-empty Authorization
        header, or accept tokens after they should have expired.

        Test:
            @pytest.mark.parametrize("bad_header", [
                "", "Bearer", "Bearer ", "Bearer junk",
                "Basic Zm9vOmJhcg==",
            ])
            def test_invalid_auth_rejected(client, bad_header):
                r = client.get("/users/me", headers={"Authorization": bad_header})
                assert r.status_code == 401, r.text
    """),
    "property_level_auth": textwrap.dedent("""\
        Vulnerability: GET /users/me returns the user's password_hash
        or internal id.

        Test:
            FORBIDDEN_FIELDS = {"password_hash", "password", "_id", "internal_id"}
            def test_no_sensitive_fields(client, alice_token):
                r = client.get("/users/me",
                               headers={"Authorization": f"Bearer {alice_token}"})
                assert r.status_code == 200
                body = r.json()
                leaked = FORBIDDEN_FIELDS & set(body.keys())
                assert not leaked, f"leaked sensitive fields: {leaked}"
    """),
    "resource_exhaustion": textwrap.dedent("""\
        Vulnerability: POST /items accepts arbitrarily large arrays,
        or GET /things has no pagination.

        Test:
            def test_oversized_body_rejected(client, alice_token):
                huge = {"items": [{"x": i} for i in range(10_000)]}
                r = client.post("/items", json=huge,
                                headers={"Authorization": f"Bearer {alice_token}"})
                assert r.status_code in (400, 413), r.text
    """),
    "ssrf": textwrap.dedent("""\
        Vulnerability: POST /fetch fetches a user-supplied URL,
        including 169.254.169.254 (AWS metadata) and localhost.

        Test:
            @pytest.mark.parametrize("url", [
                "http://169.254.169.254/latest/meta-data/",
                "http://localhost:6379/",
                "http://127.0.0.1:22",
                "http://[::1]/",
            ])
            def test_ssrf_blocked(client, alice_token, url):
                r = client.post("/fetch", json={"url": url},
                                headers={"Authorization": f"Bearer {alice_token}"})
                assert r.status_code in (400, 403), r.text
    """),
}


@dataclass
class GeneratedTestSuite:
    """The output of one generation pass."""
    pytest_code: str
    rationale: str  # what the generator says it was looking for
    properties_targeted: list[str]


_ADVERSARIAL_SYSTEM = """You are an automated security red-teamer for HTTP
services. You are given a structured specification of a server, and you
produce a single pytest module that attempts to demonstrate violations
of the specified security properties.

Constraints:
- Output a single Python module. Use `requests` and `pytest`. Fixtures
  named `client`, plus any documented in the spec's fixtures section,
  may be assumed to exist (you do NOT define them).
- Each test must be small (≤25 lines), parametrized where natural, and
  assert against the spec's stated endpoints and postconditions.
- Tag each test with the security_property name(s) it targets via a
  pytest mark, e.g. `@pytest.mark.target("bola")`.
- Do NOT write tests that depend on a specific implementation detail
  (e.g. the choice of DB). Tests must be valid against any server
  claiming to satisfy the spec.
- Output ONLY the Python source, no fences, no commentary.
"""


class AdversarialTestGenerator:
    """Generates a pytest suite from a ServerSpec.

    Two-phase generation:
      Phase 1: per security property, pick the most relevant endpoints
               and produce a targeted test file.
      Phase 2: merge into one module, dedupe overlapping tests.

    For the prototype we just do one big call. Phase split is the
    natural next step when prompt size becomes a problem.
    """

    def __init__(self, client: LLMClient):
        self.client = client

    def generate(
        self,
        spec: ServerSpec,
        extra_exemplars: dict[str, str] | None = None,
    ) -> GeneratedTestSuite:
        exemplars = dict(_FEW_SHOT_EXEMPLARS)
        if extra_exemplars:
            exemplars.update(extra_exemplars)

        # Only include exemplars relevant to the properties on this spec.
        wanted = {p.name for p in spec.security_properties}
        relevant = {k: v for k, v in exemplars.items() if k in wanted}

        user_msg = self._build_user_msg(spec, relevant)
        code = self.client.complete(_ADVERSARIAL_SYSTEM, user_msg).strip()
        if code.startswith("```"):
            code = code.strip("`")
            if code.startswith("python"):
                code = code[6:]
            code = code.strip()

        return GeneratedTestSuite(
            pytest_code=code,
            rationale="generated against properties: " + ", ".join(sorted(wanted)),
            properties_targeted=sorted(wanted),
        )

    def _build_user_msg(
        self,
        spec: ServerSpec,
        exemplars: dict[str, str],
    ) -> str:
        spec_json = json.dumps(spec.model_dump(mode="json"), indent=2, default=str)
        ex_blocks = "\n\n".join(
            f"### Exemplar for property `{name}`:\n{body}"
            for name, body in exemplars.items()
        )
        return textwrap.dedent(f"""\
            SERVER SPEC:
            ```json
            {spec_json}
            ```

            FEW-SHOT EXEMPLARS:
            {ex_blocks}

            Produce the pytest module now.
        """)
