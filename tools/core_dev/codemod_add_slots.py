"""
AST-based __slots__ codemod for Hledac Universal.

Handles three categories:
  1. @dataclass → @dataclass(slots=True)  [764 classes — trivial]
  2. Plain class with annotated __init__ → add __slots__  [545 classes — medium]
  3. Skip: ABC base classes, Protocols, exception classes, classes with setattr

Usage:
    uv run python tools/codemod_add_slots.py [--dry-run] [--verbose]
    uv run python tools/codemod_add_slots.py --diff-only
"""
from __future__ import annotations
import argparse
import ast
import os
import sys
import traceback
from collections import defaultdict
from dataclasses import field
from typing import Any

def is_project_file(filepath: str) -> bool:
    venv_patterns = ('.venv', '.venv-test', 'site-packages', 'deps')
    skip_dirs = {'__pycache__', '.git', 'node_modules', '.pytest_cache', '.mypy_cache', '.claude', 'build', 'dist', 'target', '.egg-info', '.hledac', '.ruff_cache', 'archive'}
    parts = filepath.split(os.sep)
    if any((p in venv_patterns for p in parts)):
        return False
    if '/tests/' in filepath or '/test_' in filepath:
        return False
    if any((s in parts for s in skip_dirs)):
        return False
    return True

def get_class_body_assignments(node: ast.ClassDef) -> dict[str, list[ast.stmt]]:
    """Return {attr_name: [assignment nodes]} for self.attr assignments in __init__."""
    assignments: dict[str, list[ast.stmt]] = defaultdict(list)
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == '__init__':
            for stmt in ast.walk(item):
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and (target.value.id == 'self'):
                            assignments[target.attr].append(stmt)
                elif isinstance(stmt, ast.AnnAssign):
                    target = stmt.target
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and (target.value.id == 'self'):
                        assignments[target.attr].append(stmt)
    return assignments

def has_setattr_in_init(node: ast.ClassDef) -> bool:
    """Check if __init__ contains dynamic setattr calls."""
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == '__init__':
            for stmt in ast.walk(item):
                if isinstance(stmt, ast.Call) and isinstance(stmt.func, ast.Name) and (stmt.func.id == 'setattr'):
                    return True
    return False

def get_dataclass_decorator(node: ast.ClassDef) -> ast.expr | None:
    """Return the @dataclass or @dataclass(...) Call node."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == 'dataclass':
            return dec
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and (dec.func.id == 'dataclass'):
            return dec
    return None

class SlotsTransformer(ast.NodeTransformer):
    """
    AST transformer that adds __slots__ to classes.

    Category 1 (@dataclass): Set slots=True on existing decorator
    Category 2 (plain class): Insert __slots__ = (...) as first class body item
    """
    __slots__ = tuple(('changes',))

    def __init__(self) -> None:
        self.changes: list[dict[str, Any]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        bases = [getattr(b, 'id', '') or getattr(b, 'attr', '') for b in node.bases]
        is_dc_call = False
        has_existing_slots = any((isinstance(s, ast.Assign) and any((isinstance(t, ast.Name) and t.id == '__slots__' for t in s.targets)) for s in node.body))
        dc_dec = get_dataclass_decorator(node)
        if dc_dec is not None:
            if isinstance(dc_dec, ast.Call):
                has_slots_kw = any((kw.arg == 'slots' for kw in dc_dec.keywords if kw.arg))
                if not has_slots_kw and (not has_existing_slots):
                    dc_dec.keywords.append(ast.keyword(arg='slots', value=ast.Constant(value=True)))
                    self.changes.append({'type': 'dataclass', 'file': getattr(node, '_filepath', '?'), 'line': node.lineno, 'name': node.name, 'action': 'added slots=True'})
            else:
                idx = node.decorator_list.index(dc_dec)
                new_dec = ast.Call(func=ast.Name(id='dataclass', ctx=ast.Load()), args=[ast.Constant(value=True)], keywords=[])
                node.decorator_list[idx] = new_dec
                self.changes.append({'type': 'dataclass', 'file': getattr(node, '_filepath', '?'), 'line': node.lineno, 'name': node.name, 'action': 'bare @dataclass → @dataclass(slots=True)'})
            return self.generic_visit(node)
        if has_existing_slots:
            return self.generic_visit(node)
        is_abc = 'ABC' in bases or 'ABC' in str(node.decorator_list)
        is_protocol = 'Protocol' in bases
        is_exception = any(('Exception' in b or 'Error' in b for b in bases))
        has_dynamic = has_setattr_in_init(node)
        if is_abc or is_protocol or is_exception or has_dynamic:
            return self.generic_visit(node)
        assignments = get_class_body_assignments(node)
        if not assignments:
            return self.generic_visit(node)
        attrs = sorted(assignments.keys())
        slots_node = ast.Assign(targets=[ast.Name(id='__slots__', ctx=ast.Store())], value=ast.Call(func=ast.Name(id='tuple', ctx=ast.Load()), args=[ast.Tuple(elts=[ast.Constant(value=a) for a in attrs], ctx=ast.Load())], keywords=[]))
        insert_idx = 0
        for i, item in enumerate(node.body):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                insert_idx = i
                break
        else:
            insert_idx = 0
        slots_node.lineno = node.lineno
        slots_node.col_offset = 0
        slots_node.end_lineno = node.lineno
        slots_node.end_col_offset = len('__slots__') + 2
        node.body.insert(insert_idx, slots_node)
        self.changes.append({'type': 'plain', 'file': getattr(node, '_filepath', '?'), 'line': node.lineno, 'name': node.name, 'action': f'added __slots__={attrs}', 'count': len(attrs)})
        return self.generic_visit(node)

def process_file(filepath: str, dry_run: bool=False, verbose: bool=False) -> list[dict]:
    """Process one file, return list of changes."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
    except (UnicodeDecodeError, OSError):
        return []
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []
    for node in ast.walk(tree):
        node._filepath = filepath
    transformer = SlotsTransformer()
    new_tree = transformer.visit(tree)
    if not transformer.changes:
        return []
    if not dry_run:
        try:
            new_source = ast.unparse(new_tree)
        except Exception as e:
            print(f'  SKIP (unparse error): {filepath}: {e}', file=sys.stderr)
            return []
        backup = filepath + '.bak'
        os.rename(filepath, backup)
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_source)
        except OSError:
            os.rename(backup, filepath)
            raise
        else:
            os.remove(backup)
    return transformer.changes

def main() -> None:
    parser = argparse.ArgumentParser(description='Add __slots__ to classes')
    parser.add_argument('--dry-run', action='store_true', help='Show changes without writing')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show details')
    parser.add_argument('--files', nargs='*', help='Specific files to process')
    args = parser.parse_args()
    root = '.'
    all_changes = []
    files_skipped = 0
    files_processed = 0
    if args.files:
        filepaths = [f for f in args.files if is_project_file(f)]
    else:
        filepaths = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {'__pycache__', '.git', 'node_modules', '.pytest_cache', '.mypy_cache', '.claude', 'build', 'dist', 'target', '.egg-info', '.hledac', '.ruff_cache', 'archive'}]
            for f in filenames:
                if not f.endswith('.py'):
                    continue
                fp = os.path.join(dirpath, f)
                if is_project_file(fp):
                    filepaths.append(fp)
    for filepath in sorted(filepaths):
        changes = process_file(filepath, dry_run=args.dry_run)
        if changes:
            all_changes.extend(changes)
            files_processed += 1
            if args.verbose:
                for c in changes:
                    print(f"  {c['file']}:{c['line']}  {c['name']}  [{c['type']}]  {c['action']}")
        else:
            files_skipped += 1
    dc_changes = [c for c in all_changes if c['type'] == 'dataclass']
    plain_changes = [c for c in all_changes if c['type'] == 'plain']
    print()
    print(f"{'=' * 60}")
    print(f"  {('DRY RUN — no files written' if args.dry_run else 'Files modified')}: {files_processed}")
    print(f'  Files skipped (no changes): {files_skipped}')
    print(f"{'=' * 60}")
    print(f'  @dataclass → slots=True:     {len(dc_changes):5d}  classes')
    print(f'  Plain class → __slots__:    {len(plain_changes):5d}  classes')
    print(f'  TOTAL:                      {len(all_changes):5d}  classes')
    print()
    if not args.verbose and all_changes:
        file_counts = defaultdict(int)
        for c in all_changes:
            file_counts[c['file']] += 1
        print('Top 20 files by change count:')
        for f, count in sorted(file_counts.items(), key=lambda x: -x[1])[:20]:
            f2 = f.split('hledac/')[-1] if 'hledac/' in f else f
            print(f'  {count:3d}  {f2}')
    sys.exit(0)
if __name__ == '__main__':
    main()