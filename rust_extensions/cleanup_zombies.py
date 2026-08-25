"""
ISSUE-007: Safe Rust Zombie Module Cleanup
==========================================

SAFETY-FIRST rewrite of the original cleanup_zombies.py.

The original script hard-coded a `ZOMBIE_MODULES_SAFE` / `ZOMBIE_MODULES_WITH_DEPS`
list that included modules which are ACTUALLY LIVE via the wiring layer
(circuit_breaker, content_hasher, aho_corasick_simd, simd_similarity, simhash_ext,
telemetry_agg, url_engine, tls_metadata, graph_analytics, claims_extraction, …).
Running it would have DELETED working integrations and broken the build.

This version delegates ALL classification to audit.py's dynamic analyzer. A module is
only ever considered removable when audit.py reports it as ZOMBIE — i.e. it has NO
Python caller (no wiring file, no integrations reference, no rust_backend symbol use,
no quoted getattr/FFI name) AND no internal Rust dependency (crate::X / macro X!).

Given the current state of the codebase, that set is EMPTY: every module in lib.rs is
reachable. So this tool is effectively a no-op — which is the correct, safe outcome.

Usage:
    python rust_extensions/cleanup_zombies.py --plan      # show what WOULD be removed
    python rust_extensions/cleanup_zombies.py --execute   # actually remove (asks confirm)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Use the dynamic analyzer as the single source of truth.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import (  # noqa: E402
    LIB_RS,
    SRC,
    WIRING,
    WIRING_NAME_MAP,
    run_audit,
)

ZOMBIE_ACTION_FORBIDDEN = (
    "Refusing to remove a module that is still reachable from Python or Rust. "
    "Re-run audit.py to see why it is classified ACTIVE."
)


def compute_plan():
    """Return list of removable (ZOMBIE) module names with no internal Rust deps."""
    results = run_audit()
    plan = []
    for r in results:
        if r.status != "ZOMBIE":
            continue
        # Defense in depth: refuse anything that still has any evidence.
        if r.evidence:
            print(f"  ! SKIP {r.name}: has evidence {r.evidence} (would not be ZOMBIE)")
            continue
        f = SRC / f"{r.name}.rs"
        if not f.exists():
            f = SRC / r.name / "mod.rs"
        plan.append((r.name, f if f.exists() else None))
    return plan


def print_plan(plan):
    print("\n" + "=" * 80)
    print("ISSUE-007: Rust Zombie Module Cleanup Plan (SAFE / dynamic)")
    print("=" * 80)
    if not plan:
        print("\n  ✅ Nothing to remove. Every module in lib.rs is reachable from")
        print("     Python (wiring / integrations / facade) or from another Rust module.")
        print("     The previously-reported '50+ ZOMBIE modules' were a false positive")
        print("     caused by a broken PYTHON_SOURCE_DIR in the old audit.py.")
    else:
        print(f"\n  Modules to remove ({len(plan)}):")
        for name, f in plan:
            print(f"   - {name}  ({f})")
    print("\n" + "=" * 80)


def _remove_mod_decl(name: str) -> bool:
    text = LIB_RS.read_text()
    lines = text.splitlines()
    out = []
    skipped = 0
    for i, line in enumerate(lines):
        if re.match(r"\s*mod\s+" + re.escape(name) + r"\s*;", line):
            # drop the mod line and any immediately-preceding comment / cfg lines
            while out and (out[-1].strip().startswith("//") or out[-1].strip().startswith("#[")):
                out.pop()
            skipped += 1
            continue
        out.append(line)
    if skipped:
        LIB_RS.write_text("\n".join(out) + "\n")
    return skipped > 0


def _remove_registration(name: str) -> bool:
    text = LIB_RS.read_text()
    pat = re.compile(r"^\s*" + re.escape(name) + r"::\w+\([^;]*\);\s*$")
    lines = text.splitlines()
    out = []
    removed = 0
    for i, line in enumerate(lines):
        if pat.match(line):
            # drop an immediately-preceding cfg line if present
            if out and out[-1].strip().startswith("#[cfg"):
                out.pop()
            removed += 1
            continue
        out.append(line)
    if removed:
        LIB_RS.write_text("\n".join(out) + "\n")
    return removed > 0


def execute(plan):
    for name, f in plan:
        if f is not None and f.exists():
            if f.is_dir():
                import shutil
                shutil.rmtree(f)
            else:
                f.unlink()
            print(f"  ✅ removed {f}")
        else:
            print(f"  ⚠️  {name}: source file not found (already removed?)")
        _remove_mod_decl(name)
        _remove_registration(name)
    print("\n  Reminder: run `cargo check --no-default-features` to verify.")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Safe Rust zombie cleanup (dynamic)")
    ap.add_argument("--plan", action="store_true", help="Show cleanup plan")
    ap.add_argument("--execute", action="store_true", help="Execute cleanup (asks confirm)")
    args = ap.parse_args()
    if not args.plan and not args.execute:
        ap.print_help()
        return 1
    plan = compute_plan()
    print_plan(plan)
    if args.execute:
        if not plan:
            print("Nothing to remove — aborting.")
            return 0
        resp = input("❓ Proceed with removal? (yes/no): ").strip().lower()
        if resp == "yes":
            execute(plan)
        else:
            print("Aborted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
