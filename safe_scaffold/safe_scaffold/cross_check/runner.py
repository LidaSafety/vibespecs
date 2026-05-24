"""Run a TestCase against an implementation, return a TestResult.

The implementation is just a Python callable. For cross-checking we typically
have two callables — a reference and a candidate — and we run both against the
same test cases to find disagreements.

We catch exceptions from the implementation rather than letting them propagate.
A spec violation can manifest as either "wrong output" or "exception when none
should occur"; we report both uniformly. The cross-check phase decides whether
disagreement on exceptions is interesting (it usually is).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TestCase:
    """Inputs to one call against the implementation."""

    function: str
    inputs: tuple[Any, ...]


@dataclass(frozen=True)
class TestResult:
    """Outcome of running a TestCase against one implementation."""

    case: TestCase
    output: Any
    exception: str | None  # repr() of the exception, or None
    success: bool  # False iff the call raised


def run_test_case(case: TestCase, impl: Callable[..., Any]) -> TestResult:
    """Run `impl(*case.inputs)`. Catch and record any exception."""
    try:
        out = impl(*case.inputs)
    except Exception as exc:
        return TestResult(case=case, output=None, exception=repr(exc), success=False)
    return TestResult(case=case, output=out, exception=None, success=True)
