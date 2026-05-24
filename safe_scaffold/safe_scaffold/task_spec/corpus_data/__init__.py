"""Aggregate corpus across all tasks."""

from safe_scaffold.task_spec.corpus_data.auto_mutants import MUTATED_TASKS
from safe_scaffold.task_spec.corpus_data.complex_tasks import COMPLEX_TASKS
from safe_scaffold.task_spec.corpus_data.tasks_01_05 import TASKS_01_05
from safe_scaffold.task_spec.corpus_data.tasks_06_10 import TASKS_06_10

# The core hand-authored corpus (40 (spec, candidate) pairs).
CORPUS = TASKS_01_05 + TASKS_06_10

# The extended corpus, including 5 additional tasks with mutation-generated
# candidates (60 pairs total). Use this for headline FAR / kappa numbers
# where larger N gives a tighter confidence interval; use CORPUS when the
# hand-authored realism matters.
EXTENDED_CORPUS = CORPUS + MUTATED_TASKS

# The complex corpus adds 3 multi-file realistic tasks (12 pairs) on top
# of EXTENDED_CORPUS. Use this when you want to stress the validator
# against scenarios that resemble actual engineering work — Flask auth,
# SQL schema migrations, rate-limit middleware. Mutation numbers on this
# corpus are slower (~30s) because the candidates carry more files and
# the positive tests do real HTTP / sqlite work.
FULL_CORPUS = EXTENDED_CORPUS + COMPLEX_TASKS

__all__ = ["CORPUS", "EXTENDED_CORPUS", "FULL_CORPUS", "COMPLEX_TASKS"]
