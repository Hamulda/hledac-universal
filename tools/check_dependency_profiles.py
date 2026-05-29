#!/usr/bin/env python3
"""
Dependency profile smoke checks for uv.

Verifies that dependency profiles in pyproject.toml are actually installable
and don't lose core imports. No network, no browser launch, no MLX model load.

Usage:
    uv run python tools/check_dependency_profiles.py [--profile PROFILE]
    uv run python tools/check_dependency_profiles.py --drift
    uv run python tools/check_dependency_profiles.py --strict

Profiles:
    default         - Core deps only (no torch, no browser)
    m1-local        - Apple Silicon MLX + fast parsing (default for M1)
    graph-storage   - Columnar stack (duckdb/lancedb/pyarrow/polars)
    osint-html      - Fast HTML parsing + OSINT HTTP (selectolax/curl_cffi)
    dev             - Testing + linting
    no-torch-default - Verify torch NOT importable in default env
    no-browser-default - Verify camoufox/nodriver NOT importable in default env

Drift Check:
    Compares uv pip list against physically installed site-packages.
    Reports untracked packages but does NOT fail by default.
    Use --strict to make drift a failure.

Exit codes:
    0  - all checks passed
    1  - one or more checks failed
"""

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Project root = parent of tools/
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_VENV_PYTHON = _PROJECT_ROOT / ".venv/bin/python"


# -----------------------------------------------------------------------------------------
# Profile definitions
# -----------------------------------------------------------------------------------------

@dataclass
class ProfileCheck:
    name: str
    uv_sync_args: list[str] = field(default_factory=list)
    import_smoke: list[str] = field(default_factory=list)
    # Python expression; skip if evaluates to True
    skip_guard: str | None = None
    # Post-sync verification (no sync needed)
    verify_fn: str | None = None


SYNC_PROFILES: dict[str, ProfileCheck] = {
    "default": ProfileCheck(
        name="default",
        uv_sync_args=[],
        import_smoke=["aiohttp", "duckdb", "lmdb", "msgspec", "xxhash", "ahocorasick"],
        skip_guard="sys.platform != 'darwin' or platform.machine() != 'arm64'",
    ),
    "m1-local": ProfileCheck(
        name="m1-local",
        uv_sync_args=["--extra", "m1-local", "--extra", "dev"],
        import_smoke=["mlx", "selectolax", "duckdb", "pyarrow", "rapidfuzz"],
    ),
    "graph-storage": ProfileCheck(
        name="graph-storage",
        uv_sync_args=["--extra", "graph-storage"],
        import_smoke=["duckdb", "lancedb", "pyarrow", "polars"],
    ),
    "osint-html": ProfileCheck(
        name="osint-html",
        uv_sync_args=["--extra", "osint-html"],
        import_smoke=["selectolax", "curl_cffi"],
    ),
    "transport": ProfileCheck(
        name="transport",
        uv_sync_args=["--extra", "transport"],
        import_smoke=["h2"],
    ),
    "dev": ProfileCheck(
        name="dev",
        uv_sync_args=["--extra", "dev"],
        import_smoke=["pytest", "ruff", "mypy"],
    ),
}

# No-sync verification profiles (check already-synced env)
NO_SYNC_PROFILES: dict[str, ProfileCheck] = {
    "no-torch-default": ProfileCheck(
        name="no-torch-default",
        uv_sync_args=[],
        import_smoke=[],
        verify_fn="_check_torch_not_in_default",
    ),
    "no-browser-default": ProfileCheck(
        name="no-browser-default",
        uv_sync_args=[],
        import_smoke=[],
        verify_fn="_check_browser_not_in_default",
    ),
}


# -----------------------------------------------------------------------------------------
# Verification functions
# -----------------------------------------------------------------------------------------

def _check_torch_not_in_default() -> tuple[bool, str]:
    """Verify default env does not pull in torch."""
    code = """
import sys, importlib.util
spec = importlib.util.find_spec('torch')
if spec is None or spec.origin is None:
    print('OK: torch not in default env or non-functional')
    sys.exit(0)
else:
    print('FAIL: torch found at', spec.origin)
    sys.exit(1)
"""
    result = subprocess.run(
        [str(_VENV_PYTHON), "-c", code],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stdout.strip()


def _check_browser_not_in_default() -> tuple[bool, str]:
    """Verify default env does not pull in browser automation packages."""
    code = """
import sys, importlib.util
issues = []
for pkg in ['camoufox', 'nodriver']:
    spec = importlib.util.find_spec(pkg)
    if spec is not None and spec.origin is not None:
        issues.append(f'{pkg} at {spec.origin}')
if issues:
    print('FAIL:', ', '.join(issues))
    sys.exit(1)
else:
    print('OK: no browser automation in default env')
    sys.exit(0)
"""
    result = subprocess.run(
        [str(_VENV_PYTHON), "-c", code],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0, result.stdout.strip()


# -----------------------------------------------------------------------------------------
# Drift detection
# -----------------------------------------------------------------------------------------

def _normalize_pkg(name: str) -> str:
    """Normalize package name for comparison: lowercase, hyphens→underscores."""
    return name.lower().replace("-", "_")


def get_uv_tracked_packages() -> set[str]:
    """
    Get packages tracked by uv via 'uv pip list --format=freeze'.
    Returns normalized package names from uv's lockfile perspective.
    """
    result = subprocess.run(
        ["uv", "pip", "list", "--format=freeze"],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )
    if result.returncode != 0:
        return set()
    packages: set[str] = set()
    for line in result.stdout.strip().split("\n"):
        if "==" in line:
            raw = line.split("==")[0]
            packages.add(_normalize_pkg(raw))
    return packages


def get_site_packages_dirs() -> set[str]:
    """
    Get physically installed package names via importlib.metadata.distributions().
    This is the canonical view of what Python can actually import — the same
    source used by mypy, pytest, and other tools. It includes transitive deps
    that uv installs as part of the lockfile resolution.
    """
    code = (
        "import importlib.metadata; "
        "for d in importlib.metadata.distributions(): "
        "    print(d.name)"
    )
    result = subprocess.run(
        [str(_VENV_PYTHON), "-c", code],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()
    packages: set[str] = set()
    for line in result.stdout.strip().split("\n"):
        if line.strip():
            packages.add(line.strip().lower().replace("-", "_"))
    return packages


def check_drift(strict: bool = False) -> tuple[bool, str, list[str]]:
    """
    Compare uv-tracked packages against physically installed site-packages.
    Returns (passed, message, untracked_list).
    """
    venv_path = _PROJECT_ROOT / ".venv"
    if not venv_path.exists():
        return False, "FAIL: .venv not found", []

    uv_pkgs = get_uv_tracked_packages()
    physical_pkgs = get_site_packages_dirs()

    # Physical packages that are NOT in uv's tracked list
    untracked = physical_pkgs - uv_pkgs

    # Filter out some known benign extras (dist-info only dirs)
    benign = {
        # Python stdlib / bootstrap
        "__pycache__", ".cache", "python_bootstrap",
        "python3.14", "python3.13", "python3.12",
        "distutils", "setuptools", "pip", "wheel",
        # Cython-generated stub packages (internal, always present)
        "_yaml", "_multiprocess", "_pytest", "_polars_runtime_32",
        "_sounddevice_data", "_duckdb_stubs",
    }
    untracked = {p for p in untracked if p not in benign and not p.startswith(".")}

    if untracked:
        msg = f"DRIFT: {len(untracked)} untracked package(s) in site-packages (not in uv pip list)"
        if strict:
            return False, f"FAIL: {msg}", sorted(untracked)
        return True, f"WARN: {msg} (use --strict to fail)", sorted(untracked)

    return True, "OK: no drift detected", []


# -----------------------------------------------------------------------------------------
# Profile execution
# -----------------------------------------------------------------------------------------

def run_sync_profile(profile: ProfileCheck, verbose: bool = False) -> tuple[bool, str]:
    """Run smoke checks for a profile that needs uv sync first."""

    # Guard check
    if profile.skip_guard:
        guard_code = (
            f"import sys, platform; "
            f"sys.exit(0 if ({profile.skip_guard}) else 1)"
        )
        guard_result = subprocess.run(
            [str(_VENV_PYTHON), "-c", guard_code],
            capture_output=True,
            text=True,
        )
        if guard_result.returncode != 0:
            return True, f"[{profile.name}] SKIPPED (guard: {profile.skip_guard})"

    # Sync
    sync_cmd = ["uv", "sync", "--no-install-project"] + profile.uv_sync_args
    if verbose:
        print(f"  $ {' '.join(sync_cmd)}")

    sync_result = subprocess.run(
        sync_cmd,
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )
    if sync_result.returncode != 0:
        return False, f"[{profile.name}] uv sync failed: {sync_result.stderr.strip()}"

    # Import smoke check
    return _run_import_check(profile)


def _run_import_check(profile: ProfileCheck) -> tuple[bool, str]:
    """Run import smoke checks using the synced environment."""
    if not profile.import_smoke:
        return True, f"[{profile.name}] OK (no import check)"

    imports_code = ",".join(profile.import_smoke)
    code = f"import sys; import {imports_code}; print('OK'); sys.exit(0)"
    result = subprocess.run(
        [str(_VENV_PYTHON), "-c", code],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, f"[{profile.name}] OK: all {len(profile.import_smoke)} imports smoke passed"
    return False, f"[{profile.name}] FAIL: {result.stderr.strip()}"


def run_no_sync_profile(profile: ProfileCheck) -> tuple[bool, str]:
    """Run verification profiles that don't need uv sync."""
    verify_fn = profile.verify_fn

    if verify_fn == "_check_torch_not_in_default":
        return _check_torch_not_in_default()
    elif verify_fn == "_check_browser_not_in_default":
        return _check_browser_not_in_default()
    else:
        return True, f"[{profile.name}] OK (no verification)"


def run_profile(profile_name: str, verbose: bool = False) -> tuple[bool, str]:
    """Run smoke checks for a single profile. Returns (passed, message)."""

    if profile_name in SYNC_PROFILES:
        return run_sync_profile(SYNC_PROFILES[profile_name], verbose=verbose)
    elif profile_name in NO_SYNC_PROFILES:
        return run_no_sync_profile(NO_SYNC_PROFILES[profile_name])
    else:
        return False, f"Unknown profile: {profile_name}"


# -----------------------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dependency profile smoke checks for uv-managed hledac/universal"
    )
    parser.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        help="Profile to check (can be repeated). Default: all profiles.",
    )
    parser.add_argument(
        "--drift",
        action="store_true",
        help="Run drift check only (compare uv pip list vs site-packages).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Make drift check a failure (exit 1 if untracked packages found).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    print("=== Dependency Profile Smoke Checks ===")
    print()

    all_passed = True
    results: list[tuple[str, bool, str]] = []

    # Drift check
    if args.drift or args.strict:
        passed, msg, untracked = check_drift(strict=args.strict)
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] drift: {msg}")
        if untracked:
            for pkg in untracked[:20]:
                print(f"       - {pkg}")
            if len(untracked) > 20:
                print(f"       ... and {len(untracked) - 20} more")
        results.append(("drift", passed, msg))
        all_passed = all_passed and passed
        if args.drift or args.strict:
            print()
            # Only drift check requested
            print(f"Results: {'1/1' if passed else '0/1'} passed")
            return 0 if passed else 1

    # Profile checks
    profiles = args.profiles or list(SYNC_PROFILES) + list(NO_SYNC_PROFILES)
    print(f"Profiles: {', '.join(profiles)}")
    print()

    for p in profiles:
        if args.verbose:
            print(f"Checking: {p}")
        passed, msg = run_profile(p, verbose=args.verbose)
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {msg}")
        results.append((p, passed, msg))
        all_passed = all_passed and passed

    print()
    passed_count = sum(1 for _, passed, _ in results if passed)
    total = len(results)
    print(f"Results: {passed_count}/{total} passed")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
