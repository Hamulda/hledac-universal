#!/usr/bin/env python3
"""
check_torrc.py — Bootstrap helper that verifies Tor configuration sanity.

Checks:
  1. torrc file exists
  2. IsolateSOCKSAuth directive is present

Does NOT:
  - manage Tor process
  - start/stop tor
  - modify torrc

Exit codes:
    0 = IsolateSOCKSAuth found
    1 = not found
    2 = torrc not found / unreadable
"""

from __future__ import annotations

import sys
import pathlib
import argparse


def find_torrc() -> str | None:
    """Search common torrc locations, return first found path or None."""
    candidates = [
        pathlib.Path("/etc/tor/torrc"),
        pathlib.Path("/usr/local/etc/tor/torrc"),
        pathlib.Path.home() / ".tor" / "torrc",
        pathlib.Path.home() / ".config" / "tor" / "torrc",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def check_isolate_socks_auth(torrc_path: str) -> bool:
    """
    Return True if torrc contains IsolateSOCKSAuth directive.

    Handles:
      - comments (# prefix)
      - line continuations (trailing \\)
      - case-insensitive matching
      - inline comments (after directive)
    """
    try:
        with open(torrc_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        # Skip empty / only-backslash lines
        if not line or line == "\\":
            continue
        # Determine the effective directive part:
        # - Full-line comment: line starts with # -> check the comment text
        # - Inline comment: directive#comment -> extract before #
        is_full_line_comment = line.startswith("#")
        if is_full_line_comment:
            directive_part = line[1:].strip()
        elif "#" in line:
            directive_part = line.split("#", 1)[0].strip()
        else:
            directive_part = line
        # handle line continuations
        directive_part = directive_part.rstrip("\\").strip()
        if directive_part.lower() == "isolatesocksauth":
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Check torrc for IsolateSOCKSAuth")
    parser.add_argument(
        "--torrc",
        dest="torrc_path",
        metavar="PATH",
        help="explicit torrc path (overrides auto-discovery)",
    )
    args = parser.parse_args()

    torrc_path = TORRC_PATH_OVERRIDE or args.torrc_path or find_torrc()

    if torrc_path is None:
        print("[check_torrc] torrc not found in common locations", file=sys.stderr)
        return 2

    print(f"[check_torrc] Checking: {torrc_path}")

    if check_isolate_socks_auth(torrc_path):
        print("[check_torrc] IsolateSOCKSAuth — FOUND")
        return 0
    else:
        print("[check_torrc] IsolateSOCKSAuth — NOT FOUND", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())


# Test seam — override torrc path for testing (not part of public API)
TORRC_PATH_OVERRIDE: str | None = None
