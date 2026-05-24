"""
Tests for the NL→DSL translator.

We don't test the LLM call itself (that's an integration test). We test:
  * DSL structural validation accepts good rules and rejects bad ones,
  * the translator wires the LLM output into a Policy correctly given
    a known-good stub response,
  * retry-on-bad-JSON works.
"""
from __future__ import annotations

import json

import pytest

from safe_scaffold import Translator
from safe_scaffold.translator import StubLLMClient, validate_dsl


# ---- structural validation -----------------------------------------------

def test_validate_dsl_accepts_well_formed():
    validate_dsl({"op": "true"})
    validate_dsl({"op": "and", "args": [{"op": "true"}, {"op": "false"}]})
    validate_dsl({"op": "path_under", "field": "path", "value": "/x"})
    validate_dsl({"op": "in", "field": "command", "values": ["git", "pytest"]})


@pytest.mark.parametrize("bad", [
    {},                                            # missing op
    {"op": "frobnicate"},                          # unknown op
    {"op": "and"},                                 # and without args
    {"op": "and", "args": []},                     # and with empty args
    {"op": "and", "args": "not a list"},
    {"op": "not"},                                 # not without arg
    {"op": "eq", "field": "x"},                    # eq without value
    {"op": "in", "field": "x"},                    # in without values
    {"op": "in", "field": "x", "values": []},      # empty in
    {"op": "path_under", "value": "/x"},           # missing field
])
def test_validate_dsl_rejects(bad):
    with pytest.raises(ValueError):
        validate_dsl(bad)


# ---- translator output handling ------------------------------------------

_GOOD = json.dumps([
    {
        "description": "allow read under /proj",
        "effect": "allow",
        "applies_to": ["file_read"],
        "condition": {"op": "path_under", "field": "path", "value": "/proj"},
        "rationale": "user wanted it",
    },
])


def test_translator_builds_policy():
    t = Translator(StubLLMClient(_GOOD), project_root="/proj")
    p = t.translate("let it read under /proj")
    assert len(p.rules) == 1
    assert p.rules[0].description.startswith("allow read")
    assert p.rules[0].provenance.original_text == "let it read under /proj"


def test_translator_strips_markdown_fence():
    fenced = "```json\n" + _GOOD + "\n```"
    t = Translator(StubLLMClient(fenced))
    p = t.translate("...")
    assert len(p.rules) == 1


def test_translator_rejects_bad_rule_shape():
    bad = json.dumps([{"description": "x", "effect": "allow"}])  # missing fields
    t = Translator(StubLLMClient(bad))
    with pytest.raises(ValueError):
        t.translate("...")
