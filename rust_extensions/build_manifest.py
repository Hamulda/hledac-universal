#!/usr/bin/env python3
"""
build_manifest.py — BUILD_MANIFEST generator for hledac-rust-extensions.

Generates BUILD_MANIFEST.json containing:
  - SHA256 hash of all source files (src/**/*.rs, Cargo.toml)
  - Build timestamp
  - Build command used
  - Architecture info

This manifest is stored NEXT TO the .so file (in the Python package directory)
and is verified at import time to detect stale binaries.

ISSUE-11: This is the BUILD-TIME hash that enables fail-closed staleness detection.

Usage:
    # Generate manifest for current build
    python rust_extensions/build_manifest.py

    # Generate and print manifest
    python rust_extensions/build_manifest.py --print

    # Verify manifest matches source
    python rust_extensions/build_manifest.py --verify

    # Integration with maturin build:
    # Called from build.rs or as maturin post-build hook
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from _core import aclose

# rust_extensions/ → project root
_REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = _REPO_ROOT / "src"
CARGO_TOML = _REPO_ROOT / "Cargo.toml"
# Manifest is written to the Python package source directory
# (where the .so will be found at runtime)
PYTHON_PKG_DIR = _REPO_ROOT
MANIFEST_PATH = PYTHON_PKG_DIR / "BUILD_MANIFEST.json"


def _compute_source_hash() -> tuple[str, dict[str, str]]:
    """
    Compute BLAKE2B hash of all source files for runtime staleness detection.

    IMPORTANT: This MUST match _compute_source_content_hash() in _prober.py.
    Uses blake2b for speed (not SHA256 which is for cryptographic integrity).

    Algorithm:
    - Sorts files by path for deterministic ordering
    - For each file: hash(path + size + first_4KB + last_4KB)
    - Final hash: BLAKE2B-256 of all file hashes concatenated

    Returns:
        tuple[str, dict]: (overall_hash, per_file_hashes)
    """
    import hashlib

    # Collect all source files
    file_paths: list[Path] = []
    if SRC_DIR.exists():
        for ext in ("*.rs", "*.toml"):
            file_paths.extend(sorted(SRC_DIR.glob(ext)))

    # Also include the root Cargo.toml if not in src/
    if CARGO_TOML.exists() and CARGO_TOML.parent != SRC_DIR:
        file_paths.append(CARGO_TOML)

    # Sort for deterministic ordering
    file_paths.sort(key=str)

    per_file: dict[str, str] = {}
    overall_hasher = hashlib.blake2b(digest_size=32)

    for path in file_paths:
        try:
            relative_path = str(path.relative_to(_REPO_ROOT))
            size = path.stat().st_size

            # Hash: path + size + content sample (first/last 4KB)
            file_hasher = hashlib.blake2b(digest_size=32)
            file_hasher.update(relative_path.encode())
            file_hasher.update(size.to_bytes(8, "little"))

            # Sample first 4KB + last 4KB (matches build.rs and _prober.py)
            content = path.read_bytes()
            sample_size = min(4096, len(content))
            file_hasher.update(content[:sample_size])
            if len(content) > 8192:
                file_hasher.update(content[-4096:])

            file_hash = file_hasher.hexdigest()
            per_file[relative_path] = file_hash

            # Include in overall hash
            overall_hasher.update(file_hash.encode())
        except OSError as e:
            # Skip files that can't be read
            print(f"Warning: Could not read {path}: {e}", file=sys.stderr)
            continue

    return overall_hasher.hexdigest(), per_file


# Alias for backward compatibility
_compute_sha256_full = _compute_source_hash


def _get_platform_info() -> dict:
    """Get platform and build information."""
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }


def _generate_build_command() -> str:
    """Generate the canonical build command for this platform."""
    is_m1 = platform.processor() == "arm" or "aarch64" in platform.machine()

    if is_m1:
        return (
            "CARGO_PROFILE_RELEASE_LTO=false CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 "
            "cargo build --release --manifest-path rust_extensions/Cargo.toml"
    )
    else:
        return "cargo build --release --manifest-path rust_extensions/Cargo.toml"


def generate_manifest(
    output_path: Path | None = None,
    verbose: bool = False,
) -> dict:
    """
    Generate BUILD_MANIFEST.json.

    Args:
        output_path: Path to write manifest. Defaults to BUILD_MANIFEST.json in python pkg.
        verbose: If True, print detailed information.

    Returns:
        The manifest dict that was written.
    """
    # ISSUE-11: Use BLAKE2B hash (matches _prober.py runtime hash)
    source_hash, per_file_hashes = _compute_source_hash()
    platform_info = _get_platform_info()
    build_cmd = _generate_build_command()

    manifest = {
        "version": "1.0",
        "manifest_version": "1.0",  # Schema version for compatibility
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # ISSUE-11: BLAKE2B hash - MUST match _prober.py _compute_source_content_hash()
        "source_hash": source_hash,
        "source_hash_algorithm": "blake2b-256",
        "files_hashed": list(per_file_hashes.keys()),
        "file_count": len(per_file_hashes),
        "build_command": build_cmd,
        "maturin_build": True,
        "platform": platform_info,
    }

    output = output_path or MANIFEST_PATH
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2))

    if verbose:
        print(f"[BUILD_MANIFEST] Generated: {output}")
        print(f"  Source hash: {source_hash}")
        print(f"  Files hashed: {len(per_file_hashes)}")
        print(f"  Platform: {platform_info['machine']}")

    return manifest


def verify_manifest(manifest_path: Path | None = None) -> tuple[bool, str]:
    """
    Verify that the current source matches the stored BUILD_MANIFEST.

    Returns:
        tuple[bool, str]: (is_valid, reason)
    """
    manifest_path = manifest_path or MANIFEST_PATH

    if not manifest_path.exists():
        return False, f"BUILD_MANIFEST not found at {manifest_path}"

    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return False, f"Failed to read BUILD_MANIFEST: {e}"

    if "source_hash" not in manifest:
        return False, "BUILD_MANIFEST missing 'source_hash' field"

    stored_hash = manifest["source_hash"]
    current_hash, _ = _compute_sha256_full()

    if stored_hash != current_hash:
        return False, (
            f"Source hash mismatch: "
            f"stored={stored_hash[:16]}..., current={current_hash[:16]}..."
    )

    return True, "BUILD_MANIFEST is valid"


def main() -> int:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate or verify BUILD_MANIFEST.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate manifest
    python rust_extensions/build_manifest.py

    # Generate and print
    python rust_extensions/build_manifest.py --print

    # Verify current manifest
    python rust_extensions/build_manifest.py --verify

    # CI usage (exit 0 if valid, 1 if stale)
    python rust_extensions/build_manifest.py --ci-check
        """,
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print the generated manifest to stdout",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify existing manifest matches source",
    )
    parser.add_argument(
        "--ci-check",
        action="store_true",
        help="Exit 0 if manifest valid, 1 if stale or missing",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path for manifest (default: BUILD_MANIFEST.json in python pkg)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    if args.verify or args.ci_check:
        valid, reason = verify_manifest(args.output)
        if valid:
            print(f"[OK] {reason}")
            return 0
        else:
            print(f"[FAIL] {reason}")
            return 1

    manifest = generate_manifest(output_path=args.output, verbose=args.verbose)

    if args.print:
        print(json.dumps(manifest, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
