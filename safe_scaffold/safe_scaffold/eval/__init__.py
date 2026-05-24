"""Evaluation harness: red-team prompts, metrics, runner.

Used to measure how the verifier performs against:

- A curated red-team corpus of dangerous actions an agent might propose.
  Expected outcome: every entry is either DENIED (a matched DENY rule) or
  UNKNOWN (no rule matched, fail-closed at the gate = also blocks). ALLOW
  on a red-team entry is a false negative — a security failure.
- A curated benign corpus of safe actions the agent legitimately needs.
  Expected outcome: ALLOW (matched ALLOW rule) or UNKNOWN (no rule matched,
  blocked at the gate but operator can extend the policy). DENY on a benign
  entry is a false positive — a too-broad rule.

At the runtime gate, UNKNOWN behaves identically to DENY (action is blocked).
The eval distinguishes them so we can report what fraction of the corpus is
covered by *explicit* rules versus what fraction falls through to the
fail-closed default — a calibration metric for how complete the policy is.
"""

from safe_scaffold.eval.redteam import (
    BENIGN_CORPUS,
    REDTEAM_CORPUS,
    EvalEntry,
)
from safe_scaffold.eval.runner import EvalMetrics, run_eval

__all__ = [
    "REDTEAM_CORPUS",
    "BENIGN_CORPUS",
    "EvalEntry",
    "EvalMetrics",
    "run_eval",
]
