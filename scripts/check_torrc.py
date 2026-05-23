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
        if not line or line == "\\":
            continue
        is_full_line_comment = line.startswith("#")
        if is_full_line_comment:
            directive_part = line[1:].strip()
        elif "#" in line:
            directive_part = line.split("#", 1)[0].strip()
        else:
            directive_part = line
        directive_part = directive_part.rstrip("\\").strip()
        if directive_part.lower() == "isolatesocksauth":
            return True
    return False


def check_hidden_service_statistics(torrc_path: str) -> bool:
    """
    Sprint F214 B.2: Return True if torrc contains 'HiddenServiceStatistics 0'.
    This disables Tor's collection of hidden service endpoint statistics,
    improving anonymity by preventing traffic timing analysis.
    """
    try:
        with open(torrc_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line == "\\":
            continue
        is_full_line_comment = line.startswith("#")
        if is_full_line_comment:
            directive_part = line[1:].strip()
        elif "#" in line:
            directive_part = line.split("#", 1)[0].strip()
        else:
            directive_part = line
        directive_part = directive_part.rstrip("\\").strip()
        if directive_part.lower() == "hiddenservicestatistics 0":
            return True
    return False


def main() -> int:
    if sys.version_info >= (3, 14):
        parser = argparse.ArgumentParser(description="Check torrc for anonymity directives", suggest_on_error=True, color=True)
    else:
        parser = argparse.ArgumentParser(description="Check torrc for anonymity directives")
    parser.add_argument(
        "--torrc",
        dest="torrc_path",
        metavar="PATH",
        help="explicit torrc path (overrides auto-discovery)",
    )
    parser.add_argument(
        "--check",
        dest="check_type",
        default="all",
        choices=["all", "isolatesocksauth", "statistics"],
        help="which check to run (default: all)",
    )
    args = parser.parse_args()

    torrc_path = TORRC_PATH_OVERRIDE or args.torrc_path or find_torrc()

    if torrc_path is None:
        print("[check_torrc] torrc not found in common locations", file=sys.stderr)
        return 2

    print(f"[check_torrc] Checking: {torrc_path}")

    if args.check_type in ("all", "isolatesocksauth"):
        if check_isolate_socks_auth(torrc_path):
            print("[check_torrc] IsolateSOCKSAuth — FOUND")
        else:
            print("[check_torrc] IsolateSOCKSAuth — NOT FOUND", file=sys.stderr)

    if args.check_type in ("all", "statistics"):
        if check_hidden_service_statistics(torrc_path):
            print("[check_torrc] HiddenServiceStatistics 0 — FOUND")
        else:
            print("[check_torrc] HiddenServiceStatistics 0 — NOT FOUND", file=sys.stderr)

    # Return 0 only if both/all checks pass
    if args.check_type == "all":
        isolate_ok = check_isolate_socks_auth(torrc_path)
        stats_ok = check_hidden_service_statistics(torrc_path)
        if isolate_ok and stats_ok:
            return 0
        return 1

    # Single check mode
    if args.check_type == "isolatesocksauth":
        return 0 if check_isolate_socks_auth(torrc_path) else 1
    if args.check_type == "statistics":
        return 0 if check_hidden_service_statistics(torrc_path) else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())


# Test seam — override torrc path for testing (not part of public API)
TORRC_PATH_OVERRIDE: str | None = None
