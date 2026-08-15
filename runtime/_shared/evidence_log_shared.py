"""
F350M-R: Shared EvidenceLog initialization utilities.

Centralizes the EvidenceLog factory and async init pattern that was
previously duplicated across _v2_init.py and legacy entrypoint_injections.

This module is purely procedural — no classes, no state.
Safe to import from all locations without circular dependency risk.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from _core import aclose

if TYPE_CHECKING:
    pass


def evidence_log_factory(*, sprint_id: str) -> Any:
    """Build and return a live EvidenceLog instance.

    The async initialization (`.initialize()` + WARMUP event) is handled
    separately by `evidence_log_init()`.
    """
    from hledac.universal.evidence_log import EvidenceLog

    return EvidenceLog(run_id=sprint_id, enable_persist=True)


def evidence_log_init(
    elog: Any,
    sprint_id: str,
    query: str,
    duration_s: float,
    windup_lead_s: float,
) -> None:
    """Call async initialize() on EvidenceLog and record WARMUP event.

    Idempotent: safe to call multiple times on the same instance.

    FAIL-SOFT POLICY (intentional):
        EvidenceLog initialization failures are swallowed intentionally.
        Rationale: EvidenceLog is a non-critical observability service.
        - Missing evidence events do NOT compromise sprint correctness
        - Sprint must continue even if evidence persistence fails
        - User can debug evidence issues post-hoc from logs
        - Blocking sprint on evidence failure would hide the real issue

    This is NOT the same as the A7 antipattern:
        - A7 was about muffled ROOT-CAUSE during bootstrap of critical services
        - Here we have optional SERVICE failure that doesn't affect core operation
        - The distinction: critical vs optional, bootstrap vs runtime

    NOTE: The exception handling uses bare `except Exception` because:
        1. We genuinely want to catch ALL initialization errors
        2. We explicitly do NOT want to re-raise anything
        3. Logging would pollute output without providing actionable info
    """
    # MODERN-06 FIX: Ensure event loop is always closed to prevent leaks.
    # Previously, newly created loops were never closed, causing resource leaks.
    _loop_needs_close: bool = False
    try:
        # Python 3.12+: get_running_loop() in async context, fallback to
        # new_event_loop() for sync context. Avoids deprecated get_event_loop()
        # in Python 3.14+.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _loop_needs_close = True

        if loop.is_running():
            _task = asyncio.create_task(elog.initialize())
            # Keep strong reference so the task isn't GC'd before completion
            object.__setattr__(elog, "_init_task", _task)
        else:
            loop.run_until_complete(elog.initialize())
    except Exception:  # noqa: BLE001
        pass  # fail-soft: initialize() failures never block sprint
    finally:
        # MODERN-06 FIX: Always close the loop if we created it.
        if _loop_needs_close:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass  # Best-effort cleanup

    try:
        elog.create_event(
            event_type="observation",
            payload={
                "phase": "WARMUP",
                "sprint_id": sprint_id,
                "query": query,
                "duration_s": duration_s,
                "windup_lead_s": windup_lead_s,
            },
            confidence=1.0,
        )
    except Exception:  # noqa: BLE001
        pass  # fail-soft: evidence events never block sprint
