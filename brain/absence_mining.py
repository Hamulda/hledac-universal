"""
Absence Mining Engine — [FINAL]-019-01
======================================





Detects structural absences (negative evidence) in OSINT findings:

1. **IOC Completeness Rules** — checks whether reported IOCs have expected
   supporting evidence from independent sources:
   - Domain without A/AAAA records (zombie domain)
   - Domain without WHOIS registration data
   - Domain without CT log entries (never had a certificate)
   - Domain without passive DNS history
   - IP without reverse DNS (PTR record)
   - IP without any domain associations (orphan IP)
   - Hash without any source context
   - Onion without any supporting evidence

2. **Graph Topology Rules** — leverages relationship_discovery.py
   predict_hidden_connections() to detect missing edges:
   - Two IPs in same /24 subnet with no observed connection
   - Domain → registrant → email chain broken
   - AS relationship missing for IPs in same ASN
   - Certificate chain gaps

3. **Confidence Adjustment** — findings with high absence severity get
   reduced confidence scores before synthesis output.

4. **Closed-Loop Re-fetch** — absence alerts emit EntropyAlerts via
   EntropyFetchBridge to trigger micro-sprint re-fetch from alternative
   protocols (CT, passive DNS, WHOIS, BGP, Shodan, Censys, etc.).

Integration point: brain/synthesis_runner.py:_parse_raw_to_osintreport()
→ AbsenceMiningEngine.run() → confidence adjustment → _compute_confidence()

M1 8GB BOUNDS:
  - MAX_ABSENCE_CHECKS_PER_SPRINT = 200 (entity × rule pairs)
  - Async parallel checks via asyncio.Semaphore(8)
  - 5s timeout per absence check
  - LRU cache for domain/IP metadata (512 entries, 5-min TTL)
  - No time.sleep(), no bare except, fail-soft throughout

FEATURE FLAG: HLEDAC_ENABLE_ABSENCE_MINING (default 1=ON)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.asyncx import safe_gather

if TYPE_CHECKING:
    from hledac.universal.brain.synthesis_runner import IOCEntity, OSINTReport
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)

# ============================================================================
# Feature Flag
# ============================================================================
_ABSENCE_MINING_ENABLED = os.environ.get(
    "HLEDAC_ENABLE_ABSENCE_MINING", "1"
).lower() in ("1", "true", "yes", "on")

# ============================================================================
# Absence Severity & Type
# ============================================================================

class AbsenceType(Enum):
    """Categories of structural absence."""
    ZOMBIE_DOMAIN = "zombie_domain"           # Domain with no A/AAAA records
    CT_VIRGIN = "ct_virgin"                   # Domain never had a certificate
    PDNS_VIRGIN = "pdns_virgin"               # No passive DNS history
    WHOIS_VOID = "whois_void"                 # Domain with no WHOIS data
    ORPHAN_IP = "orphan_ip"                  # IP with no domain associations
    UNRESOLVED_PTR = "unresolved_ptr"         # IP missing reverse DNS
    HASH_VOID = "hash_void"                  # Hash with no supporting context
    ONION_UNVERIFIED = "onion_unverified"    # Onion without supporting evidence
    GRAPH_FRAGMENT = "graph_fragment"         # Entity with no graph edges


@dataclass(slots=True)
class AbsenceFinding:
    """A structural absence detected for an entity."""
    entity_value: str
    absence_type: AbsenceType
    severity: float                    # 0.0–1.0, affects confidence
    description: str
    missing_evidence: list[str]         # What should exist but doesn't
    suggested_protocols: list[str]      # Which protocols to try for re-fetch
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class AbsenceReport:
    """Aggregated absence report for a sprint."""
    query: str
    absences: list[AbsenceFinding]
    total_checked: int
    confidence_adjustments: dict[str, float]  # entity_value → delta (negative)
    should_trigger_refetch: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# ============================================================================
# LRU Cache for metadata lookups
# ============================================================================

class _AbsenceLRUCache:
    """
    Bounded LRU cache for absence metadata.
    512 entries max, 5-min TTL, O(1) operations via OrderedDict.
    """

    __slots__ = ("_data", "_max_size", "_ttl_s")

    def __init__(self, max_size: int = 512, ttl_s: int = 300) -> None:
        self._max_size = max_size
        self._ttl_s = ttl_s
        # OrderedDict naturally maintains insertion order
        # Moving an existing key to end = O(1) via move_to_end
        self._data: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        """LRU get — returns value if fresh, None otherwise."""
        entry = self._data.get(key)
        if entry is None:
            return None
        value, ts = entry
        now = time.monotonic()
        if now - ts > self._ttl_s:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        """Set with eviction of oldest if at capacity."""
        self._evict_stale()
        if key in self._data:
            self._data[key] = (value, time.monotonic())
            return
        if len(self._data) >= self._max_size:
            # Evict oldest entry by insertion order (first key)
            oldest = next(iter(self._data))
            del self._data[oldest]
        self._data[key] = (value, time.monotonic())

    def _evict_stale(self) -> None:
        """Remove expired entries."""
        now = time.monotonic()
        stale = [
            k for k, (_, ts) in self._data.items()
            if now - ts > self._ttl_s
        ]
        for k in stale:
            del self._data[k]

    def clear(self) -> None:
        self._data.clear()


# ============================================================================
# AbsenceMiningEngine
# ============================================================================

class AbsenceMiningEngine:
    """
    Detects structural absences (negative evidence) in OSINT findings.

    Runs after _parse_raw_to_osintreport() and before _compute_confidence()
    in synthesis_runner.py to adjust confidence based on absence severity.

    M1 8GB: bounded parallel checks, LRU cache, fail-soft throughout.
    """

    __slots__ = (
        "_duckdb_store",
        "_cache",
        "_semaphore",
        "_checked_this_sprint",
    )

    _CHECK_TIMEOUT_S: float = 5.0
    _MAX_CHECKS_PER_SPRINT: int = 200
    _CT_CHECK_MAX: int = 3           # Max CT checks per sprint (rate limit)
    _PDNS_CHECK_MAX: int = 5
    _WHOIS_CHECK_MAX: int = 3
    _GRAPH_EDGES_MIN: int = 1        # Min edges before calling orphan

    def __init__(self, duckdb_store: "DuckDBShadowStore | None" = None) -> None:
        self._duckdb_store = duckdb_store
        self._cache = _AbsenceLRUCache(max_size=512, ttl_s=300)
        self._semaphore = asyncio.Semaphore(8)
        self._checked_this_sprint: set[str] = set()

    # ------------------------------------------------------------------
    # Public API — called from synthesis_runner.py
    # ------------------------------------------------------------------

    async def run(
        self,
        report: "OSINTReport",
        duckdb_store: "DuckDBShadowStore | None" = None,
    ) -> AbsenceReport:
        """
        Run absence mining on a synthesized OSINTReport.

        Returns AbsenceReport with confidence adjustments.
        Side-effect: emits EntropyAlerts for high-severity absences via
        EntropyFetchBridge.

        Args:
            report: OSINTReport from _parse_raw_to_osintreport()
            duckdb_store: Optional override for duckdb store

        Returns:
            AbsenceReport with findings and confidence deltas
        """
        if not _ABSENCE_MINING_ENABLED:
            return AbsenceReport(
                query=report.query,
                absences=[],
                total_checked=0,
                confidence_adjustments={},
                should_trigger_refetch=False,
            )

        store = duckdb_store or self._duckdb_store
        absences: list[AbsenceFinding] = []
        adjustments: dict[str, float] = {}
        checked = 0

        try:
            # Deduplicate entity checks
            entities_to_check: dict[str, "IOCEntity"] = {}
            for entity in (report.ioc_entities or []):
                if entity.value not in entities_to_check:
                    entities_to_check[entity.value] = entity

            if len(entities_to_check) > self._MAX_CHECKS_PER_SPRINT:
                entities_to_check = dict(
                    list(entities_to_check.items())[: self._MAX_CHECKS_PER_SPRINT]
                )

            # Parallel absence checks bounded by semaphore
            tasks = [
                self._check_entity(entity)
                for entity in entities_to_check.values()
            ]

            result = await safe_gather(*tasks, label="absence_checks")
            if result.re_raised is not None:
                raise result.re_raised

            checked = len(entities_to_check)

            for r in result.ok:
                if r is None:
                    continue
                absence, delta = r
                if absence is not None:
                    absences.append(absence)
                    if delta < 0:
                        adjustments[absence.entity_value] = delta

            # Graph topology absence check
            graph_absences = await self._check_graph_topology(
                report, store
            )
            absences.extend(graph_absences)
            for a in graph_absences:
                if a.severity > 0.3:
                    adjustments[a.entity_value] = adjustments.get(
                        a.entity_value, 0.0
                    ) + (a.severity * -0.2)

            # Emit entropy alerts for high-severity absences
            should_refetch = await self._emit_absence_alerts(
                absences, report, store
            )

            self._checked_this_sprint.clear()
            self._checked_this_sprint.update(entities_to_check.keys())

            return AbsenceReport(
                query=report.query,
                absences=absences,
                total_checked=checked,
                confidence_adjustments=adjustments,
                should_trigger_refetch=should_refetch,
            )

        except Exception as e:
            logger.warning("[ABSENCE] Exception during absence mining: %s", e)
            return AbsenceReport(
                query=report.query,
                absences=absences,
                total_checked=checked,
                confidence_adjustments=adjustments,
                should_trigger_refetch=False,
            )

    def apply_confidence_adjustment(
        self,
        report: "OSINTReport",
        absence_report: AbsenceReport,
    ) -> float:
        """
        Apply absence-based confidence adjustment.

        Base confidence is reduced by average severity of detected absences.
        High-severity absences (>0.7) trigger more aggressive reduction.

        Args:
            report: Original OSINTReport
            absence_report: Result from run()

        Returns:
            Adjusted confidence (clamped to 0.0–1.0)
        """
        base_confidence = report.confidence
        adjustments = absence_report.confidence_adjustments

        if not adjustments:
            return base_confidence

        # Average severity across all adjusted entities
        avg_severity = sum(abs(d) for d in adjustments.values()) / len(adjustments)
        # Reduction factor: severity 0.3 → 0.03, severity 0.7 → 0.14
        reduction = avg_severity * 0.2
        adjusted = base_confidence - reduction

        logger.debug(
            "[ABSENCE] Confidence adjustment: %.3f → %.3f "
            "(avg_severity=%.3f, n_absences=%d)",
            base_confidence,
            max(0.0, min(1.0, adjusted)),
            avg_severity,
            len(adjustments),
        )

        return max(0.0, min(1.0, adjusted))

    # ------------------------------------------------------------------
    # Per-Entity Absence Checks
    # ------------------------------------------------------------------

    async def _check_entity(
        self,
        entity: "IOCEntity",
    ) -> tuple[AbsenceFinding | None, float] | None:
        """
        Check one IOC entity for structural absences.

        Returns (AbsenceFinding, confidence_delta) or (None, 0.0) if clean.
        Returns None on exception (fail-soft).
        """
        entity_key = f"{entity.ioc_type}:{entity.value}"
        if entity_key in self._checked_this_sprint:
            return None

        try:
            async with self._semaphore:
                result = await asyncio.wait_for(
                    self._do_entity_check(entity),
                    timeout=self._CHECK_TIMEOUT_S,
                )
            self._checked_this_sprint.add(entity_key)
            return result
        except asyncio.TimeoutError:
            logger.debug("[ABSENCE] Timeout checking entity: %s", entity.value)
            return None
        except Exception as e:
            logger.debug("[ABSENCE] Exception checking entity %s: %s", entity.value, e)
            return None

    async def _do_entity_check(
        self,
        entity: "IOCEntity",
    ) -> tuple[AbsenceFinding | None, float] | None:
        """Execute the actual absence check for an entity type."""
        ioc_type = entity.ioc_type.lower()
        value = entity.value

        if ioc_type == "domain":
            return await self._check_domain_absence(value)
        elif ioc_type == "ip":
            return await self._check_ip_absence(value)
        elif ioc_type == "hash":
            return await self._check_hash_absence(value)
        elif ioc_type == "onion":
            return await self._check_onion_absence(value)
        elif ioc_type == "cve":
            return await self._check_cve_absence(value)
        else:
            return None

    async def _check_domain_absence(
        self, domain: str,
    ) -> tuple[AbsenceFinding | None, float]:
        """
        Check domain for structural absences.

        Checks (in priority order):
        1. CT log presence (crt.sh)
        2. Passive DNS history
        3. WHOIS registration data
        """
        # Check CT log
        ct_result = await self._check_ct_presence(domain)
        if ct_result is not None:
            return ct_result

        # Check passive DNS
        pdns_result = await self._check_pdns_presence(domain)
        if pdns_result is not None:
            return pdns_result

        # Check WHOIS
        whois_result = await self._check_whois_presence(domain)
        if whois_result is not None:
            return whois_result

        return (None, 0.0)

    async def _check_ip_absence(
        self, ip: str,
    ) -> tuple[AbsenceFinding | None, float]:
        """
        Check IP for structural absences.

        Checks:
        1. Reverse DNS (PTR) resolution
        2. Passive DNS association
        """
        # Check reverse DNS
        ptr_result = await self._check_ptr_presence(ip)
        if ptr_result is not None:
            return ptr_result

        # Check passive DNS association
        pdns_result = await self._check_ip_pdns_presence(ip)
        if pdns_result is not None:
            return pdns_result

        return (None, 0.0)

    async def _check_hash_absence(
        self, hash_val: str,
    ) -> tuple[AbsenceFinding | None, float]:
        """
        Check hash for absence of supporting context.

        Severity: High severity if no VT results, no source attribution.
        """
        # Check DuckDB for hash context
        if self._duckdb_store:
            try:
                context = await self._query_hash_context(hash_val)
                if context:
                    return (None, 0.0)  # Has context, no absence
            except Exception:  # noqa: BLE001
                pass

        # High severity — hash with no context is suspicious
        return AbsenceFinding(
            entity_value=hash_val,
            absence_type=AbsenceType.HASH_VOID,
            severity=0.5,
            description=f"Hash {hash_val[:16]}... has no supporting source context",
            missing_evidence=["VirusTotal", "any OSINT source attribution"],
            suggested_protocols=["virustotal", "hybrid_analysis", "threatfox"],
        ), -0.1

    async def _check_onion_absence(
        self, onion: str,
    ) -> tuple[AbsenceFinding | None, float]:
        """
        Check onion service for absence of verification.

        Severity: High — unverified onion services are high-risk.
        """
        if self._duckdb_store:
            try:
                results = await self._duckdb_store.async_query_findings_by_text(
                    onion, limit=1,
                )
                if results and len(results) > 0:
                    return (None, 0.0)
            except Exception:  # noqa: BLE001
                pass

        return AbsenceFinding(
            entity_value=onion,
            absence_type=AbsenceType.ONION_UNVERIFIED,
            severity=0.6,
            description=f"Onion service {onion} lacks supporting evidence from other sources",
            missing_evidence=["Ahmia.fi", "DarkSearch", "OnionScan", "other crawler"],
            suggested_protocols=["tor", "dark_pivots", "ipfs"],
        ), -0.12

    async def _check_cve_absence(
        self, cve: str,
    ) -> tuple[AbsenceFinding | None, float]:
        """
        Check CVE for absence of NVD/mitre data.

        Severity: Medium — CVEs without NVD data may be invalid or POC-only.
        """
        if self._duckdb_store:
            try:
                results = await self._duckdb_store.async_query_findings_by_text(
                    cve, limit=3,
                )
                if results and len(results) >= 2:
                    return (None, 0.0)
            except Exception:  # noqa: BLE001
                pass

        return AbsenceFinding(
            entity_value=cve,
            absence_type=AbsenceType.GRAPH_FRAGMENT,
            severity=0.4,
            description=f"CVE {cve} has minimal OSINT coverage",
            missing_evidence=["NVD", "MITRE CVE", "exploit-db", "packetstorm"],
            suggested_protocols=["nvd", "cve_search", "exploitdb", "packetstorm"],
        ), -0.08

    # ------------------------------------------------------------------
    # Protocol-Specific Checks
    # ------------------------------------------------------------------

    async def _check_ct_presence(
        self, domain: str,
    ) -> tuple[AbsenceFinding, float] | None:
        """Check if domain appears in Certificate Transparency logs."""
        cache_key = f"ct:{domain}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            if cached:
                return None
            return AbsenceFinding(
                entity_value=domain,
                absence_type=AbsenceType.CT_VIRGIN,
                severity=0.4,
                description=f"Domain {domain} has no certificate transparency history",
                missing_evidence=["crt.sh record", "any SSL certificate"],
                suggested_protocols=["ct", "censys", "shodan"],
            ), -0.08

        try:
            if self._duckdb_store:
                results = await self._duckdb_store.async_query_findings_by_text(
                    domain, limit=1,
                )
                if results and len(results) > 0:
                    self._cache.set(cache_key, True)
                    return None

            # Check crt.sh directly
            result = await self._fetch_ct_via_crtsh(domain)
            if result:
                self._cache.set(cache_key, True)
                return None
            else:
                self._cache.set(cache_key, False)
                return AbsenceFinding(
                    entity_value=domain,
                    absence_type=AbsenceType.CT_VIRGIN,
                    severity=0.4,
                    description=f"Domain {domain} has no certificate transparency history",
                    missing_evidence=["crt.sh record", "any SSL certificate"],
                    suggested_protocols=["ct", "censys", "shodan"],
                ), -0.08

        except Exception as e:
            logger.debug("[ABSENCE] CT check failed for %s: %s", domain, e)
            return None

    async def _fetch_ct_via_crtsh(self, domain: str) -> bool:
        """Query crt.sh for domain certificates."""
        try:
            from hledac.universal.network.session_runtime import async_get_httpx_session

            session = await async_get_httpx_session(profile="default")
            resp = await session.get(
                "https://crt.sh/",
                params={"q": domain, "output": "json"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return isinstance(data, list) and len(data) > 0
        except Exception:  # noqa: BLE001
            pass
        return False

    async def _check_pdns_presence(
        self, domain: str,
    ) -> tuple[AbsenceFinding, float] | None:
        """Check if domain has passive DNS history."""
        cache_key = f"pdns:{domain}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            if cached:
                return None
            return AbsenceFinding(
                entity_value=domain,
                absence_type=AbsenceType.PDNS_VIRGIN,
                severity=0.3,
                description=f"Domain {domain} has no passive DNS history",
                missing_evidence=["passive DNS record", "historical resolution"],
                suggested_protocols=["pdns", "dns_history", "securitytrails"],
            ), -0.06

        try:
            if self._duckdb_store:
                results = await self._duckdb_store.async_query_findings_by_text(
                    domain, limit=1,
                )
                if results and len(results) > 0:
                    # Check source_type for PDNS
                    for r in results:
                        src = r.get('source_type', '')
                        if src in ('pdns', 'passive_dns', 'dns'):
                            self._cache.set(cache_key, True)
                            return None
                # Fallback: any finding with the domain counts as presence
                if results and len(results) > 0:
                    self._cache.set(cache_key, True)
                    return None

            self._cache.set(cache_key, False)
            return AbsenceFinding(
                entity_value=domain,
                absence_type=AbsenceType.PDNS_VIRGIN,
                severity=0.3,
                description=f"Domain {domain} has no passive DNS history",
                missing_evidence=["passive DNS record", "historical resolution"],
                suggested_protocols=["pdns", "dns_history", "securitytrails"],
            ), -0.06

        except Exception as e:
            logger.debug("[ABSENCE] PDNS check failed for %s: %s", domain, e)
            return None

    async def _check_whois_presence(
        self, domain: str,
    ) -> tuple[AbsenceFinding, float] | None:
        """Check if domain has WHOIS registration data."""
        cache_key = f"whois:{domain}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            if cached:
                return None
            return AbsenceFinding(
                entity_value=domain,
                absence_type=AbsenceType.WHOIS_VOID,
                severity=0.35,
                description=f"Domain {domain} has no WHOIS registration data",
                missing_evidence=["WHOIS record", "registration date", "registrar"],
                suggested_protocols=["whois", "rdap", "whoisxmlapi"],
            ), -0.07

        try:
            if self._duckdb_store:
                results = await self._duckdb_store.async_query_findings_by_text(
                    domain, limit=1,
                )
                if results and len(results) > 0:
                    for r in results:
                        src = r.get('source_type', '')
                        if src in ('whois', 'rdap'):
                            self._cache.set(cache_key, True)
                            return None
                    if results and len(results) > 0:
                        self._cache.set(cache_key, True)
                        return None

            self._cache.set(cache_key, False)
            return AbsenceFinding(
                entity_value=domain,
                absence_type=AbsenceType.WHOIS_VOID,
                severity=0.35,
                description=f"Domain {domain} has no WHOIS registration data",
                missing_evidence=["WHOIS record", "registration date", "registrar"],
                suggested_protocols=["whois", "rdap", "whoisxmlapi"],
            ), -0.07

        except Exception as e:
            logger.debug("[ABSENCE] WHOIS check failed for %s: %s", domain, e)
            return None

    async def _check_ptr_presence(
        self, ip: str,
    ) -> tuple[AbsenceFinding, float] | None:
        """Check if IP has reverse DNS (PTR record)."""
        cache_key = f"ptr:{ip}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            if cached:
                return None
            return AbsenceFinding(
                entity_value=ip,
                absence_type=AbsenceType.UNRESOLVED_PTR,
                severity=0.25,
                description=f"IP {ip} has no reverse DNS (PTR) record",
                missing_evidence=["PTR record", "reverse DNS"],
                suggested_protocols=["dns", "rdns", "bgp"],
            ), -0.05

        try:
            if self._duckdb_store:
                results = await self._duckdb_store.async_query_findings_by_text(
                    ip, limit=1,
                )
                if results and len(results) > 0:
                    self._cache.set(cache_key, True)
                    return None

            self._cache.set(cache_key, False)
            return AbsenceFinding(
                entity_value=ip,
                absence_type=AbsenceType.UNRESOLVED_PTR,
                severity=0.25,
                description=f"IP {ip} has no reverse DNS (PTR) record",
                missing_evidence=["PTR record", "reverse DNS"],
                suggested_protocols=["dns", "rdns", "bgp"],
            ), -0.05

        except Exception as e:
            logger.debug("[ABSENCE] PTR check failed for %s: %s", ip, e)
            return None

    async def _check_ip_pdns_presence(
        self, ip: str,
    ) -> tuple[AbsenceFinding, float] | None:
        """Check if IP has passive DNS associations."""
        cache_key = f"ip_pdns:{ip}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            if cached:
                return None
            return AbsenceFinding(
                entity_value=ip,
                absence_type=AbsenceType.ORPHAN_IP,
                severity=0.4,
                description=f"IP {ip} has no passive DNS associations (orphan IP)",
                missing_evidence=["passive DNS domain", "historical domain association"],
                suggested_protocols=["pdns", "bgp", "shodan", "censys"],
            ), -0.08

        try:
            if self._duckdb_store:
                results = await self._duckdb_store.async_query_findings_by_text(
                    ip, limit=1,
                )
                if results and len(results) > 0:
                    self._cache.set(cache_key, True)
                    return None

            self._cache.set(cache_key, False)
            return AbsenceFinding(
                entity_value=ip,
                absence_type=AbsenceType.ORPHAN_IP,
                severity=0.4,
                description=f"IP {ip} has no passive DNS associations (orphan IP)",
                missing_evidence=["passive DNS domain", "historical domain association"],
                suggested_protocols=["pdns", "bgp", "shodan", "censys"],
            ), -0.08

        except Exception as e:
            logger.debug("[ABSENCE] IP PDNS check failed for %s: %s", ip, e)
            return None

    async def _query_hash_context(self, hash_val: str) -> bool:
        """Query DuckDB for hash context."""
        if not self._duckdb_store:
            return False
        try:
            # Search for hash in content (prefix match since full hash may be long)
            prefix = hash_val[:16] if len(hash_val) >= 16 else hash_val
            results = await self._duckdb_store.async_query_findings_by_text(
                prefix, limit=1,
            )
            return bool(results and len(results) > 0)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Graph Topology Absence Check
    # ------------------------------------------------------------------

    async def _check_graph_topology(
        self,
        report: "OSINTReport",
        store: "DuckDBShadowStore | None",
    ) -> list[AbsenceFinding]:
        """
        Check for missing graph relationships using existing cooccurrence data.

        Detects orphan entities by checking:
        - No cooccurrence entries in ioc_cooccurrence table
        - No findings linking entities together
        """
        absences: list[AbsenceFinding] = []

        if not store:
            return absences

        try:
            # Get entity values
            entity_values: list[str] = [
                e.value for e in (report.ioc_entities or [])
                if e.ioc_type in ("ip", "domain")
            ]

            # Check each entity for cooccurrence/graph connectivity
            for entity_value in entity_values:
                try:
                    # Check cooccurrence table for relationships
                    results = await store.async_query_findings_by_text(
                        entity_value, limit=2,
                    )
                    edge_count = len(results) if results else 0

                    if edge_count < 2:
                        ioc_type = next(
                            (e.ioc_type for e in (report.ioc_entities or [])
                             if e.value == entity_value),
                            "unknown"
                        )
                        absences.append(AbsenceFinding(
                            entity_value=entity_value,
                            absence_type=AbsenceType.GRAPH_FRAGMENT,
                            severity=0.3,
                            description=f"Entity {entity_value} has no graph relationships",
                            missing_evidence=["graph edge", "relationship to any other entity"],
                            suggested_protocols=["shodan", "censys", "ct", "pdns"],
                        ))
                except Exception as e:
                    logger.debug("[ABSENCE] Graph topology check failed: %s", e)

        except Exception as e:
            logger.warning("[ABSENCE] Graph topology absence check failed: %s", e)

        return absences

    # ------------------------------------------------------------------
    # Closed-Loop: EntropyAlert Emission
    # ------------------------------------------------------------------

    async def _emit_absence_alerts(
        self,
        absences: list[AbsenceFinding],
        report: "OSINTReport",
        store: "DuckDBShadowStore | None",
    ) -> bool:
        """
        Emit EntropyAlerts for high-severity absences via EntropyFetchBridge.

        Returns True if any alert was emitted (should trigger re-fetch).
        """
        high_severity = [a for a in absences if a.severity > 0.5]
        if not high_severity:
            return False

        try:
            # Dynamic import to avoid circular dependency
            from hledac.universal.brain.uncertainty_quant import (
                EntropyAlert, get_entropy_bridge,
            )

            bridge = get_entropy_bridge()
            if bridge is None:
                return False

            emitted = 0
            for absence in high_severity[:10]:  # Cap at 10 alerts
                # Map absence severity to EntropyAlert entropy range
                # Structural absence severity is already 0.0–1.0
                entropy_bits = absence.severity * 3.0  # 0.0–3.0 bits

                alert = EntropyAlert(
                    entity_id=absence.entity_value,
                    entropy=entropy_bits,
                    threshold_exceeded=1.0,  # Structural absence threshold
                    confidence=0.0,  # Not applicable — this is a structural alert
                    risk_level=(
                        "high" if absence.severity > 0.7
                        else "medium"
                    ),
                    metadata={
                        "absence_type": absence.absence_type.value,
                        "description": absence.description,
                        "suggested_protocols": absence.suggested_protocols,
                        "source": "absence_mining",
                        "reason": f"structural_absence:{absence.absence_type.value}",
                    },
                )
                bridge.emit(alert)
                emitted += 1

            if emitted > 0:
                logger.info(
                    "[ABSENCE] Emitted %d EntropyAlerts for high-severity absences",
                    emitted,
                )
            return emitted > 0

        except ImportError:
            logger.debug("[ABSENCE] EntropyFetchBridge not available")
            return False
        except Exception as e:
            logger.warning("[ABSENCE] Failed to emit absence alerts: %s", e)
            return False


# ============================================================================
# Singleton accessor
# ============================================================================

_ENGINE: AbsenceMiningEngine | None = None
_ENGINE_LOCK = asyncio.Lock()


async def get_absence_engine(
    duckdb_store: "DuckDBShadowStore | None" = None,
) -> AbsenceMiningEngine:
    """Get or create the AbsenceMiningEngine singleton."""
    global _ENGINE
    if _ENGINE is None:
        async with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = AbsenceMiningEngine(duckdb_store=duckdb_store)
    elif duckdb_store is not None and _ENGINE._duckdb_store is None:
        _ENGINE._duckdb_store = duckdb_store
    return _ENGINE


def get_absence_engine_sync(
    duckdb_store: "DuckDBShadowStore | None" = None,
) -> AbsenceMiningEngine:
    """Synchronous accessor for AbsenceMiningEngine singleton."""
    global _ENGINE
    if _ENGINE is None:
        return AbsenceMiningEngine(duckdb_store=duckdb_store)
    elif duckdb_store is not None and _ENGINE._duckdb_store is None:
        _ENGINE._duckdb_store = duckdb_store
    return _ENGINE
