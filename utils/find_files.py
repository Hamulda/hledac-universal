#!/usr/bin/env python3
"""File search utility created by Claude Code agent simulation."""

from pathlib import Path
from typing import List, Union, Optional
import fnmatch


def find_files(
    directory: Union[str, Path],
    pattern: str = "*",
    recursive: bool = True,
    case_sensitive: bool = True,
    max_depth: Optional[int] = None
) -> List[Path]:
    """
    Find files matching a pattern in a directory tree.

    Args:
        directory: The root directory to search in
        pattern: Glob pattern to match (e.g., "*.py", "**/*.json")
        recursive: Whether to search recursively
        case_sensitive: Whether pattern matching should be case sensitive
        max_depth: Maximum recursion depth (None for unlimited)

    Returns:
        List of Path objects for matching files

    Raises:
        ValueError: If directory doesn't exist
        PermissionError: If lacking read permissions
    """
    dir_path = Path(directory)

    # Validate input directory
    if not dir_path.exists():
        raise ValueError(f"Directory does not exist: {directory}")

    if not dir_path.is_dir():
        raise ValueError(f"Path is not a directory: {directory}")

    matches = []

    # Choose the appropriate glob method
    if recursive:
        if case_sensitive:
            # Use pattern directly for case-sensitive search
            if pattern.startswith("**/"):
                glob_pattern = pattern
            else:
                glob_pattern = f"**/{pattern}"

            for file_path in dir_path.rglob(glob_pattern):
                if file_path.is_file():
                    # Check depth limit if specified
                    if max_depth is not None:
                        relative_path = file_path.relative_to(dir_path)
                        depth = len(relative_path.parts)
                        if depth > max_depth:
                            continue
                    matches.append(file_path)
        else:
            # For case insensitive, search all files and filter
            for file_path in dir_path.rglob("*"):
                if file_path.is_file():
                    # Apply case insensitive pattern matching
                    if fnmatch.fnmatch(file_path.name.lower(), pattern.lower()):
                        # Check depth limit if specified
                        if max_depth is not None:
                            relative_path = file_path.relative_to(dir_path)
                            depth = len(relative_path.parts)
                            if depth > max_depth:
                                continue
                        matches.append(file_path)
    else:
        # Non-recursive search
        if case_sensitive:
            for file_path in dir_path.glob(pattern):
                if file_path.is_file():
                    matches.append(file_path)
        else:
            # Case insensitive non-recursive search
            for file_path in dir_path.glob("*"):
                if file_path.is_file() and fnmatch.fnmatch(file_path.name.lower(), pattern.lower()):
                    matches.append(file_path)

    return matches


def find_files_by_extension(
    directory: Union[str, Path],
    extensions: Union[str, List[str]],
    recursive: bool = True
) -> List[Path]:
    """
    Find files by extension(s).

    Args:
        directory: The root directory to search in
        extensions: File extension(s) to search for (e.g., "py" or ["py", "js"])
        recursive: Whether to search recursively

    Returns:
        List of Path objects for matching files
    """
    if isinstance(extensions, str):
        extensions = [extensions]

    all_matches = []
    for ext in extensions:
        # Remove leading dot if present
        ext = ext.lstrip('.')
        pattern = f"*.{ext}"
        matches = find_files(directory, pattern, recursive)
        all_matches.extend(matches)

    # Remove duplicates while preserving order
    seen = set()
    unique_matches = []
    for match in all_matches:
        if match not in seen:
            seen.add(match)
            unique_matches.append(match)

    return unique_matches


# Example usage
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        search_dir = sys.argv[1]
        pattern = sys.argv[2] if len(sys.argv) > 2 else "*.py"

        try:
            files = find_files(search_dir, pattern)
            print(f"Found {len(files)} files matching '{pattern}' in {search_dir}:")
            for f in files[:10]:  # Show first 10
                print(f"  {f}")
            if len(files) > 10:
                print(f"  ... and {len(files) - 10} more")
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Usage: python find_files.py <directory> [pattern]")
        print("Example: python find_files.py . '*.py'")