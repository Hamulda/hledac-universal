#!/usr/bin/env python3
"""
ISSUE 6.1 — Masová migrace safe_gather_ok → parallel_ok (F-10)
=============================================================


Codemod: safe_gather_ok(*coros, label=X) → parallel_ok(*coros, label=X)
         safe_gather_ok(*tasks) → parallel_ok(*tasks)
Dead branch removal: isinstance(r, Exception): continue

Usage:
    python tools/migrate/migrate_safe_gather_to_parallel_ok.py --dry-run
    python tools/migrate/migrate_safe_gather_to_parallel_ok.py --apply
    python tools/migrate/migrate_safe_gather_to_parallel_ok.py --dry-run --files discovery/ti_feed_adapter.py
"""

import argparse
import ast
import os
import sys
from pathlib import Path
from typing import Any


SKIP_PARTS = {
    '.venv', 'venv', '__pycache__', 'node_modules', '.git', 'build', 'dist',
    '.venv-test', 'site-packages', '.cache', 'target',
    'tools/migrate_safe_gather_to_parallel_ok.py',
    'utils/async_helpers.py',  # source of truth, not migrated
}


def is_safe_to_migrate(path: str) -> bool:
    parts = Path(path).parts
    return not any(part in SKIP_PARTS for part in parts)


class SafeGatherTransformer(ast.NodeTransformer):
    """Remove isinstance(r, Exception) branches + rename safe_gather_ok → parallel_ok."""

    def __init__(self) -> None:
        super().__init__()
        self.changes: list[str] = []

    def visit_Call(self, node: ast.Call) -> ast.Call | list[ast.Name]:
        # Rename safe_gather_ok → parallel_ok
        if isinstance(node.func, ast.Name) and node.func.id == 'safe_gather_ok':
            node.func = ast.Name(id='parallel_ok', ctx=ast.Load())
            self.changes.append(f"  renamed safe_gather_ok → parallel_ok at line {node.lineno}")
        elif isinstance(node.func, ast.Attribute) and node.func.attr == 'safe_gather_ok':
            node.func = ast.Attribute(value=node.func.value, attr='parallel_ok', ctx=ast.Load())
            self.changes.append(f"  renamed safe_gather_ok → parallel_ok at line {node.lineno}")
        self.generic_visit(node)
        return node

    def visit_For(self, node: ast.For) -> ast.For | list[ast.stmt]:
        """Remove dead isinstance(r, Exception): continue branches in for-loops."""
        # Check if this is a for-loop iterating over safe_gather_ok results
        is_safe_gather_iter = False
        if isinstance(node.iter, ast.Call):
            func = node.iter.func
            if isinstance(func, ast.Name) and func.id == 'safe_gather_ok':
                is_safe_gather_iter = True
            elif isinstance(func, ast.Attribute) and func.attr == 'safe_gather_ok':
                is_safe_gather_iter = True

        if not is_safe_gather_iter:
            self.generic_visit(node)
            return node

        # Filter body: keep only non-isinstance-continue statements
        new_body: list[ast.stmt] = []
        i = 0
        while i < len(node.body):
            stmt = node.body[i]
            # Detect: isinstance(X, Exception)\ncontinue
            if (
                isinstance(stmt, ast.If)
                and len(stmt.body) == 1
                and isinstance(stmt.body[0], ast.Continue)
                and isinstance(stmt.test, ast.Call)
                and isinstance(stmt.test.func, ast.Name)
                and stmt.test.func.id == 'isinstance'
                and len(stmt.test.args) >= 2
            ):
                self.changes.append(f"  removed dead isinstance Exception branch at line {stmt.lineno}")
                i += 1
                continue
            new_body.append(stmt)
            i += 1

        node.body = new_body
        self.generic_visit(node)
        return node


def process_file(path: str, dry_run: bool = True) -> tuple[bool, list[str]]:
    """Process one file. Returns (changed, messages)."""
    try:
        with open(path, encoding='utf-8') as f:
            source = f.read()
    except OSError as e:
        return False, [f"  ERROR reading {path}: {e}"]

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return False, [f"  SYNTAX ERROR in {path}: {e}"]

    transformer = SafeGatherTransformer()
    new_tree = transformer.visit(tree)

    if not transformer.changes:
        return False, []

    if dry_run:
        return True, transformer.changes

    # Apply changes
    new_source = ast.unparse(new_tree)
    backup_path = path + '.bak'
    os.rename(path, backup_path)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_source)
    return True, transformer.changes


def main() -> None:
    parser = argparse.ArgumentParser(description='ISSUE 6.1: safe_gather_ok → parallel_ok migration')
    parser.add_argument('--dry-run', action='store_true', default=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--files', nargs='*', help='Specific files to process')
    parser.add_argument('--root', default='.', help='Root directory to scan')
    args = parser.parse_args()

    dry_run = not args.apply

    if args.files:
        files = args.files
    else:
        # Scan for files using safe_gather_ok
        import subprocess
        result = subprocess.run(
            ['rg', '-l', 'safe_gather_ok', args.root, '--type', 'py'],
            capture_output=True, text=True, cwd=args.root
        )
        if result.returncode != 0:
            print("No files found with safe_gather_ok")
            sys.exit(0)
        files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]

    total_changed = 0
    total_unchanged = 0

    for file in sorted(set(files)):
        if not is_safe_to_migrate(file):
            continue
        full_path = os.path.join(args.root, file) if not os.path.isabs(file) else file
        if not os.path.exists(full_path):
            full_path = file
        changed, msgs = process_file(full_path, dry_run=dry_run)
        if changed:
            total_changed += 1
            print(f"\n{'[DRY-RUN] Would change' if dry_run else '[CHANGED]'}: {full_path}")
            for msg in msgs:
                print(msg)
        else:
            total_unchanged += 1

    action = "Would change" if dry_run else "Changed"
    print(f"\n{action}: {total_changed} files | Unchanged: {total_unchanged} files")

    if dry_run:
        print("\nRun with --apply to apply changes.")


if __name__ == '__main__':
    main()
