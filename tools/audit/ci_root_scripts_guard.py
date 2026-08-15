#!/usr/bin/env uv run python
"""
CI Guard: ROOT-SCRIPTS — root directory cannot contain debug/test scripts.

Run: uv run python tools/ci_root_scripts_guard.py
Exit 1 = violation found (underscore-prefixed or analyze/autonomous scripts in root)
Exit 0 = clean

Enforced patterns in project root:
  - _test_*.py   — debug test scripts
  - analyze_*.py  — debug analysis scripts
  - autonomous_*.py — debug autonomous scripts
  - _analyze_deep.py — debug analysis scripts

Allowed locations:
  - tests/manual/   — debug test scripts
  - tools/analyze/  — debug analysis scripts
"""

import sys
from pathlib import Path
from _core import aclose

ROOT = Path(__file__).parent.parent

# Patterns that are forbidden in the project root
FORBIDDEN_PATTERNS = (
    "_test_",      # debug test scripts
    "analyze_",    # debug analysis scripts
    "autonomous_", # debug autonomous scripts
    "_analyze_deep",  # specific debug script
    "_debug_",     # debug scripts
    "_floor_check", # debug scripts
    "_aclose",      # debug aclose scripts
)

ALLOWED_DIRS = {
    "tests/manual",   # debug test scripts go here
    "tools/analyze", # debug analysis scripts go here
}


def _check_root() -> list[str]:
    """Check root directory for forbidden script patterns."""
    violations = []
    for path in ROOT.glob("*.py"):
        name = path.name
        # Skip non-forbidden files
        if not any(name.startswith(pat) for pat in FORBIDDEN_PATTERNS):
            continue
        violations.append(f"  {path.relative_to(ROOT)} — move to {' or '.join(ALLOWED_DIRS)}")
    return violations


def main() -> int:
    violations = _check_root()
    if violations:
        print("ROOT-SCRIPTS: violation(s) found — debug scripts must not live in project root:")
        for v in violations:
            print(v)
        print(f"\nMove them to: {', '.join(ALLOWED_DIRS)}")
        return 1
    print("ROOT-SCRIPTS: clean — no debug scripts in project root")
    return 0


if __name__ == "__main__":
    sys.exit(main())
