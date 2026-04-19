#!/usr/bin/env python3
"""
Automatic Import Optimization Script for Hledac Project

This script automatically optimizes Python imports according to PEP8 standards:
- Removes duplicate imports
- Sorts imports according to PEP8 (stdlib, third-party, local)
- Removes unused imports
- Fixes common import issues

Usage:
    python optimize_imports.py [--dry-run] [--check-only] [path]

Examples:
    python optimize_imports.py --dry-run hledac/     # Preview changes
    python optimize_imports.py --check-only         # Check if fixes needed
    python optimize_imports.py hledac/               # Apply fixes
"""

import ast
import os
import sys
import argparse
import subprocess
from pathlib import Path
from typing import List, Set, Tuple, Optional
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


class ImportOptimizer:
    """Optimizes Python imports in source files."""

    def __init__(self, check_only: bool = False, dry_run: bool = False):
        self.check_only = check_only
        self.dry_run = dry_run
        self.files_with_issues = []

    def optimize_file(self, filepath: Path) -> bool:
        """Optimize imports in a single file."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            original_content = content

            # Parse AST to analyze imports
            try:
                tree = ast.parse(content)
            except SyntaxError as e:
                logger.warning(f"Syntax error in {filepath}: {e}")
                    return False

            # Find and fix issues
            content = self._remove_duplicate_imports(content)
            content = self._organize_imports(content, tree, filepath)
            content = self._remove_unused_imports(content, filepath)

            # Check if changes were made
            if content != original_content:
                    if self.check_only:
                    logger.info(f"Would fix: {filepath}")
                    self.files_with_issues.append(filepath)
                        return True
                elif self.dry_run:
                        logger.info(f"Changes needed in: {filepath}")
                    self.files_with_issues.append(filepath)
                        return True
                else:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(content)
                    logger.info(f"Fixed: {filepath}")
                        return True

                return False

        except Exception as e:
            logger.error(f"Error processing {filepath}: {e}")
                return False

    def _remove_duplicate_imports(self, content: str) -> str:
        """Remove duplicate import statements."""
        lines = content.split('\n')
        seen_imports = set()
        result_lines = []

        for line in lines:
            stripped = line.strip()
            # Check for simple import statements
            if stripped.startswith('import ') and not stripped.startswith('import .'):
                    import_name = stripped.replace('import ', '').split(' as ')[0]
                if import_name not in seen_imports:
                        seen_imports.add(import_name)
                    result_lines.append(line)
                else:
                    logger.debug(f"Removing duplicate import: {line}")
            else:
                result_lines.append(line)

            return '\n'.join(result_lines)

    def _organize_imports(self, content: str, tree: ast.AST, filepath: Path) -> str:
        """Organize imports according to PEP8."""
        lines = content.split('\n')

        # Extract import statements
        stdlib_imports = []
        thirdparty_imports = []
        local_imports = []

        # Simple categorization (can be enhanced)
        stdlib_modules = {
            'asyncio', 'json', 'time', 'logging', 'pickle', 'uuid', 'hashlib',
            'os', 're', 'tempfile', 'shutil', 'zipfile', 'tarfile', 'gzip',
            'bz2', 'lzma', 'math', 'sqlite3', 'collections', 'datetime',
            'pathlib', 'contextlib', 'enum', 'dataclasses', 'typing', 'urllib'
        }

        current_section = []
        in_import_section = True

        for line in lines:
            stripped = line.strip()

            if stripped.startswith(('import ', 'from ')) and not stripped.startswith('#'):
                    if not in_import_section:
                    # Start of new import section
                    in_import_section = True

                # Categorize import
                if any(module in stripped for module in stdlib_modules):
                        stdlib_imports.append(line)
                elif 'aiohttp' in stripped or 'aiofiles' in stripped or 'numpy' in stripped or 'redis' in stripped:
                        thirdparty_imports.append(line)
                elif 'hledac.' in stripped:
                        local_imports.append(line)
                else:
                    # Default to stdlib for unknown modules
                    stdlib_imports.append(line)
            elif stripped == '' or (not stripped.startswith(('import ', 'from ')) and in_import_section):
                # End of import section
                if current_section:
                    # Add current section to appropriate category
                    if current_section and any('hledac.' in imp for imp in current_section):
                            local_imports.extend(current_section)
                    elif any('aiohttp' in imp or 'aiofiles' in imp for imp in current_section):
                            thirdparty_imports.extend(current_section)
                    else:
                        stdlib_imports.extend(current_section)
                    current_section = []

                in_import_section = False
                result_lines.append(line)
            else:
                result_lines.append(line)

        # Add remaining imports
        if current_section:
                if any('hledac.' in imp for imp in current_section):
                local_imports.extend(current_section)
            else:
                stdlib_imports.extend(current_section)

        # Rebuild content with organized imports
        if stdlib_imports or thirdparty_imports or local_imports:
                organized_lines = []

            if stdlib_imports:
                    organized_lines.extend(sorted(stdlib_imports))
                organized_lines.append('')

            if thirdparty_imports:
                    organized_lines.extend(sorted(thirdparty_imports))
                organized_lines.append('')

            if local_imports:
                    organized_lines.extend(sorted(local_imports))
                organized_lines.append('')

            # Find where to insert organized imports
            result_lines = []
            imports_added = False

            for line in lines:
                stripped = line.strip()
                if stripped.startswith(('import ', 'from ')) and not imports_added:
                        if not imports_added:
                        result_lines.extend(organized_lines)
                        imports_added = True
                elif not stripped.startswith(('import ', 'from ')) and not imports_added:
                    # Insert imports before first non-import line
                    if organized_lines:
                            result_lines.extend(organized_lines)
                        imports_added = True
                    result_lines.append(line)
                else:
                    if not (stripped.startswith(('import ', 'from ')) and imports_added):
                            result_lines.append(line)

                return '\n'.join(result_lines)

            return content

    def _remove_unused_imports(self, content: str, filepath: Path) -> str:
        """Remove unused imports (basic implementation)."""
        # This is a simplified implementation
        # For production, use tools like autoflake or pyflakes
        try:
            # Run autoflake if available
            result = subprocess.run([
                sys.executable, '-m', 'autoflake',
                '--remove-all-unused-imports',
                '--remove-unused-variables',
                '--remove-duplicate-keys',
                '--stdin-display-name', str(filepath)
            ], input=content.encode('utf-8'), capture_output=True)

            if result.returncode == 0:
                        return result.stdout.decode('utf-8')
        except FileNotFoundError:
            logger.debug("autoflake not available, skipping unused import removal")

            return content

    def optimize_directory(self, path: Path) -> None:
        """Optimize imports in all Python files in directory."""
        python_files = list(path.rglob('*.py'))

        if not python_files:
                logger.info(f"No Python files found in {path}")
                return

        logger.info(f"Processing {len(python_files)} Python files in {path}")

        files_fixed = 0
        for filepath in python_files:
            if self.optimize_file(filepath):
                    files_fixed += 1

        if self.check_only:
                if files_fixed > 0:
                logger.error(f"Found issues in {files_fixed} files. Run with --fix to resolve.")
                sys.exit(1)
            else:
                logger.info("All files are properly formatted!")
        elif self.dry_run:
                logger.info(f"Would fix {files_fixed} files.")
        else:
            logger.info(f"Fixed {files_fixed} files.")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Optimize Python imports in Hledac project')
    parser.add_argument('path', nargs='?', default='hledac/',
                        help='Path to optimize (default: hledac/)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be changed without making changes')
    parser.add_argument('--check-only', action='store_true',
                        help='Check if files need fixing (exit code 1 if issues found)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output')

    args = parser.parse_args()

    if args.verbose:
            logging.getLogger().setLevel(logging.DEBUG)

    path = Path(args.path)
    if not path.exists():
            logger.error(f"Path does not exist: {path}")
        sys.exit(1)

    optimizer = ImportOptimizer(check_only=args.check_only, dry_run=args.dry_run)

    if path.is_file():
            optimizer.optimize_file(path)
    else:
        optimizer.optimize_directory(path)


if __name__ == '__main__':
        main()