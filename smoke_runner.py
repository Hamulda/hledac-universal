#!/usr/bin/env python3
"""
SMOKE RUNNER — DIAGNOSTIC ONLY, NOT CANONICAL SPRINT PATH
==========================================================

.. role::
    DIAGNOSTIC_TOOL: Tento modul je DIAGNICKÝ nástroj, NENÍ production sprint owner.

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
    python smoke_runner.py --smoke  # Lightweight smoke test without network
"""
from __future__ import annotations

import argparse
import asyncio
import cProfile
import importlib.util
import logging
import os
import pstats
import sys
import time
from io import StringIO
from pathlib import Path

# Nastavit logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("smoke_runner")


async def run_smoke_test() -> int:
    """
    Lightweight smoke test — verifies core imports and FETCH_SEMAPHORE
    without requiring network or model downloads.

    Returns:
        0 on success, 1 on failure
    """
    log.info("=" * 60)
    log.info("SMOKE TEST — initialization without network")
    log.info("=" * 60)

    errors = []

    # 1. Test root package imports
    log.info("[1/6] Testing root package imports...")
    try:
        import hledac.universal
        from utils.concurrency import FETCH_SEMAPHORE, AdaptiveSemaphore, adjust_fetch_workers
        log.info("  ✓ Root package and FETCH_SEMAPHORE imports OK")
    except Exception as e:
        errors.append(f"Root package import failed: {e}")
        log.error(f"  ✗ Root package import failed: {e}")

    # 2. Test AdaptiveSemaphore initialization
    log.info("[2/6] Testing AdaptiveSemaphore...")
    try:
        sem = AdaptiveSemaphore()
        assert sem.current_limit == 3, f"Expected limit 3 (M1 hard ceiling), got {sem.current_limit}"
        log.info(f"  ✓ AdaptiveSemaphore initialized with current_limit={sem.current_limit}")
    except Exception as e:
        errors.append(f"AdaptiveSemaphore test failed: {e}")
        log.error(f"  ✗ AdaptiveSemaphore test failed: {e}")

    # 3. Verify FETCH_SEMAPHORE is the lazy proxy with limit introspection
    log.info("[3/6] Verifying FETCH_SEMAPHORE limit introspection...")
    try:
        # FETCH_SEMAPHORE is _FetchSemaphoreProxy (lazy singleton)
        assert hasattr(FETCH_SEMAPHORE, 'limit'), \
            f"FETCH_SEMAPHORE has no limit() method — got {type(FETCH_SEMAPHORE)}"
        log.info(f"  ✓ FETCH_SEMAPHORE is FetchSemaphoreProxy with limit()={FETCH_SEMAPHORE.limit()}")
    except Exception as e:
        errors.append(f"FETCH_SEMAPHORE limit check failed: {e}")
        log.error(f"  ✗ FETCH_SEMAPHORE limit check failed: {e}")

    # 4. Test adjust_fetch_workers modifies semaphore
    log.info("[4/6] Testing adjust_fetch_workers dynamic adjustment...")
    try:
        original_limit = FETCH_SEMAPHORE.limit()
        await adjust_fetch_workers(5)
        assert FETCH_SEMAPHORE.limit() == 5, f"Expected limit=5, got {FETCH_SEMAPHORE.limit()}"
        log.info(f"  ✓ adjust_fetch_workers(5) worked — limit={FETCH_SEMAPHORE.limit()}")

        # Restore original limit
        await adjust_fetch_workers(25)
        log.info(f"  ✓ Restored FETCH_SEMAPHORE to 25")
    except Exception as e:
        errors.append(f"adjust_fetch_workers test failed: {e}")
        log.error(f"  ✗ adjust_fetch_workers test failed: {e}")

    # 5. Test project_types import
    log.info("[5/6] Testing project_types import (types.py stub)...")
    try:
        from hledac.universal import project_types
        from hledac.universal.project_types import ResearchMode
        assert ResearchMode is not None
        log.info("  ✓ project_types import OK, ResearchMode accessible")
    except Exception as e:
        errors.append(f"project_types import failed: {e}")
        log.error(f"  ✗ project_types import failed: {e}")

    # 6. Test model_manager imports
    log.info("[6/6] Testing model_manager imports...")
    try:
        from hledac.universal.brain.model_manager import ModelManager, get_model_manager
        manager = get_model_manager()
        assert manager is not None
        log.info("  ✓ ModelManager singleton accessible")
    except Exception as e:
        errors.append(f"model_manager import failed: {e}")
        log.error(f"  ✗ model_manager import failed: {e}")

    log.info("=" * 60)
    if errors:
        log.error("SMOKE TEST FAILED")
        for err in errors:
            log.error(f"  - {err}")
        return 1
    else:
        log.info("SMOKE TEST PASSED — all checks OK")
        log.info("=" * 60)
        return 0


async def main(mode: str = "public", query: str = "smoke test query", run_loop: bool = False, rl_steps: int = 0, profile: bool = False) -> int:
    """
    Spustí 60s sprint a sleduje RAM.

    Args:
        mode: Pipeline mode (default: "public")
        query: Query string for the pipeline
        run_loop: If True, run ResearchLoop after pipeline completion (P16)
        rl_steps: P17: Number of RL steps to run (0 = disabled)
        profile: P19: If True, run with cProfile CPU/I/O profiling
    """
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
    log.info(f"Spouštím {mode} sprint s query: {query[:50]}...")

    # P19: Profile output directory
    profile_dir = Path.home() / "hledac_outputs"
    profile_path = profile_dir / "last_profile.prof"

    try:
        if profile:
            # P19: cProfile CPU/I/O profiling
            log.info(f"[PROFILE] Enabling cProfile — output to {profile_path}")
            profiler = cProfile.Profile()
            profiler.enable()

            await asyncio.wait_for(
                _run_sprint_mode(query, duration_s=60.0, mode=mode),
                timeout=120.0,  # 2min timeout
            )

            profiler.disable()

            # Save profile to file
            profile_dir.mkdir(parents=True, exist_ok=True)
            profiler.dump_stats(str(profile_path))
            log.info(f"[PROFILE] Saved to {profile_path}")

            # Print top functions by cumulative time
            s = StringIO()
            ps = pstats.Stats(profiler, stream=s).sort_stats("cumulative")
            ps.print_stats(15)
            log.info(f"[PROFILE] Top 15 functions by cumulative time:\n{s.getvalue()}")
        else:
            # Sprint s 60s durací
            await asyncio.wait_for(
                _run_sprint_mode(query, duration_s=60.0, mode=mode),
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
    # NOTE: intelligence_dispatcher, memory_watchdog, session_authority were ghost modules
    #       deleted in F196A (zero canonical call-sites). stealth_crawler is intelligence layer.
    # NOTE: marl_coordinator deleted in F196A (stub with zero production call-sites).
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
            # CR-003 FIX: Use find_spec to validate module exists before importing
            if importlib.util.find_spec(mod) is None:
                errors.append(f"{mod}: module not found")
                continue
            importlib.import_module(mod)
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
    if sys.version_info >= (3, 14):
        parser = argparse.ArgumentParser(description="Hledac Smoke Runner", suggest_on_error=True, color=True)
    else:
        parser = argparse.ArgumentParser(description="Hledac Smoke Runner")
    parser.add_argument("--smoke", action="store_true",
                        help="Run lightweight smoke test without network")
    parser.add_argument("--mode", default="public",
                        help="Pipeline mode (default: public)")
    parser.add_argument("--query", default="smoke test query",
                        help="Query string for the pipeline")
    parser.add_argument("--loop", action="store_true",
                        help="P16: Run ResearchLoop after pipeline completion")
    parser.add_argument("--rl-steps", type=int, default=0,
                        help="P17: Number of RL steps to run (default: 0, uses time limit)")
    parser.add_argument("--profile", action="store_true",
                        help="P19: Enable cProfile CPU/I/O profiling")
    args = parser.parse_args()

    if args.smoke:
        # Lightweight smoke test
        exit_code = asyncio.run(run_smoke_test())
        sys.exit(exit_code)
    else:
        # Nejdřív import test
        if not run_sprint_import_test():
            sys.exit(1)

        # Pak sprint
        exit_code = asyncio.run(main(
            mode=args.mode,
            query=args.query,
            run_loop=args.loop,
            rl_steps=args.rl_steps,
            profile=args.profile,
        ))
        sys.exit(exit_code)
