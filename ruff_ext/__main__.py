"""Entry point: python -m ruff_ext"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _core import aclose


def main() -> int:
    parser = argparse.ArgumentParser(description="ruff_ext: Custom lint rules")
    parser.add_argument("--ci", action="store_true", help="CI mode: exit 1 on violations")

    subparsers = parser.add_subparsers(dest="rule", help="Select rule to run")

    # RUFF022: Banned import paths
    ruff022 = subparsers.add_parser("ruff022", help="RUFF022: Banned import paths")
    ruff022.add_argument("--root", type=Path, default=Path(__file__).parent.parent)

    # TUUID7: uuid.uuid4() in hot paths
    tuuid7 = subparsers.add_parser("tuuid7", help="TUUID7: uuid.uuid4() in hot paths detector")
    tuuid7.add_argument("--root", type=Path, default=Path(__file__).parent.parent)

    args = parser.parse_args()

    if args.rule == "ruff022":
        from ruff_ext import check_directory as ruff022_check
        violations = ruff022_check(args.root)
        if not violations:
            print("RUFF022: 0 violations")
            return 0
        print(f"RUFF022: {len(violations)} violation(s):")
        for v in violations:
            rel = v.file.relative_to(args.root)
            print(f"  {rel}:{v.line}: {v.message}")
        return 1 if args.ci else 0

    elif args.rule == "tuuid7":
        from ruff_ext.tuuid7 import check_directory as tuuid7_check
        violations = tuuid7_check(args.root)
        if not violations:
            print("TUUID7: 0 violations")
            return 0
        print(f"TUUID7: {len(violations)} violation(s):")
        for v in violations:
            rel = v.file.relative_to(args.root)
            print(f"  {rel}:{v.line}: {v.message}")
        return 1 if args.ci else 0

    else:
        # Default: run both
        from ruff_ext import check_directory as ruff022_check
        from ruff_ext.tuuid7 import check_directory as tuuid7_check

        all_violations = []
        all_violations.extend(ruff022_check(Path(__file__).parent.parent))
        all_violations.extend(tuuid7_check(Path(__file__).parent.parent))

        if not all_violations:
            print("All ruff_ext checks passed (0 violations)")
            return 0

        print(f"Total: {len(all_violations)} violation(s)")
        return 1 if args.ci else 0


if __name__ == "__main__":
    sys.exit(main())
