#!/usr/bin/env python3
"""
F271 — Detect @dataclass decorators without slots=True
Analyzes Python files and reports:
1. Files with @dataclass WITHOUT slots=True
2. Files with @dataclass(frozen=True) WITHOUT slots=True
3. Summary statistics
"""
import ast
from pathlib import Path


def analyze_dataclass(node: ast.ClassDef) -> dict:
    """Analyze a class decorated with @dataclass."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name) and dec.func.id == 'dataclass':
                # Parse keyword arguments
                has_slots = False
                has_frozen = False
                is_plain = True

                for kw in dec.keywords:
                    if kw.arg == 'slots':
                        if isinstance(kw.value, ast.Constant):
                            has_slots = kw.value.value is True
                    if kw.arg == 'frozen':
                        if isinstance(kw.value, ast.Constant):
                            has_frozen = kw.value.value is True

                if has_slots:
                    is_plain = False

                return {
                    'name': node.name,
                    'lineno': node.lineno,
                    'has_slots': has_slots,
                    'has_frozen': has_frozen,
                    'is_plain': is_plain,
                    'is_frozen_only': has_frozen and not has_slots,
                }
        elif isinstance(dec, ast.Name):
            if dec.id == 'dataclass':
                return {
                    'name': node.name,
                    'lineno': node.lineno,
                    'has_slots': False,
                    'has_frozen': False,
                    'is_plain': True,
                    'is_frozen_only': False,
                }
    return {'name': node.name, 'lineno': node.lineno, 'has_slots': False, 'has_frozen': False, 'is_plain': True, 'is_frozen_only': False}


def analyze_file(path: Path) -> dict:
    """Analyze a Python file for dataclass usage."""
    try:
        content = path.read_text(encoding='utf-8')
        tree = ast.parse(content, filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as e:
        return {'error': str(e), 'path': str(path)}

    dataclasses = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            result = analyze_dataclass(node)
            if result:
                result['path'] = str(path)
                dataclasses.append(result)

    return {
        'path': str(path),
        'dataclasses': dataclasses,
        'total': len(dataclasses),
        'with_slots': sum(1 for d in dataclasses if d['has_slots']),
        'without_slots': sum(1 for d in dataclasses if not d['has_slots']),
        'frozen_only': sum(1 for d in dataclasses if d['is_frozen_only']),
        'plain': sum(1 for d in dataclasses if d['is_plain']),
    }


def main():
    root = Path('/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')

    # Find all Python files - ONLY in project directory
    py_files = []
    for pattern in ['**/*.py']:
        py_files.extend(root.glob(pattern))
    py_files = [f for f in py_files if '__pycache__' not in str(f)]
    py_files = [f for f in py_files if '.pytest_cache' not in str(f)]
    py_files = [f for f in py_files if 'node_modules' not in str(f)]
    py_files = [f for f in py_files if '.venv' not in str(f)]
    py_files = [f for f in py_files if 'site-packages' not in str(f)]

    print(f"Scanning {len(py_files)} Python files...")
    print("=" * 80)

    files_without_slots = []
    all_dataclasses = []

    for i, path in enumerate(sorted(py_files)):
        if i % 100 == 0:
            print(f"Progress: {i}/{len(py_files)}")
        if path.is_dir():
            continue

        result = analyze_file(path)
        if 'error' in result:
            continue

        if result['without_slots'] > 0:
            files_without_slots.append(result)
            all_dataclasses.extend(result['dataclasses'])

    # Statistics
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)

    total_dataclasses = len(all_dataclasses)
    with_slots = sum(1 for d in all_dataclasses if d['has_slots'])
    without_slots = sum(1 for d in all_dataclasses if not d['has_slots'])
    frozen_only = sum(1 for d in all_dataclasses if d['is_frozen_only'])
    plain_only = sum(1 for d in all_dataclasses if d['is_plain'])

    print(f"\nTotal @dataclass definitions found: {total_dataclasses}")
    print(f"  ✅ WITH slots=True:        {with_slots} ({100*with_slots/total_dataclasses:.1f}%)")
    print(f"  ❌ WITHOUT slots=True:     {without_slots} ({100*without_slots/total_dataclasses:.1f}%)")
    print(f"     - frozen=True only:     {frozen_only}")
    print(f"     - plain @dataclass:      {plain_only}")
    print(f"\nFiles needing migration: {len(files_without_slots)}")

    print("\n" + "=" * 80)
    print("FILES REQUIRING MIGRATION (sorted by count)")
    print("=" * 80)

    # Sort by number of missing slots
    files_without_slots.sort(key=lambda x: x['without_slots'], reverse=True)

    for result in files_without_slots[:30]:  # Top 30
        path = result['path'].replace(str(root) + '/', '')
        print(f"\n{path}")
        print(f"  Total: {result['total']}, Missing slots: {result['without_slots']}")
        for dc in result['dataclasses']:
            if not dc['has_slots']:
                flags = []
                if dc['has_frozen']:
                    flags.append('frozen')
                if dc['is_plain']:
                    flags.append('plain')
                print(f"    Line {dc['lineno']}: {dc['name']} ({', '.join(flags) if flags else 'MUTABLE'})")

    if len(files_without_slots) > 30:
        print(f"\n... and {len(files_without_slots) - 30} more files")

    # Save detailed report
    report_path = root / '.scratch' / 'F271-dataclass-analysis' / 'migration_report.txt'
    with open(report_path, 'w') as f:
        f.write("F271 Dataclass Migration Report\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Total @dataclass: {total_dataclasses}\n")
        f.write(f"With slots=True: {with_slots}\n")
        f.write(f"Without slots=True: {without_slots}\n")
        f.write(f"  - frozen only: {frozen_only}\n")
        f.write(f"  - plain: {plain_only}\n")
        f.write(f"\nFiles requiring migration: {len(files_without_slots)}\n\n")

        for result in files_without_slots:
            path = result['path'].replace(str(root) + '/', '')
            f.write(f"\n{path}\n")
            f.write(f"  Total: {result['total']}, Missing: {result['without_slots']}\n")
            for dc in result['dataclasses']:
                if not dc['has_slots']:
                    flags = []
                    if dc['has_frozen']:
                        flags.append('frozen')
                    if dc['is_plain']:
                        flags.append('plain')
                    f.write(f"    {dc['name']} ({', '.join(flags) if flags else 'MUTABLE'})\n")

    print(f"\nDetailed report saved to: {report_path}")


if __name__ == '__main__':
    main()
