#!/usr/bin/env python3
"""
CI Import Check — Validates that critical modules import cleanly.
This script should be run by CI to catch broken imports before merge.

Usage: python ci_import_check.py
Exit code 0 = all OK, non-zero = failures
"""
import sys
from pathlib import Path

# Critical modules that MUST import cleanly
CRITICAL_MODULES = [
    ("hledac.universal.runtime.sprint_scheduler", "SprintScheduler"),
    ("hledac.universal.coordinators.fetch_coordinator", "FetchCoordinator"),
    ("hledac.universal.knowledge.duckdb_store", "DuckDBShadowStore"),
    ("hledac.universal.brain.hermes3_engine", "Hermes3Engine"),
    ("hledac.universal.brain.model_manager", "ModelManager"),
    ("hledac.universal.utils.concurrency", "adjust_fetch_workers"),
    ("hledac.universal.layers", "build_temporal_priority_hints"),
    ("hledac.universal.transport", "TransportContext"),
    ("hledac.universal.utils", "ActionResult"),
    ("hledac.universal.export", "render_diagnostic_markdown_to_path"),
]

def main():
    # ci_import_check.py is in hledac/universal/, project_root = hledac's parent
    project_root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(project_root))

    failures = []

    for module_path, symbol_name in CRITICAL_MODULES:
        try:
            module = __import__(module_path, fromlist=[symbol_name])
            getattr(module, symbol_name)
            print(f"✓ {module_path}.{symbol_name}")
        except ImportError as e:
            print(f"✗ {module_path}.{symbol_name}: {e}")
            failures.append(f"{module_path}.{symbol_name}")

    if failures:
        print(f"\n❌ {len(failures)} critical imports FAILED")
        return 1
    else:
        print(f"\n✅ All {len(CRITICAL_MODULES)} critical imports OK")
        return 0

if __name__ == "__main__":
    sys.exit(main())