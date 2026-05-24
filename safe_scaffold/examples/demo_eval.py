"""Demo: run the built-in eval against the safe_default_policy."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from safe_scaffold.eval import BENIGN_CORPUS, REDTEAM_CORPUS, run_eval  # noqa: E402
from safe_scaffold.policy import safe_default_policy  # noqa: E402


def main() -> int:
    metrics = run_eval(safe_default_policy(), REDTEAM_CORPUS, BENIGN_CORPUS)
    print(metrics.report())
    return 0 if metrics.false_allow_rate == 0.0 else 1


if __name__ == "__main__":
    sys.exit(main())
