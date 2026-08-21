#!/usr/bin/env python3
"""
Safe codemod to replace simple lambda x: x.attr patterns with operator.attrgetter.

This version is conservative - only replaces patterns that are definitely safe:
- key=attrgetter("attr") -> key=attrgetter('attr')
- key=itemgetter("'") -> key=itemgetter('attr')

Does NOT replace complex patterns like:
- lambda x: x.method()
- lambda x: x.attr1.attr2
- lambda x: some_func(x.attr)
"""

import os
import re
from pathlib import Path


def needs_operator_import(content: str) -> bool:
    """Check if file already has operator import."""
    return "from operator import" in content


def add_operator_import(content: str) -> str:
    """Add operator import if not present."""
    if "from operator import attrgetter" in content:
        return content

    # Find a good place to add the import
    lines = content.split("\n")

    # After __future__ imports
    for i, line in enumerate(lines):
        if "from __future__ import" in line:
            continue
        if line.startswith("import ") or line.startswith("from "):
            if i + 1 < len(lines) and not lines[i + 1].startswith(("import ", "from ")):
                # Insert after this import block
                insert_pos = i + 1
                while insert_pos < len(lines) and lines[insert_pos].strip() == "":
                    insert_pos += 1
                lines.insert(insert_pos, "from operator import attrgetter, itemgetter")
                return "\n".join(lines)

    # Fallback: add at the beginning after any __future__ imports
    for i, line in enumerate(lines):
        if "from __future__ import" in line:
            continue
        if line.strip():
            lines.insert(i, "from operator import attrgetter, itemgetter")
            return "\n".join(lines)

    # Last resort
    return "from operator import attrgetter, itemgetter\n" + content


def replace_simple_lambda(content: str) -> tuple[str, int]:
    """Replace simple lambda patterns with attrgetter/itemgetter."""
    count = 0

    # Pattern 1: key=attrgetter("attr")
    def repl_attr(m) -> str:
        nonlocal count
        m.group(1)
        attr = m.group(2)
        count += 1
        return f'key=attrgetter("{attr}")'

    pattern1 = r"key=lambda\s+(\w+):\s*\1\.(\w+)"
    new_content = re.sub(pattern1, repl_attr, content)

    # Pattern 2: key=itemgetter("'")
    def repl_item(m) -> str:
        nonlocal count
        m.group(1)
        attr = m.group(2)
        count += 1
        return f'key=itemgetter("{attr}")'

    pattern2 = r"key=lambda\s+(\w+):\s*\1\[([\'\"])(\w+)\2\]"
    new_content = re.sub(pattern2, repl_item, new_content)

    # Pattern 3: key=attrgetter("attr1").attr2 - NOT safe, skip
    # Pattern 4: key=attrgetter("method")() - NOT safe, skip

    return new_content, count


def process_file(filepath: str, dry_run: bool = False) -> tuple[bool, int]:
    """Process a single file."""
    try:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
    except:
        return False, 0

    original = content
    new_content, count = replace_simple_lambda(content)

    if count > 0 and new_content != original:
        if not needs_operator_import(new_content):
            new_content = add_operator_import(new_content)

        if not dry_run:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)

        return True, count

    return False, count


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Replace simple lambdas with attrgetter")
    parser.add_argument("paths", nargs="*", help="Files/directories to process")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Show changes without applying")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    base_path = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
    paths = args.paths if args.paths else ["."]

    total_files = 0
    total_count = 0

    for path_str in paths:
        path = base_path / path_str if not Path(path_str).is_absolute() else Path(path_str)

        if path.is_dir():
            for root, dirs, files in os.walk(path):
                # Skip certain directories
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in ["venv", "__pycache__", ".venv", ".venv-test", "archive", ".git", "test"]
                ]

                for f in files:
                    if f.endswith(".py"):
                        filepath = os.path.join(root, f)
                        changed, count = process_file(filepath, args.dry_run)
                        if changed:
                            total_files += 1
                            total_count += count
                            if args.verbose:
                                print(f"{'[DRY-RUN] ' if args.dry_run else ''}{filepath}: {count} replacements")
        elif path.is_file():
            changed, count = process_file(str(path), args.dry_run)
            if changed:
                total_files += 1
                total_count += count
                if args.verbose:
                    print(f"{'[DRY-RUN] ' if args.dry_run else ''}{path}: {count} replacements")

    print(f"\nTotal: {total_count} replacements in {total_files} files")


if __name__ == "__main__":
    main()
