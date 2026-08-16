"""
Automatic __slots__ fixer for Hledac Universal.

This script:
1. Scans Python files for classes without __slots__
2. Extracts instance attributes from __init__ type hints and assignments
3. Adds __slots__ = (...) to eligible classes
4. Handles dataclass → @dataclass(slots=True) conversion
5. Validates syntax before writing

Safety rules:
- Skip ABC, Protocol, Exception classes
- Skip classes with dynamic setattr or __dict__ access
- Skip classes inheriting from external libraries
- Only process project files (not tests, venv, etc.)

Usage:
    python tools/core_dev/auto_add_slots.py [--dry-run] [--verbose]
    python tools/core_dev/auto_add_slots.py --diff-only
    python tools/core_dev/auto_add_slots.py --files path/to/file.py
"""
from __future__ import annotations
import argparse
import ast
import os
import sys
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
SKIP_BASES = {'ABC', 'Protocol', 'Exception', 'Error', 'Warning', 'Enum', 'IntEnum', 'Flag', 'IntFlag'}
SAFE_BASES = {'object', 'BaseException', 'dict', 'list', 'tuple', 'set', 'frozenset', 'ContextVar', 'Lock', 'RLock', 'Semaphore', 'Condition', 'BaseModel', 'Struct', 'ModuleType', 'LogRecord'}
EXCLUDE_DIRS = {'__pycache__', '.git', '.venv', 'venv', '.venv-test', 'site-packages', 'node_modules', '.pytest_cache', '.mypy_cache', '.claude', 'build', 'dist', 'target', '.egg-info', '.ruff_cache', 'archive', '.hledac', '.ruff_cache', '.test', '.tox', 'tests', 'probe', 'benchmarks'}

@dataclass(slots=True)
class Change:
    """Represents a change to be made."""
    file: str
    class_name: str
    line: int
    change_type: str
    attributes: list[str] = field(default_factory=list)
    old_decorator: Optional[str] = None
    new_decorator: Optional[str] = None

class SlotsFixer(ast.NodeTransformer):
    """AST transformer that adds __slots__ to eligible classes."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.changes: list[Change] = []
        self._current_class: Optional[ast.ClassDef] = None

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        old_class = self._current_class
        self._current_class = node
        bases = self._get_base_names(node)
        if self._has_existing_slots(node):
            return self.generic_visit(node)
        dc_info = self._get_dataclass_info(node)
        if dc_info:
            if not dc_info.get('has_slots'):
                self._add_dataclass_slots(node, dc_info)
            return self.generic_visit(node)
        if self._should_skip(bases):
            return self.generic_visit(node)
        if self._has_dynamic_attrs(node):
            return self.generic_visit(node)
        attrs = self._extract_attributes(node)
        if not attrs:
            return self.generic_visit(node)
        self._add_slots(node, attrs)
        self._current_class = old_class
        return self.generic_visit(node)

    def _get_base_names(self, node: ast.ClassDef) -> list[str]:
        """Extract base class names."""
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                try:
                    bases.append(ast.unparse(base))
                except Exception:
                    bases.append('attr')
            elif isinstance(base, ast.Subscript):
                try:
                    val = ast.unparse(base.value) if hasattr(ast, 'unparse') else 'Generic'
                    bases.append(val)
                except Exception:
                    bases.append('Generic')
        return bases

    def _has_existing_slots(self, node: ast.ClassDef) -> bool:
        """Check if class already has __slots__."""
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == '__slots__':
                        return True
                    if isinstance(target, ast.Assign):
                        for inner in getattr(target, 'targets', []):
                            if isinstance(inner, ast.Name) and inner.id == '__slots__':
                                return True
        return False

    def _get_dataclass_info(self, node: ast.ClassDef) -> Optional[dict]:
        """Get dataclass decorator info."""
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == 'dataclass':
                return {'has_slots': False, 'is_bare': True}
            if isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name) and dec.func.id == 'dataclass':
                    has_slots = any((kw.arg == 'slots' and isinstance(kw.value, ast.Constant) and (kw.value.value is True) for kw in dec.keywords if kw.arg))
                    return {'has_slots': has_slots, 'is_bare': False}
        return None

    def _should_skip(self, bases: list[str]) -> bool:
        """Determine if class should be skipped."""
        if any((b in SKIP_BASES for b in bases)):
            return True
        name = self._current_class.name if self._current_class else ''
        if any((name.endswith(suffix) for suffix in ('Exception', 'Error', 'Warning'))):
            return True
        if 'Enum' in bases or 'Flag' in bases:
            return True
        if self._has_external_base(bases):
            return True
        return False

    def _has_external_base(self, bases: list[str]) -> bool:
        """Check if class has external non-safe base."""
        for base in bases:
            if base in SAFE_BASES:
                continue
            if base in SKIP_BASES:
                continue
            return True
        return False

    def _has_dynamic_attrs(self, node: ast.ClassDef) -> bool:
        """Check if __init__ uses dynamic attributes."""
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Call):
                        if isinstance(stmt.func, ast.Name) and stmt.func.id == 'setattr':
                            return True
                        if isinstance(stmt.func, ast.Attribute) and stmt.func.attr == 'setattr':
                            return True
                    if isinstance(stmt, ast.Attribute):
                        if stmt.attr in ('__dict__', '__weakref__'):
                            return True
        return False

    def _extract_attributes(self, node: ast.ClassDef) -> list[str]:
        """Extract instance attribute names from __init__."""
        attrs: set[str] = set()
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Assign):
                        for target in stmt.targets:
                            if isinstance(target, ast.Attribute):
                                if isinstance(target.value, ast.Name) and target.value.id == 'self':
                                    attr = target.attr
                                    if not attr.startswith('__') and (not attr.endswith('__')):
                                        attrs.add(attr)
                    elif isinstance(stmt, ast.AnnAssign):
                        target = stmt.target
                        if isinstance(target, ast.Attribute):
                            if isinstance(target.value, ast.Name) and target.value.id == 'self':
                                attr = target.attr
                                if not attr.startswith('__') and (not attr.endswith('__')):
                                    attrs.add(attr)
        return sorted(attrs)

    def _add_dataclass_slots(self, node: ast.ClassDef, dc_info: dict) -> None:
        """Add slots=True to a dataclass decorator."""
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == 'dataclass':
                old_str = '@dataclass'
                new_node = ast.Call(func=ast.Name(id='dataclass', ctx=ast.Load()), args=[], keywords=[ast.keyword(arg='slots', value=ast.Constant(value=True))])
                idx = node.decorator_list.index(dec)
                node.decorator_list[idx] = new_node
                self.changes.append(Change(file=self.filepath, class_name=node.name, line=node.lineno, change_type='dataclass_slots', old_decorator='@dataclass', new_decorator='@dataclass(slots=True)'))
                break
            elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and (dec.func.id == 'dataclass'):
                has_slots = any((kw.arg == 'slots' for kw in dec.keywords if kw.arg))
                if not has_slots:
                    dec.keywords.append(ast.keyword(arg='slots', value=ast.Constant(value=True)))
                    self.changes.append(Change(file=self.filepath, class_name=node.name, line=node.lineno, change_type='dataclass_slots'))
                break

    def _add_slots(self, node: ast.ClassDef, attrs: list[str]) -> None:
        """Add __slots__ = (...) to a class."""
        slots_value = ast.Tuple(elts=[ast.Constant(value=a) for a in attrs], ctx=ast.Load())
        slots_node = ast.Assign(targets=[ast.Name(id='__slots__', ctx=ast.Store())], value=slots_value, lineno=node.lineno, col_offset=0)
        insert_idx = 0
        for i, item in enumerate(node.body):
            if i == 0 and isinstance(item, ast.Expr):
                val = item.value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    insert_idx = 1
                    continue
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                insert_idx = i
                break
        else:
            insert_idx = len(node.body)
        node.body.insert(insert_idx, slots_node)
        self.changes.append(Change(file=self.filepath, class_name=node.name, line=node.lineno, change_type='add_slots', attributes=attrs))

def is_project_file(filepath: str) -> bool:
    """Check if file is a project file (not test/venv/etc)."""
    parts = filepath.split(os.sep)
    for part in parts:
        if part in EXCLUDE_DIRS:
            return False
        if part.startswith('.venv') or part.startswith('venv'):
            return False
        if part in ('site-packages', 'node_modules', '.git', '__pycache__'):
            return False
    if '/tests/' in filepath or '/test_' in filepath or '_test.py' in filepath:
        return False
    return True

def scan_files(root: str) -> list[str]:
    """Scan directory for Python files to process."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and (not d.startswith('.'))]
        for f in filenames:
            if not f.endswith('.py'):
                continue
            if f == '__init__.py':
                continue
            fp = os.path.join(dirpath, f)
            if is_project_file(fp):
                files.append(fp)
    return files

def process_file(filepath: str, dry_run: bool=False, verbose: bool=False) -> list[Change]:
    """Process a single file, return list of changes."""
    try:
        content = Path(filepath).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as e:
        if verbose:
            print(f'  SKIP {filepath}: read error {e}', file=sys.stderr)
        return []
    try:
        tree = ast.parse(content, filename=filepath)
    except SyntaxError as e:
        if verbose:
            print(f'  SKIP {filepath}: syntax error {e}', file=sys.stderr)
        return []
    transformer = SlotsFixer(filepath)
    new_tree = transformer.visit(tree)
    if not transformer.changes:
        return []
    if dry_run:
        return transformer.changes
    try:
        new_content = ast.unparse(new_tree)
    except Exception as e:
        if verbose:
            print(f'  SKIP {filepath}: unparse error {e}', file=sys.stderr)
        return []
    try:
        ast.parse(new_content)
    except SyntaxError as e:
        if verbose:
            print(f'  ABORT {filepath}: re-parse failed {e}', file=sys.stderr)
        return []
    try:
        path = Path(filepath)
        backup_path = Path(filepath + '.slots.bak')
        backup_path.write_text(content, encoding='utf-8')
        path.write_text(new_content, encoding='utf-8')
        if backup_path.exists():
            backup_path.unlink()
    except OSError as e:
        if verbose:
            print(f'  ERROR writing {filepath}: {e}', file=sys.stderr)
        return []
    return transformer.changes

def show_diff(filepath: str, changes: list[Change]) -> None:
    """Show diff of changes for a file."""
    print(f"\n{'=' * 70}")
    print(f'  {filepath}')
    print('=' * 70)
    for change in changes:
        if change.change_type == 'dataclass_slots':
            print(f'  Line {change.line}: {change.class_name}')
            print(f'    {change.old_decorator} → {change.new_decorator}')
        else:
            print(f'  Line {change.line}: {change.class_name}')
            print(f'    + __slots__ = {change.attributes}')

def main():
    parser = argparse.ArgumentParser(description='Automatically add __slots__ to classes', formatter_class=argparse.RawDescriptionHelpFormatter, epilog='\nExamples:\n    python tools/core_dev/auto_add_slots.py --dry-run --verbose\n    python tools/core_dev/auto_add_slots.py --diff-only\n    python tools/core_dev/auto_add_slots.py --files path/to/file.py\n        ')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Show changes without writing')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed output')
    parser.add_argument('--diff-only', '-d', action='store_true', help='Show diff of changes (implies --dry-run)')
    parser.add_argument('--files', '-f', nargs='*', help='Specific files to process')
    parser.add_argument('--root', '-r', default='.', help='Root directory to scan')
    args = parser.parse_args()
    dry_run = args.dry_run or args.diff_only
    if args.files:
        files = [f for f in args.files if is_project_file(f)]
    else:
        files = scan_files(args.root)
    if not files:
        print('No files to process.')
        sys.exit(0)
    print(f"{('DRY RUN: ' if dry_run else '')}Processing {len(files)} files...", file=sys.stderr)
    all_changes = []
    errors = []
    for i, filepath in enumerate(files):
        if args.verbose and i % 100 == 0:
            print(f'  Progress: {i}/{len(files)}', file=sys.stderr)
        try:
            changes = process_file(filepath, dry_run=dry_run, verbose=args.verbose)
            if changes:
                all_changes.extend(changes)
                if args.diff_only:
                    show_diff(filepath, changes)
        except Exception as e:
            errors.append((filepath, str(e)))
            if args.verbose:
                traceback.print_exc()
    print()
    print('=' * 70)
    if dry_run:
        print(f'  DRY RUN - {len(all_changes)} changes would be made')
    else:
        print(f'  Applied {len(all_changes)} changes')
    if errors:
        print(f'  Errors: {len(errors)}')
        for fp, err in errors[:10]:
            print(f'    {fp}: {err}')
    print('=' * 70)
    add_slots = [c for c in all_changes if c.change_type == 'add_slots']
    dc_slots = [c for c in all_changes if c.change_type == 'dataclass_slots']
    print(f'\n  __slots__ additions:     {len(add_slots):>5} classes')
    print(f'  @dataclass(slots=True): {len(dc_slots):>5} classes')
    print(f'  TOTAL:                  {len(all_changes):>5} classes')
    if all_changes:
        file_counts: dict[str, int] = defaultdict(int)
        for c in all_changes:
            file_counts[c.file] += 1
        print('\n  Top files by change count:')
        for fp, count in sorted(file_counts.items(), key=lambda x: -x[1])[:10]:
            rel_path = fp.split('hledac/universal/')[-1] if 'hledac/universal/' in fp else fp
            print(f'    {count:3d}  {rel_path}')
if __name__ == '__main__':
    main()