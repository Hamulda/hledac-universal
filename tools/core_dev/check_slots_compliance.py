"""
Comprehensive __slots__ compliance checker for Hledac Universal.

Reports:
- Classes with explicit __slots__
- Classes with @dataclass(slots=True)
- Classes using msgspec.Struct (automatic slots)
- Plain classes without __slots__

Usage:
    python tools/core_dev/check_slots_compliance.py [--verbose] [--json] [--by-file]
    python tools/core_dev/check_slots_compliance.py --summary
"""
from __future__ import annotations
import argparse
import ast
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

@dataclass(slots=True)
class ClassInfo:
    """Information about a single class."""
    file: str
    name: str
    line: int
    category: str
    bases: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    reason: Optional[str] = None

@dataclass(slots=True)
class FileReport:
    """Report for a single file."""
    path: str
    total_classes: int = 0
    with_slots: int = 0
    with_dataclass_slots: int = 0
    with_msgspec: int = 0
    plain_no_slots: int = 0
    skipped: int = 0
    classes: list[ClassInfo] = field(default_factory=list)

class SlotsComplianceChecker(ast.NodeVisitor):
    """AST visitor that checks __slots__ compliance."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.classes: list[ClassInfo] = []
        self._current_class: Optional[ast.ClassDef] = None
        self._module_imports: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._module_imports.add(alias.asname or alias.name.split('.')[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._module_imports.add(node.module.split('.')[0])
        for alias in node.names:
            self._module_imports.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        old_class = self._current_class
        self._current_class = node
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(ast.unparse(base) if hasattr(ast, 'unparse') else 'attr')
            elif isinstance(base, ast.Subscript):
                bases.append('Generic')
        info = ClassInfo(file=self.filepath, name=node.name, line=node.lineno, category='unknown', bases=bases)
        has_existing_slots = self._has_existing_slots(node)
        dc_info = self._get_dataclass_info(node)
        is_msgspec = self._is_msgspec_struct(node)
        is_pydantic = self._is_pydantic_model(node)
        if has_existing_slots:
            info.category = 'slots'
            info.attributes = self._extract_slots(node)
        elif dc_info:
            if dc_info.get('has_slots'):
                info.category = 'dataclass_slots'
            else:
                info.category = 'dataclass_no_slots'
        elif is_msgspec:
            info.category = 'msgspec_struct'
        elif is_pydantic:
            info.category = 'pydantic_model'
        elif self._should_skip_class(node, bases):
            info.category = 'skipped'
            info.reason = self._get_skip_reason(node, bases)
        else:
            info.category = 'plain_no_slots'
            info.attributes = self._extract_init_attributes(node)
            if not info.attributes:
                info.reason = 'no instance attributes found'
        self.classes.append(info)
        self._current_class = old_class
        self.generic_visit(node)

    def _has_existing_slots(self, node: ast.ClassDef) -> bool:
        """Check if class has __slots__ definition."""
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == '__slots__':
                        return True
                    if isinstance(target, ast.Assign) and any((isinstance(t, ast.Name) and t.id == '__slots__' for t in target.targets)):
                        return True
        return False

    def _extract_slots(self, node: ast.ClassDef) -> list[str]:
        """Extract slot attribute names."""
        slots = []
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == '__slots__':
                        if isinstance(item.value, (ast.Tuple, ast.List)):
                            for elt in item.value.elts:
                                if isinstance(elt, ast.Constant):
                                    slots.append(str(elt.value))
                                elif isinstance(elt, ast.Str):
                                    slots.append(elt.s)
        return slots

    def _get_dataclass_info(self, node: ast.ClassDef) -> Optional[dict]:
        """Check if class is a dataclass and get its options."""
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == 'dataclass':
                return {'is_dc': True, 'has_slots': False, 'bare': True}
            if isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name) and dec.func.id == 'dataclass':
                    has_slots = any((kw.arg == 'slots' and isinstance(kw.value, ast.Constant) and (kw.value.value is True) for kw in dec.keywords if kw.arg))
                    return {'is_dc': True, 'has_slots': has_slots, 'bare': False}
        return None

    def _is_msgspec_struct(self, node: ast.ClassDef) -> bool:
        """Check if class inherits from msgspec.Struct."""
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == 'Struct':
                # Check if msgspec is imported (direct or via compat shim)
                if 'msgspec' in self._module_imports or self._has_msgspec_import():
                    return True
                # Also check for the compat shim import pattern
                if self._has_compat_struct_import():
                    return True
            elif isinstance(base, ast.Attribute):
                base_str = ast.unparse(base) if hasattr(ast, 'unparse') else ''
                if 'msgspec' in base_str.lower():
                    return True
        return False

    def _has_msgspec_import(self) -> bool:
        """Check if msgspec is imported in the module."""
        try:
            content = Path(self.filepath).read_text(encoding='utf-8')
            return 'import msgspec' in content or 'from msgspec' in content
        except Exception:
            return False

    def _has_compat_struct_import(self) -> bool:
        """Check if Struct is imported from the compat shim."""
        try:
            content = Path(self.filepath).read_text(encoding='utf-8')
            return 'from hledac.universal.compat.msgspec_gc_compat import Struct' in content
        except Exception:
            return False

    def _is_pydantic_model(self, node: ast.ClassDef) -> bool:
        """Check if class is a Pydantic model."""
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id in ('BaseModel', 'ConfigDict'):
                return True
            elif isinstance(base, ast.Attribute):
                base_str = ast.unparse(base) if hasattr(ast, 'unparse') else ''
                if 'pydantic' in base_str.lower():
                    return True
        return False

    def _should_skip_class(self, node: ast.ClassDef, bases: list[str]) -> bool:
        """Determine if class should be skipped from slots addition."""
        if 'ABC' in bases:
            return True
        if any(('ABC' in ast.unparse(b) for b in node.bases if hasattr(ast, 'unparse'))):
            return True
        if 'Protocol' in bases:
            return True
        if any(('Exception' in b or 'Error' in b for b in bases)):
            return True
        if node.name.endswith(('Exception', 'Error', 'Warning')):
            return True
        if 'Enum' in bases or 'IntEnum' in bases or 'Flag' in bases:
            return True
        if self._has_dynamic_attrs(node):
            return True
        if self._has_external_base(node):
            return True
        return False

    def _get_skip_reason(self, node: ast.ClassDef, bases: list[str]) -> str:
        """Get the reason why a class should be skipped."""
        if 'ABC' in bases:
            return 'inherits from ABC'
        if 'Protocol' in bases:
            return 'is a Protocol class'
        if any(('Exception' in b or 'Error' in b for b in bases)):
            return 'is an Exception class'
        if 'Enum' in bases or 'IntEnum' in bases:
            return 'is an Enum class'
        if self._has_dynamic_attrs(node):
            return 'uses dynamic __dict__ access'
        if self._has_external_base(node):
            return 'inherits from external class'
        return 'unknown skip reason'

    def _has_dynamic_attrs(self, node: ast.ClassDef) -> bool:
        """Check if __init__ uses setattr or accesses __dict__."""
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Call):
                        if isinstance(stmt.func, ast.Name) and stmt.func.id == 'setattr':
                            return True
                        if isinstance(stmt.func, ast.Attribute) and stmt.func.attr == 'setattr':
                            return True
                    if isinstance(stmt, ast.Attribute):
                        if stmt.attr == '__dict__':
                            return True
                        if stmt.attr == '__weakref__':
                            return True
        return False

    def _has_external_base(self, node: ast.ClassDef) -> bool:
        """Check if class inherits from external library."""
        external_bases = {'object', 'Exception', 'Error', 'BaseException', 'Enum', 'IntEnum', 'Flag', 'IntFlag', 'dict', 'list', 'tuple', 'set', 'frozenset', 'ContextVar', 'Lock', 'RLock', 'Semaphore', 'BaseModel', 'Struct', 'ModuleType'}
        for base in node.bases:
            if isinstance(base, ast.Name):
                if base.id not in external_bases:
                    return True
            elif isinstance(base, ast.Subscript):
                if isinstance(base.value, ast.Name):
                    if base.value.id not in external_bases:
                        return True
        return False

    def _extract_init_attributes(self, node: ast.ClassDef) -> list[str]:
        """Extract instance attribute names from __init__."""
        attrs = []
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == '__init__':
                for stmt in ast.walk(item):
                    if isinstance(stmt, ast.Assign):
                        for target in stmt.targets:
                            if isinstance(target, ast.Attribute):
                                if isinstance(target.value, ast.Name) and target.value.id == 'self':
                                    if target.attr not in attrs and (not target.attr.startswith('_')):
                                        attrs.append(target.attr)
                    elif isinstance(stmt, ast.AnnAssign):
                        target = stmt.target
                        if isinstance(target, ast.Attribute):
                            if isinstance(target.value, ast.Name) and target.value.id == 'self':
                                if target.attr not in attrs and (not target.attr.startswith('_')):
                                    attrs.append(target.attr)
        return sorted(attrs)

def check_file(filepath: str, verbose: bool=False) -> FileReport:
    """Check __slots__ compliance for a single file."""
    rel_path = str(filepath).replace(str(Path.cwd()), '.').lstrip('/\\')
    try:
        content = Path(filepath).read_text(encoding='utf-8')
        tree = ast.parse(content, filename=filepath)
    except (SyntaxError, OSError) as e:
        if verbose:
            print(f'  ERROR: {rel_path}: {e}', file=sys.stderr)
        return FileReport(path=rel_path)
    checker = SlotsComplianceChecker(filepath)
    checker.visit(tree)
    report = FileReport(path=rel_path)
    report.classes = checker.classes
    report.total_classes = len(checker.classes)
    for cls in checker.classes:
        if cls.category == 'slots':
            report.with_slots += 1
        elif cls.category in ('dataclass_slots', 'msgspec_struct', 'pydantic_model'):
            report.with_dataclass_slots += 1
        elif cls.category == 'plain_no_slots':
            report.plain_no_slots += 1
        elif cls.category == 'skipped':
            report.skipped += 1
    return report

def scan_directory(root: str, exclude_dirs: Optional[set[str]]=None) -> list[str]:
    """Scan directory for Python files."""
    if exclude_dirs is None:
        exclude_dirs = {'__pycache__', '.git', '.venv', 'venv', '.venv-test', 'site-packages', 'node_modules', '.pytest_cache', '.mypy_cache', '.claude', 'build', 'dist', 'target', '.egg-info', '.ruff_cache', 'archive', '.hledac', '.ruff_cache', '.test', '.tox'}
    files = []
    skip_test = {'test_', '_test.py', 'tests/', 'test/'}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs and (not d.startswith('.'))]
        for f in filenames:
            if not f.endswith('.py'):
                continue
            if any((t in f for t in skip_test)):
                continue
            if f == '__init__.py':
                continue
            files.append(os.path.join(dirpath, f))
    return files

def generate_summary(reports: list[FileReport]) -> dict:
    """Generate summary statistics."""
    total_classes = sum((r.total_classes for r in reports))
    with_slots = sum((r.with_slots for r in reports))
    with_dataclass_slots = sum((r.with_dataclass_slots for r in reports))
    plain_no_slots = sum((r.plain_no_slots for r in reports))
    skipped = sum((r.skipped for r in reports))
    files_with_slots = sum((1 for r in reports if r.with_slots > 0))
    files_plain_no_slots = sum((1 for r in reports if r.plain_no_slots > 0))
    total_files = len(reports)
    return {'total_files': total_files, 'files_with_slots': files_with_slots, 'files_plain_no_slots': files_plain_no_slots, 'total_classes': total_classes, 'with_explicit_slots': with_slots, 'with_dataclass_or_msgspec': with_dataclass_slots, 'plain_no_slots': plain_no_slots, 'skipped': skipped, 'slots_compliance_pct': round(with_slots / total_classes * 100 if total_classes > 0 else 0, 1), 'overall_optimized_pct': round((with_slots + with_dataclass_slots) / total_classes * 100 if total_classes > 0 else 0, 1)}

def main():
    parser = argparse.ArgumentParser(description='Check __slots__ compliance across the codebase', formatter_class=argparse.RawDescriptionHelpFormatter, epilog='\nExamples:\n    python tools/core_dev/check_slots_compliance.py --summary\n    python tools/core_dev/check_slots_compliance.py --verbose\n    python tools/core_dev/check_slots_compliance.py --by-file --limit 50\n    python tools/core_dev/check_slots_compliance.py --json --output compliance_report.json\n        ')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show verbose output')
    parser.add_argument('--summary', '-s', action='store_true', help='Show summary only')
    parser.add_argument('--by-file', '-f', action='store_true', help='Group by file')
    parser.add_argument('--limit', '-l', type=int, default=0, help='Limit files to check (0=all)')
    parser.add_argument('--json', '-j', action='store_true', help='Output JSON format')
    parser.add_argument('--output', '-o', type=str, help='Output file for JSON')
    parser.add_argument('--root', '-r', default='.', help='Root directory to scan')
    args = parser.parse_args()
    files = scan_directory(args.root)
    if args.limit > 0:
        files = files[:args.limit]
    print(f'Scanning {len(files)} Python files...', file=sys.stderr)
    reports = []
    for i, filepath in enumerate(files):
        if args.verbose and i % 50 == 0:
            print(f'  Progress: {i}/{len(files)}', file=sys.stderr)
        report = check_file(filepath, verbose=args.verbose)
        if report.total_classes > 0:
            reports.append(report)
    summary = generate_summary(reports)
    if args.json:
        import json
        output = {'summary': summary, 'files': [{'path': r.path, 'total_classes': r.total_classes, 'with_slots': r.with_slots, 'with_dataclass_slots': r.with_dataclass_slots, 'plain_no_slots': r.plain_no_slots, 'classes': [{'name': c.name, 'line': c.line, 'category': c.category, 'attributes': c.attributes, 'reason': c.reason} for c in r.classes if c.category == 'plain_no_slots']} for r in reports if r.plain_no_slots > 0]}
        if args.output:
            Path(args.output).write_text(json.dumps(output, indent=2), encoding='utf-8')
            print(f'JSON report written to {args.output}')
        else:
            print(json.dumps(output, indent=2))
    else:
        print()
        print('=' * 70)
        print('  __slots__ COMPLIANCE REPORT')
        print('=' * 70)
        print()
        print(f"  Total Files Scanned:        {summary['total_files']:>6}")
        print(f"  Files with __slots__:      {summary['files_with_slots']:>6}")
        print(f"  Files needing __slots__:    {summary['files_plain_no_slots']:>6}")
        print()
        print(f"  Total Classes:              {summary['total_classes']:>6}")
        print(f"  With explicit __slots__:    {summary['with_explicit_slots']:>6}  ({summary['slots_compliance_pct']}%)")
        print(f"  With dataclass/msgspec:    {summary['with_dataclass_or_msgspec']:>6}  (auto-optimized)")
        print(f"  Plain without __slots__:   {summary['plain_no_slots']:>6}  ⚠️  MEMORY WASTE")
        print(f"  Skipped (ABC/Protocol):    {summary['skipped']:>6}")
        print()
        print(f"  Overall Memory Optimization: {summary['overall_optimized_pct']}%")
        print()
        if not args.summary:
            needs_attention = [r for r in reports if r.plain_no_slots > 0]
            needs_attention.sort(key=lambda r: -r.plain_no_slots)
            print('-' * 70)
            print('  TOP FILES NEEDING __slots__ ATTENTION')
            print('-' * 70)
            for r in needs_attention[:30]:
                classes = [c for c in r.classes if c.category == 'plain_no_slots']
                class_names = ', '.join((c.name for c in classes[:5]))
                if len(classes) > 5:
                    class_names += f' ... +{len(classes) - 5} more'
                print(f'  {r.plain_no_slots:3d}  {r.path}')
                if args.verbose:
                    print(f'       → {class_names}')
            if len(needs_attention) > 30:
                print(f'  ... and {len(needs_attention) - 30} more files')
if __name__ == '__main__':
    main()