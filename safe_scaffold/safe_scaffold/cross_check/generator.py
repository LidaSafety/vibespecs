"""Generate concrete test cases from a FunctionSpec.

Strategy: if the spec provides a `sample_inputs` generator, use it (this lets
the spec author hand-curate boundary-rich inputs — zero, one, modulus-minus-1,
power-of-two boundaries). Otherwise we fall back to random sampling from a
reasonable default domain (integers in [-1000, 1000], strings of varying
length, etc.) and let `input_domain` filter.

The generator deliberately favors REPRODUCIBILITY over coverage: a fixed seed
on the random source so re-running CI gives the same test cases. This is the
counterintuitive but right call for spec validation: we want flaky-test-free
regression detection more than we want the maximum possible coverage on each
run. Coverage is what evals are for.
"""

from __future__ import annotations

import random
from typing import Iterable

from safe_scaffold.cross_check.runner import TestCase
from safe_scaffold.cross_check.spec import FunctionSpec


def generate_test_cases(
    spec: FunctionSpec,
    *,
    n: int = 200,
    seed: int = 42,
) -> Iterable[TestCase]:
    """Yield TestCase records for `spec`.

    `n` is an upper bound; for hand-curated `sample_inputs` generators that
    produce fewer cases, fewer are yielded.
    """
    if spec.sample_inputs is not None:
        for inputs in spec.sample_inputs(n):
            if spec.input_domain(*inputs):
                yield TestCase(function=spec.name, inputs=inputs)
        return

    rng = random.Random(seed)
    yielded = 0
    attempts = 0
    while yielded < n and attempts < n * 20:
        attempts += 1
        # Default sampler: pretend every input is an int. The right place to
        # customize this is the spec author providing `sample_inputs`.
        inputs = tuple(rng.randint(-1000, 1000) for _ in spec.inputs)
        if not spec.input_domain(*inputs):
            continue
        yield TestCase(function=spec.name, inputs=inputs)
        yielded += 1
