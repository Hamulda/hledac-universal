# anti_analysis.py — Anti-Analysis Evasion Engine Domain
"""
NEXTGEN-02: Pre-fetch TLS/HTTP2 challenge detection for OSINT evasion.

This module provides a typed Python domain facade for the Rust anti_analysis
module, enabling pre-fetch abandonment of domains that exhibit bot-detection
fingerprints at the TLS handshake level — before wasting bandwidth, CPU, or
LLM tokens on tarpits.

Architecture:
    fetch_via_curl_cffi()
           ↓
    anti_analysis.quick_probe_async(url) ──── Abandoned! ───→ Skip domain (0 cost)
           ↓
    curl_cffi session.get() ──→ Response ──→ tarpit_detector (secondary defense)

Integration:
    from hledac.universal._core.rust_backend import rust

    # Fast pre-fetch gate (≤50ms)
    result = await rust.anti_analysis.quick_probe_async("https://example.com")
    if result.abandoned:
        print(f"Abandoning: {result.reason}")
        return None

    # Detailed TLS fingerprint analysis
    tls_result = await rust.anti_analysis.tls_fingerprint_challenge_detect_async("example.com", 443)

    # HTTP/2 SETTINGS anomaly check
    h2_result = await rust.anti_analysis.http2_settings_anomaly_detect_async("example.com")

    # 3-request micro-probe
    probe = await rust.anti_analysis.early_honeypot_probe_async("https://example.com")

    # Domain abandonment (persistent across sprint)
    rust.anti_analysis.mark_host_abandoned("bad-domain.com", "cf_turnstile_detected")

    # Telemetry export
    telemetry = rust.anti_analysis.get_evasion_telemetry()

Feature Gate:
    Requires `anti_analysis` feature in Cargo.toml (enabled by default).
    Python fallback: returns safe defaults when Rust module unavailable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:

# Availability flag — set once at module load
_ANALYSIS_RUST_AVAILABLE = False

try:
    from hledac.universal.rust_extensions import anti_analysis as _aa_rust
    _ANALYSIS_RUST_AVAILABLE = True
except ImportError:
    _aa_rust = None  # type: ignore[assignment]
    _ANALYSIS_RUST_AVAILABLE = False



class _RustAntiAnalysisDomain:
    """Rust-backed anti-analysis evasion engine.

    Detects Cloudflare Turnstile, DataDome, Akamai at TLS handshake level.
    Abandoned domains skip entire fetch (0 bandwidth, 0 LLM tokens).
    """

    __slots__ = ("_ext",)

    def __init__(self, ext: object) -> None:
        self._ext = ext

    async def quick_probe_async(
        self,
        url: str,
        timeout_ms: int | None = None,
    ) -> "QuickProbeResult":
        """
        Fast combined check for hot path (≤50ms budget).

        Performs parallel TLS fingerprint + micro-probe.
        Returns abandonment decision immediately.

        Args:
            url: Target URL to probe
            timeout_ms: Probe timeout (default: 5000)

        Returns:
            QuickProbeResult with abandoned, reason, confidence, evasion_type
        """
        return await self._ext.quick_probe_async(url, timeout_ms=timeout_ms)

    async def tls_fingerprint_challenge_detect_async(
        self,
        host: str,
        port: int = 443,
        timeout_ms: int | None = None,
        sni: str | None = None,
    ) -> "TlsChallengeResult":
        """
        Detect TLS fingerprint challenges (Cloudflare Turnstile, DataDome, Akamai).

        Performs TLS handshake with the target and analyzes the JA4 fingerprint
        for known bot-detection patterns. Returns early if challenge detected.

        Args:
            host: Target hostname
            port: Target port (default 443)
            timeout_ms: Connection timeout (default 5000)
            sni: SNI hostname (defaults to host)

        Returns:
            TlsChallengeResult with challenge_detected, challenge_type, confidence
        """
        return await self._ext.tls_fingerprint_challenge_detect_async(
            host, port, timeout_ms=timeout_ms, sni=sni
    )

    async def http2_settings_anomaly_detect_async(
        self,
        host: str,
        port: int = 443,
        timeout_ms: int | None = None,
    ) -> "H2SettingsResult":
        """
        Detect HTTP/2 SETTINGS anomalies (Safari WebKit mismatch detection).

        Performs HTTP/2 protocol handshake and analyzes server's response for
        anomalies that indicate bot detection.

        Args:
            host: Target hostname
            port: Target port (default 443)
            timeout_ms: Connection timeout (default 5000)

        Returns:
            H2SettingsResult with anomaly_detected, anomaly_type, bot_score
        """
        return await self._ext.http2_settings_anomaly_detect_async(
            host, port, timeout_ms=timeout_ms
    )

    async def early_honeypot_probe_async(
        self,
        url: str,
        timeout_ms: int | None = None,
        profile: str | None = None,
    ) -> "HoneypotProbeResult":
        """
        Early honeypot micro-probe (3-request).

        Performs HEAD /robots.txt, GET /, GET /wp-admin micro-probe.
        Uses timing heuristics, link labyrinth detection, and hidden element analysis.

        Args:
            url: Target URL to probe
            timeout_ms: Probe timeout (default: 5000)
            profile: TLS impersonation profile (default: None)

        Returns:
            HoneypotProbeResult with honeypot_detected, honeypot_type, confidence
        """
        return await self._ext.early_honeypot_probe_async(
            url, timeout_ms=timeout_ms, profile=profile
    )

    def mark_host_abandoned(self, domain: str, reason: str) -> None:
        """
        Mark domain as abandoned (persistent across sprint).

        Args:
            domain: Domain to mark
            reason: Abandonment reason (e.g., "cf_turnstile_detected")
        """
        self._ext.mark_host_abandoned(domain, reason)

    def is_host_abandoned(self, domain: str) -> "AbandonCheckResult":
        """
        Check if domain is abandoned.

        Args:
            domain: Domain to check

        Returns:
            AbandonCheckResult with abandoned, reason, abandoned_at, trust_score
        """
        return self._ext.is_host_abandoned(domain)

    def clear_abandoned_hosts(self) -> None:
        """Clear all abandoned hosts."""
        self._ext.clear_abandoned_hosts()

    def get_abandoned_domains(self) -> list[str]:
        """Get list of abandoned domains."""
        return self._ext.get_abandoned_domains()

    def sync_abandoned_from_python(self, domains: list[tuple[str, str]]) -> None:
        """
        Sync abandoned domains from Python tracker.

        Args:
            domains: List of (domain, reason) tuples
        """
        self._ext.sync_abandoned_from_python(domains)

    def get_evasion_telemetry(self) -> dict[str, Any]:
        """
        Get telemetry of evasion operations.

        Returns:
            Dict with probes_total, abandoned_domains_count, abandoned_domains
        """
        return self._ext.get_evasion_telemetry()


class _PythonAntiAnalysisDomain:
    """Python anti-analysis fallback (no-op when Rust unavailable).

    Provides safe defaults that never abandon domains — used when the
    Rust anti_analysis module is not compiled or unavailable.
    """

    __slots__ = ()

    async def quick_probe_async(
        self,
        url: str,
        timeout_ms: int | None = None,
    ) -> "QuickProbeResult":
        """Safe fallback: never abandons domains."""
        return QuickProbeResult(
            abandoned=False,
            reason="",
            confidence=0.0,
            evasion_type="none",
            probe_time_ms=0.0,
    )

    async def tls_fingerprint_challenge_detect_async(
        self,
        host: str,
        port: int = 443,
        timeout_ms: int | None = None,
        sni: str | None = None,
    ) -> "TlsChallengeResult":
        """Safe fallback: no challenge detected."""
        return TlsChallengeResult(
            challenge_detected=False,
            challenge_type="none",
            confidence=0.0,
            ja4="",
            anomaly_flags=[],
            raw_indicators=[],
    )

    async def http2_settings_anomaly_detect_async(
        self,
        host: str,
        port: int = 443,
        timeout_ms: int | None = None,
    ) -> "H2SettingsResult":
        """Safe fallback: no anomaly detected."""
        return H2SettingsResult(
            anomaly_detected=False,
            anomaly_type="none",
            bot_score=0.0,
            expected_window_size=65535,
            actual_window_size=None,
            mismatch_details="",
    )

    async def early_honeypot_probe_async(
        self,
        url: str,
        timeout_ms: int | None = None,
        profile: str | None = None,
    ) -> "HoneypotProbeResult":
        """Safe fallback: no honeypot detected."""
        return HoneypotProbeResult(
            honeypot_detected=False,
            honeypot_type="none",
            confidence=0.0,
            response_times_ms=[],
            internal_links=0,
            external_links=0,
            hidden_elements=0,
            probe_url=url,
            total_time_ms=0.0,
    )

    def mark_host_abandoned(self, domain: str, reason: str) -> None:
        """No-op in Python fallback."""

    def is_host_abandoned(self, domain: str) -> "AbandonCheckResult":
        """Safe fallback: domain not abandoned."""
        return AbandonCheckResult(
            abandoned=False,
            reason=None,
            abandoned_at=None,
            trust_score=1.0,
    )

    def clear_abandoned_hosts(self) -> None:
        """No-op in Python fallback."""

    def get_abandoned_domains(self) -> list[str]:
        """Safe fallback: no abandoned domains."""
        return []

    def sync_abandoned_from_python(self, domains: list[tuple[str, str]]) -> None:
        """No-op in Python fallback."""

    def get_evasion_telemetry(self) -> dict[str, Any]:
        """Safe fallback: zero telemetry."""
        return {
            "probes_total": 0,
            "abandoned_domains_count": 0,
            "abandoned_domains": [],
        }



class QuickProbeResult:
    """Result of quick probe (combined fast check)."""

    __slots__ = (
        "abandoned",
        "reason",
        "confidence",
        "evasion_type",
        "probe_time_ms",
    )

    def __init__(
        self,
        abandoned: bool,
        reason: str,
        confidence: float,
        evasion_type: str,
        probe_time_ms: float,
    ) -> None:
        self.abandoned = abandoned
        self.reason = reason
        self.confidence = confidence
        self.evasion_type = evasion_type
        self.probe_time_ms = probe_time_ms


class TlsChallengeResult:
    """Result of TLS fingerprint challenge detection."""

    __slots__ = (
        "challenge_detected",
        "challenge_type",
        "confidence",
        "ja4",
        "anomaly_flags",
        "raw_indicators",
    )

    def __init__(
        self,
        challenge_detected: bool,
        challenge_type: str,
        confidence: float,
        ja4: str,
        anomaly_flags: list[str],
        raw_indicators: list[str],
    ) -> None:
        self.challenge_detected = challenge_detected
        self.challenge_type = challenge_type
        self.confidence = confidence
        self.ja4 = ja4
        self.anomaly_flags = anomaly_flags
        self.raw_indicators = raw_indicators


class H2SettingsResult:
    """Result of HTTP/2 SETTINGS anomaly detection."""

    __slots__ = (
        "anomaly_detected",
        "anomaly_type",
        "bot_score",
        "expected_window_size",
        "actual_window_size",
        "mismatch_details",
    )

    def __init__(
        self,
        anomaly_detected: bool,
        anomaly_type: str,
        bot_score: float,
        expected_window_size: int,
        actual_window_size: int | None,
        mismatch_details: str,
    ) -> None:
        self.anomaly_detected = anomaly_detected
        self.anomaly_type = anomaly_type
        self.bot_score = bot_score
        self.expected_window_size = expected_window_size
        self.actual_window_size = actual_window_size
        self.mismatch_details = mismatch_details


class HoneypotProbeResult:
    """Result of early honeypot micro-probe."""

    __slots__ = (
        "honeypot_detected",
        "honeypot_type",
        "confidence",
        "response_times_ms",
        "internal_links",
        "external_links",
        "hidden_elements",
        "probe_url",
        "total_time_ms",
    )

    def __init__(
        self,
        honeypot_detected: bool,
        honeypot_type: str,
        confidence: float,
        response_times_ms: list[float],
        internal_links: int,
        external_links: int,
        hidden_elements: int,
        probe_url: str,
        total_time_ms: float,
    ) -> None:
        self.honeypot_detected = honeypot_detected
        self.honeypot_type = honeypot_type
        self.confidence = confidence
        self.response_times_ms = response_times_ms
        self.internal_links = internal_links
        self.external_links = external_links
        self.hidden_elements = hidden_elements
        self.probe_url = probe_url
        self.total_time_ms = total_time_ms


class AbandonCheckResult:
    """Result of domain abandonment check."""

    __slots__ = (
        "abandoned",
        "reason",
        "abandoned_at",
        "trust_score",
    )

    def __init__(
        self,
        abandoned: bool,
        reason: str | None,
        abandoned_at: float | None,
        trust_score: float,
    ) -> None:
        self.abandoned = abandoned
        self.reason = reason
        self.abandoned_at = abandoned_at
        self.trust_score = trust_score



def _get_domain() -> "_RustAntiAnalysisDomain | _PythonAntiAnalysisDomain":
    """Get the appropriate anti_analysis domain based on Rust availability."""
    if _ANALYSIS_RUST_AVAILABLE and _aa_rust is not None:
        return _RustAntiAnalysisDomain(_aa_rust)
    return _PythonAntiAnalysisDomain()


# Module-level singleton (lazy initialization via property)
_domain: "_RustAntiAnalysisDomain | _PythonAntiAnalysisDomain | None" = None


def __getattr__(name: str) -> Any:
    """Lazy attribute access for domain switching."""
    global _domain
    if _domain is None:
        _domain = _get_domain()
    return getattr(_domain, name)
