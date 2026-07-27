"""
ClaimsCoordinator - Delegates claims pipeline to coordinator
==========================================================

Implements the stable coordinator interface (start/step/shutdown) for:
- Claim extraction from evidence
- ClaimClusterIndex updates
- Stance scoring and veracity updates

This enables the orchestrator to become a thin "spine" that delegates
claims logic to this coordinator.
"""
import logging
import re
from collections import deque
from dataclasses import dataclass
import msgspec
from typing import Any
from hledac.universal.intel.confidence_policy import compute_confidence
from .base import UniversalCoordinator
from hledac.universal.utils.async_helpers import parallel
logger = logging.getLogger(__name__)
MAX_UNCERTAIN_CLUSTERS = 10
MAX_PENDING_EVIDENCE_IDS = 10000
MAX_CLAIMS_PER_EVIDENCE = 20
MAX_SENTENCE_LENGTH = 512
BASE_CONFIDENCE = 0.45
URL_BONUS = 0.1
PROVENANCE_BONUS = 0.1
TITLE_AGREEMENT_BONUS = 0.1
MAX_CONFIDENCE = 0.75

class ClaimsCoordinatorConfig(msgspec.Struct, gc=False):
    """Configuration for ClaimsCoordinator."""
    max_evidence_per_step: int = 10
    max_clusters_per_step: int = 20
    enable_stance_update: bool = True
    enable_veracity_update: bool = True

class ClaimsCoordinator(UniversalCoordinator):
    """
    Coordinator for claims pipeline delegation.

    Responsibilities:
    - Extract claims from new evidence
    - Update ClaimClusterIndex
    - Update stance scores and veracity priors
    - Return bounded outputs (cluster counts, uncertain IDs)
    """
    __slots__ = tuple(('_claims_extracted_count', '_claims_extraction_packets_seen', '_claims_extraction_packets_with_claims', '_claims_low_confidence_count', '_claims_negative_count', '_claims_neutral_count', '_claims_positive_count', '_clusters_updated', '_config', '_ctx', '_evidence_processed', '_orchestrator', '_pending_evidence_ids', '_pending_evidence_set', '_stop_reason', '_uncertain_clusters'))

    def __init__(self, config: ClaimsCoordinatorConfig | None=None, max_concurrent: int=3):
        super().__init__(name='ClaimsCoordinator', max_concurrent=max_concurrent)
        self._config = config or ClaimsCoordinatorConfig()
        self._pending_evidence_ids: deque = deque(maxlen=MAX_PENDING_EVIDENCE_IDS)
        self._pending_evidence_set: set[str] = set()
        self._clusters_updated: int = 0
        self._evidence_processed: int = 0
        self._uncertain_clusters: list[str] = []
        self._stop_reason: str | None = None
        self._claims_extracted_count: int = 0
        self._claims_positive_count: int = 0
        self._claims_negative_count: int = 0
        self._claims_neutral_count: int = 0
        self._claims_low_confidence_count: int = 0
        self._claims_extraction_packets_seen: int = 0
        self._claims_extraction_packets_with_claims: int = 0
        self._orchestrator: Any | None = None
        self._ctx: dict[str, Any] = {}

    def get_supported_operations(self) -> list[Any]:
        """Return supported operation types."""
        from .base import OperationType
        return [OperationType.SYNTHESIS, OperationType.RESEARCH]

    async def handle_request(self, operation_ref: str, decision: Any) -> Any:  # noqa: ARG001
        """
        Handle a decision request (required by UniversalCoordinator base).

        For spine pattern, we use start/step/shutdown instead.
        This is a compatibility method.
        """
        result = await self.step({'decision': decision})
        return result

    async def _do_initialize(self) -> bool:
        """Initialize coordinator."""
        logger.info('ClaimsCoordinator initialized')
        return True

    async def _do_start(self, ctx: dict[str, Any]) -> None:
        """
        Start coordinator with context from orchestrator.

        Expected ctx keys:
        - pending_evidence: list[str] - evidence IDs to process
        - orchestrator: reference to orchestrator instance
        - claim_index: ClaimClusterIndex instance
        """
        self._ctx = ctx
        self._orchestrator = ctx.get('orchestrator')
        if 'pending_evidence' in ctx:
            items = list(ctx['pending_evidence'])[-MAX_PENDING_EVIDENCE_IDS:]
            self._pending_evidence_ids = deque(items, maxlen=MAX_PENDING_EVIDENCE_IDS)
            self._pending_evidence_set = set(items)
        logger.info(f'ClaimsCoordinator started with {len(self._pending_evidence_ids)} pending evidence')

    async def _do_step(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Execute one claims processing step.

        Process up to max_evidence_per_step from pending evidence.
        Returns bounded output with cluster updates.
        """
        self._ctx.update(ctx)
        new_evidence = ctx.get('new_evidence_ids', [])
        for evidence_id in new_evidence:
            if evidence_id not in self._pending_evidence_set:
                self._pending_evidence_set.add(evidence_id)
                self._pending_evidence_ids.append(evidence_id)
        if not self._pending_evidence_ids:
            self._stop_reason = 'no_pending_evidence'
            return self._get_step_result()
        evidence_to_process = []
        for _ in range(min(self._config.max_evidence_per_step, len(self._pending_evidence_ids))):
            if self._pending_evidence_ids:
                eid = self._pending_evidence_ids.popleft()
                self._pending_evidence_set.discard(eid)
                evidence_to_process.append(eid)
        clusters_updated = 0
        uncertain_clusters = []
        coros: list = [self._process_evidence(e) for e in evidence_to_process]
        raw = await parallel(coros, concurrency=self._max_concurrent, policy="collect", ctx="claims_coordinator.step")
        for result in raw.ok:
            if result:
                self._evidence_processed += 1
                clusters_updated += result.get('clusters_updated', 0)
                uncertain = result.get('uncertain_clusters', [])
                uncertain_clusters.extend(uncertain)
        self._clusters_updated += clusters_updated
        self._uncertain_clusters = (self._uncertain_clusters + uncertain_clusters)[:MAX_UNCERTAIN_CLUSTERS]
        return self._get_step_result(clusters_updated, uncertain_clusters)

    def _get_step_result(self, clusters_updated: int=0, uncertain_clusters: list[str] | None=None) -> dict[str, Any]:
        """Get bounded step result."""
        return {'clusters_updated': clusters_updated, 'evidence_processed': self._evidence_processed, 'total_clusters_updated': self._clusters_updated, 'uncertain_clusters': (uncertain_clusters or [])[:MAX_UNCERTAIN_CLUSTERS], 'stop_reason': self._stop_reason, 'pending_evidence': len(self._pending_evidence_ids)}

    async def _process_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        """
        Process a single evidence ID for claims.

        Delegates to orchestrator's claim extraction methods.
        """
        if not self._orchestrator:
            logger.warning(f'ClaimsCoordinator: no orchestrator reference for {evidence_id}')
            return None
        try:
            claim_index = None
            if hasattr(self._orchestrator, '_research_mgr'):
                rm = self._orchestrator._research_mgr
                if hasattr(rm, '_claim_index'):
                    claim_index = rm._claim_index
            if not claim_index:
                logger.warning('ClaimsCoordinator: no claim_index available')
                return None
            evidence_packet = self._load_evidence_packet(evidence_id)
            if not evidence_packet:
                return None
            claims = await self._extract_claims(evidence_packet)
            self._claims_extraction_packets_seen += 1
            if claims:
                self._claims_extraction_packets_with_claims += 1
                self._claims_extracted_count += len(claims)
                for claim in claims:
                    pol = claim.get('polarity', 'neutral')
                    if pol == 'positive':
                        self._claims_positive_count += 1
                    elif pol == 'negative':
                        self._claims_negative_count += 1
                    else:
                        self._claims_neutral_count += 1
                    if claim.get('confidence', 1.0) < BASE_CONFIDENCE + 0.05:
                        self._claims_low_confidence_count += 1
            if not claims:
                return None
            uncertain = []
            for claim in claims:
                cluster_id = claim_index.add_claim(evidence_id=evidence_id, claim_text=claim.get('text', ''), polarity=claim.get('polarity', 'neutral'), domain=evidence_packet.get('domain', 'unknown'))
                if cluster_id:
                    cluster = claim_index.get_cluster(cluster_id)
                    if cluster and len(cluster.evidence_ids) < 3:
                        uncertain.append(cluster_id)
            return {'clusters_updated': len(claims), 'uncertain_clusters': uncertain}
        except Exception as e:
            logger.warning(f'ClaimsCoordinator: failed to process {evidence_id}: {e}')
            return None

    def _load_evidence_packet(self, evidence_id: str) -> dict[str, Any] | None:
        """Load evidence packet from disk (RAM-safe)."""
        if not self._orchestrator:
            return None
        try:
            if hasattr(self._orchestrator, '_research_mgr'):
                rm = self._orchestrator._research_mgr
                if hasattr(rm, '_evidence_packet_storage'):
                    storage = rm._evidence_packet_storage
                    return storage.load_packet(evidence_id)
            return None
        except Exception as e:
            logger.debug(f'ClaimsCoordinator: failed to load packet {evidence_id}: {e}')
            return None

    async def _extract_claims(self, evidence_packet: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Deterministic claim extraction from evidence packet.

        Safety: no network, no MLX, no blocking I/O. Malformed packet returns [].
        """
        if not evidence_packet or not isinstance(evidence_packet, dict):
            return []
        claims = []
        json_claims = self._extract_json_claims(evidence_packet)
        if json_claims:
            return json_claims[:MAX_CLAIMS_PER_EVIDENCE]
        text_content = self._extract_text_content(evidence_packet)
        if not text_content:
            return []
        sentences = self._split_into_sentences(text_content)
        title = evidence_packet.get('title', '') or ''
        summary = evidence_packet.get('summary', '') or ''
        for sentence in sentences:
            if not sentence or len(sentence) > MAX_SENTENCE_LENGTH:
                continue
            if len(sentence) < 20:
                continue
            polarity = self._derive_polarity(sentence)
            confidence = self._derive_confidence(sentence, evidence_packet, title, summary)
            claims.append({'text': sentence, 'polarity': polarity, 'confidence': confidence, 'source': 'deterministic_claim_extractor', 'evidence_type': evidence_packet.get('type') or evidence_packet.get('evidence_type')})
            if len(claims) >= MAX_CLAIMS_PER_EVIDENCE:
                break
        return claims

    def _extract_json_claims(self, evidence_packet: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract claims from JSON claims field if present."""
        claims_field = evidence_packet.get('claims')
        if isinstance(claims_field, list) and claims_field:
            result = []
            for item in claims_field:
                if isinstance(item, dict) and 'text' in item:
                    result.append({'text': item['text'], 'polarity': item.get('polarity', 'neutral'), 'confidence': min(item.get('confidence', BASE_CONFIDENCE), MAX_CONFIDENCE), 'source': 'deterministic_claim_extractor', 'evidence_type': evidence_packet.get('type') or evidence_packet.get('evidence_type')})
            return result
        return []

    def _extract_text_content(self, evidence_packet: dict[str, Any]) -> str:
        """Extract text content from evidence packet fields."""
        fields = ['claim', 'claims', 'title', 'summary', 'text', 'payload_text', 'content']
        parts = []
        for field in fields:
            value = evidence_packet.get(field)
            if not value:
                continue
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        parts.append(item.strip())
        return ' '.join(parts)

    def _split_into_sentences(self, text: str) -> list[str]:
        """Split text into sentence-like units deterministically."""
        text = ' '.join(text.split())
        sentences = re.split('(?<=[.!?])\\s+(?=[A-Z])', text)
        result = []
        for s in sentences:
            s = s.strip()
            if s and len(s) >= 20:
                result.append(s)
        return result

    def _derive_polarity(self, text: str) -> str:
        """Derive polarity from text heuristics."""
        text_lower = text.lower()
        negative_indicators = ['not', 'no evidence', 'false', 'denies', 'debunked', 'failed to']
        for indicator in negative_indicators:
            if indicator in text_lower:
                return 'negative'
        positive_indicators = ['confirmed', 'observed', 'detected', 'reported', 'evidence shows']
        for indicator in positive_indicators:
            if indicator in text_lower:
                return 'positive'
        return 'neutral'

    def _derive_confidence(self, text: str, evidence_packet: dict[str, Any], title: str, summary: str) -> float:
        """Derive confidence using canonical confidence policy."""
        source = evidence_packet.get('source_type', evidence_packet.get('source', '')).upper()
        if source in ('CT', 'CERTIFICATE_TRANSPARENCY'):
            source_family = 'CT'
        elif source in ('FEED', 'RSS', 'ATOM'):
            source_family = 'FEED'
        elif source in ('WAYBACK', 'ARCHIVE', 'ARCHIVE_ORG'):
            source_family = 'WAYBACK'
        elif source in ('STEALTH', 'HIDDEN'):
            source_family = 'STEALTH'
        else:
            source_family = 'PUBLIC'
        has_provenance = bool(evidence_packet.get('source') or evidence_packet.get('provenance'))
        ioc_pattern = re.compile('https?://|www\\.|\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}\\b|\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b')
        has_ioc = bool(ioc_pattern.search(text))
        corroboration_count = 0
        if title and summary:
            title_words = set(title.lower().split())
            summary_words = set(summary.lower().split())
            text_words = set(text.lower().split())
            overlap = title_words & summary_words & text_words
            if overlap:
                corroboration_count = 1
        confidence = compute_confidence(source_family=source_family, has_provenance=has_provenance, has_ioc=has_ioc, corroboration_count=corroboration_count)
        return min(confidence, MAX_CONFIDENCE)

    def get_claims_runtime_status(self) -> dict:
        """
        Return lightweight claims runtime status dict.

        F225A: Makes claim extraction visible as first-class runtime signal
        without requiring live network or LLM.
        """
        return {'claims_extracted_count': self._claims_extracted_count, 'claims_positive_count': self._claims_positive_count, 'claims_negative_count': self._claims_negative_count, 'claims_neutral_count': self._claims_neutral_count, 'claims_low_confidence_count': self._claims_low_confidence_count, 'claims_extraction_packets_seen': self._claims_extraction_packets_seen, 'claims_extraction_packets_with_claims': self._claims_extraction_packets_with_claims}

    async def _do_shutdown(self, ctx_: dict[str, Any]) -> None:
        """Cleanup on shutdown."""
        logger.info(f'ClaimsCoordinator shutting down: {self._evidence_processed} evidence processed')
        self._pending_evidence_ids.clear()
        self._pending_evidence_set.clear()
        self._uncertain_clusters.clear()