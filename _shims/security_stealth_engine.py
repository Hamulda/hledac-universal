"""
Adapter: StealthSession → StealthEngine interface.
Bridges security_coordinator.py to canonical StealthSession from stealth/stealth_session.py.
Sprint F214Q — StealthEngine aliasing.
"""


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
        from hledac.universal.stealth.stealth_manager import StealthManager
        self._manager = StealthManager()
        self._active = False
        self._activations = 0

    async def initialize(self) -> None:
        """No-op: StealthSession has no init requirement."""
        pass

    async def activate_stealth_mode(
        self,
        operation_type: str = "research",
        confidence_threshold: float = 0.0,
        security_level: int = 1,
    ) -> dict:
        """
        Activate stealth mode via StealthManager.

        Returns dict shape expected by SecurityCoordinator._execute_stealth_operation.
        """
        self._activations += 1
        self._active = True

        await self._manager.rotate_all()
        ua = self._manager.header_spoofer.get_random_ua() if self._manager.header_spoofer else "unknown"
        js_prot = self._manager.get_js_protection()
        profile = self._manager.get_browser_profile()

        return {
            "active": True,
            "success": True,
            "measures_activated": 4,
            "ua_used": ua[:60],
            "operation_type": operation_type,
            "canvas_normalized": profile is not None,
            "webgl_spoofed": profile is not None,
            "webdriver_hidden": profile is not None,
            "js_protection_script": js_prot,
            "browser_profile": profile,
        }

    async def cleanup(self) -> None:
        """No-op: StealthManager has no close requirement."""
        self._active = False

    def is_active(self) -> bool:
        """Return whether stealth mode is currently active."""
        return self._active


__all__ = ["StealthEngine"]
