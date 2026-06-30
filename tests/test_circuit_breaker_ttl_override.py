"""
TestCircuitBreakerTTLOverride — Sprint F266 + F275 + Phase 3.3

Tests:
1. crt.sh gets 120s TTL (sprint-aware cap, was 300s)
2. certstream gets 120s TTL (aligned with crt.sh, was 60s)
3. unknown domain gets BOOT_RECOVERY_TIMEOUT_S (5s) during boot phase
4. certstream fallback called when crt.sh circuit breaker is OPEN
5. circuit_breakers field present in runtime truth (acquisition report)

F275 adds:
- BOOT_RECOVERY_TIMEOUT_S (5s) for unknown domains during first 60s of boot
- Domain-specific overrides (crt.sh, certstream) always take precedence

Phase 3.3:
- BASE_RECOVERY_TIMEOUT_S reduced from 30s to 15s for faster recovery
- CT domain TTLs capped at 120s (sprint-aware ceiling)

Invariant table:
| Test | Invariant |
| test_crtsh_ttl_120 | crt.sh TTL = 120s (sprint-aware cap) |
| test_certspotter_ttl_120 | certstream TTL = 120s (aligned with crt.sh) |
| test_unknown_domain_boot_phase_ttl | unknown TTL = 5s during boot (F275) |
| test_certspotter_fallback_on_crtsh_open | CB OPEN → certstream called |
| test_circuit_breakers_in_acquisition_report | report["circuit_breakers"] present |

Always-on, bounded, fail-safe.
"""


import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.transport.circuit_breaker import (
    _CIRCUIT_BREAKER_TTL_S,
    BOOT_RECOVERY_TIMEOUT_S,
    CBState,
    clear_all_breakers,
    get_breaker,
    per_domain_stats,
)


class TestCircuitBreakerTTLOverride:
    """F266: Domain-specific TTL override for CT circuit breakers."""

    def setup_method(self) -> None:
        # F275: Reset _boot_started_at so each test starts with clean boot-phase
        # state. Tests that need boot-phase behavior set it explicitly.
        import transport.circuit_breaker as cb_module
        cb_module._boot_started_at = 0.0
        cb_module._BREAKERS.clear()

    def teardown_method(self) -> None:
        clear_all_breakers()

    def test_crtsh_ttl_120(self) -> None:
        """crt.sh domain gets 120s TTL (sprint-aware cap, was 300s in F266)."""
        breaker = get_breaker("crt.sh")
        assert breaker.recovery_timeout == 120.0
        assert breaker.recovery_timeout == _CIRCUIT_BREAKER_TTL_S["crt.sh"]

    def test_certspotter_ttl_120(self) -> None:
        """certstream domain gets 120s TTL (was 60s, aligned with crt.sh)."""
        breaker = get_breaker("certstream")
        assert breaker.recovery_timeout == 120.0
        assert breaker.recovery_timeout == _CIRCUIT_BREAKER_TTL_S["certstream"]

    def test_unknown_domain_boot_phase_ttl(self) -> None:
        """F275: Unknown domain gets BOOT_RECOVERY_TIMEOUT_S (5s) during boot phase."""
        import transport.circuit_breaker as cb_module

        # Ensure fresh boot phase
        cb_module._boot_started_at = time.monotonic()
        breaker = get_breaker("fresh-domain.com")
        assert breaker.recovery_timeout == BOOT_RECOVERY_TIMEOUT_S
        assert breaker.recovery_timeout == 5.0

    def test_existing_breaker_not_overwritten(self) -> None:
        """Existing breaker TTL is preserved when accessed again."""
        b1 = get_breaker("crt.sh")
        b2 = get_breaker("crt.sh")
        assert b1 is b2
        assert b1.recovery_timeout == 120.0

    def test_record_failure_preserves_ttl(self) -> None:
        """Opening a breaker preserves its domain-specific TTL."""
        breaker = get_breaker("crt.sh")
        assert breaker.recovery_timeout == 120.0
        breaker.record_failure(failure_kind="test_error")
        breaker.record_failure(failure_kind="test_error")
        breaker.record_failure(failure_kind="test_error")
        assert breaker._state == CBState.OPEN
        assert breaker._opened_at_monotonic > 0
        assert breaker.recovery_timeout == 120.0

    def test_per_domain_stats_includes_recovery_timeout(self) -> None:
        """per_domain_stats includes recovery_timeout_s for crt.sh."""
        get_breaker("crt.sh")
        stats = per_domain_stats()
        assert "crt.sh" in stats
        assert stats["crt.sh"]["recovery_timeout_s"] == 120.0


class TestCertspotterFallbackOnCrtshOpen:
    """F266: certstream fallback when crt.sh circuit breaker is OPEN."""

    def setup_method(self) -> None:
        clear_all_breakers()

    def teardown_method(self) -> None:
        clear_all_breakers()

    @pytest.mark.asyncio
    async def test_fetch_ct_with_fallback_returns_provider_name(self) -> None:
        """_fetch_ct_with_fallback returns (raw, provider) tuple with correct provider."""
        from pathlib import Path

        from hledac.universal.intelligence.ct_log_client import CTLogClient

        client = CTLogClient(cache_dir=Path(tempfile.mkdtemp()))
        mock_session = MagicMock()

        crtsh_entries = [
            {"name_value": "sub.example.com", "issuer_name": "CN=Test CA",
             "not_before": "2024-01-01 00:00:00Z", "not_after": "2025-01-01 00:00:00Z"}
        ]
        with patch(
            "hledac.universal.transport.circuit_breaker.checked_aiohttp_get",
            new_callable=AsyncMock,
            return_value=(crtsh_entries, 200, None),
        ):
            raw, provider = await client._fetch_ct_with_fallback("example.com", mock_session)

        assert provider == "crtsh"
        assert raw == crtsh_entries

    @pytest.mark.asyncio
    async def test_crtsh_open_triggers_certstream(self) -> None:
        """When crt.sh CB is OPEN, _fetch_ct_with_fallback falls through to certstream."""
        from pathlib import Path

        from hledac.universal.intelligence.ct_log_client import CTLogClient

        crtsh_breaker = get_breaker("crt.sh")
        crtsh_breaker.record_failure(failure_kind="test_error")
        crtsh_breaker.record_failure(failure_kind="test_error")
        crtsh_breaker.record_failure(failure_kind="test_error")
        assert crtsh_breaker.get_state() == "open"

        client = CTLogClient(cache_dir=Path(tempfile.mkdtemp()))
        mock_session = MagicMock()

        certstream_entries = [
            {"dns_names": ["sub.example.com"], "serial_number": "1234",
             "not_before": "2024-01-01T00:00:00Z", "not_after": "2025-01-01T00:00:00Z",
             "issuer": {"Name": "Test CA"}}
        ]
        with patch(
            "hledac.universal.transport.circuit_breaker.checked_aiohttp_get",
            new_callable=AsyncMock,
            side_effect=[
                (None, 0, "circuit_breaker_open"),
                (None, 0, "network_error"),
            ],
        ), patch.object(
            CTLogClient, "_fetch_certspotter",
            new_callable=AsyncMock,
            return_value=certstream_entries,
        ):
            raw, provider = await client._fetch_ct_with_fallback("example.com", mock_session)

        assert provider == "certspotter"
        assert raw == certstream_entries


class TestCircuitBreakersInAcquisitionReport:
    """F266: circuit_breakers field in runtime truth (acquisition report)."""

    def setup_method(self) -> None:
        clear_all_breakers()

    def teardown_method(self) -> None:
        clear_all_breakers()

    def test_build_acquisition_report_has_circuit_breakers_field(self) -> None:
        """build_acquisition_report includes circuit_breakers in output dict."""
        from hledac.universal.runtime.acquisition_strategy import build_acquisition_report

        cb_state = {
            "crt.sh": {
                "state": "OPEN",
                "failure_count": 3,
                "resets_at_s": time.monotonic() + 300.0,
            },
            "certstream": {
                "state": "CLOSED",
                "failure_count": 0,
                "resets_at_s": None,
            },
        }

        report = build_acquisition_report(circuit_breakers_state=cb_state)

        assert "circuit_breakers" in report
        assert report["circuit_breakers"]["crt.sh"]["state"] == "OPEN"
        assert report["circuit_breakers"]["crt.sh"]["failure_count"] == 3
        assert report["circuit_breakers"]["crt.sh"]["resets_at_s"] is not None
        assert report["circuit_breakers"]["certstream"]["state"] == "CLOSED"

    def test_circuit_breakers_default_empty(self) -> None:
        """When circuit_breakers_state is None, field defaults to empty dict."""
        from hledac.universal.runtime.acquisition_strategy import build_acquisition_report

        report = build_acquisition_report()

        assert "circuit_breakers" in report
        assert report["circuit_breakers"] == {}

    def test_per_domain_stats_wired_into_acquisition_report(self) -> None:
        """per_domain_stats() output maps directly into circuit_breakers report field."""
        from hledac.universal.runtime.acquisition_strategy import build_acquisition_report

        cb_crt = get_breaker("crt.sh")
        cb_crt.record_failure(failure_kind="test_error")
        cb_crt.record_failure(failure_kind="test_error")
        cb_crt.record_failure(failure_kind="test_error")

        get_breaker("certstream")

        stats = per_domain_stats()
        cb_state = {}
        for domain, s in stats.items():
            cb_state[domain] = {
                "state": s["state"],
                "failure_count": s["failure_count"],
                "resets_at_s": (
                    round(s["opened_at_monotonic"] + s["recovery_timeout_s"], 1)
                    if s["opened_at_monotonic"] > 0 else None
                ),
            }

        report = build_acquisition_report(circuit_breakers_state=cb_state)

        assert "circuit_breakers" in report
        assert report["circuit_breakers"]["crt.sh"]["state"] == "open"
        assert report["circuit_breakers"]["crt.sh"]["failure_count"] == 3
        assert report["circuit_breakers"]["certstream"]["state"] == "closed"
