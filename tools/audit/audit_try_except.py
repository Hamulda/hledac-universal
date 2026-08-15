#!/usr/bin/env python3
"""
Audit script for try/except patterns.
Flags overly broad try/except: bare except, except Exception: pass


Acceptable:
  - except SpecificException: (e.g., except (OSError, ValueError):)
  - except SpecificException as e: with logging

Refactor:
  - except Exception: pass → specific exception type
  - except: pass (bare) → always a bug

CI threshold: fail if violations > 10 in production code
Acceptance: try/except Exception: pass < 5 in production code
"""

import ast
import sys
from pathlib import Path
from typing import NamedTuple
from _core import aclose


class Violation(NamedTuple):
    file: Path
    line: int
    kind: str  # "bare_except" | "except_Exception_pass"
    code_snippet: str


EXCLUDE_DIRS = {"__pycache__", ".venv", ".venv-test", "probe_", ".claude", ".git", ".mypy_cache", ".pytest_cache"}


def is_excluded(path: Path) -> bool:
    s = str(path)
    return any(ex in s for ex in EXCLUDE_DIRS)


def get_code_snippet(source_lines: list[str], lineno: int, end_lineno: int | None = None) -> str:
    """Extract the try block snippet for context."""
    start = max(0, lineno - 1)
    end = end_lineno or lineno
    lines = source_lines[start:end]
    return "".join(lines).strip()[:120]


def audit_file(py_file: Path) -> list[Violation]:
    violations = []
    try:
        source = py_file.read_text()
        source_lines = source.splitlines(keepends=True)
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if handler.type is None:
                # bare except
                violations.append(Violation(
                    py_file, handler.lineno, "bare_except",
                    get_code_snippet(source_lines, handler.lineno)
                ))
            elif (
                isinstance(handler.type, ast.Name)
                and handler.type.id == "Exception"
                and len(handler.body) == 1
                and isinstance(handler.body[0], ast.Pass)
            ):
                # except Exception: pass
                violations.append(Violation(
                    py_file, handler.lineno, "except_Exception_pass",
                    get_code_snippet(source_lines, handler.lineno)
                ))
    return violations


def audit_tree(root: Path = Path(".")) -> list[Violation]:
    all_violations = []
    for py_file in root.rglob("*.py"):
        if is_excluded(py_file):
            continue
        all_violations.extend(audit_file(py_file))
    return all_violations


def main() -> None:
    root = Path(".")
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])

    violations = audit_tree(root)

    # Count by kind
    bare = [v for v in violations if v.kind == "bare_except"]
    pass_ = [v for v in violations if v.kind == "except_Exception_pass"]
    project = [v for v in violations if ".venv" not in str(v.file)]

    print(f"=== TRY/EXCEPT AUDIT REPORT ===")
    print(f"Total try blocks:        (see full AST count above)")
    print(f"Bare except violations:  {len(bare)}")
    print(f"except Exception pass:    {len(pass_)}")
    print(f"Project violations:      {len([v for v in project if v.kind in ('bare_except','except_Exception_pass')])}")
    print()

    if violations:
        print(f"VIOLATIONS ({len(violations)}):")
        for v in sorted(violations, key=lambda x: (str(x.file), x.line))[:60]:
            print(f"  {v.file}:{v.line}  [{v.kind}]  {v.code_snippet!r:.60}")
        if len(violations) > 60:
            print(f"  ... and {len(violations) - 60} more")

    # CI gate
    ci_threshold = 10
    project_violations = len([v for v in violations if ".venv" not in str(v.file) and v.kind in ("bare_except", "except_Exception_pass")])
    if project_violations > ci_threshold:
        print(f"\nCI FAIL: {project_violations} violations (threshold: {ci_threshold})")
        sys.exit(1)
    else:
        print(f"\nCI PASS: {project_violations} violations (threshold: {ci_threshold})")
        sys.exit(0)


if __name__ == "__main__":
    main()
