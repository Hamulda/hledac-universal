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
import logging
import msgspec
import msgspec.json as _json
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from hledac.universal.runtime.hermes_pivot_contract import MAX_INFERENCE_ITEMS, HermesInferenceOutput
from hledac.universal.utils.confidence import normalize_source_quality
__all__ = ['Pivot', 'PivotStats', 'PivotType', 'PivotPlanner', 'MAX_PIVOTS', 'MAX_PIVOT_CANDIDATES', 'generate_pivot_candidates_from_query', 'score_pivot_for_mission', 'estimate_pivot_cost', 'explain_pivot_score', 'apply_scoring_metadata', 'HermesInferenceOutput', 'MAX_INFERENCE_ITEMS']
try:
    from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackSummary
    _HAS_HYPOTHESIS_FEEDBACK = True
except ImportError:
    HypothesisFeedbackSummary = None
    _HAS_HYPOTHESIS_FEEDBACK = False
logger = logging.getLogger(__name__)
MAX_PIVOTS: int = 20
MAX_PIVOT_CANDIDATES: int = 25

class PivotType:
    """Pivot type constants."""
    DOMAIN = 'domain'
    IDENTITY = 'identity'
    LEAK = 'leak'
    ARCHIVE = 'archive'
    GRAPH = 'graph'

class Pivot(msgspec.Struct, frozen=True):
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
    pivot_id: str = field(compare=False, default='')
    pivot_type: str = field(compare=False, default='domain')
    ioc_value: str = field(compare=False, default='')
    ioc_type: str = field(compare=False, default='unknown')
    reason: str = field(compare=False, default='')
    expected_value: float = field(compare=False, default=0.5)
    source_hint: str = field(compare=False, default='')
    evidence_pointers: tuple[str, ...] = field(compare=False, default_factory=tuple)
    score_reason: str = field(compare=False, default='')
    estimated_cost: float = field(compare=False, default=0.5)
    mission_boost: float = field(compare=False, default=1.0)

class PivotStats(msgspec.Struct):
    """
    Tracks pivot usage history for exponential decay scoring.
    Tracks successes/failures so underperforming or stale pivots lose priority.
    """
    pivot_id: str
    success_count: int = 0
    failure_count: int = 0
    last_used: float = 0.0
    decay_rate: float = 0.95
    staleness_threshold: float = 3600.0

    def record_success(self, timestamp: float) -> None:
        """Record a successful pivot use."""
        self.success_count += 1
        self.last_used = timestamp

    def record_failure(self, timestamp: float) -> None:
        """Record a failed pivot use."""
        self.failure_count += 1
        self.last_used = timestamp

    def decayed_score(self, base_score: float, current_time: float) -> float:
        """
        Apply exponential decay to base_score based on usage history.
        Older pivots and failed pivots lose priority.
        """
        if self.last_used == 0.0:
            return base_score
        age = current_time - self.last_used
        time_decay = math.pow(self.decay_rate, age / self.staleness_threshold)
        failure_penalty = 1.0 / (1.0 + self.failure_count * 0.5)
        success_bonus = 1.0 + self.success_count * 0.1
        return base_score * time_decay * failure_penalty * success_bonus

def _extract_ioc_from_finding(finding: Any) -> tuple[str | None, str | None]:
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
    payload = getattr(finding, 'payload_text', None) or ''
    if isinstance(payload, str) and payload:
        url_match = re.search('https?://[^\\s\\"\'<>]+', payload)
        if url_match:
            return (url_match.group(0), 'url')
        email_match = re.search('\\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,})\\b', payload)
        if email_match:
            return (email_match.group(1).lower(), 'email')
        ip_match = re.search('\\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\b', payload)
        if ip_match:
            return (ip_match.group(0), 'ip')
        hash_match = re.search('\\b([a-fA-F0-9]{32,64})\\b', payload)
        if hash_match:
            h = hash_match.group(1).lower()
            if len(h) == 32:
                return (h, 'md5')
            elif len(h) == 40:
                return (h, 'sha1')
            elif len(h) == 64:
                return (h, 'sha256')
        domain_match = re.search('(?:https?://)?([a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?(?:\\.[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?)+)', payload)
        if domain_match:
            return (domain_match.group(1).lower(), 'domain')
    src = getattr(finding, 'source_type', '') or ''
    if src in ('ct_log', 'certificate'):
        query = getattr(finding, 'query', '') or ''
        domain_match = re.search('([a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?(?:\\.[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?)+)', query)
        if domain_match:
            return (domain_match.group(1).lower(), 'domain')
    return (None, None)

def _deserialize_envelope(finding: Any) -> dict | None:
    """Deserialize evidence envelope from finding payload_text."""
    payload = getattr(finding, 'payload_text', None)
    if not payload or not isinstance(payload, str):
        return None
    try:
        env = _json.loads(payload)
        if isinstance(env, dict) and env.get('audit_reason'):
            return env
    except Exception:
        pass
    return None

def _cheap_score_finding(finding: Any, envelope: dict | None) -> float:
    """
    Cheap heuristic scoring without model inference.

    Score based on:
    - confidence: finding confidence [0.0, 1.0]
    - signal_facets: if available, average of facet values
    - source_type: some source types are higher quality
    """
    score = getattr(finding, 'confidence', 0.5) or 0.5
    if envelope and isinstance(envelope, dict):
        facets = envelope.get('signal_facets', {})
        if facets and isinstance(facets, dict):
            facet_values = [v for v in facets.values() if isinstance(v, (int, float))]
            if facet_values:
                score = (score + sum(facet_values) / len(facet_values)) / 2.0
    src = getattr(finding, 'source_type', '') or ''
    high_quality_sources = {'ct_log', 'certificate', 'cisa_kev', 'threatfox_ioc', 'public', 'deep_probe', 'forensics', 'multimodal'}
    if src in high_quality_sources:
        score = min(1.0, score + 0.1)
    return max(0.0, min(1.0, score))

def _graph_stats_available(graph_stats: dict | None) -> bool:
    """
    F238F: Check if graph_stats represents an explicitly available graph.

    graph_stats is None  → graph unavailable (fail-soft fallback)
    graph_stats == {}    → graph unavailable (fail-soft fallback)
    graph_stats has keys → graph explicitly available (even if values are empty)

    Examples:
        None                    → False (graph unavailable)
        {}                      → False (graph unavailable)
        {"domains": set()}      → True  (explicitly empty graph)
        {"domains": {"x.com"}}  → True  (graph with data)
    """
    if not graph_stats:
        return False
    return bool('domains' in graph_stats or 'node_degrees' in graph_stats or 'connected_iocs' in graph_stats or ('existing_domains' in graph_stats))

def _score_pivot_domain(domain: str, confidence: float, envelope: dict | None, graph_stats: dict, source_quality_score: float | None=None) -> float:
    """
    Score a domain pivot based on multiple signals.

    F238A: Uses normalize_source_quality to interpret heterogeneous
    source_quality_score values (0-90 int, 0-1 float, or None).
    Applies degree-weighted noise penalty to high-degree generic domains.

    F238F: Graph bonuses/penalties only apply when graph_stats is explicitly
    available. None and {} both mean "graph unavailable" → no novelty bonus,
    no seen-before penalty, no degree penalty.
    """
    norm = normalize_source_quality(source_quality_score)
    score = norm * 0.6 + confidence * 0.4
    if _graph_stats_available(graph_stats):
        existing_domains = graph_stats.get('domains', [])
        if domain not in existing_domains:
            score += 0.2
        else:
            score -= 0.05
        node_degree = graph_stats.get('node_degrees', {}).get(domain, 0)
        score -= min(0.15, node_degree * 0.01)
        conf_by_node = graph_stats.get('confidence_by_node', {})
        domain_conf = conf_by_node.get(domain, 0.5)
        suspicious_patterns = ('lockbit', 'ransomware', 'apt', 'emotet', 'icedid', 'qakbot')
        is_suspicious = any((p in domain.lower() for p in suspicious_patterns))
        if not is_suspicious:
            if domain_conf >= 0.75 and node_degree < 50:
                score += 0.07
            elif domain_conf <= 0.35 and node_degree > 10:
                score -= 0.07
    if envelope and isinstance(envelope, dict):
        facets = envelope.get('signal_facets', {})
        if isinstance(facets, dict):
            if facets.get('novelty_score', 0) > 0.5:
                score += 0.1
    return min(1.0, max(0.0, score))

def _score_pivot_identity(ioc_value: str, ioc_type: str, confidence: float) -> float:
    """Score an identity pivot based on IOC type and confidence."""
    score = confidence * 0.5
    identity_types = {'email', 'username', 'name', 'handle', 'profile'}
    if ioc_type.lower() in identity_types:
        score += 0.2
    if ioc_type == 'url' and any((x in ioc_value for x in ['github.com', 'twitter.com', 'linkedin.com'])):
        score += 0.25
    return min(1.0, max(0.0, score))

def _score_pivot_leak(ioc_value: str, confidence: float) -> float:
    """Score a leak pivot."""
    score = confidence * 0.7
    if '@' in ioc_value:
        score += 0.15
    return min(1.0, max(0.0, score))

def _score_pivot_archive(domain: str, confidence: float, graph_stats: dict | None=None) -> float:
    """
    Score an archive pivot.

    F238A: Applies degree-weighted noise penalty — high-degree generic domains
    (CDN, registrar, parking) get reduced archive value since their historical
    records are noisy. Suspicious/ransomware-looking domains are NOT penalized.

    F238F: Degree penalty only applies when graph_stats is explicitly available.
    None and {} both mean "graph unavailable" → no degree penalty.
    """
    score = confidence * 0.4
    if graph_stats is not None and _graph_stats_available(graph_stats):
        node_degree = graph_stats.get('node_degrees', {}).get(domain, 0)
        score -= min(0.15, node_degree * 0.01)
        generic_patterns = ('dyndns.', 'no-ip.', 'freedns.', 'duckdns.', 'changeip.', 'hopto.', 'servegame.', 'mydns.', 'afraid.org')
        if any((domain.endswith(f'.{g}') or g in domain for g in generic_patterns)):
            score -= 0.1
        if node_degree > 20 and any((x in domain for x in ['cloudfront', 'akamai', 'fastly', 'cloudflare', 'azureedge', 'googleusercontent', 'googlehosted', 'appspot', 'parking', 'sedo', 'namecheap', 'godaddy', 'registrar', 'forwarded', 'redirect'])):
            score -= 0.1
    return min(1.0, max(0.0, score))

def _score_pivot_graph(ioc_value: str, _ioc_type: str, confidence: float, graph_stats: dict) -> float:
    """
    Score a graph traversal pivot.

    F238F: Graph bonuses only apply when graph_stats is explicitly available.
    None and {} both mean "graph unavailable" → no novelty bonus, no degree bonus.
    """
    score = confidence * 0.5
    if _graph_stats_available(graph_stats):
        connected_iocs = graph_stats.get('connected_iocs', set())
        if ioc_value not in connected_iocs:
            score += 0.2
        node_degree = graph_stats.get('node_degrees', {}).get(ioc_value, 0)
        if node_degree > 5:
            score += 0.15
    return min(1.0, max(0.0, score))
_MISSION_BOOST_RULES: list[tuple[tuple[str, ...], str, float]] = [(('domain', 'archive', 'graph'), 'domain_recon', 1.25), (('domain', 'archive', 'graph'), 'infra_recon', 1.2), (('graph',), 'wallet_recon', 1.3), (('graph',), 'cve_recon', 1.15), (('archive', 'domain', 'graph'), 'cve_recon', 1.1), (('leak', 'identity'), 'person_recon', 1.25)]

def _pivot_type_for_ioc(ioc_type: str) -> str:
    """Map IOC type to primary pivot type."""
    if ioc_type in ('md5', 'sha1', 'sha256', 'hash'):
        return 'graph'
    if ioc_type == 'email':
        return 'leak'
    return 'domain'

def score_pivot_for_mission(pivot: Pivot, mission_intent: str | None) -> float:
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
    if pivot.pivot_type == 'archive':
        return 0.3
    if pivot.pivot_type == 'leak':
        return 0.4
    if pivot.pivot_type == 'identity':
        return 0.5
    if pivot.pivot_type == 'domain':
        return 0.5
    if pivot.pivot_type == 'graph':
        if pivot.ioc_type in ('md5', 'sha1', 'sha256', 'hash'):
            return 0.4
        return 0.6
    return 0.5

def explain_pivot_score(pivot: Pivot, mission_intent: str | None) -> str:
    """
    F225D: Human-readable score explanation for debugging/audit.

    Returns a one-line string describing the score components.
    """
    parts = []
    parts.append(f'base={pivot.expected_value:.2f}')
    if pivot.score_reason:
        parts.append(f'reason={pivot.score_reason}')
    if mission_intent and mission_intent != 'unknown':
        parts.append(f'mission={mission_intent}')
        parts.append(f'boost={pivot.mission_boost:.2f}')
    if pivot.estimated_cost:
        parts.append(f'cost={pivot.estimated_cost:.1f}')
    if pivot.evidence_pointers:
        parts.append(f'evidence={len(pivot.evidence_pointers)}')
    if not pivot.source_hint:
        parts.append('no_source=-0.1')
    return ' | '.join(parts)

def apply_scoring_metadata(pivot: Pivot, mission_intent: str | None=None, base_score: float | None=None) -> Pivot:
    """
    F225D: Apply full scoring metadata to a pivot.

    Mutates score_reason, estimated_cost, mission_boost via replacement
    (frozen dataclass — returns new instance with updated fields).

    Caps final expected_value to [0.0, 1.0].
    """
    score = base_score if base_score is not None else pivot.expected_value
    evidence_boost = 0.0
    if pivot.evidence_pointers:
        evidence_boost = 0.05 * min(len(pivot.evidence_pointers), 3)
    source_penalty = -0.1 if not pivot.source_hint else 0.0
    mission_mult = score_pivot_for_mission(pivot, mission_intent)
    cost_factor = 1.0 + (0.5 - estimate_pivot_cost(pivot)) * 0.2
    final_score = (score + evidence_boost + source_penalty) * mission_mult * cost_factor
    final_score = max(0.0, min(1.0, final_score))
    reason_parts = []
    if evidence_boost > 0:
        reason_parts.append(f'+evidence({evidence_boost:.2f})')
    if source_penalty < 0:
        reason_parts.append(f'no_source({source_penalty:.2f})')
    if mission_mult != 1.0:
        reason_parts.append(f'mission({mission_mult:.2f})')
    if cost_factor != 1.0:
        reason_parts.append(f'cost_factor({cost_factor:.2f})')
    score_reason_str = '; '.join(reason_parts) if reason_parts else 'base'
    return Pivot(priority=pivot.priority, pivot_id=pivot.pivot_id, pivot_type=pivot.pivot_type, ioc_value=pivot.ioc_value, ioc_type=pivot.ioc_type, reason=pivot.reason, expected_value=round(final_score, 3), source_hint=pivot.source_hint, evidence_pointers=pivot.evidence_pointers, score_reason=score_reason_str, estimated_cost=estimate_pivot_cost(pivot), mission_boost=round(mission_mult, 3))

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
    __slots__ = tuple(('_last_error', '_model_lifecycle', '_tot_adapter', '_use_model'))

    def __init__(self, use_model_scoring: bool=False, model_lifecycle_manager: Any | None=None) -> None:
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
        self._tot_adapter = None
        self._last_error: str | None = None

    def _generate_pivots_from_findings(self, findings: list, graph_stats: dict | None=None, feedback_summary: dict | None=None, hermes_boost_map: dict[tuple[str, str, str], float] | None=None, hermes_pivot_info: dict[tuple[str, str, str], dict] | None=None) -> list[Pivot]:
        """
        Issue #17: Single-pass pivot generation from findings.

        Optional hermes_boost_map allows boosting heuristic pivots with Hermes scores
        in a single pass, instead of iterating findings twice.

        Args:
            findings: List of findings to process
            graph_stats: Optional graph statistics for scoring
            feedback_summary: Optional feedback penalties
            hermes_boost_map: Optional Hermes boost map (pivot_key → boost_score)
            hermes_pivot_info: Optional Hermes pivot metadata (pivot_key → info_dict)

        Note: caller handles max_pivots cap via slice after sort.
        """
        graph_stats = graph_stats or {}
        hermes_boost_map = hermes_boost_map or {}
        hermes_pivot_info = hermes_pivot_info or {}
        pivots: list[Pivot] = []
        for finding in findings:
            ioc_value, ioc_type_raw = _extract_ioc_from_finding(finding)
            if not ioc_value:
                continue
            ioc_type = ioc_type_raw or 'unknown'
            envelope = _deserialize_envelope(finding)
            base_score = _cheap_score_finding(finding, envelope)
            new_pivots = self._generate_pivots_for_ioc(ioc_value, ioc_type, base_score, finding, envelope, graph_stats, feedback_summary=feedback_summary)
            for pivot in new_pivots:
                key = (pivot.pivot_type, pivot.ioc_type, pivot.ioc_value)
                if key in hermes_boost_map:
                    boost = hermes_boost_map[key]
                    boosted_value = pivot.expected_value + boost * 0.5
                    object.__setattr__(pivot, 'expected_value', min(1.0, boosted_value))
                    info = hermes_pivot_info.get(key, {})
                    if info:
                        object.__setattr__(pivot, 'source_hint', info.get('source_hint', pivot.source_hint))
                        object.__setattr__(pivot, 'score_reason', info.get('score_reason', pivot.score_reason))
            pivots.extend(new_pivots)
        return pivots

    def plan_pivots(self, findings: list, graph_stats: dict | None=None, max_pivots: int=MAX_PIVOTS, feedback_summary: dict | None=None) -> list[Pivot]:
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
        try:
            pivots = self._generate_pivots_from_findings(findings, graph_stats=graph_stats, feedback_summary=feedback_summary)
            pivots = self._deduplicate_pivots(pivots)
            pivots.sort(key=lambda p: p.expected_value, reverse=True)
            return pivots[:max_pivots]
        except Exception as e:
            logger.debug(f'[F202G] plan_pivots failed: {e}')
            self._last_error = str(e)
            return []

    def get_last_error(self) -> str | None:
        """Return last error message, or None if no error."""
        return self._last_error

    def score_with_hermes_output(self, findings: list, hermes_outputs: list[HermesInferenceOutput], max_pivots: int=MAX_PIVOTS, graph_stats: dict | None=None, mission_intent: str | None=None, feedback_summary: dict | None=None) -> list[Pivot]:
        """
        Sprint F256 + Issue #17: Single-pass Hermes+heuristic pivot scoring.

        OPTIMIZATION: Previously iterated findings TWICE (Hermes path + heuristic
        path). Now builds a Hermes pivot map first, then iterates findings ONCE,
        boosting heuristic pivots with Hermes scores during the single pass.

        When hermes_outputs is non-empty:
        - Primary: extract IOCs/entities from HermesInferenceOutput.key_iocs
          and key_entities to generate pivots with boosted expected_value
        - Secondary: use HermesInferenceOutput.pivot_suggestions directly
        - Fallback: if hermes_outputs empty, fall back to existing heuristic path

        When hermes_outputs is empty:
        - Fall back to plan_pivots() heuristic path

        Bounds:
        - MAX_PIVOTS=20 (unchanged)
        - hermes_outputs capped at MAX_INFERENCE_ITEMS=50
        - Each HermesInferenceOutput key_iocs/key_entities capped at 20 items each
        - Each HermesInferenceOutput pivot_suggestions capped at 10 items each

        Args:
            findings: list of CanonicalFinding objects
            hermes_outputs: list of HermesInferenceOutput from Hermes3Engine
            max_pivots: maximum number of pivots to return (default MAX_PIVOTS=20)
            graph_stats: optional graph statistics for scoring
            mission_intent: optional mission intent string for scoring

        Returns:
            list[Pivot] sorted by priority (highest first)
            Always returns at least [] (fail-safe)
        """
        try:
            outputs = list(hermes_outputs)[:MAX_INFERENCE_ITEMS]
            if not outputs:
                return self.plan_pivots(findings, max_pivots=max_pivots, graph_stats=graph_stats, feedback_summary=feedback_summary)
            hermes_boost_map: dict[tuple[str, str, str], float] = {}
            hermes_pivot_info: dict[tuple[str, str, str], dict] = {}
            for output in outputs:
                base_priority = output.confidence
                for ioc_value in output.key_iocs[:20]:
                    ioc_type = self._ioc_type_from_value(ioc_value)
                    pivot_type = self._pivot_type_for_ioc(ioc_type)
                    key = (pivot_type, ioc_type, ioc_value)
                    hermes_boost_map[key] = max(hermes_boost_map.get(key, 0.0), base_priority)
                    if key not in hermes_pivot_info:
                        hermes_pivot_info[key] = {'source_hint': f'hermes:{output.inference_type}', 'score_reason': f'hermes_{output.inference_type}_confidence:{output.confidence:.2f}'}
                for entity in output.key_entities[:20]:
                    key = (PivotType.IDENTITY, 'entity', entity)
                    hermes_boost_map[key] = max(hermes_boost_map.get(key, 0.0), base_priority * 0.9)
                    if key not in hermes_pivot_info:
                        hermes_pivot_info[key] = {'source_hint': f'hermes:{output.inference_type}', 'score_reason': f'hermes_entity_{output.inference_type}'}
                for suggestion in output.pivot_suggestions[:10]:
                    key = (PivotType.DOMAIN, 'query', suggestion)
                    hermes_boost_map[key] = max(hermes_boost_map.get(key, 0.0), base_priority * 0.85)
                    if key not in hermes_pivot_info:
                        hermes_pivot_info[key] = {'source_hint': f'hermes:{output.inference_type}', 'score_reason': f'hermes_suggestion_{output.inference_type}'}
            try:
                pivots = self._generate_pivots_from_findings(findings, graph_stats=graph_stats, feedback_summary=feedback_summary, hermes_boost_map=hermes_boost_map, hermes_pivot_info=hermes_pivot_info)
                pivots = self._deduplicate_pivots(pivots)
            except Exception as e:
                logger.debug(f'[F256] Single-pass pivot generation failed: {e}')
                pivots = []
                for key, boost in hermes_boost_map.items():
                    pivot_type, ioc_type, ioc_value = key
                    info = hermes_pivot_info.get(key, {})
                    pivots.append(Pivot(priority=-boost, pivot_id=str(uuid.uuid4()), pivot_type=pivot_type, ioc_value=ioc_value, ioc_type=ioc_type, reason=f'LLM-extracted {ioc_type} from Hermes', expected_value=boost, source_hint=info.get('source_hint', 'hermes:fallback'), evidence_pointers=(), score_reason=info.get('score_reason', 'hermes_fallback'), estimated_cost=0.5, mission_boost=1.0))
            if mission_intent:
                for p in pivots:
                    boost = score_pivot_for_mission(p, mission_intent)
                    object.__setattr__(p, 'expected_value', p.expected_value * boost)
            pivots.sort(key=lambda p: p.expected_value, reverse=True)
            return pivots[:max_pivots]
        except Exception as e:
            logger.debug(f'[F256] score_with_hermes_output failed: {e}')
            self._last_error = str(e)
            return []

    def _ioc_type_from_value(self, value: str) -> str:
        """Infer IOC type from value string."""
        if self._looks_like_ip(value):
            return 'ip'
        if self._looks_like_domain(value):
            return 'domain'
        if len(value) == 32:
            return 'md5'
        if len(value) == 40:
            return 'sha1'
        if len(value) == 64:
            return 'sha256'
        if '@' in value:
            return 'email'
        if value.startswith('http://') or value.startswith('https://'):
            return 'url'
        return 'unknown'

    def _looks_like_domain(self, value: str) -> bool:
        """Check if value looks like a domain name."""
        if not value or len(value) > 253:
            return False
        if '/' in value or '@' in value:
            return False
        parts = value.split('.')
        return all((len(p) <= 63 and p and (not p.startswith('-')) and (not p.endswith('-')) for p in parts if p))

    def _looks_like_ip(self, value: str) -> bool:
        """Check if value looks like an IP address."""
        parts = value.split('.')
        if len(parts) != 4:
            return False
        try:
            return all((0 <= int(p) <= 255 for p in parts))
        except (ValueError, TypeError):
            return False

    def _pivot_type_for_ioc(self, ioc_type: str) -> str:
        """Map IOC type to pivot type."""
        mapping = {'domain': PivotType.DOMAIN, 'ip': PivotType.DOMAIN, 'md5': PivotType.GRAPH, 'sha1': PivotType.GRAPH, 'sha256': PivotType.GRAPH, 'hash': PivotType.GRAPH, 'email': PivotType.IDENTITY, 'url': PivotType.ARCHIVE, 'entity': PivotType.IDENTITY, 'unknown': PivotType.GRAPH}
        return mapping.get(ioc_type, PivotType.GRAPH)

    def _generate_pivots_for_ioc(self, ioc_value: str, ioc_type: str, base_score: float, finding: Any, envelope: dict | None, graph_stats: dict, feedback_summary: dict | None=None) -> list[Pivot]:
        """Generate pivots for a single IOC."""
        pivots = []
        fid = getattr(finding, 'finding_id', None) or ''
        if ioc_type == 'domain' or self._looks_like_domain(ioc_value):
            domain = ioc_value if ioc_type == 'domain' else ioc_value
            sqs = getattr(finding, 'source_quality_score', None)
            score = _score_pivot_domain(domain, base_score, envelope, graph_stats, sqs)
            penalty = self._get_feedback_penalty(PivotType.DOMAIN, 'domain', feedback_summary)
            score = score * penalty
            pivots.append(Pivot(priority=-score, pivot_id=str(uuid.uuid4()), pivot_type=PivotType.DOMAIN, ioc_value=domain, ioc_type='domain', reason=f"Domain pivot from {getattr(finding, 'source_type', 'unknown')}", expected_value=score, source_hint=f'finding:{fid}' if fid else 'unknown', evidence_pointers=(fid,) if fid else ()))
            archive_score = _score_pivot_archive(domain, base_score, graph_stats)
            archive_penalty = self._get_feedback_penalty(PivotType.ARCHIVE, 'domain', feedback_summary)
            archive_score = archive_score * archive_penalty
            pivots.append(Pivot(priority=-archive_score, pivot_id=str(uuid.uuid4()), pivot_type=PivotType.ARCHIVE, ioc_value=domain, ioc_type='domain', reason='Archive historical records for domain', expected_value=archive_score, source_hint=f'finding:{fid}' if fid else 'unknown', evidence_pointers=(fid,) if fid else ()))
        elif ioc_type in ('ip', 'ipv4'):
            score = base_score * 0.7
            penalty = self._get_feedback_penalty(PivotType.DOMAIN, 'ip', feedback_summary)
            score = score * penalty
            pivots.append(Pivot(priority=-score, pivot_id=str(uuid.uuid4()), pivot_type=PivotType.DOMAIN, ioc_value=ioc_value, ioc_type='ip', reason='Reverse DNS / domain lookup for IP', expected_value=score, source_hint=f'finding:{fid}' if fid else 'unknown', evidence_pointers=(fid,) if fid else ()))
            graph_score = _score_pivot_graph(ioc_value, ioc_type, base_score, graph_stats)
            graph_penalty = self._get_feedback_penalty(PivotType.GRAPH, 'ip', feedback_summary)
            graph_score = graph_score * graph_penalty
            pivots.append(Pivot(priority=-graph_score, pivot_id=str(uuid.uuid4()), pivot_type=PivotType.GRAPH, ioc_value=ioc_value, ioc_type='ip', reason='Graph traversal from IP IOC', expected_value=graph_score, source_hint=f'finding:{fid}' if fid else 'unknown', evidence_pointers=(fid,) if fid else ()))
        elif ioc_type in ('md5', 'sha1', 'sha256'):
            score = base_score * 0.7
            penalty = self._get_feedback_penalty(PivotType.GRAPH, ioc_type, feedback_summary)
            score = score * penalty
            pivots.append(Pivot(priority=-score, pivot_id=str(uuid.uuid4()), pivot_type=PivotType.GRAPH, ioc_value=ioc_value, ioc_type=ioc_type, reason=f'Threat intelligence lookup for {ioc_type.upper()} hash', expected_value=score, source_hint=f'finding:{fid}' if fid else 'unknown', evidence_pointers=(fid,) if fid else ()))
        elif ioc_type == 'email':
            leak_score = _score_pivot_leak(ioc_value, base_score)
            leak_penalty = self._get_feedback_penalty(PivotType.LEAK, 'email', feedback_summary)
            leak_score = leak_score * leak_penalty
            pivots.append(Pivot(priority=-leak_score, pivot_id=str(uuid.uuid4()), pivot_type=PivotType.LEAK, ioc_value=ioc_value, ioc_type='email', reason='Check email for breach/leak exposure', expected_value=leak_score, source_hint=f'finding:{fid}' if fid else 'unknown', evidence_pointers=(fid,) if fid else ()))
            identity_score = _score_pivot_identity(ioc_value, ioc_type, base_score)
            identity_penalty = self._get_feedback_penalty(PivotType.IDENTITY, 'email', feedback_summary)
            identity_score = identity_score * identity_penalty
            pivots.append(Pivot(priority=-identity_score, pivot_id=str(uuid.uuid4()), pivot_type=PivotType.IDENTITY, ioc_value=ioc_value, ioc_type='email', reason='Identity resolution for email address', expected_value=identity_score, source_hint=f'finding:{fid}' if fid else 'unknown', evidence_pointers=(fid,) if fid else ()))
        elif ioc_type == 'url':
            domain = self._extract_domain_from_url(ioc_value)
            if domain:
                score = base_score * 0.6
                penalty = self._get_feedback_penalty(PivotType.DOMAIN, 'domain', feedback_summary)
                score = score * penalty
                pivots.append(Pivot(priority=-score, pivot_id=str(uuid.uuid4()), pivot_type=PivotType.DOMAIN, ioc_value=domain, ioc_type='domain', reason='Domain extracted from URL', expected_value=score, source_hint=f'finding:{fid}' if fid else 'unknown', evidence_pointers=(fid,) if fid else ()))
            archive_score = _score_pivot_archive(ioc_value, base_score * 0.5, graph_stats)
            archive_penalty = self._get_feedback_penalty(PivotType.ARCHIVE, 'url', feedback_summary)
            archive_score = archive_score * archive_penalty
            pivots.append(Pivot(priority=-archive_score, pivot_id=str(uuid.uuid4()), pivot_type=PivotType.ARCHIVE, ioc_value=ioc_value, ioc_type='url', reason='Archive historical snapshot of URL', expected_value=archive_score, source_hint=f'finding:{fid}' if fid else 'unknown', evidence_pointers=(fid,) if fid else ()))
        return pivots

    def _get_feedback_penalty(self, pivot_type: str, ioc_type: str, feedback_summary: dict | None) -> float:
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
        if hasattr(summary, 'penalty_multiplier'):
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
        if '.' not in value:
            return False
        if re.match('^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$', value):
            return False
        if not re.match('^[a-zA-Z0-9.\\-]+$', value):
            return False
        return True

    def _extract_domain_from_url(self, url: str) -> str | None:
        """Extract domain from URL."""
        match = re.search('https?://([a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?(?:\\.[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?)+)', url)
        if match:
            return match.group(1).lower()
        return None

def _looks_like_ip(s: str) -> bool:
    """Check if string looks like an IP address."""
    if not s:
        return False
    parts = s.split('.')
    if len(parts) != 4:
        return False
    try:
        return all((0 <= int(p) <= 255 for p in parts))
    except (ValueError, TypeError):
        return False

def _looks_like_domain(s: str) -> bool:
    """Check if string looks like a domain name (module-level, no self)."""
    if not s or len(s) < 4:
        return False
    if '.' not in s:
        return False
    if re.match('^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$', s):
        return False
    if not re.match('^[a-zA-Z0-9.\\-]+$', s):
        return False
    return True

def _looks_like_hash(s: str) -> bool:
    """Check if string looks like a hash."""
    if not s:
        return False
    if len(s) not in (32, 40, 64):
        return False
    return bool(re.match('^[a-fA-F0-9]+$', s))

def _looks_like_url(s: str) -> bool:
    """Check if string looks like a URL."""
    return bool(re.match('^https?://', s)) or bool(re.match('^ftp://', s))

def _looks_like_email(s: str) -> bool:
    """Check if string looks like an email address."""
    return '@' in s and '.' in s.split('@')[-1]

def _extract_root_domain(domain: str) -> str:
    """Extract root domain from subdomain."""
    parts = domain.split('.')
    if len(parts) <= 2:
        return domain
    return '.'.join(parts[-2:])

def _query_domain_score(domain: str, base_score: float, graph_stats: dict | None) -> float:
    """
    F238A: Apply degree-weighted penalty to a query-level domain pivot score.

    High-degree generic domains (CDN, registrar, parking, dynamic DNS) are noisy.
    Ransomwar/malware-looking keywords are NOT penalized (suspicious = interesting).

    F238F: All penalties/bonuses only apply when graph_stats is explicitly
    available. None and {} both mean "graph unavailable" → return base_score.
    """
    if not _graph_stats_available(graph_stats):
        return base_score
    gs = graph_stats
    node_degree = gs.get('node_degrees', {}).get(domain, 0)
    existing_domains = gs.get('domains', [])
    score = base_score
    score -= min(0.15, node_degree * 0.01)
    if domain in existing_domains:
        score -= 0.05
    generic_patterns = ('dyndns.', 'no-ip.', 'freedns.', 'duckdns.', 'changeip.', 'hopto.', 'servegame.', 'mydns.', 'afraid.org', 'cloudfront', 'akamai', 'fastly', 'cloudflare', 'azureedge', 'googleusercontent', 'googlehosted', 'appspot', 'parking', 'sedo', 'namecheap', 'godaddy', 'forwarded', 'redirect')
    if any((p in domain for p in generic_patterns)) and node_degree > 5:
        score -= 0.1
    suspicious_patterns = ('lockbit', 'ransomware', 'APT', 'emotet', 'icedid', 'qakbot')
    if any((p.lower() in domain.lower() for p in suspicious_patterns)):
        score += 0.05
    return min(1.0, max(0.0, score))

def generate_pivot_candidates_from_query(query: str, max_candidates: int=MAX_PIVOT_CANDIDATES, mission_intent: str | None=None, graph_stats: dict | None=None) -> list[Pivot]:
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
    ioc_type: str = 'unknown'
    ioc_value: str = query
    if _looks_like_ip(query):
        ioc_type = 'ip'
        ioc_value = query
    elif _looks_like_hash(query):
        h = query.lower()
        if len(h) == 32:
            ioc_type = 'md5'
        elif len(h) == 40:
            ioc_type = 'sha1'
        elif len(h) == 64:
            ioc_type = 'sha256'
        else:
            ioc_type = 'hash'
    elif _looks_like_url(query):
        ioc_type = 'url'
        url_match = re.search('https?://([a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?(?:\\.[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?)+)', query)
        if url_match:
            ioc_value = url_match.group(1).lower()
        else:
            ioc_type = 'unknown'
    elif _looks_like_email(query):
        ioc_type = 'email'
    elif _looks_like_domain(query):
        ioc_type = 'domain'
        ioc_value = query
    else:
        domain_match = re.search('([a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?(?:\\.[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?)+)', query)
        if domain_match:
            ioc_type = 'domain'
            ioc_value = domain_match.group(1).lower()
    if ioc_type == 'unknown':
        return []
    source_hint = 'query:direct'
    if ioc_type == 'domain':
        root_domain = _extract_root_domain(ioc_value)
        root_score = _query_domain_score(root_domain, 0.9, graph_stats)
        candidates.append(Pivot(priority=-root_score, pivot_id=f'{pivot_id_base}-root', pivot_type=PivotType.DOMAIN, ioc_value=root_domain, ioc_type='domain', reason='Root domain extracted from query', expected_value=root_score, source_hint=source_hint, evidence_pointers=()))
        if ioc_value != root_domain:
            www_domain = f'www.{root_domain}'
            www_score = _query_domain_score(www_domain, 0.7, graph_stats)
            candidates.append(Pivot(priority=-www_score, pivot_id=f'{pivot_id_base}-www', pivot_type=PivotType.DOMAIN, ioc_value=www_domain, ioc_type='domain', reason='Common www prefix variant', expected_value=www_score, source_hint=source_hint, evidence_pointers=()))
        candidates.append(Pivot(priority=-0.5, pivot_id=f'{pivot_id_base}-archive', pivot_type=PivotType.ARCHIVE, ioc_value=ioc_value, ioc_type='domain', reason='Archive historical records for domain', expected_value=0.5, source_hint=source_hint, evidence_pointers=()))
    elif ioc_type in ('ip', 'ipv4'):
        candidates.append(Pivot(priority=-0.7, pivot_id=f'{pivot_id_base}-rdns', pivot_type=PivotType.DOMAIN, ioc_value=ioc_value, ioc_type='ip', reason='Reverse DNS / domain lookup for IP', expected_value=0.7, source_hint=source_hint, evidence_pointers=()))
        candidates.append(Pivot(priority=-0.5, pivot_id=f'{pivot_id_base}-graph', pivot_type=PivotType.GRAPH, ioc_value=ioc_value, ioc_type='ip', reason='Graph traversal from IP IOC', expected_value=0.5, source_hint=source_hint, evidence_pointers=()))
    elif ioc_type == 'url' and ioc_value != query:
        candidates.append(Pivot(priority=-0.8, pivot_id=f'{pivot_id_base}-url-domain', pivot_type=PivotType.DOMAIN, ioc_value=ioc_value, ioc_type='domain', reason='Domain extracted from URL', expected_value=0.8, source_hint=source_hint, evidence_pointers=()))
        candidates.append(Pivot(priority=-0.4, pivot_id=f'{pivot_id_base}-url-archive', pivot_type=PivotType.ARCHIVE, ioc_value=query, ioc_type='url', reason='Archive historical snapshot of URL', expected_value=0.4, source_hint=source_hint, evidence_pointers=()))
    elif ioc_type in ('md5', 'sha1', 'sha256', 'hash'):
        candidates.append(Pivot(priority=-0.6, pivot_id=f'{pivot_id_base}-threat', pivot_type=PivotType.GRAPH, ioc_value=ioc_value, ioc_type=ioc_type, reason=f"Threat intelligence lookup for {(ioc_type.upper() if ioc_type != 'hash' else 'hash')} hash", expected_value=0.6, source_hint=source_hint, evidence_pointers=()))
    elif ioc_type == 'email':
        candidates.append(Pivot(priority=-0.7, pivot_id=f'{pivot_id_base}-leak', pivot_type=PivotType.LEAK, ioc_value=ioc_value, ioc_type='email', reason='Check email for breach/leak exposure', expected_value=0.7, source_hint=source_hint, evidence_pointers=()))
        candidates.append(Pivot(priority=-0.5, pivot_id=f'{pivot_id_base}-identity', pivot_type=PivotType.IDENTITY, ioc_value=ioc_value, ioc_type='email', reason='Identity resolution for email address', expected_value=0.5, source_hint=source_hint, evidence_pointers=()))
    candidates.sort(key=lambda p: p.priority)
    if len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]
    if mission_intent and candidates:
        candidates = [apply_scoring_metadata(p, mission_intent) for p in candidates]
        candidates.sort(key=lambda p: p.expected_value, reverse=True)
    return candidates

async def _score_with_model(pivot: Pivot, context: dict, tot_adapter: Any) -> float:
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
        query = f'Evaluate pivot: {pivot.ioc_type}:{pivot.ioc_value} for {pivot.pivot_type} investigation'
        should_use, confidence = tot_adapter.should_activate_tot(query, context)
        if should_use:
            return min(1.0, (pivot.expected_value + confidence) / 2.0)
    except Exception:
        pass
    return pivot.expected_value