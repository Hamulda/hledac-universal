#!/usr/bin/env python3
"""
Dependency profile smoke checks for uv.

Verifies that dependency profiles in pyproject.toml are actually installable
and don't lose core imports. No network, no browser launch, no MLX model load.

Usage:
    uv run python tools/check_dependency_profiles.py [--profile PROFILE]

Profiles:
    default      - Core deps only (no torch, no browser)
    m1-local     - Apple Silicon MLX + fast parsing
    browser      - JS rendering (import smoke only)
    graph-storage - Columnar stack (duckdb/lancedb/pyarrow/polars)
    all          - Everything except torch

Exit codes:
    0  - all checks passed
    1  - one or more checks failed
"""

import argparse
import sys
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProfileCheck:
    name: str
    uv_sync_args: list[str]
    import_smoke: list[str]
    guard: Optional[str] = None  # Python expression; skip if False


# Profiles that need `uv sync` first
SYNC_PROFILES = {
    "default": ProfileCheck(
        name="default",
        uv_sync_args=[],
        import_smoke=["aiohttp", "duckdb", "lmdb", "msgspec", "xxhash", "ahocorasick"],
        guard="sys.platform != 'darwin' or platform_machine() != 'arm64'",
    ),
    "m1-local": ProfileCheck(
        name="m1-local",
        uv_sync_args=["--extra", "m1-local", "--extra", "dev"],
        import_smoke=["mlx", "selectolax", "duckdb", "pyarrow", "rapidfuzz"],
    ),
    "browser": ProfileCheck(
        name="browser",
        uv_sync_args=["--extra", "browser"],
        import_smoke=["camoufox", "nodriver"],
    ),
    "graph-storage": ProfileCheck(
        name="graph-storage",
        uv_sync_args=["--extra", "graph-storage"],
        import_smoke=["duckdb", "lancedb", "pyarrow", "polars"],
    ),
    "all": ProfileCheck(
        name="all",
        uv_sync_args=["--extra", "all"],
        import_smoke=["duckdb", "lancedb", "mlx", "selectolax", "rapidfuzz", "cryptography"],
    ),
}

# Profiles that verify a property in already-synced env (no sync needed)
NO_SYNC_PROFILES = {
    "no-torch-in-default": ProfileCheck(
        name="no-torch-in-default",
        uv_sync_args=[],  # no sync
        import_smoke=[],  # no import check
        guard="True",  # always run
    ),
}


def _check_torch_not_in_default() -> tuple[bool, str]:
    """Verify default env does not pull in torch."""
    code = "\n".join([
        "import sys",
        "import importlib.util",
        "spec = importlib.util.find_spec('torch')",
        "if spec is None or spec.origin is None:",
        "    print('OK: torch not in default env or non-functional')",
        "    sys.exit(0)",
        "else:",
        "    print('FAIL: torch found at', spec.origin)",
        "    sys.exit(1)",
    ])
    result = subprocess.run(
        [".venv/bin/python", "-c", code],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stdout.strip()


def _check_imports(profile: ProfileCheck) -> tuple[bool, str]:
    """Run import smoke checks for a profile after syncing."""
    imports = ", ".join(profile.import_smoke)
    code = f"import sys, platform; print(','.join([{imports}]))"
    result = subprocess.run(
        ["uv", "run", "-e", *profile.uv_sync_args, "--", "python", "-c", code],
        capture_output=True,
        text=True,
        cwd=".venv",
    )
    if result.returncode == 0:
        return True, f"OK: {result.stdout.strip()}"
    return False, f"FAIL: {result.stderr.strip()}"


def run_profile(profile_name: str, verbose: bool = False) -> tuple[bool, str]:
    """Run smoke checks for a single profile. Returns (passed, message)."""
    if profile_name == "no-torch-in-default":
        passed, msg = _check_torch_not_in_default()
        return passed, f"[no-torch-in-default] {msg}"

    if profile_name not in SYNC_PROFILES:
        return False, f"Unknown profile: {profile_name}"

    profile = SYNC_PROFILES[profile_name]

    # Guard check
    if profile.guard:
        guard_code = f"import sys, platform; result = 1 if ({profile.guard}) else 0; sys.exit(result)"
        guard_result = subprocess.run(
            ["uv", "run", "python", "-c", guard_code],
            capture_output=True,
            text=True,
        )
        if guard_result.returncode != 0:
            return True, f"[{profile_name}] SKIPPED (guard: {profile.guard})"

    # Sync
    sync_cmd = ["uv", "sync", "--no-install-project", *profile.uv_sync_args]
    if verbose:
        print(f"  $ {' '.join(sync_cmd)}")

    sync_result = subprocess.run(
        sync_cmd,
        capture_output=True,
        text=True,
    )
    if sync_result.returncode != 0:
        return False, f"[{profile_name}] uv sync failed: {sync_result.stderr.strip()}"

    passed, msg = _check_imports(profile)
    return passed, f"[{profile_name}] {msg}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Dependency profile smoke checks")
    parser.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        help="Profile to check (can be repeated). Default: all profiles.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    profiles = args.profiles or list(SYNC_PROFILES) + list(NO_SYNC_PROFILES)

    print("=== Dependency Profile Smoke Checks ===")
    print(f"Profiles: {', '.join(profiles)}")
    print()

    results = []
    for p in profiles:
        if args.verbose:
            print(f"Checking: {p}")
        passed, msg = run_profile(p, verbose=args.verbose)
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {msg}")
        results.append(passed)

    print()
    passed_count = sum(results)
    total = len(results)
    print(f"Results: {passed_count}/{total} passed")

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())