"""
Adapter: StealthSession → StealthEngine interface.
Bridges security_coordinator.py to canonical StealthSession from stealth/stealth_session.py.
Sprint F214Q — StealthEngine aliasing.
"""
from __future__ import annotations

import asyncio
import random


class StealthEngine:
    """
    Adapter wrapping hledac.universal.stealth.stealth_session.StealthSession
    to expose the StealthEngine API expected by SecurityCoordinator.

    Wraps:
    - rotate_ua() / get_random_ua() → activate_stealth_mode metrics
    - apply_jitter() → timing variance

    Does NOT provide full StealthEngine semantics — only what
    SecurityCoordinator._execute_stealth_operation actually calls.
    """

    def __init__(self, *args, **kwargs) -> None:  # noqa: ARG002
        from hledac.universal.stealth.stealth_session import StealthSession
        self._session = StealthSession()
        self._active = False
        self._activations = 0

    async def initialize(self) -> None:
        """No-op: StealthSession has no init requirement."""
        pass

    async def activate_stealth_mode(
        self,
        operation_type: str,
        confidence_threshold: float,
        security_level: int,
    ) -> dict:
        """
        Simulate stealth activation using StealthSession primitives.

        Returns dict shape expected by SecurityCoordinator._execute_stealth_operation:
        {
            'active': bool,
            'success': bool,
            'measures_activated': int,
        }
        """
        self._activations += 1

        # Canonical stealth: timing variance + UA rotation
        jitter_min = getattr(self._session, '_jitter_min', 0.05)
        jitter_max = getattr(self._session, '_jitter_max', 5.0)
        delay = random.uniform(jitter_min, jitter_max)
        await asyncio.sleep(delay)

        ua = self._session.rotate_ua()
        self._active = True

        return {
            'active': True,
            'success': True,
            'measures_activated': 1,  # UA rotation counts as one measure
            'ua_used': ua[:60],
            'operation_type': operation_type,
        }

    async def cleanup(self) -> None:
        """Close the underlying StealthSession if it supports close()."""
        if hasattr(self._session, 'close'):
            try:
                await self._session.close()
            except Exception:
                pass
        self._active = False

    def is_active(self) -> bool:
        """Return whether stealth mode is currently active."""
        return self._active


__all__ = ["StealthEngine"]
