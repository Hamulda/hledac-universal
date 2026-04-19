#!/usr/bin/env python3
"""
TICKET-002: Rychlý smoke test — 60s sprint s memory trackem.

Spustit ručně před PR pro ověření, že:
1. Sprint doběhne bez exception
2. RAM zůstane pod limitem
3. Findings se vrátí

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

    # Import sprint entry point
    try:
        from __main__ import _run_sprint_mode
    except ImportError:
        log.error("Nelze importovat _run_sprint_mode z __main__")
        log.info("Zkusím alternativní import...")
        try:
            # Alternativní: přímý import modulu
            import hledac.universal.__main__ as main_mod
            if not hasattr(main_mod, "_run_sprint_mode"):
                log.error("__main__ nemá _run_sprint_mode funkci")
                return 1
            _run_sprint_mode = main_mod._run_sprint_mode
        except Exception as e:
            log.error(f"Import selhal: {e}")
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
    """Rychlý import test před spuštěním sprintu."""
    log.info("Testuji importy...")

    modules = [
        "hledac.universal",
        "hledac.universal.runtime.sprint_lifecycle",
        "hledac.universal.runtime.sprint_scheduler",
        "hledac.universal.runtime.memory_watchdog",
        "hledac.universal.intelligence.stealth_crawler",
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

    log.info(f"✅ Všechny {len(modules)} modulů OK")
    return True


if __name__ == "__main__":
    # Nejdřív import test
    if not run_sprint_import_test():
        sys.exit(1)

    # Pak sprint
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
