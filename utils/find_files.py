"""File search utility."""

import fnmatch
from pathlib import Path
from core import aclose


def find_files(
    directory: str | Path,
    pattern: str = "*",
    recursive: bool = True,
    case_sensitive: bool = True,
    max_depth: int | None = None
) -> list[Path]:
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

    def _append_if_valid(file_path: Path) -> bool:
        """Check depth and add to matches. Returns True if added."""
        if max_depth is not None:
            relative_path = file_path.relative_to(dir_path)
            if len(relative_path.parts) > max_depth:
                return False
        matches.append(file_path)
        return True

    # Choose the appropriate glob method
    if recursive:
        if case_sensitive:
            # Use pattern directly for case-sensitive search
            glob_pattern = pattern if pattern.startswith("**/") else f"**/{pattern}"
            for file_path in dir_path.rglob(glob_pattern):
                if file_path.is_file():
                    _append_if_valid(file_path)
        else:
            # For case insensitive, search all files and filter
            for file_path in dir_path.rglob("*"):
                if file_path.is_file() and fnmatch.fnmatch(file_path.name.lower(), pattern.lower()):
                    _append_if_valid(file_path)
    else:
        # Non-recursive search
        glob_pattern = "*" if case_sensitive else "*"
        for file_path in dir_path.glob(glob_pattern):
            if file_path.is_file() and (case_sensitive or fnmatch.fnmatch(file_path.name.lower(), pattern.lower())):
                _append_if_valid(file_path)

    return matches


def find_files_by_extension(
    directory: str | Path,
    extensions: str | list[str],
    recursive: bool = True
) -> list[Path]:
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
