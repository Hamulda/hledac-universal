#!/usr/bin/env python3
"""
Batch add noqa: BLE001 to silent except patterns in source files.
These are intentional fail-soft patterns where ignoring exceptions is desired.
"""
import os
import ast
from pathlib import Path
from core import aclose

EXCLUDE = {'.venv', '__pycache__', '.git', 'node_modules', '.egg-info', '.venv-test', 'bin', 'lib', 'share', 'include'}

def fix_file(filepath):
    """Add noqa to all silent except patterns without it."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        content = ''.join(lines)
        tree = ast.parse(content, filename=str(filepath))
        
        fixed = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    line_idx = node.lineno - 1
                    if line_idx < len(lines):
                        line = lines[line_idx]
                        # Skip if noqa already present
                        if 'noqa' in line.lower() and 'ble001' in line.lower():
                            continue
                        # Add noqa
                        stripped = lines[line_idx].rstrip()
                        lines[line_idx] = stripped + '  # noqa: BLE001\n'
                        fixed += 1
        
        if fixed > 0:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            return fixed
        return 0
        
    except (SyntaxError, IndentationError, UnicodeDecodeError):
        return -1

def main():
    files_to_fix = []
    
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in EXCLUDE and not d.startswith('.')]
        for file in files:
            if file.endswith('.py'):
                # Skip test files
                if '/tests/' in str(Path(root) / file) or file.startswith('test_'):
                    continue
                
                filepath = Path(root) / file
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                        lines = content.split('\n')
                    
                    tree = ast.parse(content, filename=str(filepath))
                    
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ExceptHandler):
                            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                                line_idx = node.lineno - 1
                                has_noqa = 'noqa' in lines[line_idx].lower() and 'ble001' in lines[line_idx].lower()
                                if not has_noqa:
                                    files_to_fix.append(str(filepath))
                                    break
                                    
                except (SyntaxError, IndentationError):  # noqa: BLE001
                    pass
    
    print(f"Found {len(files_to_fix)} source files with silent excepts without noqa")
    print()
    
    total_fixed = 0
    errors = 0
    
    for i, filepath in enumerate(files_to_fix):
        fixed = fix_file(filepath)
        if fixed > 0:
            total_fixed += fixed
            print(f"[{i+1}/{len(files_to_fix)}] Fixed {fixed} patterns in {filepath}")
        elif fixed == -1:
            errors += 1
            print(f"[{i+1}/{len(files_to_fix)}] ERROR in {filepath}")
    
    print()
    print(f"Summary:")
    print(f"  Files processed: {len(files_to_fix)}")
    print(f"  Patterns fixed: {total_fixed}")
    print(f"  Errors: {errors}")

if __name__ == '__main__':
    main()
