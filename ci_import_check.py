#!/usr/bin/env python3
"""
CI: fail if critical modules have ImportError.
Install hook: after uv pip install -e . in CI environment.
Tests actual production import patterns, NOT broken_imports.json (which tracks
stale/dead top-level imports from modules outside universal/).
"""
from __future__ import annotations

import sys

CRITICAL_MODULES = [
    "hledac.universal.runtime.sprint_scheduler",
    "hledac.universal.knowledge.duckdb_store",
    "hledac.universal.coordinators.fetch_coordinator",
    "hledac.universal.brain.hermes3_engine",
    "hledac.universal.knowledge.graph_service",
    "hledac.universal.brain.model_manager",
    "hledac.universal.utils.concurrency",
    "hledac.universal.transport.base",
    "hledac.universal.security.temporal_anonymizer",
    # StealthEngine uses lazy import: from _shims.security_stealth_engine import StealthEngine
    # Verify _shims is importable and the class exists
    "hledac.universal._shims.security_stealth_engine",
]

def main() -> int:
    failed = []
    for mod in CRITICAL_MODULES:
        try:
            __import__(mod)
            name = mod.split('.', 2)[2]
            print(f"  {name}: OK")
        except ImportError as e:
            print(f"  {mod}: FAIL -- {e}")
            failed.append(mod)

    if failed:
        print(f"\nCRITICAL import failures: {len(failed)}")
        for f in failed:
            print(f"  - {f}")
        return 1

    print(f"\nAll {len(CRITICAL_MODULES)} critical imports OK")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
