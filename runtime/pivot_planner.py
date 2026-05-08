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
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "Pivot",
    "PivotType",
    "PivotPlanner",
    "MAX_PIVOTS",
    "MAX_PIVOT_CANDIDATES",
    "generate_pivot_candidates_from_query",
    "score_pivot_for_mission",
    "estimate_pivot_cost",
    "explain_pivot_score",
    "apply_scoring_metadata",
]

# Optional import for F203G feedback — no hard dependency
try:
    from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackSummary
    _HAS_HYPOTHESIS_FEEDBACK = True
except ImportError:
    HypothesisFeedbackSummary = None
    _HAS_HYPOTHESIS_FEEDBACK = False

logger = logging.getLogger(__name__)

# Bounded: max 20 pivots per sprint
MAX_PIVOTS: int = 20
# F216F: Max pivot candidates generated from query (not from findings)
MAX_PIVOT_CANDIDATES: int = 25


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
        pivot_id: Stable unique identifier for this pivot.
        pivot_type: One of domain/identity/leak/archive/graph
        ioc_value: The IOC value to pivot on
        ioc_type: Type of IOC (ip, domain, hash, email, url, etc.)
        reason: Human-readable justification for this pivot
        expected_value: Confidence score [0.0, 1.0]
        source_hint: Which finding/envelope triggered this pivot
        evidence_pointers: List of source finding_ids
    """
    priority: float = field(compare=True)
    pivot_id: str = field(compare=False, default="")
    pivot_type: str = field(compare=False, default="domain")
    ioc_value: str = field(compare=False, default="")
    ioc_type: str = field(compare=False, default="unknown")
    reason: str = field(compare=False, default="")
    expected_value: float = field(compare=False, default=0.5)
    source_hint: str = field(compare=False, default="")
    evidence_pointers: tuple[str, ...] = field(compare=False, default_factory=tuple)
    # F225D: Optional scoring metadata (backward-compatible — all have defaults)
    score_reason: str = field(compare=False, default="")
    estimated_cost: float = field(compare=False, default=0.5)
    mission_boost: float = field(compare=False, default=1.0)


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
    _domain: str,
    confidence: float,
) -> float:
    """Score an archive pivot."""
    # Archive is supplementary, medium value
    return confidence * 0.4


def _score_pivot_graph(
    ioc_value: str,
    _ioc_type: str,
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
# F225D: Mission-aware & evidence-aware scoring helpers
# ---------------------------------------------------------------------------

_MISSION_BOOST_RULES: list[tuple[tuple[str, ...], str, float]] = [
    # (pivot_types), mission_prefix, boost multiplier
    (("domain", "archive", "graph"), "domain_recon", 1.25),
    (("domain", "archive", "graph"), "infra_recon", 1.20),
    (("graph",), "wallet_recon", 1.30),
    (("graph",), "cve_recon", 1.15),
    (("archive", "domain", "graph"), "cve_recon", 1.10),
    (("leak", "identity"), "person_recon", 1.25),
]


def _pivot_type_for_ioc(ioc_type: str) -> str:
    """Map IOC type to primary pivot type."""
    if ioc_type in ("md5", "sha1", "sha256", "hash"):
        return "graph"
    if ioc_type == "email":
        return "leak"
    return "domain"


def score_pivot_for_mission(pivot: Pivot, mission_intent: Optional[str]) -> float:
    """
    F225D: Apply mission-aware boost to a pivot.

    domain_recon  → boosts domain/archive/graph pivots
    wallet_recon  → boosts graph (hash) pivots
    cve_recon     → boosts public/feed/archive pivots
    infra_recon   → boosts IP/domain/graph pivots
    person_recon  → boosts leak/identity pivots
    unknown       → no boost

    Returns multiplier in [0.5, 1.5].
    """
    if not mission_intent:
        return 1.0

    boost = 1.0
    for pivot_types, mission_prefix, multiplier in _MISSION_BOOST_RULES:
        if mission_intent.startswith(mission_prefix) and pivot.pivot_type in pivot_types:
            boost = max(boost, multiplier)
            break

    # Fallback: unknown mission gets no boost
    return max(0.5, min(1.5, boost))


def estimate_pivot_cost(pivot: Pivot) -> float:
    """
    F225D: Estimate relative cost/effort to execute a pivot.

    Returns cost tier:
      0.3 = trivial (archive, passive graph)
      0.5 = moderate (domain WHOIS, passive DNS)
      0.7 = expensive (live crawl, active scan)
      1.0 = very expensive (model-backed inference)
    """
    if pivot.pivot_type == "archive":
        return 0.3
    if pivot.pivot_type == "leak":
        return 0.4
    if pivot.pivot_type == "identity":
        return 0.5
    if pivot.pivot_type == "domain":
        return 0.5
    if pivot.pivot_type == "graph":
        # Hash pivots are cheap, IP domain is moderate
        if pivot.ioc_type in ("md5", "sha1", "sha256", "hash"):
            return 0.4
        return 0.6
    return 0.5


def explain_pivot_score(pivot: Pivot, mission_intent: Optional[str]) -> str:
    """
    F225D: Human-readable score explanation for debugging/audit.

    Returns a one-line string describing the score components.
    """
    parts = []
    parts.append(f"base={pivot.expected_value:.2f}")
    if pivot.score_reason:
        parts.append(f"reason={pivot.score_reason}")
    if mission_intent and mission_intent != "unknown":
        parts.append(f"mission={mission_intent}")
        parts.append(f"boost={pivot.mission_boost:.2f}")
    if pivot.estimated_cost:
        parts.append(f"cost={pivot.estimated_cost:.1f}")
    if pivot.evidence_pointers:
        parts.append(f"evidence={len(pivot.evidence_pointers)}")
    if not pivot.source_hint:
        parts.append("no_source=-0.1")
    return " | ".join(parts)


def apply_scoring_metadata(
    pivot: Pivot,
    mission_intent: Optional[str] = None,
    base_score: Optional[float] = None,
) -> Pivot:
    """
    F225D: Apply full scoring metadata to a pivot.

    Mutates score_reason, estimated_cost, mission_boost via replacement
    (frozen dataclass — returns new instance with updated fields).

    Caps final expected_value to [0.0, 1.0].
    """
    score = base_score if base_score is not None else pivot.expected_value

    # Evidence boost: having evidence_pointers is a positive signal
    evidence_boost = 0.0
    if pivot.evidence_pointers:
        evidence_boost = 0.05 * min(len(pivot.evidence_pointers), 3)  # max +0.15

    # No source_hint penalty
    source_penalty = -0.1 if not pivot.source_hint else 0.0

    # Mission boost
    mission_mult = score_pivot_for_mission(pivot, mission_intent)

    # Cost factor (cheaper pivots get slight bump)
    cost_factor = 1.0 + (0.5 - estimate_pivot_cost(pivot)) * 0.2

    # Final score
    final_score = (score + evidence_boost + source_penalty) * mission_mult * cost_factor
    final_score = max(0.0, min(1.0, final_score))

    reason_parts = []
    if evidence_boost > 0:
        reason_parts.append(f"+evidence({evidence_boost:.2f})")
    if source_penalty < 0:
        reason_parts.append(f"no_source({source_penalty:.2f})")
    if mission_mult != 1.0:
        reason_parts.append(f"mission({mission_mult:.2f})")
    if cost_factor != 1.0:
        reason_parts.append(f"cost_factor({cost_factor:.2f})")
    score_reason_str = "; ".join(reason_parts) if reason_parts else "base"

    return Pivot(
        priority=pivot.priority,
        pivot_id=pivot.pivot_id,
        pivot_type=pivot.pivot_type,
        ioc_value=pivot.ioc_value,
        ioc_type=pivot.ioc_type,
        reason=pivot.reason,
        expected_value=round(final_score, 3),
        source_hint=pivot.source_hint,
        evidence_pointers=pivot.evidence_pointers,
        score_reason=score_reason_str,
        estimated_cost=estimate_pivot_cost(pivot),
        mission_boost=round(mission_mult, 3),
    )


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
        feedback_summary: Optional[dict] = None,
    ) -> list[Pivot]:
        """
        Generate bounded pivots from accepted findings.

        Args:
            findings: List of CanonicalFinding (or dict-like) objects
            graph_stats: Optional graph statistics for scoring
            max_pivots: Maximum number of pivots to generate (default MAX_PIVOTS=20)
            feedback_summary: Optional dict mapping (pivot_type, ioc_type) to
                           HypothesisFeedbackSummary for scoring penalties (F203G).
                           If None or empty, no penalty is applied.

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
                    ioc_value, ioc_type, base_score, finding, envelope, graph_stats,
                    feedback_summary=feedback_summary,
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
        feedback_summary: Optional[dict] = None,
    ) -> list[Pivot]:
        """Generate pivots for a single IOC."""
        pivots = []
        fid = getattr(finding, "finding_id", None) or ""

        # Domain pivots
        if ioc_type == "domain" or self._looks_like_domain(ioc_value):
            domain = ioc_value if ioc_type == "domain" else ioc_value
            score = _score_pivot_domain(domain, base_score, envelope, graph_stats)
            penalty = self._get_feedback_penalty(PivotType.DOMAIN, "domain", feedback_summary)
            score = score * penalty
            pivots.append(Pivot(
                priority=-score,
                pivot_id=str(uuid.uuid4()),
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
            archive_penalty = self._get_feedback_penalty(PivotType.ARCHIVE, "domain", feedback_summary)
            archive_score = archive_score * archive_penalty
            pivots.append(Pivot(
                priority=-archive_score,
                pivot_id=str(uuid.uuid4()),
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
            penalty = self._get_feedback_penalty(PivotType.DOMAIN, "ip", feedback_summary)
            score = score * penalty
            pivots.append(Pivot(
                priority=-score,
                pivot_id=str(uuid.uuid4()),
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
            graph_penalty = self._get_feedback_penalty(PivotType.GRAPH, "ip", feedback_summary)
            graph_score = graph_score * graph_penalty
            pivots.append(Pivot(
                priority=-graph_score,
                pivot_id=str(uuid.uuid4()),
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
            penalty = self._get_feedback_penalty(PivotType.GRAPH, ioc_type, feedback_summary)
            score = score * penalty
            pivots.append(Pivot(
                priority=-score,
                pivot_id=str(uuid.uuid4()),
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
            leak_penalty = self._get_feedback_penalty(PivotType.LEAK, "email", feedback_summary)
            leak_score = leak_score * leak_penalty
            pivots.append(Pivot(
                priority=-leak_score,
                pivot_id=str(uuid.uuid4()),
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
            identity_penalty = self._get_feedback_penalty(PivotType.IDENTITY, "email", feedback_summary)
            identity_score = identity_score * identity_penalty
            pivots.append(Pivot(
                priority=-identity_score,
                pivot_id=str(uuid.uuid4()),
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
                penalty = self._get_feedback_penalty(PivotType.DOMAIN, "domain", feedback_summary)
                score = score * penalty
                pivots.append(Pivot(
                    priority=-score,
                    pivot_id=str(uuid.uuid4()),
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
            archive_penalty = self._get_feedback_penalty(PivotType.ARCHIVE, "url", feedback_summary)
            archive_score = archive_score * archive_penalty
            pivots.append(Pivot(
                priority=-archive_score,
                pivot_id=str(uuid.uuid4()),
                pivot_type=PivotType.ARCHIVE,
                ioc_value=ioc_value,
                ioc_type="url",
                reason="Archive historical snapshot of URL",
                expected_value=archive_score,
                source_hint=f"finding:{fid}" if fid else "unknown",
                evidence_pointers=(fid,) if fid else (),
            ))

        return pivots

    def _get_feedback_penalty(
        self,
        pivot_type: str,
        ioc_type: str,
        feedback_summary: Optional[dict],
    ) -> float:
        """
        F203G: Get penalty multiplier for a pivot type + ioc type combination.

        Returns 1.0 (no penalty) if no feedback exists or feedback module unavailable.
        """
        if not feedback_summary:
            return 1.0
        key = (pivot_type, ioc_type)
        if key not in feedback_summary:
            return 1.0
        summary = feedback_summary[key]
        if hasattr(summary, "penalty_multiplier"):
            return float(summary.penalty_multiplier)
        return 1.0

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
# F216F: Pivot candidate generation from query string (no findings needed)
# ---------------------------------------------------------------------------

def _looks_like_ip(s: str) -> bool:
    """Check if string looks like an IP address."""
    if not s:
        return False
    parts = s.split('.')
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except (ValueError, TypeError):
        return False


def _looks_like_domain(s: str) -> bool:
    """Check if string looks like a domain name (module-level, no self)."""
    if not s or len(s) < 4:
        return False
    if "." not in s:
        return False
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", s):
        return False
    if not re.match(r"^[a-zA-Z0-9.\-]+$", s):
        return False
    return True


def _looks_like_hash(s: str) -> bool:
    """Check if string looks like a hash."""
    if not s:
        return False
    if len(s) not in (32, 40, 64):
        return False
    return bool(re.match(r'^[a-fA-F0-9]+$', s))


def _looks_like_url(s: str) -> bool:
    """Check if string looks like a URL."""
    return bool(re.match(r'^https?://', s)) or bool(re.match(r'^ftp://', s))


def _looks_like_email(s: str) -> bool:
    """Check if string looks like an email address."""
    return '@' in s and '.' in s.split('@')[-1]


def _extract_root_domain(domain: str) -> str:
    """Extract root domain from subdomain."""
    parts = domain.split('.')
    if len(parts) <= 2:
        return domain
    # Strip leading subdomains, keep last 2 parts
    return '.'.join(parts[-2:])


def generate_pivot_candidates_from_query(
    query: str,
    max_candidates: int = MAX_PIVOT_CANDIDATES,
    mission_intent: Optional[str] = None,
) -> list[Pivot]:
    """
    [F216F] Generate bounded pivot candidates from a query string.

    This is the FIRST-CLASS pivot executor entry point: given only a query
    (no findings needed), generate diagnostic pivot candidates that can be
    used even when no lane accepts the query.

    F225D: Added mission_intent parameter for mission-aware scoring.
    When provided, applies mission_boost and score_reason to each pivot.

    Generation rules (NO network, NO brute-force):
    - domain: root domain, www prefix variant, archive pivot
    - IP: reverse DNS domain pivot, graph pivot
    - URL: extract domain and generate domain/archive pivots
    - Hash: graph pivot
    - Email: leak pivot, identity pivot
    - unknown: no pivots generated

    Args:
        query: The input query string
        max_candidates: Maximum number of candidates (default MAX_PIVOT_CANDIDATES=25)
        mission_intent: Optional mission intent string (e.g. "domain_recon", "wallet_recon")
                      for mission-aware scoring. None = no boost.

    Returns:
        List of Pivot objects, sorted by priority (highest first).
        Empty list if query type is not pivotable or is None.
    """
    if not query or not isinstance(query, str):
        return []

    query = query.strip()
    if not query:
        return []

    candidates: list[Pivot] = []
    pivot_id_base = str(uuid.uuid4())[:8]

    # Determine IOC type from query
    ioc_type: str = "unknown"
    ioc_value: str = query

    if _looks_like_ip(query):
        ioc_type = "ip"
        ioc_value = query
    elif _looks_like_hash(query):
        # Determine hash type by length
        h = query.lower()
        if len(h) == 32:
            ioc_type = "md5"
        elif len(h) == 40:
            ioc_type = "sha1"
        elif len(h) == 64:
            ioc_type = "sha256"
        else:
            ioc_type = "hash"
    elif _looks_like_url(query):
        ioc_type = "url"
        # Extract domain from URL
        url_match = re.search(
            r'https?://([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+)',
            query,
        )
        if url_match:
            ioc_value = url_match.group(1).lower()
        else:
            ioc_type = "unknown"
    elif _looks_like_email(query):
        ioc_type = "email"
    elif _looks_like_domain(query):
        ioc_type = "domain"
        ioc_value = query
    else:
        # Unknown type - try to extract something
        # Check for domain-like pattern
        domain_match = re.search(
            r'([a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+)',
            query,
        )
        if domain_match:
            ioc_type = "domain"
            ioc_value = domain_match.group(1).lower()

    if ioc_type == "unknown":
        return []

    # Generate pivots based on IOC type
    source_hint = "query:direct"

    # Domain pivots
    if ioc_type == "domain":
        # Root domain pivot
        root_domain = _extract_root_domain(ioc_value)
        candidates.append(Pivot(
            priority=-0.9,
            pivot_id=f"{pivot_id_base}-root",
            pivot_type=PivotType.DOMAIN,
            ioc_value=root_domain,
            ioc_type="domain",
            reason="Root domain extracted from query",
            expected_value=0.9,
            source_hint=source_hint,
            evidence_pointers=(),
        ))

        # If original was subdomain, also add www variant
        if ioc_value != root_domain:
            # Try www.{root_domain}
            www_domain = f"www.{root_domain}"
            candidates.append(Pivot(
                priority=-0.7,
                pivot_id=f"{pivot_id_base}-www",
                pivot_type=PivotType.DOMAIN,
                ioc_value=www_domain,
                ioc_type="domain",
                reason="Common www prefix variant",
                expected_value=0.7,
                source_hint=source_hint,
                evidence_pointers=(),
            ))

        # Archive pivot for domain
        candidates.append(Pivot(
            priority=-0.5,
            pivot_id=f"{pivot_id_base}-archive",
            pivot_type=PivotType.ARCHIVE,
            ioc_value=ioc_value,
            ioc_type="domain",
            reason="Archive historical records for domain",
            expected_value=0.5,
            source_hint=source_hint,
            evidence_pointers=(),
        ))

    # IP pivots
    elif ioc_type == "ip":
        # Domain pivot for IP (reverse DNS hint)
        candidates.append(Pivot(
            priority=-0.7,
            pivot_id=f"{pivot_id_base}-rdns",
            pivot_type=PivotType.DOMAIN,
            ioc_value=ioc_value,
            ioc_type="ip",
            reason="Reverse DNS / domain lookup for IP",
            expected_value=0.7,
            source_hint=source_hint,
            evidence_pointers=(),
        ))

        # Graph pivot for IP
        candidates.append(Pivot(
            priority=-0.5,
            pivot_id=f"{pivot_id_base}-graph",
            pivot_type=PivotType.GRAPH,
            ioc_value=ioc_value,
            ioc_type="ip",
            reason="Graph traversal from IP IOC",
            expected_value=0.5,
            source_hint=source_hint,
            evidence_pointers=(),
        ))

    # URL pivots
    elif ioc_type == "url" and ioc_value != query:
        # Domain pivot from URL
        candidates.append(Pivot(
            priority=-0.8,
            pivot_id=f"{pivot_id_base}-url-domain",
            pivot_type=PivotType.DOMAIN,
            ioc_value=ioc_value,
            ioc_type="domain",
            reason="Domain extracted from URL",
            expected_value=0.8,
            source_hint=source_hint,
            evidence_pointers=(),
        ))

        # Archive pivot for URL
        candidates.append(Pivot(
            priority=-0.4,
            pivot_id=f"{pivot_id_base}-url-archive",
            pivot_type=PivotType.ARCHIVE,
            ioc_value=query,
            ioc_type="url",
            reason="Archive historical snapshot of URL",
            expected_value=0.4,
            source_hint=source_hint,
            evidence_pointers=(),
        ))

    # Hash pivots
    elif ioc_type in ("md5", "sha1", "sha256", "hash"):
        candidates.append(Pivot(
            priority=-0.6,
            pivot_id=f"{pivot_id_base}-threat",
            pivot_type=PivotType.GRAPH,
            ioc_value=ioc_value,
            ioc_type=ioc_type,
            reason=f"Threat intelligence lookup for {ioc_type.upper() if ioc_type != 'hash' else 'hash'} hash",
            expected_value=0.6,
            source_hint=source_hint,
            evidence_pointers=(),
        ))

    # Email pivots
    elif ioc_type == "email":
        # Leak pivot
        candidates.append(Pivot(
            priority=-0.7,
            pivot_id=f"{pivot_id_base}-leak",
            pivot_type=PivotType.LEAK,
            ioc_value=ioc_value,
            ioc_type="email",
            reason="Check email for breach/leak exposure",
            expected_value=0.7,
            source_hint=source_hint,
            evidence_pointers=(),
        ))

        # Identity pivot
        candidates.append(Pivot(
            priority=-0.5,
            pivot_id=f"{pivot_id_base}-identity",
            pivot_type=PivotType.IDENTITY,
            ioc_value=ioc_value,
            ioc_type="email",
            reason="Identity resolution for email address",
            expected_value=0.5,
            source_hint=source_hint,
            evidence_pointers=(),
        ))

    # Sort by priority (highest first, since priority is negative)
    candidates.sort(key=lambda p: p.priority)

    # Enforce bound
    if len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]

    # F225D: Apply mission-aware scoring metadata if mission_intent provided
    if mission_intent and candidates:
        candidates = [apply_scoring_metadata(p, mission_intent) for p in candidates]
        # Re-sort after scoring (mission boost may reorder)
        candidates.sort(key=lambda p: p.expected_value, reverse=True)

    return candidates


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
