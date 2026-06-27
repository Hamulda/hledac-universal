# Sprint F252 / F266: TI Feed Aspirational Registry
# Sprint F266: Refactored to clear aspirational registry pattern.
#
# These adapters were documented in DISCOVERY_CAPABILITY_AUDIT as existing but
# were NEVER implemented. Kept as stubs so the aspirational scope is visible
# and future implementation has a clear entry point.
#
# DO NOT wire these into sprint_scheduler — they are aspirational stubs.
# Real TI feeds (NVD, CISA KEV) are wired via _run_ti_feed_sidecar() in
# sprint_scheduler.py.
#
# Real adapters (free, no-auth, production-ready):
#   NvdApiAdapter, CisaKevAdapter → sprint_scheduler._run_ti_feed_sidecar()
#
# ── Architecture ──────────────────────────────────────────────────────────────
# Each aspirational adapter is defined as:
#   1. An AspirationalAdapter marker class (exception-subclass for registry)
#   2. A PROTOCOL placeholder showing the intended SourceAdapter interface
#   3. Implementation notes: auth reqs, rate limits, source_type
#
# To implement: copy the PROTOCOL stub to a new file under intelligence/
# and replace the raise with actual REST/HTTP logic.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

# ── Aspirational Adapter Markers ─────────────────────────────────────────────
# Each marker documents WHY the adapter is aspirational and what implementing
# it requires. They inherit from Exception so they can be raised as signals
# when dead-code detection finds a referenc to the aspirational name.


class AspirationalAdapter(Exception):  # noqa: N818
    """
    Base marker for aspirational TI adapters.

    Inherits from Exception so that any accidental instantiation or
    reference surfaces as a clear error with adapter-specific context.
    Subclasses define: why aspirational, auth requirements, rate limits.
    """

    adapter_name: str = "GenericTI"
    auth_required: str = "unknown"
    rate_limit: str = "unknown"
    source_type: str = "unknown"
    env_vars: tuple[str, ...] = ()
    implementation_notes: str = ""

    def __init__(self, message: str | None = None) -> None:
        base = (
            f"[{self.adapter_name}] aspirational adapter — not implemented.\n"
            f"  auth: {self.auth_required}\n"
            f"  rate_limit: {self.rate_limit}\n"
            f"  source_type: {self.source_type}\n"
        )
        if self.env_vars:
            base += f"  env_vars: {', '.join(self.env_vars)}\n"
        if self.implementation_notes:
            base += f"  notes: {self.implementation_notes}\n"
        if message:
            base += f"  detail: {message}"
        super().__init__(base)


class MispAdapter(AspirationalAdapter):
    """
    MISP (Malware Information Sharing Platform) — aspirational.

    Rationale: MISP requires auth (org UID + API key), self-hosted or cloud
    instance. Free community MISP instances are rate-limited and unreliable
    for OSINT.

    Auth:       MISP API key (HLEDAC_MISP_API_KEY) + instance URL
    Rate limit: 60 req/min with auth; community feeds = no key, heavily limited
    Source type: misp_event
    Notes:      Use MISP API v2 (pymisp library or REST directly).
                Implement as intelligence/misp_adapter.py following the
                SourceAdapter protocol.
    """

    adapter_name = "MISP"
    auth_required = "MISP API key + instance URL"
    rate_limit = "60 req/min (auth), community = unreliable"
    source_type = "misp_event"
    env_vars = ("HLEDAC_MISP_API_KEY",)
    implementation_notes = (
        "Use MISP API v2. Rate limit: max 60 req/min with auth. "
        "Output: list[NormalizedEntry] → CanonicalFinding via sprint_scheduler"
    )


class AlienVaultOTXAdapter(AspirationalAdapter):
    """
    AlienVault OTX (Open Threat Exchange) — aspirational.

    Rationale: AlienVault OTX API requires API key.
    Free tier: 10,000 req/day.

    Auth:       OTX API key (HLEDAC_OTX_API_KEY)
    Rate limit: 10k/day → fail-soft after 100 pulses/sprint
    Source type: otx_pulse
    Notes:      Pulse DOR endpoint: GET /api/v1/pulses/dumplast/30days/
                Cassette replay supported (F239A pattern).
    """

    adapter_name = "AlienVault OTX"
    auth_required = "OTX API key"
    rate_limit = "10k/day (free tier)"
    source_type = "otx_pulse"
    env_vars = ("HLEDAC_OTX_API_KEY",)
    implementation_notes = (
        "Pulse DOR endpoint: GET /api/v1/pulses/dumplast/30days/. "
        "Cassette replay supported (F239A pattern)."
    )


class MITREATTACKAdapter(AspirationalAdapter):
    """
    MITRE ATT&CK — aspirational (NOT a feed, reference taxonomy).

    Rationale: MITRE ATT&CK is a static reference taxonomy, not a feed.
    No "new techniques" to fetch — instead cross-reference CT findings
    against ATT&CK technique IDs stored in knowledge/graph_service.

    Auth:       None (public TAXII server or static dump)
    Rate limit: N/A (static data)
    Source type: mitre_attack_technique
    Notes:      Proper approach: enrichment sidecar that maps existing
                findings to ATT&CK, not a feed to query.
                See export/stix_exporter.py for technique mapping.
    """

    adapter_name = "MITRE ATT&CK"
    auth_required = "None (public TAXII or static dump)"
    rate_limit = "N/A (static reference data)"
    source_type = "mitre_attack_technique"
    env_vars = ()
    implementation_notes = (
        "ATT&CK is a reference taxonomy, NOT a feed. "
        "Enrichment sidecar: cross-ref existing findings → ATT&CK IDs. "
        "See export/stix_exporter.py for technique mapping."
    )


class IBMXForceAdapter(AspirationalAdapter):
    """
    IBM X-Force Exchange — aspirational.

    Rationale: IBM X-Force Exchange requires IBMid auth + API key.
    Free tier: 50k req/month with heavy rate limits.

    Auth:       IBMid + API key (HLEDAC_XFORCE_API_KEY + HLEDAC_XFORCE_API_SECRET)
    Rate limit: 50k/month (free tier)
    Source type: xforce_report
    Notes:      OAuth2 token exchange required before API calls.
                Exchange API: GET /api/iocs/search?type=indicator
    """

    adapter_name = "IBM X-Force"
    auth_required = "IBMid + API key + OAuth2 token exchange"
    rate_limit = "50k/month (free tier)"
    source_type = "xforce_report"
    env_vars = ("HLEDAC_XFORCE_API_KEY", "HLEDAC_XFORCE_API_SECRET")
    implementation_notes = (
        "OAuth2 token exchange required. "
        "Exchange API: GET /api/iocs/search?type=indicator"
    )


class PulseDiveAdapter(AspirationalAdapter):
    """
    PulseDive (by Pulsedive) — aspirational.

    Rationale: PulseDive API requires key.
    Free tier: 1,000 req/day.

    Auth:       PulseDive API key (HLEDAC_PULSEDIVE_API_KEY)
    Rate limit: 1k/day → fail-soft after 50 lookups/sprint
    Source type: pulse_dive
    Notes:      IOC search: GET /api/ioc/search?q={query}
                Pulse lookup: GET /api/pulse/info/{pulse_id}
    """

    adapter_name = "PulseDive"
    auth_required = "PulseDive API key"
    rate_limit = "1k/day (free tier)"
    source_type = "pulse_dive"
    env_vars = ("HLEDAC_PULSEDIVE_API_KEY",)
    implementation_notes = (
        "IOC search: GET /api/ioc/search?q={query}. "
        "Pulse lookup: GET /api/pulse/info/{pulse_id}"
    )


# ── SourceAdapter Protocol (for reference) ─────────────────────────────────────
# Uncomment and implement in intelligence/ when ready:
#
# from typing import TYPE_CHECKING, Any
# from hledac.universal.discovery.source_adapter import SourceAdapter
# from hledac.universal.types import NormalizedEntry
#
# class MispAdapter(SourceAdapter):
#     """MISP adapter — implement with pymisp or REST API."""
#
#     adapter_name = "MISP"
#
#     async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
#         raise NotImplementedError(
#             "MISP adapter — implement with pymisp or REST API. "
#             "See discovery/ti_aspirational.py for full context."
#         )
#
#     async def query_event(self, event_id: str) -> NormalizedEntry | None:
#         raise NotImplementedError("MISP event query")


# ── CanonicalFindings from NormalizedEntry (pattern) ───────────────────────────
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
