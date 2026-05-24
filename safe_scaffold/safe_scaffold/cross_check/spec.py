"""Structured spec for cross-checking.

A ServerSpec describes an implementation we want to validate. It consists of:

- A set of FunctionSpecs: named entry points with input/output domains.
- A set of Properties: predicates that any correct implementation must satisfy.
  Properties can be:
    * input-only invariants ("the input is in a valid range")
    * output-only invariants ("the output is in a valid range")
    * input-output relations ("output equals reference(input)")
    * cross-pair relations ("encode then decode is identity")

The spec is deliberately small. We are not trying to replace ACSL or
Cryptol — we just want a structure that lets the test generator know what
shapes of input to try and what to compare the output against.

For the Cryspen reproduction, the spec is short:

    FunctionSpec(name="decompress_d", inputs=[x:int, d:int],
                 input_domain=lambda x, d: 0 <= x < Q and d in [1,4,5,10,11])
    Property(name="matches_FIPS_203_reference",
             check=lambda inputs, output: output == reference_decompress(*inputs))

That's the entire formal validation harness for the headline bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class Property:
    """A predicate any correct implementation must satisfy.

    `check` returns True (holds) or False (violated). `name` and `description`
    are for human-readable reports.

    The signature is intentionally generic: `check(inputs: tuple, output: Any) -> bool`.
    For input-only invariants you can ignore `output`; for cross-pair properties
    a higher-level driver passes both.
    """

    name: str
    description: str
    check: Callable[[tuple[Any, ...], Any], bool]


@dataclass(frozen=True)
class FunctionSpec:
    """A single entry point we want to validate."""

    name: str
    # Description of inputs: list of (param_name, type_hint_string) pairs.
    # Used for reports and to drive the generator.
    inputs: tuple[tuple[str, str], ...]
    # A predicate that returns True for valid inputs in the spec's domain.
    # Inputs failing this predicate are skipped (they're out of scope).
    input_domain: Callable[..., bool]
    # An optional generator: yields concrete input tuples to test. If None,
    # the cross_check.generator pulls from input_domain by sampling.
    sample_inputs: Callable[[int], Iterable[tuple[Any, ...]]] | None = None
    properties: tuple[Property, ...] = field(default_factory=tuple)
    description: str = ""


@dataclass(frozen=True)
class ServerSpec:
    """Top-level spec: a named bundle of function specs."""

    name: str
    description: str = ""
    functions: tuple[FunctionSpec, ...] = field(default_factory=tuple)
    # Global properties that apply across function pairs (e.g. encode/decode
    # round-trip). The check signature is `check(impl: Any) -> bool` — the
    # caller hands the implementation and the property runs whatever test it
    # likes against it.
    cross_properties: tuple[Property, ...] = field(default_factory=tuple)
