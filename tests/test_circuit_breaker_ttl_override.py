"""
TestCircuitBreakerTTLOverride — Sprint F266

Tests:
1. crt.sh gets 300s TTL (not 30s default)
2. certspotter gets 60s TTL
3. unknown domain gets BASE_RECOVERY_TIMEOUT_S (30s)
4. certspotter fallback called when crt.sh circuit breaker is OPEN
5. circuit_breakers field present in runtime truth (acquisition report)

Invariant table:
| Test | Invariant |
|------|-----------|
| test_crtsh_ttl_300 | crt.sh TTL = 300s |
| test_certspotter_ttl_60 | certspotter TTL = 60s |
| test_unknown_domain_default_ttl | unknown TTL = 30s |
| test_certspotter_fallback_on_crtsh_open | CB OPEN → certspotter called |
| test_circuit_breakers_in_acquisition_report | report["circuit_breakers"] present |

Always-on, bounded, fail-safe.
"""

from __future__ import annotations

import tempfile
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.transport.circuit_breaker import (
    _CIRCUIT_BREAKER_TTL_S,
    _DEFAULT_TTL_S,
    BASE_RECOVERY_TIMEOUT_S,
    CBState,
    clear_all_breakers,
    get_breaker,
    per_domain_stats,
)


class TestCircuitBreakerTTLOverride:
    """F266: Domain-specific TTL override for CT circuit breakers."""

    def setup_method(self) -> None:
        clear_all_breakers()

    def teardown_method(self) -> None:
        clear_all_breakers()

    def test_crtsh_ttl_300(self) -> None:
        """crt.sh domain gets 300s TTL, not 30s default."""
        breaker = get_breaker("crt.sh")
        assert breaker.recovery_timeout == 300.0
        assert breaker.recovery_timeout == _CIRCUIT_BREAKER_TTL_S["crt.sh"]

    def test_certspotter_ttl_60(self) -> None:
        """certspotter domain gets 60s TTL."""
        breaker = get_breaker("certstream")
        assert breaker.recovery_timeout == 60.0
        assert breaker.recovery_timeout == _CIRCUIT_BREAKER_TTL_S["certstream"]

    def test_unknown_domain_default_ttl(self) -> None:
        """Unknown domain falls back to BASE_RECOVERY_TIMEOUT_S (30s)."""
        breaker = get_breaker("example.com")
        assert breaker.recovery_timeout == BASE_RECOVERY_TIMEOUT_S
        assert breaker.recovery_timeout == 30.0
        assert breaker.recovery_timeout == _DEFAULT_TTL_S

    def test_existing_breaker_not_overwritten(self) -> None:
        """Existing breaker TTL is preserved when accessed again."""
        b1 = get_breaker("crt.sh")
        b2 = get_breaker("crt.sh")
        assert b1 is b2
        assert b1.recovery_timeout == 300.0

    def test_record_failure_preserves_ttl(self) -> None:
        """Opening a breaker preserves its domain-specific TTL."""
        breaker = get_breaker("crt.sh")
        assert breaker.recovery_timeout == 300.0
        breaker.record_failure(failure_kind="test_error")
        breaker.record_failure(failure_kind="test_error")
        breaker.record_failure(failure_kind="test_error")
        assert breaker._state == CBState.OPEN
        assert breaker._opened_at_monotonic > 0
        assert breaker.recovery_timeout == 300.0

    def test_per_domain_stats_includes_recovery_timeout(self) -> None:
        """per_domain_stats includes recovery_timeout_s for crt.sh."""
        get_breaker("crt.sh")
        stats = per_domain_stats()
        assert "crt.sh" in stats
        assert stats["crt.sh"]["recovery_timeout_s"] == 300.0


class TestCertspotterFallbackOnCrtshOpen:
    """F266: certspotter.io fallback when crt.sh circuit breaker is OPEN."""

    def setup_method(self) -> None:
        clear_all_breakers()

    def teardown_method(self) -> None:
        clear_all_breakers()

    @pytest.mark.asyncio
    async def test_fetch_ct_with_fallback_returns_provider_name(self) -> None:
        """_fetch_ct_with_fallback returns (raw, provider) tuple with correct provider."""
        from pathlib import Path

        from hledac.universal.intelligence.ct_log_client import CTLogClient

        # crt.sh is CLOSED → first provider succeeds
        client = CTLogClient(cache_dir=Path(tempfile.mkdtemp()))
        mock_session = MagicMock()

        # Patch checked_aiohttp_get to return crt.sh-style entries
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
    async def test_crtsh_open_triggers_certspotter(self) -> None:
        """When crt.sh CB is OPEN, _fetch_ct_with_fallback falls through to certspotter."""
        from pathlib import Path

        from hledac.universal.intelligence.ct_log_client import CTLogClient

        # Force crt.sh OPEN
        crtsh_breaker = get_breaker("crt.sh")
        crtsh_breaker.record_failure(failure_kind="test_error")
        crtsh_breaker.record_failure(failure_kind="test_error")
        crtsh_breaker.record_failure(failure_kind="test_error")
        assert crtsh_breaker.get_state() == "open"

        client = CTLogClient(cache_dir=Path(tempfile.mkdtemp()))
        mock_session = MagicMock()

        # crt.sh checked_aiohttp_get would be skipped (OPEN)
        # crt.sh identity also fails; certspotter succeeds
        certspotter_entries = [
            {"dns_names": ["sub.example.com"], "serial_number": "1234",
             "not_before": "2024-01-01T00:00:00Z", "not_after": "2025-01-01T00:00:00Z",
             "issuer": {"name": "Test CA"}}
        ]
        with patch(
            "hledac.universal.transport.circuit_breaker.checked_aiohttp_get",
            new_callable=AsyncMock,
            side_effect=[
                (None, 0, "circuit_breaker_open"),  # crt.sh OPEN → skipped
                (None, 0, "network_error"),  # crt.sh identity fails
            ],
        ), patch.object(
            CTLogClient, "_fetch_certspotter",
            new_callable=AsyncMock,
            return_value=certspotter_entries,
        ):
            raw, provider = await client._fetch_ct_with_fallback("example.com", mock_session)

        # crt.sh OPEN → certspotter was called
        assert provider == "certspotter"
        assert raw == certspotter_entries


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

        # Set up breakers with known state
        cb_crt = get_breaker("crt.sh")
        cb_crt.record_failure(failure_kind="test_error")
        cb_crt.record_failure(failure_kind="test_error")
        cb_crt.record_failure(failure_kind="test_error")  # OPEN

        get_breaker("certstream")  # CLOSED

        # Build cb_state from per_domain_stats (as would happen in scheduler)
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
