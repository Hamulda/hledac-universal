#!/usr/bin/env python3
"""Migrate core/ → _core/ to fix Python 3.14 'core' keyword conflict.

In Python 3.14+, 'core' became a reserved keyword due to structural pattern matching.
This script renames the core/ directory to _core/ and updates all imports.
"""

import re
import shutil
from pathlib import Path

ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")


def migrate_file(src_path: Path, dry_run: bool = True) -> tuple[int, int]:
    """Apply transformations to a single file. Returns (substitutions, errors)."""
    try:
        content = src_path.read_text(encoding="utf-8")
    except Exception:
        return 0, 1

    original = content
    substitutions = 0

    # Skip if already migrated
    if "_core" in content and "from _core" in content:
        return 0, 0

    # Pattern 1: `from core import` → `from _core import` (but not `coremltools`)
    # Handle: `from core import x` AND `from core.submodule import x`
    content = re.sub(
        r"(?<![a-zA-Z])from core(\.[a-zA-Z_]\w*)* import",
        lambda m: m.group(0).replace("from core", "from _core"),
        content,
    )

    # Pattern 2: `import core` → `import _core as core` (but not `coremltools`)
    # Match standalone 'import core' not preceded by 'coreml'
    content = re.sub(r"(?<![a-zA-Z])import core(?!\w)", "import _core as core", content)

    # Pattern 3: `from hledac.universal.core.` → `from hledac.universal._core.`
    content = re.sub(r"hledac\.universal\.core\.", "hledac.universal._core.", content)

    # Pattern 4: `hledac.universal.core` as module path → `hledac.universal._core`
    content = re.sub(r"hledac\.universal\.core(?!\w)", "hledac.universal._core", content)

    # Count actual substitutions
    substitutions = (
        original.count("from core import") + original.count("import core") + original.count("hledac.universal.core")
    )

    if not dry_run and content != original:
        src_path.write_text(content, encoding="utf-8")
        backup_path = src_path.with_suffix(src_path.suffix + ".bak_core_keyword")
        backup_path.write_text(original, encoding="utf-8")

    return substitutions, 0


def main(dry_run: bool = True) -> None:
    root = ROOT
    core_dir = root / "core"
    _core_dir = root / "_core"

    print(f"{'DRY RUN' if dry_run else 'EXECUTING'} - Core module migration")
    print(f"Root: {root}")

    if not dry_run:
        if core_dir.exists():
            if _core_dir.exists():
                print(f"ERROR: {_core_dir} already exists!")
                return
            shutil.move(str(core_dir), str(_core_dir))
            print(f"Moved {core_dir} → {_core_dir}")
    else:
        if core_dir.exists():
            print(f"Would rename: {core_dir} → {_core_dir}")

    all_files = list(root.rglob("*.py"))
    print(f"\nScanning {len(all_files)} Python files...")

    total_subs = 0
    total_errors = 0
    modified_files = []

    for py_file in all_files:
        # Skip virtual env and cache
        if ".venv" in py_file.parts or "__pycache__" in py_file.parts:
            continue

        subs, errors = migrate_file(py_file, dry_run)
        if subs > 0:
            modified_files.append((py_file, subs))
            total_subs += subs
        total_errors += errors

    print("\nResults:")
    print(f"  Files to modify: {len(modified_files)}")
    print(f"  Total substitutions: {total_subs}")
    print(f"  Errors: {total_errors}")

    if dry_run:
        print("\nTop 20 files that would be modified:")
        for f, s in sorted(modified_files, key=lambda x: -x[1])[:20]:
            print(f"  {s:4d} | {f.relative_to(root)}")
    else:
        print("\nModified files:")
        for f, s in sorted(modified_files, key=lambda x: -x[1])[:20]:
            print(f"  {s:4d} | {f.relative_to(root)}")
        if len(modified_files) > 20:
            print(f"  ... and {len(modified_files) - 20} more")


if __name__ == "__main__":
    import sys

    dry = "--dry-run" in sys.argv or "-n" in sys.argv
    main(dry_run=dry)
