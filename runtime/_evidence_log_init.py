"""
F350M-R: Shared EvidenceLog initialization utilities.

Centralizes the EvidenceLog factory and async init pattern that was
duplicated across:
  - runtime/scheduler_v2/_v2_init.py
  - runtime/sprint_entrypoint_injections.py

This module is purely procedural — no classes, no state.
Safe to import from both locations without circular dependency risk.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

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
    Fail-soft: any exception is swallowed so initialization failures
    never block the sprint.
    """
    try:
        # Python 3.12+: get_running_loop() in async context, fallback to
        # new_event_loop() for sync context. Avoids deprecated get_event_loop()
        # in Python 3.14+.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            _task = asyncio.create_task(elog.initialize())
            # Keep strong reference so the task isn't GC'd before completion
            object.__setattr__(elog, "_init_task", _task)
        else:
            loop.run_until_complete(elog.initialize())
    except Exception:
        pass  # fail-soft: initialize() failures never block sprint

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
    except Exception:
        pass  # fail-soft: evidence events never block sprint
