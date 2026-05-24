"""Path normalization and matching utilities.

Path comparisons in security policies are notoriously bug-prone. This module
isolates the rules so policy code never reaches for `os.path.startswith`
directly.

The function we want, `is_under(child, parent)`, has to:

1. Resolve `..` segments so `/tmp/foo/../../etc/passwd` is recognized as
   `/etc/passwd`, not as a path under `/tmp`.
2. NOT follow symlinks. We are reasoning about the path string as the agent
   proposed it, not what it would resolve to on a particular filesystem.
   (Symlink-following is a separate threat model; agents can also race the
   filesystem between check and use. We document this assumption.)
3. Treat trailing slashes consistently so `/etc` and `/etc/` are the same.
4. Forbid empty patterns and reject relative parents, because comparing against
   `..` or `.` would be a footgun.

Glob matching uses `fnmatch` semantics with one extension: a trailing `/**` on
a pattern means "any descendant", and a bare `**` segment is rejected because
its semantics differ across tools and users would expect git/zsh behavior we
don't actually implement.
"""

from __future__ import annotations

import fnmatch
import os
import posixpath


def normalize(path: str) -> str:
    """Normalize a path: resolve `..`, collapse `//`, strip trailing slashes.

    Does NOT follow symlinks or touch the filesystem.

    Raises:
        ValueError: if `path` is empty.
    """
    if not path:
        raise ValueError("path must be non-empty")
    # On Windows, normpath would switch to backslashes — we always use posix.
    p = posixpath.normpath(path)
    # normpath turns "/" into "/", but turns "" into "."; we caught empty above.
    # Strip a trailing slash unless the entire path is "/".
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def is_under(child: str, parent: str) -> bool:
    """True iff `child` is `parent` itself or lies within the parent directory.

    Both are normalized before comparison. The parent must be absolute (start
    with `/`); a relative parent in a security check is almost always a bug.

    Examples:
        >>> is_under("/etc/passwd", "/etc")
        True
        >>> is_under("/etc", "/etc")
        True
        >>> is_under("/etcetera", "/etc")
        False
        >>> is_under("/tmp/foo/../../etc/passwd", "/tmp")
        False
        >>> is_under("/tmp/foo/../../etc/passwd", "/etc")
        True
    """
    parent_n = normalize(parent)
    child_n = normalize(child)
    if not parent_n.startswith("/"):
        raise ValueError(
            f"parent path must be absolute, got {parent!r}. "
            "Relative parents in security checks are too easy to misuse."
        )
    if not child_n.startswith("/"):
        # An attacker-controlled path that starts relative could be resolved
        # against an unexpected cwd later; treat it as not-under for safety.
        return False
    if child_n == parent_n:
        return True
    # Add a slash boundary so /etc does not match /etcetera.
    prefix = parent_n if parent_n.endswith("/") else parent_n + "/"
    return child_n.startswith(prefix)


def matches_glob(path: str, pattern: str) -> bool:
    """Match `path` against a glob `pattern`.

    Pattern rules:
        * `*` matches any sequence within a single path segment.
        * `?` matches a single character within a segment.
        * `**` is allowed only as a suffix, written `/**`, meaning "any
          descendant of the preceding directory". This is not full ant-style
          globbing; it's the only `**` shape we accept because more general
          forms surprise users.

    Examples:
        >>> matches_glob("/tmp/foo.txt", "/tmp/*.txt")
        True
        >>> matches_glob("/tmp/a/b.txt", "/tmp/*.txt")
        False
        >>> matches_glob("/tmp/a/b.txt", "/tmp/**")
        True
        >>> matches_glob("/etc/passwd", "/tmp/**")
        False
    """
    if not pattern:
        raise ValueError("glob pattern must be non-empty")
    # Reject `**` in any position other than as a trailing `/**`.
    if "**" in pattern:
        if not pattern.endswith("/**"):
            raise ValueError(
                f"pattern {pattern!r}: `**` is only allowed as a trailing "
                "`/**` meaning 'any descendant'. Use plain `*` for single-segment."
            )
        prefix = pattern[:-3]
        if not prefix:
            # `/**` alone matches anything absolute.
            return normalize(path).startswith("/")
        # `/foo/**` matches `/foo/...` (anything strictly under foo, plus foo itself).
        return is_under(path, prefix)
    # Single-segment glob. fnmatch's `*` happily eats `/`, which is wrong for
    # path globs; users expect `/tmp/*.txt` to mean "files directly in /tmp",
    # not "anywhere under /tmp". Split both on `/` and match segment-by-segment.
    path_n = normalize(path)
    path_parts = path_n.split("/")
    pattern_parts = pattern.split("/")
    if len(path_parts) != len(pattern_parts):
        return False
    return all(
        fnmatch.fnmatchcase(p, pat) for p, pat in zip(path_parts, pattern_parts, strict=True)
    )


def looks_like_destructive_root(path: str) -> bool:
    """Heuristic: would deleting this path destroy something catastrophic?

    Used by the default-deny base policy and by Z3 invariant checks. Catches
    `/`, `/etc`, `/usr`, `/var`, `~`, `$HOME`, plus their normalized variants.
    Conservative on purpose — we'd rather refuse a legitimate `/var/tmp/foo`
    delete and force the user to whitelist it than allow a `/var` wipe.
    """
    if not path:
        return True  # empty argument to rm -rf is suspicious in itself
    p = normalize(path)
    catastrophic = {"/", "/etc", "/usr", "/var", "/bin", "/boot", "/lib", "/sbin", "/home"}
    if p in catastrophic:
        return True
    # Watch for unexpanded env-var-as-tilde forms.
    if path.strip() in {"~", "$HOME", "${HOME}", "*"}:
        return True
    return False
