#!/usr/bin/env python3
"""
J1: CI Gate — Reject New Zombies

Prevents new zombie Rust modules from entering the codebase without documentation.
A "zombie" is a Rust extension with no Python callers and no documented future plan.

Usage:
    python tools/ci/reject_new_zombies.py [--verbose]

Exit codes:
    0 = CI passed (all zombies are documented in ALLOWED_ZOMBIES)
    1 = CI failed (new unauthorized zombies detected)
    2 = Configuration/validation error
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
ALLOWED_ZOMBIES: frozenset[str] = frozenset({
    # Files we explicitly accept as dormant (documented plans below)
    "aioquic_http3",  # gated by feature http3, optional dep
    "evasive_tls",  # gated by feature evasive_tls, used in research only
    "sendfile",  # rarely hit, only Tor path
    "native_db",  # used in production torrents, not yet wired through facade
    "crypto_accelerate",  # pending security clearance (MANUAL_SECURITY_REVIEW)
    "lmdb_dht",  # pending Kademlia refactor
})

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
_logger = logging.getLogger(__name__)


def run_audit_json() -> dict:
    """Run rust_extensions/audit.py --json and return parsed output."""
    audit_script = PROJECT_ROOT / "rust_extensions" / "audit.py"
    if not audit_script.exists():
        _logger.error("audit.py not found at %s", audit_script)
        sys.exit(2)

    # audit.py --json requires a file path, use tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        json_path = tmp.name

    try:
        result = subprocess.run(
            [sys.executable, str(audit_script), "--json", json_path],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
    except Exception as e:
        _logger.error("Failed to run audit.py: %s", e)
        sys.exit(2)

    if result.returncode != 0:
        _logger.error("audit.py exited with code %d", result.returncode)
        _logger.error("stderr: %s", result.stderr)
        sys.exit(2)

    try:
        data = json.loads(Path(json_path).read_text())
    except json.JSONDecodeError as e:
        _logger.error("Failed to parse audit.py JSON output: %s", e)
        sys.exit(2)
    finally:
        Path(json_path).unlink(missing_ok=True)

    return data


def check_audit(allow_with_doc: bool = True, verbose: bool = False) -> int:
    """
    Run rust_extensions audit, fail CI if any new ZOMBIE without doc.

    Args:
        allow_with_doc: If True, zombies in ALLOWED_ZOMBIES are permitted.
        verbose: If True, print detailed output.

    Returns:
        0 if CI passes, 1 if CI fails.
    """
    audit_data = run_audit_json()

    details = audit_data.get("details", [])
    zombies = [m for m in details if m.get("status") == "ZOMBIE"]

    if verbose:
        _logger.info("Total zombies found: %d", len(zombies))
        _logger.info("Allowed zombies: %d", len(ALLOWED_ZOMBIES))

    unauthorized: list[str] = []
    for module in zombies:
        name = module.get("name", "")
        if name not in ALLOWED_ZOMBIES:
            unauthorized.append(name)
            _logger.warning("NEW ZOMBIE: %s — doc required (add to ALLOWED_ZOMBIES or wire to Python)", name)
        elif verbose:
            _logger.info("Allowed zombie: %s", name)

    if unauthorized:
        _logger.error("CI GATE FAILED: %d unauthorized zombie(s) found", len(unauthorized))
        _logger.error("To fix: add to ALLOWED_ZOMBIES in tools/ci/reject_new_zombies.py")
        _logger.error("Unauthorized: %s", ", ".join(sorted(unauthorized)))
        return 1

    _logger.info("CI GATE PASSED: All zombies are documented")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="J1: CI Gate — Reject New Zombies")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument(
        "--no-allow-doc",
        action="store_true",
        help="Reject all zombies (for testing, ignores ALLOWED_ZOMBIES)",
    )
    args = parser.parse_args()

    allow_with_doc = not args.no_allow_doc
    return check_audit(allow_with_doc=allow_with_doc, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
