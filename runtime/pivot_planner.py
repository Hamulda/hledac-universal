"""
Sprint F202G: Hypothesis-Driven Pivot Planner

Bounded advisory layer that generates next pivots from accepted findings
and envelope facets. Scheduler uses pivots as advisory ordering input,
NOT as new sprint owner.

Bounds:
- MAX_PIVOTS=20 per sprint
- Planner failure never blocks export or sprint
- Model load/unload only via brain.model_lifecycle

Pivot types:
- domain: DNS, WHOIS, passive DNS pivots
- identity: entity resolution, profile correlation
- leak: paste/GitHub/breach signal pivots
- archive: wayback, archive.org historical pivots
- graph: IOC graph traversal pivots

Each pivot output:
- reason: why this pivot is suggested
- expected_value: confidence score [0.0, 1.0]
- source_hint: which finding/envelope triggered this pivot
- evidence_pointers: list of source finding_ids
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "Pivot",
    "PivotType",
    "PivotPlanner",
    "MAX_PIVOTS",
]

logger = logging.getLogger(__name__)

# Bounded: max 20 pivots per sprint
MAX_PIVOTS: int = 20


class PivotType:
    """Pivot type constants."""
    DOMAIN = "domain"
    IDENTITY = "identity"
    LEAK = "leak"
    ARCHIVE = "archive"
    GRAPH = "graph"


@dataclass(frozen=True, order=True)
class Pivot:
    """
    A single investigation pivot derived from findings.

    Fields:
        priority: Order key (negative = higher priority first)
        pivot_type: One of domain/identity/leak/archive/graph
        ioc_value: The IOC value to pivot on
        ioc_type: Type of IOC (ip, domain, hash, email, url, etc.)
        reason: Human-readable justification for this pivot
        expected_value: Confidence score [0.0, 1.0]
        source_hint: Which finding/envelope triggered this pivot
        evidence_pointers: List of source finding_ids
    """
    priority: float = field(compare=True)
    pivot_type: str = field(compare=False, default="domain")
    ioc_value: str = field(compare=False, default="")
    ioc_type: str = field(compare=False, default="unknown")
    reason: str = field(compare=False, default="")
    expected_value: float = field(compare=False, default=0.5)
    source_hint: str = field(compare=False, default="")
    evidence_pointers: tuple[str, ...] = field(compare=False, default_factory=tuple)


# ---------------------------------------------------------------------------
# Finding envelope helpers
# ---------------------------------------------------------------------------

def _extract_domain_from_finding(finding: Any) -> Optional[str]:
    """Extract domain IOC from a finding. DEPRECATED: Use _extract_ioc_from_finding."""
    # Check payload_text for domain-like content
    payload = getattr(finding, "payload_text", None) or ""
    if isinstance(payload, str) and payload:
        # Try to find domain in payload
        domain_match = re.search(
            r"(?:https?://)?([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+)",
            payload,
        )
        if domain_match:
            return domain_match.group(1).lower()

    # Try source_type/ip for IP addresses
    src = getattr(finding, "source_type", "") or ""
    if src in ("ip", "ipv4", "ipv6"):
        return None  # Not a domain

    return None


def _extract_ioc_from_finding(finding: Any) -> tuple[Optional[str], Optional[str]]:
    """
    Extract IOC value and type from a finding.

    Returns (ioc_value, ioc_type) or (None, None).

    Extraction order (most specific first):
    1. URL (has :// prefix)
    2. Email (has @)
    3. IP (specific pattern)
    4. Hash (specific length)
    5. Domain (generic fallback)
    """
    # Try payload_text first
    payload = getattr(finding, "payload_text", None) or ""
    if isinstance(payload, str) and payload:
        # URL pattern - most specific (has :// prefix)
        url_match = re.search(r"https?://[^\s\"'<>]+", payload)
        if url_match:
            return url_match.group(0), "url"

        # Email pattern - has @ delimiter
        email_match = re.search(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b", payload)
        if email_match:
            return email_match.group(1).lower(), "email"

        # IP address pattern
        ip_match = re.search(
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b",
            payload,
        )
        if ip_match:
            return ip_match.group(0), "ip"

        # Hash patterns
        hash_match = re.search(r"\b([a-fA-F0-9]{32,64})\b", payload)
        if hash_match:
            h = hash_match.group(1).lower()
            if len(h) == 32:
                return h, "md5"
            elif len(h) == 40:
                return h, "sha1"
            elif len(h) == 64:
                return h, "sha256"

        # Domain pattern - generic fallback (checked last)
        domain_match = re.search(
            r"(?:https?://)?([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+)",
            payload,
        )
        if domain_match:
            return domain_match.group(1).lower(), "domain"

    # Fallback to source_type as hint
    src = getattr(finding, "source_type", "") or ""
    if src in ("ct_log", "certificate"):
        # Check for domain in query field
        query = getattr(finding, "query", "") or ""
        domain_match = re.search(
            r"([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+)",
            query,
        )
        if domain_match:
            return domain_match.group(1).lower(), "domain"

    return None, None


def _deserialize_envelope(finding: Any) -> Optional[dict]:
    """Deserialize evidence envelope from finding payload_text."""
    payload = getattr(finding, "payload_text", None)
    if not payload or not isinstance(payload, str):
        return None
    try:
        env = json.loads(payload)
        if isinstance(env, dict) and env.get("audit_reason"):
            return env
    except (json.JSONDecodeError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Pivot scoring adapters
# ---------------------------------------------------------------------------

def _cheap_score_finding(finding: Any, envelope: Optional[dict]) -> float:
    """
    Cheap heuristic scoring without model inference.

    Score based on:
    - confidence: finding confidence [0.0, 1.0]
    - signal_facets: if available, average of facet values
    - source_type: some source types are higher quality
    """
    score = getattr(finding, "confidence", 0.5) or 0.5

    # Boost from signal_facets if available
    if envelope and isinstance(envelope, dict):
        facets = envelope.get("signal_facets", {})
        if facets and isinstance(facets, dict):
            facet_values = [v for v in facets.values() if isinstance(v, (int, float))]
            if facet_values:
                score = (score + sum(facet_values) / len(facet_values)) / 2.0

    # Source type quality boost
    src = getattr(finding, "source_type", "") or ""
    high_quality_sources = {
        "ct_log", "certificate", "cisa_kev", "threatfox_ioc",
        "public", "deep_probe", "forensics", "multimodal",
    }
    if src in high_quality_sources:
        score = min(1.0, score + 0.1)

    return max(0.0, min(1.0, score))


def _score_pivot_domain(
    domain: str,
    confidence: float,
    envelope: Optional[dict],
    graph_stats: dict,
) -> float:
    """Score a domain pivot based on multiple signals."""
    score = confidence * 0.6

    # Graph signal: nodes with this domain already in graph reduce novelty
    existing_domains = graph_stats.get("domains", [])
    if domain not in existing_domains:
        score += 0.2  # Novelty bonus

    # Envelope signal_facets boost
    if envelope and isinstance(envelope, dict):
        facets = envelope.get("signal_facets", {})
        if isinstance(facets, dict):
            if facets.get("novelty_score", 0) > 0.5:
                score += 0.1

    return min(1.0, max(0.0, score))


def _score_pivot_identity(
    ioc_value: str,
    ioc_type: str,
    confidence: float,
) -> float:
    """Score an identity pivot based on IOC type and confidence."""
    score = confidence * 0.5

    # Identity-relevant IOC types get boost
    identity_types = {"email", "username", "name", "handle", "profile"}
    if ioc_type.lower() in identity_types:
        score += 0.2

    # URL with potential identity info
    if ioc_type == "url" and any(x in ioc_value for x in ["github.com", "twitter.com", "linkedin.com"]):
        score += 0.25

    return min(1.0, max(0.0, score))


def _score_pivot_leak(
    ioc_value: str,
    confidence: float,
) -> float:
    """Score a leak pivot."""
    # Leaks are generally medium-high value
    score = confidence * 0.7

    # Email in breach is high value
    if "@" in ioc_value:
        score += 0.15

    return min(1.0, max(0.0, score))


def _score_pivot_archive(
    domain: str,
    confidence: float,
) -> float:
    """Score an archive pivot."""
    # Archive is supplementary, medium value
    return confidence * 0.4


def _score_pivot_graph(
    ioc_value: str,
    ioc_type: str,
    confidence: float,
    graph_stats: dict,
) -> float:
    """Score a graph traversal pivot."""
    score = confidence * 0.5

    # Check if IOC is already well-connected in graph
    connected_iocs = graph_stats.get("connected_iocs", set())
    if ioc_value not in connected_iocs:
        score += 0.2  # Novel node

    # High-degree nodes are more valuable for graph traversal
    node_degree = graph_stats.get("node_degrees", {}).get(ioc_value, 0)
    if node_degree > 5:
        score += 0.15

    return min(1.0, max(0.0, score))


# ---------------------------------------------------------------------------
# Pivot Planner
# ---------------------------------------------------------------------------

class PivotPlanner:
    """
    F202G: Hypothesis-driven pivot planner.

    Generates bounded next pivots from accepted findings and envelope facets.
    Advisory only: scheduler uses pivots as ordering input, NOT as sprint owner.

    Bounds:
    - MAX_PIVOTS=20 per sprint
    - Planner failure never blocks export or sprint
    - Model load/unload only via brain.model_lifecycle

    Usage:
        planner = PivotPlanner()
        pivots = planner.plan_pivots(findings, graph_stats=graph_stats)
        for pivot in pivots:
            print(pivot.ioc_value, pivot.pivot_type, pivot.reason)
    """

    def __init__(
        self,
        use_model_scoring: bool = False,
        model_lifecycle_manager: Optional[Any] = None,
    ) -> None:
        """
        Initialize pivot planner.

        Args:
            use_model_scoring: If True, use model-backed scoring via tot_integration.
                              Requires model_lifecycle_manager for model load/unload.
            model_lifecycle_manager: Optional model lifecycle manager for model-backed scoring.
                                   Must be provided if use_model_scoring=True.
        """
        self._use_model = use_model_scoring
        self._model_lifecycle = model_lifecycle_manager
        self._tot_adapter = None  # Lazy-loaded tot_integration
        self._last_error: Optional[str] = None

    # ── Public API ─────────────────────────────────────────────────────────

    def plan_pivots(
        self,
        findings: list,
        graph_stats: Optional[dict] = None,
        max_pivots: int = MAX_PIVOTS,
    ) -> list[Pivot]:
        """
        Generate bounded pivots from accepted findings.

        Args:
            findings: List of CanonicalFinding (or dict-like) objects
            graph_stats: Optional graph statistics for scoring
            max_pivots: Maximum number of pivots to generate (default MAX_PIVOTS=20)

        Returns:
            List of Pivot objects, sorted by priority (highest first).
            Empty list on any error (fail-soft).
        """
        if not findings:
            return []

        graph_stats = graph_stats or {}
        pivots: list[Pivot] = []

        try:
            for finding in findings:
                if len(pivots) >= max_pivots:
                    break

                # Extract IOC
                ioc_value, ioc_type_raw = _extract_ioc_from_finding(finding)
                if not ioc_value:
                    continue
                # Ensure ioc_type is never None
                ioc_type = ioc_type_raw or "unknown"

                # Deserialize envelope if available
                envelope = _deserialize_envelope(finding)

                # Score finding for base confidence
                base_score = _cheap_score_finding(finding, envelope)

                # Generate pivots based on IOC type and finding characteristics
                new_pivots = self._generate_pivots_for_ioc(
                    ioc_value, ioc_type, base_score, finding, envelope, graph_stats
                )
                pivots.extend(new_pivots)

            # Deduplicate by (ioc_type, ioc_value)
            pivots = self._deduplicate_pivots(pivots)

            # Sort by expected_value descending (higher score = higher priority)
            pivots.sort(key=lambda p: p.expected_value, reverse=True)

            # Trim to max_pivots
            return pivots[:max_pivots]

        except Exception as e:
            logger.debug(f"[F202G] plan_pivots failed: {e}")
            self._last_error = str(e)
            return []  # Fail-soft: empty list on error

    def get_last_error(self) -> Optional[str]:
        """Return last error message, or None if no error."""
        return self._last_error

    # ── Internal ───────────────────────────────────────────────────────────

    def _generate_pivots_for_ioc(
        self,
        ioc_value: str,
        ioc_type: str,
        base_score: float,
        finding: Any,
        envelope: Optional[dict],
        graph_stats: dict,
    ) -> list[Pivot]:
        """Generate pivots for a single IOC."""
        pivots = []
        fid = getattr(finding, "finding_id", None) or ""

        # Domain pivots
        if ioc_type == "domain" or self._looks_like_domain(ioc_value):
            domain = ioc_value if ioc_type == "domain" else ioc_value
            score = _score_pivot_domain(domain, base_score, envelope, graph_stats)
            pivots.append(Pivot(
                priority=-score,
                pivot_type=PivotType.DOMAIN,
                ioc_value=domain,
                ioc_type="domain",
                reason=f"Domain pivot from {getattr(finding, 'source_type', 'unknown')}",
                expected_value=score,
                source_hint=f"finding:{fid}" if fid else "unknown",
                evidence_pointers=(fid,) if fid else (),
            ))

            # Archive pivot for domain
            archive_score = _score_pivot_archive(domain, base_score)
            pivots.append(Pivot(
                priority=-archive_score,
                pivot_type=PivotType.ARCHIVE,
                ioc_value=domain,
                ioc_type="domain",
                reason="Archive historical records for domain",
                expected_value=archive_score,
                source_hint=f"finding:{fid}" if fid else "unknown",
                evidence_pointers=(fid,) if fid else (),
            ))

        # IP pivots
        elif ioc_type == "ip":
            # Domain resolution pivot
            score = base_score * 0.7
            pivots.append(Pivot(
                priority=-score,
                pivot_type=PivotType.DOMAIN,
                ioc_value=ioc_value,
                ioc_type="ip",
                reason="Reverse DNS / domain lookup for IP",
                expected_value=score,
                source_hint=f"finding:{fid}" if fid else "unknown",
                evidence_pointers=(fid,) if fid else (),
            ))

            # Graph pivot for IP
            graph_score = _score_pivot_graph(ioc_value, ioc_type, base_score, graph_stats)
            pivots.append(Pivot(
                priority=-graph_score,
                pivot_type=PivotType.GRAPH,
                ioc_value=ioc_value,
                ioc_type="ip",
                reason="Graph traversal from IP IOC",
                expected_value=graph_score,
                source_hint=f"finding:{fid}" if fid else "unknown",
                evidence_pointers=(fid,) if fid else (),
            ))

        # Hash pivots
        elif ioc_type in ("md5", "sha1", "sha256"):
            # VirusTotal/MalwareBazaar pivot
            score = base_score * 0.7
            pivots.append(Pivot(
                priority=-score,
                pivot_type=PivotType.GRAPH,
                ioc_value=ioc_value,
                ioc_type=ioc_type,
                reason=f"Threat intelligence lookup for {ioc_type.upper()} hash",
                expected_value=score,
                source_hint=f"finding:{fid}" if fid else "unknown",
                evidence_pointers=(fid,) if fid else (),
            ))

        # Email pivots
        elif ioc_type == "email":
            # Breach/leak pivot
            leak_score = _score_pivot_leak(ioc_value, base_score)
            pivots.append(Pivot(
                priority=-leak_score,
                pivot_type=PivotType.LEAK,
                ioc_value=ioc_value,
                ioc_type="email",
                reason="Check email for breach/leak exposure",
                expected_value=leak_score,
                source_hint=f"finding:{fid}" if fid else "unknown",
                evidence_pointers=(fid,) if fid else (),
            ))

            # Identity pivot for email
            identity_score = _score_pivot_identity(ioc_value, ioc_type, base_score)
            pivots.append(Pivot(
                priority=-identity_score,
                pivot_type=PivotType.IDENTITY,
                ioc_value=ioc_value,
                ioc_type="email",
                reason="Identity resolution for email address",
                expected_value=identity_score,
                source_hint=f"finding:{fid}" if fid else "unknown",
                evidence_pointers=(fid,) if fid else (),
            ))

        # URL pivots
        elif ioc_type == "url":
            # Domain pivot from URL
            domain = self._extract_domain_from_url(ioc_value)
            if domain:
                score = base_score * 0.6
                pivots.append(Pivot(
                    priority=-score,
                    pivot_type=PivotType.DOMAIN,
                    ioc_value=domain,
                    ioc_type="domain",
                    reason="Domain extracted from URL",
                    expected_value=score,
                    source_hint=f"finding:{fid}" if fid else "unknown",
                    evidence_pointers=(fid,) if fid else (),
                ))

            # Archive pivot for URL
            archive_score = _score_pivot_archive(ioc_value, base_score * 0.5)
            pivots.append(Pivot(
                priority=-archive_score,
                pivot_type=PivotType.ARCHIVE,
                ioc_value=ioc_value,
                ioc_type="url",
                reason="Archive historical snapshot of URL",
                expected_value=archive_score,
                source_hint=f"finding:{fid}" if fid else "unknown",
                evidence_pointers=(fid,) if fid else (),
            ))

        return pivots

    def _deduplicate_pivots(self, pivots: list[Pivot]) -> list[Pivot]:
        """Deduplicate pivots by (pivot_type, ioc_type, ioc_value), keeping highest score per type."""
        seen: dict[tuple[str, str, str], Pivot] = {}
        for pivot in pivots:
            key = (pivot.pivot_type, pivot.ioc_type, pivot.ioc_value)
            if key not in seen or pivot.expected_value > seen[key].expected_value:
                seen[key] = pivot
        return list(seen.values())

    def _looks_like_domain(self, value: str) -> bool:
        """Check if value looks like a domain name."""
        if not value or len(value) < 4:
            return False
        # Has at least one dot
        if "." not in value:
            return False
        # Doesn't look like IP
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", value):
            return False
        # Basic domain charset
        if not re.match(r"^[a-zA-Z0-9.\-]+$", value):
            return False
        return True

    def _extract_domain_from_url(self, url: str) -> Optional[str]:
        """Extract domain from URL."""
        match = re.search(
            r"https?://([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+)",
            url,
        )
        if match:
            return match.group(1).lower()
        return None


# ---------------------------------------------------------------------------
# Model-backed scoring (optional, via tot_integration)
# ---------------------------------------------------------------------------

async def _score_with_model(
    pivot: Pivot,
    context: dict,
    tot_adapter: Any,
) -> float:
    """
    Optional model-backed scoring via tot_integration.

    This is an async function that uses the ToT integration layer
    for deeper analysis. Only called when use_model_scoring=True
    and tot_adapter is available.

    Args:
        pivot: The pivot to score
        context: Context dict with query, findings, etc.
        tot_adapter: TotIntegrationLayer instance

    Returns:
        Enhanced score [0.0, 1.0]
    """
    if tot_adapter is None:
        return pivot.expected_value

    try:
        query = f"Evaluate pivot: {pivot.ioc_type}:{pivot.ioc_value} for {pivot.pivot_type} investigation"
        should_use, confidence = tot_adapter.should_activate_tot(query, context)

        if should_use:
            # Model suggests this is complex, use its confidence
            return min(1.0, (pivot.expected_value + confidence) / 2.0)
    except Exception:
        pass  # Fail-soft: return original score

    return pivot.expected_value
