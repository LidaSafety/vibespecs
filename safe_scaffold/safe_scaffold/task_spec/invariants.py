"""Invariant library: structural constraints a task spec can require.

Each invariant is a tiny dataclass with a `check(diff, before_repo, after_repo)`
method that returns an InvariantResult. The validator runs each in turn.

Design principles:

1. Invariants operate on the diff and on the two repo snapshots, not on
   abstract behavior. The user shouldn't have to reason about program
   semantics; structural diff predicates are what they already use in code
   review.

2. The menu is small on purpose. Six invariant types cover most of what a
   coding-task spec needs:

      FilesUnchanged([paths])      -- these files MUST NOT change
      OnlyFilesModified([paths])   -- changes are scoped to these files only
      PositiveTestPasses(test)     -- a positive test must pass
      NoNewImports([modules])      -- no new imports of <forbidden> modules
      NoSecretsInDiff()            -- no credential-shaped strings introduced
      DiffSmallerThan(n)           -- diff is <= N changed lines

   Each has an obvious semantics, an obvious failure mode, and a cheap
   implementation. None requires running the program.

3. The implementation never imports the candidate code or anything from the
   repo into the validator process. It diffs strings and counts lines. The
   exception is PositiveTestPasses, which shells out to pytest in a
   subprocess against a temp directory; that's how we keep the validator
   process isolated from candidate behavior.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from safe_scaffold.task_spec.spec import PositiveTest, RepoState


@dataclass(frozen=True)
class InvariantResult:
    """Outcome of one invariant check on one candidate.

    `uncertain=True` means the check couldn't be evaluated — e.g. the
    positive test crashed on import, or `Invariant.check` raised. In
    that case `holds` should be treated as undefined, and the verdict
    should be ABSTAIN rather than REJECT. We don't conflate "did not
    pass" with "couldn't evaluate".
    """

    invariant_name: str
    holds: bool
    details: str = ""  # short human-readable explanation
    uncertain: bool = False


@dataclass(frozen=True)
class Invariant:
    """Base class. Subclasses override `check`.

    Frozen dataclasses with class-level `name` ClassVar — the same pattern
    used by `world.Action` and `conditions.Condition` elsewhere in the
    codebase. Keeps the file structure familiar to anyone who's read those
    modules.
    """

    name: ClassVar[str] = "_base"

    def check(
        self,
        *,
        before: "RepoState",
        after: "RepoState",
        repo_dir: str,
    ) -> InvariantResult:  # pragma: no cover - overridden
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Structural diff invariants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilesUnchanged(Invariant):
    """The listed files must be byte-identical before and after.

    Use to pin "don't touch the public API", "don't change the schema",
    "don't modify production config".
    """

    paths: tuple[str, ...]
    name: ClassVar[str] = "files_unchanged"

    def check(self, *, before, after, repo_dir):
        violated = []
        for p in self.paths:
            b = before.get(p)
            a = after.get(p)
            if b != a:
                violated.append(p)
        if violated:
            return InvariantResult(
                invariant_name=f"FilesUnchanged({list(self.paths)})",
                holds=False,
                details=f"modified files that must be unchanged: {violated}",
            )
        return InvariantResult(
            invariant_name=f"FilesUnchanged({list(self.paths)})",
            holds=True,
            details=f"all {len(self.paths)} pinned files unchanged",
        )


@dataclass(frozen=True)
class OnlyFilesModified(Invariant):
    """All changes must lie within the named paths.

    Catches "scope creep": agent fixes the requested bug but also
    refactors three unrelated files. The set is closed; anything not
    listed and not in `before` exactly is a violation.

    Note: file CREATION counts as modification. Pass new file paths in
    `allowed_paths` if the task is expected to create them.
    """

    allowed_paths: tuple[str, ...]
    name: ClassVar[str] = "only_files_modified"

    def check(self, *, before, after, repo_dir):
        all_paths = set(before) | set(after)
        violated: list[str] = []
        for p in sorted(all_paths):
            if before.get(p) == after.get(p):
                continue  # unchanged
            if p not in self.allowed_paths:
                violated.append(p)
        if violated:
            return InvariantResult(
                invariant_name=f"OnlyFilesModified({list(self.allowed_paths)})",
                holds=False,
                details=f"modified files outside scope: {violated}",
            )
        return InvariantResult(
            invariant_name=f"OnlyFilesModified({list(self.allowed_paths)})",
            holds=True,
            details="all modifications stayed within allowed scope",
        )


@dataclass(frozen=True)
class NoNewImports(Invariant):
    """No new top-level `import` of a module in `forbidden`.

    Best-effort. Regex-based, so will miss runtime imports via importlib.
    Pragmatic: most "introduce a dependency on requests/urllib for
    exfiltration" patterns are top-level imports because that's how
    Python developers naturally write code.
    """

    forbidden: tuple[str, ...]
    name: ClassVar[str] = "no_new_imports"

    def check(self, *, before, after, repo_dir):
        import_re = re.compile(
            r"^\s*(?:from\s+(\S+)|import\s+(\S+))",
            re.MULTILINE,
        )

        def imports_in(code: str) -> set[str]:
            found = set()
            for match in import_re.finditer(code):
                mod = match.group(1) or match.group(2)
                if mod:
                    # Normalize: `requests.adapters` -> `requests`
                    found.add(mod.split(".")[0].rstrip(","))
            return found

        before_imports: set[str] = set()
        after_imports: set[str] = set()
        for code in before.values():
            before_imports |= imports_in(code)
        for code in after.values():
            after_imports |= imports_in(code)

        new = after_imports - before_imports
        forbidden_new = sorted(new & set(self.forbidden))
        if forbidden_new:
            return InvariantResult(
                invariant_name=f"NoNewImports({list(self.forbidden)})",
                holds=False,
                details=f"agent introduced forbidden imports: {forbidden_new}",
            )
        return InvariantResult(
            invariant_name=f"NoNewImports({list(self.forbidden)})",
            holds=True,
            details=f"no new imports from forbidden set",
        )


# Curated "looks like a credential" patterns. Inspired by truffleHog /
# detect-secrets but pared to a few high-precision shapes.
_SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "aws access key"),
    (r"sk-ant-[A-Za-z0-9_\-]{20,}", "anthropic api key"),
    (r"sk-[A-Za-z0-9]{20,}", "openai-shaped api key"),
    (r"ghp_[A-Za-z0-9]{20,}", "github personal token"),
    (r"-----BEGIN [A-Z ]+PRIVATE KEY-----", "private key block"),
    (r"['\"](?:password|passwd|pwd)['\"]:\s*['\"][^'\"]{8,}['\"]", "hardcoded password literal"),
]


@dataclass(frozen=True)
class NoSecretsInDiff(Invariant):
    """Diff must not introduce strings that look like credentials.

    Useful as a security backstop. Won't catch every credential format,
    but catches the obvious patterns (AKIA, sk-ant-, ghp_, BEGIN PRIVATE KEY).
    """

    name: ClassVar[str] = "no_secrets_in_diff"

    def check(self, *, before, after, repo_dir):
        # Construct a single string of all *added* content. We do this by
        # walking the unified diff; substrings present in `before` don't
        # count as "added."
        before_blob = "\n".join(before.values())
        for path, after_code in after.items():
            before_code = before.get(path, "")
            for line in difflib.ndiff(
                before_code.splitlines(), after_code.splitlines()
            ):
                if not line.startswith("+ "):
                    continue
                added = line[2:]
                # Skip if this exact line happens to appear elsewhere
                # in `before` (e.g. moved code).
                if added in before_blob:
                    continue
                for pattern, label in _SECRET_PATTERNS:
                    if re.search(pattern, added):
                        return InvariantResult(
                            invariant_name="NoSecretsInDiff()",
                            holds=False,
                            details=f"introduced {label}-shaped string in {path}",
                        )
        return InvariantResult(
            invariant_name="NoSecretsInDiff()",
            holds=True,
            details="no credential-shaped strings in diff",
        )


@dataclass(frozen=True)
class DiffSmallerThan(Invariant):
    """Total added+removed lines across the diff must be <= n.

    Soft signal for "agent went way beyond the task." Not a security
    guarantee — a malicious 5-line patch is still 5 lines — but a useful
    heuristic for catching agents that rewrite the world when asked to
    fix a typo.
    """

    max_lines: int
    name: ClassVar[str] = "diff_smaller_than"

    def check(self, *, before, after, repo_dir):
        total = 0
        all_paths = set(before) | set(after)
        for p in all_paths:
            b = before.get(p, "").splitlines()
            a = after.get(p, "").splitlines()
            # ndiff: lines starting with "+ " or "- " are additions / removals.
            for line in difflib.ndiff(b, a):
                if line.startswith("+ ") or line.startswith("- "):
                    total += 1
        if total > self.max_lines:
            return InvariantResult(
                invariant_name=f"DiffSmallerThan({self.max_lines})",
                holds=False,
                details=f"diff is {total} lines, limit is {self.max_lines}",
            )
        return InvariantResult(
            invariant_name=f"DiffSmallerThan({self.max_lines})",
            holds=True,
            details=f"diff is {total} lines (within limit)",
        )


# ---------------------------------------------------------------------------
# Behavioral invariant: the positive test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositiveTestPasses(Invariant):
    """The named positive test (referenced by its `path` in the spec) must
    pass when run via pytest in the post-candidate repo.

    Implementation runs in validator.py because it needs the temp directory
    and subprocess machinery. This dataclass is just the spec.
    """

    test_path: str  # path of the PositiveTest within the spec
    name: ClassVar[str] = "positive_test_passes"

    def check(self, *, before, after, repo_dir):
        # Real implementation in validator.py — this is invoked there with
        # the correct context. We never run pytest from inside `check`
        # because that would require threading subprocess state through.
        raise NotImplementedError(
            "PositiveTestPasses is dispatched by the validator, not invoked directly."
        )
