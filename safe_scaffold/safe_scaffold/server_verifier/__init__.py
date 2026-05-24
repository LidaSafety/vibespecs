"""
Server-code verification (the second track of the proposal).

While the rest of the project gates the *actions* a coding agent takes,
this subpackage gates the *server programs* the agent produces. The two
tracks share the architecture (world model → spec → verifier) but use
different verifier strategies:

  Actions: deductive verification via Z3 against formal rule conditions.
  Server code: empirical verification via adversarial test-case
               generation by an LLM primed with security-vulnerability
               exemplars.

The choice of empirical verification here mirrors the proposal:
formally proving server programs correct is too expensive at the
scope of this fellowship, so we lean on automated red-teaming as a
practical approximation.
"""
from .spec import ServerSpec, Endpoint, SecurityProperty
from .adversarial import AdversarialTestGenerator, GeneratedTestSuite
from .runner import VerificationRun, run_test_suite

__all__ = [
    "ServerSpec",
    "Endpoint",
    "SecurityProperty",
    "AdversarialTestGenerator",
    "GeneratedTestSuite",
    "VerificationRun",
    "run_test_suite",
]
