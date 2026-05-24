"""
Track 2 demo: ServerSpec → adversarial pytest suite.

A toy spec for a tiny `/notes` service. We use a stub LLM client so
this runs without API access; the canned output is representative of
what the real generator produces (a pytest module hitting several
OWASP categories).
"""
from __future__ import annotations

from safe_scaffold.server_verifier import (
    AdversarialTestGenerator, Endpoint, ServerSpec,
)
from safe_scaffold.server_verifier.spec import (
    AuthScheme, HttpMethod, Param, ParamSource, SecurityProperty,
)
from safe_scaffold.translator import StubLLMClient


CANNED_SUITE = '''\
"""Auto-generated security tests for notes-api."""
import pytest


@pytest.mark.target("broken_auth")
@pytest.mark.parametrize("bad_header", ["", "Bearer", "Bearer junk", "Basic xx"])
def test_invalid_auth_rejected(client, bad_header):
    r = client.get("/notes", headers={"Authorization": bad_header})
    assert r.status_code == 401, r.text


@pytest.mark.target("bola")
def test_bola_other_users_note(client, alice_token, bob_note_id):
    r = client.get(f"/notes/{bob_note_id}",
                   headers={"Authorization": f"Bearer {alice_token}"})
    assert r.status_code in (403, 404), r.text


@pytest.mark.target("injection")
@pytest.mark.parametrize("payload", ["' OR 1=1 --", '"; DROP TABLE notes; --'])
def test_no_sql_injection_in_search(client, alice_token, payload):
    r = client.get("/notes", params={"q": payload},
                   headers={"Authorization": f"Bearer {alice_token}"})
    assert r.status_code in (200, 400)
    if r.status_code == 200:
        # Should never return more than the caller's own notes
        rows = r.json()
        assert all(n["owner"] == "alice" for n in rows), rows


@pytest.mark.target("mass_assignment")
def test_cannot_promote_self_to_admin(client, alice_token, alice_user_id):
    client.patch(f"/users/{alice_user_id}",
                 json={"display_name": "A", "is_admin": True},
                 headers={"Authorization": f"Bearer {alice_token}"})
    me = client.get(f"/users/{alice_user_id}",
                    headers={"Authorization": f"Bearer {alice_token}"}).json()
    assert me.get("is_admin") is not True
'''


def build_spec() -> ServerSpec:
    return ServerSpec(
        name="notes-api",
        description="Tiny per-user notes service.",
        base_url="http://localhost:8000",
        auth=AuthScheme.BEARER,
        endpoints=[
            Endpoint(
                method=HttpMethod.GET, path="/notes",
                summary="List the caller's notes; optional ?q= search.",
                auth=AuthScheme.BEARER,
                params=[Param(name="q", source=ParamSource.QUERY, type="string", required=False)],
                postconditions=[
                    "Only notes owned by the authenticated caller are returned.",
                    "Search terms must be parameterized; no SQL/Mongo injection.",
                ],
            ),
            Endpoint(
                method=HttpMethod.GET, path="/notes/{id}",
                summary="Get one note by id.",
                auth=AuthScheme.BEARER,
                params=[Param(name="id", source=ParamSource.PATH, type="string")],
                postconditions=["Returns 403/404 unless the caller owns the note."],
            ),
            Endpoint(
                method=HttpMethod.PATCH, path="/users/{id}",
                summary="Update the caller's own user profile.",
                auth=AuthScheme.BEARER,
                params=[Param(name="id", source=ParamSource.PATH, type="string")],
                postconditions=[
                    "Caller may only update their own profile.",
                    "is_admin and similar privileged fields are NEVER settable here.",
                ],
            ),
        ],
        security_properties=[
            SecurityProperty(name="bola", description="Per-note auth.", severity="critical"),
            SecurityProperty(name="broken_auth", description="Token validation.", severity="critical"),
            SecurityProperty(name="injection", description="Search parameter.", severity="critical"),
            SecurityProperty(name="mass_assignment", description="Profile patch.", severity="high"),
        ],
        fixtures={
            "alice_token": "fixture: a valid bearer token for user alice",
            "alice_user_id": "fixture: alice's user id",
            "bob_note_id": "fixture: the id of a note owned by bob, not alice",
        },
    )


def main() -> int:
    spec = build_spec()
    gen = AdversarialTestGenerator(StubLLMClient(CANNED_SUITE))
    suite = gen.generate(spec)

    print(f"Generated test suite targeting: {suite.properties_targeted}")
    print("---- pytest module (first 40 lines) ----")
    for ln in suite.pytest_code.splitlines()[:40]:
        print(ln)
    print("--- (truncated) ---")
    print()
    print("To run against a candidate server:")
    print("  from safe_scaffold.server_verifier import run_test_suite")
    print("  run = run_test_suite(suite, 'http://localhost:8000',")
    print("                       fixture_file='examples/server_demo/conftest.py')")
    print("  print(run.summary, run.violated_properties)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
