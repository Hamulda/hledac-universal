"""
coordinators/gc_policy.py — Centralizovaná GC strategie pro M1 8GB (Issue 6)

Účel:
    Jednotné místo pro všechny gc.collect() v hot path.
    Elimituje STW pauzy (50-200ms) při mlx_model load/unload.

Problém řešený:
    - Přímé gc.collect() v async hot path blokuje event loop
    - Vícenásobné gc.collect() za sebou plýtvají CPU
    - Každý soubor má vlastní strategii → nekonzistence

Řešení:
    1. gc.collect() v hot path VŽDY přes asyncio.to_thread() — event loop neběží
    2. gc.collect(0) pro krátké objekty, gc.collect(2) jen když je potřeba
    3. Jeden gc.collect() na cleanup, ne více za sebou
    4. gc.set_threshold(700, 50, 20) — agresivnější gen-0, šetrnější gen-2

Invarianty (CLAUDE.md):
    - Always-on, no feature flags
    - Fail-safe: každý thread offload obalen try/except
    - Bounded: žádné nové globální stavy
    - mx.eval([]) PŘED gc.collect() — clear_cache je no-op bez barrier

Import guidelines:
    from coordinators.gc_policy import gc_collect, gc_collect_aggressive

    # Hot path (model load/unload):
    await asyncio.to_thread(gc_collect, generation=0)

    # Winddown (sprint boundary):
    await asyncio.to_thread(gc_collect_aggressive)
"""
from __future__ import annotations


import asyncio
import gc as _gc
import logging
import sys
import threading
from typing import Literal

logger = logging.getLogger(__name__)

# Issue 6: agresivnější threshold pro M1 8GB s krátkými objekty.
# - gen0: 700 (default 700) — rychlá kolekce pro krátce žijící objekty
# - gen1: 50 (default 10) — střední generace
# - gen2: 20 (default 10) — gen-2 scan jen když je 20+ gen-2 allocací
# Původní __main__.py používá (1000, 50, 50) — toto je o něco agresivnější.
_GC_THRESHOLD = (700, 50, 20)

# F266-U4: Verze s opraveným gc.freeze() (gilstate_tss_set regression)
_GC_FREEZE_ENABLED: bool = sys.version_info >= (3, 14, 7)

_configured = False
_configure_lock = threading.Lock()


def _ensure_configured() -> None:
    """Apply gc.set_threshold a gc.freeze() — volá se jednou na začátku."""
    global _configured
    if _configured:
        return
    with _configure_lock:
        if _configured:
            return
        _apply_gc_config()


def _apply_gc_config() -> None:
    """Apply gc thresholds + freeze. Idempotent."""
    try:
        _gc.set_threshold(*_GC_THRESHOLD)
        logger.debug(f"[GC_POLICY] gc.set_threshold{_GC_THRESHOLD}")
    except Exception as exc:
        logger.debug(f"[GC_POLICY] set_threshold failed: {exc}")

    if _GC_FREEZE_ENABLED:
        try:
            _gc.freeze()
            logger.debug("[GC_POLICY] gc.freeze() applied at startup")
        except Exception as exc:
            logger.debug(f"[GC_POLICY] freeze failed: {exc}")

    global _configured
    _configured = True


# === Public API ===

def gc_collect(generation: Literal[0, 1, 2] = 0) -> None:
    """
    Fail-safe gc.collect() — volá se z thread poolu přes asyncio.to_thread().

    Args:
        generation: 0 = pouze gen-0 (rychlé), 2 = plný sweep (drahý)
    """
    try:
        _gc.collect(generation)
    except Exception as exc:
        logger.debug(f"[GC_POLICY] gc.collect({generation}) failed: {exc}")


def gc_collect_aggressive() -> None:
    """
    Agresivní GC: gen-0 + gen-2 sweep + freeze.
    Pro winddown fázi sprintu.

    Canonical order (F183C):
        1. gc.collect(0) — uvolní krátce žijící refs PRVNÍ
        2. mx.eval([]) — barrier (volá se odděleně v cleanup chainu)
        3. gc.collect(2) — plný sweep permanentních objektů
        4. gc.freeze() — pin permanentní množinu

    POZNÁMKA: mx.eval() se volá odděleně v MLX cleanup chainu.
    Tato funkce dělá jen Python GC část.
    """
    try:
        _gc.collect(0)  # rychlá kolekce
    except Exception as exc:
        logger.debug(f"[GC_POLICY] gc.collect(0) failed: {exc}")

    if _GC_FREEZE_ENABLED:
        try:
            _gc.freeze()
        except Exception as exc:
            logger.debug(f"[GC_POLICY] gc.freeze() failed: {exc}")


async def gc_collect_async(
    generation: Literal[0, 1, 2] = 0,
    force_aggressive: bool = False,
) -> None:
    """
    Async wrapper — gc.collect() v thread poolu, neblokuje event loop.

    Args:
        generation: 0 = gen-0 only (fast), 2 = full sweep (expensive)
        force_aggressive: pokud True, pustí i gen-2 + freeze
    """
    _ensure_configured()

    def _work() -> None:
        if force_aggressive:
            gc_collect_aggressive()
        else:
            gc_collect(generation)

    try:
        await asyncio.to_thread(_work)
    except Exception as exc:
        logger.debug(f"[GC_POLICY] async gc_collect failed: {exc}")


def get_stats() -> dict:
    """Return GC stats pro telemetry."""
    try:
        stats = _gc.get_stats()
        return {
            "generation_thresholds": _GC_THRESHOLD,
            "gc_freeze_enabled": _GC_FREEZE_ENABLED,
            "generation_stats": stats if stats else [],
        }
    except Exception as exc:
        logger.debug(f"[GC_POLICY] get_stats failed: {exc}")
        return {}


__all__ = [
    "gc_collect",
    "gc_collect_aggressive",
    "gc_collect_async",
    "get_stats",
]
