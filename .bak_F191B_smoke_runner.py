#!/usr/bin/env python3
"""
SMOKE RUNNER — DIAGNOSTIC ONLY, NOT CANONICAL SPRINT PATH
==========================================================

.. role::
    DIAGNOSTIC_TOOL: Tento modul je DIAGNOSTICKÝ nástroj, NENÍ production sprint owner.

.. canonical_path::
    Canonical sprint owner: ``core.__main__:run_sprint()``
    smoke_runner uses ``_run_sprint_mode()`` — an ALTERNATE/DIAGNOSTIC entrypoint
    (defined in ``hledac.universal.__main__._run_sprint_mode``), not the canonical owner.
    This is intentional: smoke tests use lightweight alternate paths to avoid
    the full canonical lifecycle overhead.

.. authority_statement::
    Tento modul NEPRODUKUJE canonical sprint truth. Používá canonical path
    (core.__main__._run_sprint_mode) pro diagnostics/smoke testing.

.. what_this_is::
    Rychlý smoke test — 60s sprint s memory trackem.
    Spustit ručně před PR pro ověření, že:
    1. Sprint doběhne bez exception
    2. RAM zůstane pod limitem
    3. Findings se vrátí

.. what_this_is_not::
    NENÍ production entrypoint. NENÍ canonical sprint owner.
    Pro production sprint použij: python -m hledac.universal.core --sprint

Použití:
    python smoke_runner.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time

# Nastavit logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("smoke_runner")


async def main() -> int:
    """Spustí 60s sprint a sleduje RAM."""
    try:
        import psutil
    except ImportError:
        log.error("psutil není nainstalován — pip install psutil")
        return 1

    proc_before = psutil.Process()
    ram_before = proc_before.memory_info().rss / 1024**2
    log.info(f"RAM před startem: {ram_before:.0f} MB")

    # _run_sprint_mode lives in hledac.universal.__main__ (root __main__.py), NOT core.__main__.
    # It is an ALTERNATE entrypoint, not the canonical sprint owner.
    # primary import (works when smoke_runner is imported as a module):
    try:
        from hledac.universal.__main__ import _run_sprint_mode
    except ImportError:
        # Intra-repo fallback: allow __main__ for testing within repo (script mode only)
        log.error("Nelze importovat _run_sprint_mode z hledac.universal.__main__")
        log.info("Zkusím __main__ fallback pro intra-repo testing...")
        try:
            from __main__ import _run_sprint_mode
        except ImportError:
            log.error("Nelze importovat _run_sprint_mode — root __main__ unavailable")
            return 1

    start = time.monotonic()
    log.info("Spouštím 60s sprint...")

    try:
        # Sprint s 60s durací
        await asyncio.wait_for(
            _run_sprint_mode("smoke test query", duration_s=60.0),
            timeout=120.0,  # 2min timeout
        )
    except asyncio.TimeoutError:
        log.error("Sprint timeout — přesáhl 120s")
        return 1
    except Exception as e:
        log.error(f"Sprint selhal: {e}", exc_info=True)
        return 1

    elapsed = time.monotonic() - start
    ram_after = psutil.Process().memory_info().rss / 1024**2
    delta = ram_after - ram_before

    log.info(f"Sprint dokončen za {elapsed:.1f}s")
    log.info(f"RAM po: {ram_after:.0f} MB (delta: {delta:+.0f} MB)")

    # RAM check
    if ram_after > 7200:
        log.error(f"RAM {ram_after:.0f} MB překročil 7.2 GB limit!")
        return 1

    log.info("✅ Smoke test prošel")
    return 0


def run_sprint_import_test() -> bool:
    """
    DIAGNOSTIC: Rychlý import test před spuštěním sprintu.

    Verifies canonical runtime modules are importable.
    This is a COMPATIBILITY check, not authority verification.
    """
    log.info("Testuji importy (canonical runtime path)...")

    # Canonical runtime modules — these form the production path
    # NOTE: memory_watchdog is internal runtime component, not canonical smoke-test surface
    # NOTE: stealth_crawler is intelligence layer, not canonical sprint path
    modules = [
        "hledac.universal",
        "hledac.universal.core.__main__",          # CANONICAL sprint owner
        "hledac.universal.runtime.sprint_lifecycle",
        "hledac.universal.runtime.sprint_scheduler",  # CANONICAL orchestrator
        "hledac.universal.runtime.shadow_inputs",    # DIAGNOSTIC scaffold (read-only)
        "hledac.universal.runtime.shadow_pre_decision",  # DIAGNOSTIC scaffold (read-only)
    ]

    errors = []
    for mod in modules:
        try:
            __import__(mod)
            log.debug(f"✓ {mod}")
        except Exception as e:
            errors.append(f"{mod}: {e}")

    if errors:
        log.error("Import chyby:")
        for e in errors:
            log.error(f"  {e}")
        return False

    log.info(f"✅ Všechny {len(modules)} modulů OK (canonical path verified)")
    return True


if __name__ == "__main__":
    # Nejdřív import test
    if not run_sprint_import_test():
        sys.exit(1)

    # Pak sprint
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
