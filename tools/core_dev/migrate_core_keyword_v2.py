#!/usr/bin/env python3
"""Migrate core/ → _core/ to fix Python 3.14 'core' keyword conflict.

In Python 3.14+, 'core' became a reserved keyword due to structural pattern matching.
This script renames the core/ directory to _core/ and updates all imports.
"""
import re
import shutil
from pathlib import Path

ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

def migrate_content(content: str) -> tuple[str, int]:
    """Apply transformations to content. Returns (new_content, substitutions)."""
    original = content
    substitutions = 0
    
    # Process line by line to avoid issues with multi-line imports
    lines = content.split('\n')
    new_lines = []
    
    for line in lines:
        new_line = line
        
        # Pattern 1: `from core import x` or `from core.submodule import x` at line start
        if re.match(r'^from core(\.[a-zA-Z_]\w*)* import', new_line):
            if not new_line.strip().startswith('from .core'):
                new_line = re.sub(r'^from core(\.[a-zA-Z_]\w*)* import', 
                                  lambda m: 'from _core' + m.group(0)[5:], new_line)
                substitutions += 1
        
        # Pattern 2: `import core` as standalone statement
        if re.match(r'^import core$', new_line.strip()):
            new_line = re.sub(r'^import core$', 'import _core as core', new_line.strip())
            substitutions += 1
        
        # Pattern 3: hledac.universal.core. → hledac.universal._core.
        if 'hledac.universal.core.' in new_line:
            new_line = new_line.replace('hledac.universal.core.', 'hledac.universal._core.')
            substitutions += 1
        
        # Pattern 4: hledac.universal.core (not followed by .)
        if 'hledac.universal.core' in new_line and 'hledac.universal._core' not in new_line:
            new_line = re.sub(r'hledac\.universal\.core(?!\w)', 'hledac.universal._core', new_line)
            if 'hledac.universal._core' in new_line:
                substitutions += 1
        
        new_lines.append(new_line)
    
    return '\n'.join(new_lines), substitutions


def migrate_file(src_path: Path, dry_run: bool = True) -> tuple[int, int]:
    """Apply transformations to a single file. Returns (substitutions, errors)."""
    try:
        content = src_path.read_text(encoding="utf-8")
    except Exception:
        return 0, 1
    
    new_content, substitutions = migrate_content(content)
    
    if not dry_run and content != new_content:
        backup_path = src_path.with_suffix(src_path.suffix + ".bak_core_keyword")
        if not backup_path.exists():
            backup_path.write_text(content, encoding="utf-8")
        src_path.write_text(new_content, encoding="utf-8")
    
    return substitutions, 0


def main(dry_run: bool = True):
    root = ROOT
    core_dir = root / "core"
    _core_dir = root / "_core"
    
    print(f"{'DRY RUN' if dry_run else 'EXECUTING'} - Core module migration")
    print(f"Root: {root}")
    
    # Step 1: Rename core/ → _core/
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
    
    # Step 2: Update all Python files
    all_files = list(root.rglob("*.py"))
    print(f"\nScanning {len(all_files)} Python files...")
    
    total_subs = 0
    total_errors = 0
    modified_files = []
    
    for py_file in all_files:
        if ".venv" in py_file.parts or "__pycache__" in py_file.parts:
            continue
        if ".bak_core_keyword" in py_file.name:
            continue
            
        subs, errors = migrate_file(py_file, dry_run)
        if subs > 0:
            modified_files.append((py_file, subs))
            total_subs += subs
        total_errors += errors
    
    print(f"\nResults:")
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
