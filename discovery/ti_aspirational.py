# Sprint F252: TI Feed Aspirational Stubs
# These adapters were documented in DISCOVERY_CAPABILITY_AUDIT as existing but
# were NEVER implemented. Kept as stubs so the aspirational scope is visible
# and future implementation has a clear entry point.
#
# DO NOT wire these into sprint_scheduler — they are NotImplemented stubs.
# Real TI feeds (NVD, CISA KEV) are wired via _run_ti_feed_sidecar() in sprint_scheduler.py.
#
# Real adapters (free, no-auth, production-ready):
#   NvdApiAdapter, CisaKevAdapter → sprint_scheduler._run_ti_feed_sidecar()

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MispAdapterNotImplemented(Exception):
    """MISP adapter is aspirational — not yet implemented."""
    pass


class AlienVaultOTXAdapterNotImplemented(Exception):
    """AlienVault OTX adapter is aspirational — not yet implemented."""
    pass


class MITREATTACKAdapterNotImplemented(Exception):
    """MITRE ATT&CK adapter is aspirational — not yet implemented."""
    pass


class IBMXForceAdapterNotImplemented(Exception):
    """IBM X-Force adapter is aspirational — not yet implemented."""
    pass


class PulseDiveAdapterNotImplemented(Exception):
    """PulseDive adapter is aspirational — not yet implemented."""
    pass


# ── MispAdapter ──────────────────────────────────────────────────────────────
# Status: ASPIRATIONAL — not implemented
#
# Rationale: MISP requires auth (org UID + API key), self-hosted or cloud instance.
# Free community MISP instances are rate-limited and unreliable for OSINT.
#
# When implementing:
#   1. Use MISP API v2 (Python pymisp library or REST directly)
#   2. Rate limit: max 60 req/min with auth, no key = use community feeds
#   3. Source type: "misp_event"
#   4. Output: list[NormalizedEntry] → CanonicalFinding via sprint_scheduler
#
# class MispAdapter(SourceAdapter):
#     API_KEY: str | None = None  # Set via env HLEDAC_MISP_API_KEY
#     INSTANCE_URL: str = "https://misp.example.com"
#
#     async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
#         raise NotImplementedError("MISP adapter — implement with pymisp or REST API")
#
#     async def query_event(self, event_id: str) -> NormalizedEntry | None:
#         raise NotImplementedError("MISP event query")


# ── AlienVaultOTXAdapter ──────────────────────────────────────────────────────
# Status: ASPIRATIONAL — not implemented
#
# Rationale: AlienVault OTX API requires API key (free tier: 10k req/day).
# Pulse DOR (Direct Observation) endpoint: GET /api/v1/pulses/dumplast/30days/
#
# When implementing:
#   1. Env var: HLEDAC_OTX_API_KEY
#   2. Rate limit: 10k/day → fail-soft after 100 pulses/sprint
#   3. Source type: "otx_pulse"
#   4. Cassette replay supported (F239A pattern)
#
# class AlienVaultOTXAdapter(SourceAdapter):
#     API_KEY: str | None = None
#
#     async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
#         raise NotImplementedError("OTX adapter — implement with REST API + HLEDAC_OTX_API_KEY")


# ── MITREATTACKAdapter ────────────────────────────────────────────────────────
# Status: ASPIRATIONAL — not implemented
#
# Rationale: MITRE ATT&CK is a static reference taxonomy, not a feed.
# No "new techniques" to fetch — instead cross-reference CT findings against
# ATT&CK technique IDs stored in knowledge/graph_service.
#
# Proper approach: enrichment sidecar that maps existing findings to ATT&CK,
# not a feed to query. See export/stix_exporter.py for technique mapping.
#
# class MITREATTACKAdapter(SourceAdapter):
#     async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
#         raise NotImplementedError("ATT&CK is reference taxonomy, not a feed")


# ── IBMXForceAdapter ──────────────────────────────────────────────────────────
# Status: ASPIRATIONAL — not implemented
#
# Rationale: IBM X-Force Exchange requires IBMid auth + API key.
# Free tier: 50k req/month, heavy rate limits.
# Exchanger API: GET /api/iocs/search?type=indicator
#
# When implementing:
#   1. Env var: HLEDAC_XFORCE_API_KEY + HLEDAC_XFORCE_API_SECRET
#   2. OAuth2 token exchange required
#   3. Source type: "xforce_report"
#
# class IBMXForceAdapter(SourceAdapter):
#     async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
#         raise NotImplementedError("X-Force adapter — requires OAuth2")


# ── PulseDiveAdapter ───────────────────────────────────────────────────────────
# Status: ASPIRATIONAL — not implemented
#
# Rationale: PulseDive API requires key (free tier: 1k req/day).
# Pulse lookup: GET /api/pulse/info/{pulse_id}
# IOC search: GET /api/ioc/search?q={query}
#
# When implementing:
#   1. Env var: HLEDAC_PULSEDIVE_API_KEY
#   2. Rate limit: 1k/day → fail-soft after 50 lookups/sprint
#   3. Source type: "pulse_dive"
#
# class PulseDiveAdapter(SourceAdapter):
#     async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
#         raise NotImplementedError("PulseDive adapter")


# ── CanonicalFindings from NormalizedEntry ────────────────────────────────────
# Pattern for future implementation:
#
# from hledac.universal.knowledge.duckdb_store import CanonicalFinding
#
# def normalized_to_canonical(entry: NormalizedEntry) -> CanonicalFinding:
#     ts_now = time.time()
#     return CanonicalFinding(
#         finding_id=f"ti_{entry.source_type}_{entry.entry_hash[:16]}_{int(ts_now * 1000)}",
#         query=entry.raw_identifiers[0] if entry.raw_identifiers else entry.title[:128],
#         source_type=entry.source_type,
#         confidence=0.7,
#         ts=ts_now,
#         provenance=(entry.source_type, entry.source_url or "", entry.title),
#         payload_text=entry.body_text[:2048] if entry.body_text else None,
#     )
