#!/usr/bin/env python3
"""
build_ffi_manifest.py — FFI symbol manifest generator for hledac-rust-extensions.

Generates core/rust_backend/_ffi_manifest.json from lib.rs static analysis.
This manifest is the authoritative list of PyO3 symbols that Rust exports.

Output: _ffi_manifest.json with schema:

    {
      "version": "1.0.0",
      "generated_at": "<ISO8601>",
      "git_rev": "<git-rev>",
      "lib_rs_symbols": {<module>: [<symbol>, ...]},
      "all_lib_rs": [<symbol>, ...]
    }

CI usage:
    python rust_extensions/build_ffi_manifest.py [--check]
        --check: exit 1 if manifest is stale (lib.rs changed but manifest not regenerated)

Python-side usage in core/rust_backend.__init__:
    manifest = json.loads((ABI_DIR / "_ffi_manifest.json").read_text())
    rust_symbols = set(manifest["all_lib_rs"])
    # compare with self._ext.* calls...
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# rust_extensions/  →  project root
_REPO_ROOT = Path(__file__).resolve().parent  # .../hledac/universal/rust_extensions
REPO_ROOT = _REPO_ROOT.parent  # .../hledac/universal
ABI_OUT = REPO_ROOT / "core" / "rust_backend"
MANIFEST_PATH = ABI_OUT / "_ffi_manifest.json"
LIB_RS_PATH = _REPO_ROOT / "src" / "lib.rs"


# ---------------------------------------------------------------------------
# Static analysis — parse lib.rs
# ---------------------------------------------------------------------------

def _extract_class_name(line: str) -> tuple[str, str] | None:
    """
    Parse a 'm.add_class::<...::ClassName>()?;' line.

    Returns (module_name, class_name) or None if not an add_class line.
    Example: 'm.add_class::<bloom::BloomFilter>()?;' → ('bloom', 'BloomFilter')
    """
    gt = line.rfind("<")
    gt2 = line.find(">", gt)
    if gt < 0 or gt2 < 0:
        return None
    path = line[gt + 1:gt2]  # e.g. "bloom::BloomFilter"
    segments = path.split("::")
    if len(segments) < 2:
        return None
    module = segments[0]
    class_name = segments[-1]
    return (module, class_name)


def parse_lib_rs_symbols(lib_rs_text: str) -> dict[str, list[str]]:
    """
    Parse lib.rs and extract all PyO3-exported symbols per module.

    Handles four export patterns:
      1. m.add_class::<module::ClassName>()?  → (module, ClassName)
      2. m.add_function(wrap_pyfunction!(module::func, m))? → (module, func)
      3. module_name::register...(m)?           → (module_name, "module_name.register...")
         (catches register, register_functions, register_class, register_module)

    Returns: {module_name: [symbol, ...]}
    """
    result: dict[str, list[str]] = {}

    for line in lib_rs_text.splitlines():
        stripped = line.strip()
        # Pattern 1: m.add_class
        if "m.add_class" in line:
            extracted = _extract_class_name(line)
            if extracted:
                module, class_name = extracted
                result.setdefault(module, []).append(class_name)
            continue

        # Pattern 2: m.add_function(wrap_pyfunction!(module::func, m))
        # e.g. m.add_function(wrap_pyfunction!(content_hasher::batch_content_hash, m))?;
        if "wrap_pyfunction!" in line:
            m = re.search(r"wrap_pyfunction!\((\w+)::(\w+),", line)
            if m:
                mod_name, func_name = m.group(1), m.group(2)
                result.setdefault(mod_name, []).append(func_name)
            continue

        # Pattern 3: module_name::register...(m)?
        colon = line.find("::")
        if colon < 0:
            continue
        module = line[:colon].rsplit(maxsplit=1)[-1]
        if not module or not module[0].isalpha():
            continue

        if "register_functions(m)" in line:
            result.setdefault(module, []).append(f"{module}.register_functions")
        elif "register_module(m)" in line:
            result.setdefault(module, []).append(f"{module}.register_module")
        elif "register_class(m)" in line:
            result.setdefault(module, []).append(f"{module}.register_class")
        elif "register(m)" in line:
            result.setdefault(module, []).append(f"{module}.register")

    return result


# ---------------------------------------------------------------------------
# Git revision
# ---------------------------------------------------------------------------

def get_git_rev() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_manifest(check: bool = False) -> dict:
    lib_rs_text = LIB_RS_PATH.read_text()

    lib_rs_symbols = parse_lib_rs_symbols(lib_rs_text)

    # Flat set of all symbols derived from lib.rs
    all_lib_rs: list[str] = []
    for symbols in lib_rs_symbols.values():
        all_lib_rs.extend(symbols)
    all_lib_rs = sorted(set(all_lib_rs))

    manifest = {
        "version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_rev": get_git_rev(),
        "lib_rs_symbols": lib_rs_symbols,
        "all_lib_rs": all_lib_rs,
    }

    if check and MANIFEST_PATH.exists():
        old = json.loads(MANIFEST_PATH.read_text())
        old_rev = old.get("git_rev", "unknown")
        new_rev = manifest["git_rev"]
        if old_rev != new_rev:
            print(
                f"[build_ffi_manifest] MANIFEST STALE: git rev {old_rev} -> {new_rev}",
                file=sys.stderr,
            )
            print(
                f"  Run: python rust_extensions/build_ffi_manifest.py to regenerate",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            f"[build_ffi_manifest] MANIFEST OK: rev={new_rev}, "
            f"modules={len(lib_rs_symbols)}, symbols={len(all_lib_rs)}"
        )
    else:
        ABI_OUT.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
        print(f"[build_ffi_manifest] Written: {MANIFEST_PATH}")
        print(
            f"  modules={len(lib_rs_symbols)}, "
            f"symbols={len(all_lib_rs)}"
        )

    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FFI manifest generator")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if manifest git rev changed but manifest not regenerated",
    )
    args = parser.parse_args()
    generate_manifest(check=args.check)
