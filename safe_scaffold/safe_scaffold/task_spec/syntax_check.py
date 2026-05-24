"""Cheap stdlib-only Python syntax checker.

Used by the iterative-pipeline tab's "Syntax check (ast.parse)" button.
Reports SyntaxError per file with line + offset + message. No subprocess,
no third-party deps. Catches malformed Python before the user wastes a
PBT run on a file that doesn't even parse.
"""

from __future__ import annotations

import ast


def check_python_syntax(files: dict[str, str]) -> list[dict]:
    """For each (path, code), report whether `ast.parse(code)` succeeds.

    Returns a list of result dicts:
      - on success: {"path": <path>, "ok": True}
      - on SyntaxError: {"path": <path>, "ok": False, "line": <int>,
                          "offset": <int>, "msg": <str>}
    """
    results: list[dict] = []
    for path, code in files.items():
        try:
            ast.parse(code, filename=path)
            results.append({"path": path, "ok": True})
        except SyntaxError as e:
            results.append({
                "path": path,
                "ok": False,
                "line": e.lineno or 0,
                "offset": e.offset or 0,
                "msg": e.msg or "syntax error",
            })
        except Exception as e:  # pragma: no cover — defensive
            results.append({
                "path": path,
                "ok": False,
                "line": 0,
                "offset": 0,
                "msg": f"{type(e).__name__}: {e}",
            })
    return results
