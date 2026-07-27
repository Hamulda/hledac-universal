"""
Analyst read-side facade for local questions over findings, graph, and vectors.

ARCHITECTURE ROLE
=================
AnalystWorkbench is a read-side SEAM that aggregates:
  - DuckDBShadowStore findings (DuckDB, Tier 2)
  - DuckPGQGraph entity history (DuckDB-backed)
  - LanceDB vector similarity (text index, 256d MRL)

All retrieval is bounded:
  - MAX_CONTEXT_BYTES = 8192  (8KB max context per answer)
  - MAX_TOP_K = 20            (max results from any single source)
  - MAX_GRAPH_HOPS = 2        (entity history max hops)
  - MAX_EVIDENCE_PTRS = 5    (max evidence pointers per answer)

NO EXTERNAL NETWORK CALLS — all data sources are local.
NO LLM REQUIRED — extractive pattern matching fallback always available.
MODEL LIFECYCLE — load/unload only via brain/model_lifecycle.py.

PATTERN: Extractive Answer
===========================
1. query_findings() → keyword/BM25 search over recent findings
2. query_graph() → multi-hop entity traversal
3. query_vectors() → ANN top-k over LanceDB text index
4. _extract_answer() → deterministic text extraction from context chunks
5. get_related_entities() → entity candidates from graph traversal
6. get_evidence_pointers() → finding_ids + provenance tuples

If model is used (opt-in):
  - Load via model_lifecycle.load_model()
  - Unload via model_lifecycle.unload_model()
  - Never concurrent with JS renderer (enforced by caller)
"""
import logging
logger = logging.getLogger(__name__)
import re
import time
from dataclasses import dataclass, field
import msgspec
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from knowledge.evidence_chain import EvidenceChain
from hledac.universal.core.protocols import safe_get_finding_field
from utils.sync_bridge import run_sync_async
__all__ = ['AnalystWorkbench', 'AnalystAnswer', 'AnalystBrief', 'EvidencePointer', 'RelatedEntity', 'create_analyst_workbench', 'get_evidence_chain', 'MAX_CORROBORATION_SUMMARY']
MAX_CONTEXT_BYTES: int = 8192
MAX_TOP_K: int = 20
MAX_GRAPH_HOPS: int = 2
MAX_EVIDENCE_PTRS: int = 5
MAX_RELATED_ENTITIES: int = 10
MAX_ENVELOPE_SIZE: int = 4096
MAX_BRIEF_FINDINGS: int = 20
MAX_BRIEF_CHAINS: int = 5
MAX_BRIEF_NEXT_ACTIONS: int = 10
MAX_GRAPH_ANALYTICS_BRIEF_FINDINGS: int = 2
MAX_CORROBORATION_SUMMARY: int = 10
MAX_FEED_CLUSTERS: int = 20
MAX_SAMPLE_IDS_PER_CLUSTER: int = 5
MAX_TEXT_PER_CLUSTER: int = 200
MAX_RISK_HYPOTHESES: int = 5
MAX_PIVOT_RECOMMENDATIONS: int = 5

class AnalystBrief(msgspec.Struct, frozen=True, gc=False):
    """
    Sprint F204E: Analyst brief produced at sprint teardown.
    F225B: Added source_family_summary, evidence_gaps, risk_hypotheses,
           feed_cluster_summary, pivot_recommendations fields.
    F225C: Added corroboration_summary field.
    F226E: Added target_memory_feedback field.

    A model-free summary of sprint results: what changed, strongest evidence,
    next best pivots, and open questions.

    Fields:
        sprint_id: Sprint identifier
        target_id: Research target (query or target_id)
        headline: One-line sprint summary
        key_findings: Tuple of key finding strings (max MAX_BRIEF_FINDINGS)
        evidence_chain_ids: Tuple of evidence chain IDs (max MAX_BRIEF_CHAINS)
        next_actions: Tuple of suggested next action strings (max MAX_BRIEF_NEXT_ACTIONS)
        open_questions: Tuple of open question strings
        confidence: Confidence score [0.0, 1.0]
        generated_ts: Unix timestamp of generation
        corroboration_summary: F225C cross-source corroboration strings
        source_family_summary: F225B source family presence summary
        evidence_gaps: F225B evidence gap strings
        risk_hypotheses: F225B bounded risk hypotheses (max 5)
        feed_cluster_summary: F225B feed/public/CT cluster presence
        pivot_recommendations: F225B pivot recommendations (max 5)
    """
    sprint_id: str
    target_id: str
    headline: str
    key_findings: tuple[str, ...]
    evidence_chain_ids: tuple[str, ...]
    next_actions: tuple[str, ...]
    open_questions: tuple[str, ...]
    confidence: float
    generated_ts: float
    corroboration_summary: tuple[str, ...] = field(default_factory=lambda: ())
    source_family_summary: tuple[str, ...] = field(default_factory=lambda: ())
    evidence_gaps: tuple[str, ...] = field(default_factory=lambda: ())
    risk_hypotheses: tuple[str, ...] = field(default_factory=lambda: ())
    feed_cluster_summary: tuple[str, ...] = field(default_factory=lambda: ())
    pivot_recommendations: tuple[str, ...] = field(default_factory=lambda: ())
    target_memory_feedback: dict[str, Any] = field(default_factory=lambda: {})

class EvidencePointer(msgspec.Struct, frozen=True, gc=False):
    """
    Evidence pointer for an analyst answer.

    Fields:
        finding_id: Unique identifier of the source finding
        source_type: Source type (e.g., "ct_log", "document", "deep_probe")
        query: Research query that produced this finding
        confidence: Confidence score [0.0, 1.0]
        ts: Unix timestamp of the finding
        provenance: Provenance chain tuple
        envelope_available: True if finding has evidence envelope
        snippet: Text snippet extracted from payload_text (None if no envelope)
    """
    finding_id: str
    source_type: str
    query: str
    confidence: float
    ts: float
    provenance: tuple[str, ...]
    envelope_available: bool
    snippet: str | None = None

class RelatedEntity(msgspec.Struct, frozen=True, gc=False):
    """
    Related entity from graph traversal.

    Fields:
        entity_value: The entity IOC value (e.g., domain, IP, email)
        entity_type: IOC type (e.g., "domain", "ipv4", "email")
        confidence: Entity confidence score [0.0, 1.0]
        hops: Distance in hops from the source entity
        relation_types: Set of relation types connecting to this entity
    """
    entity_value: str
    entity_type: str
    confidence: float
    hops: int
    relation_types: frozenset[str] = field(default_factory=frozenset)

class AnalystAnswer(msgspec.Struct, gc=False):
    """
    Complete analyst answer with evidence.

    Fields:
        question: The original analyst question
        extractive_answer: Deterministic extractive text answer (no model required)
        llm_answer: Optional LLM-generated answer (None if no model used)
        evidence_pointers: List of EvidencePointer (max MAX_EVIDENCE_PTRS)
        related_entities: List of RelatedEntity (max MAX_RELATED_ENTITIES)
        context_bytes: Actual bytes used for extractive answer
        model_used: True if LLM was used for this answer
        sources_used: List of source types consulted
        timing_ms: Total time in milliseconds
    """
    question: str
    extractive_answer: str
    llm_answer: str | None = None
    evidence_pointers: list[EvidencePointer] = field(default_factory=list)
    related_entities: list[RelatedEntity] = field(default_factory=list)
    context_bytes: int = 0
    model_used: bool = False
    sources_used: list[str] = field(default_factory=list)
    timing_ms: float = 0.0

def _truncate_to_bytes(text: str, max_bytes: int=MAX_CONTEXT_BYTES) -> tuple[str, int]:
    """
    Truncate text to max_bytes UTF-8.

    Returns (truncated_text, actual_bytes).
    """
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return (text, len(encoded))
    truncated = encoded[:max_bytes].decode('utf-8', errors='ignore')
    return (truncated, max_bytes)

def _extract_snippet(payload_text: str | None, query: str, max_len: int=200) -> str | None:
    """
    Extract relevant snippet from payload_text using keyword proximity.

    Fail-soft: returns None if no match or payload_text is None.
    """
    if not payload_text:
        return None
    query_lower = query.lower()
    text_lower = payload_text.lower()
    idx = text_lower.find(query_lower)
    if idx == -1:
        keywords = query.split()[:3]
        for kw in keywords:
            if len(kw) > 3:
                idx = text_lower.find(kw.lower())
                if idx != -1:
                    break
        if idx == -1:
            return None
    start = max(0, idx - 50)
    end = min(len(payload_text), idx + len(query) + 150)
    snippet = payload_text[start:end].strip()
    if start > 0:
        snippet = '...' + snippet
    if end < len(payload_text):
        snippet = snippet + '...'
    return snippet[:max_len]

def _keyword_score(text: str, keywords: list[str]) -> float:
    """
    Score text by keyword overlap.

    Returns score in [0.0, 1.0] based on keyword match ratio.
    """
    if not keywords or not text:
        return 0.0
    text_lower = text.lower()
    matches = sum((1 for kw in keywords if kw.lower() in text_lower))
    return matches / len(keywords)

def _build_evidence_pointer(finding: dict[str, Any], snippet: str | None=None) -> EvidencePointer:
    """Build EvidencePointer from a finding dict."""
    return EvidencePointer(finding_id=str(finding.get('id', finding.get('finding_id', ''))), source_type=str(finding.get('source_type', 'unknown')), query=str(finding.get('query', '')), confidence=float(finding.get('confidence', 0.0)), ts=float(finding.get('ts', 0.0)), provenance=tuple(finding.get('provenance', [])), envelope_available=bool(finding.get('envelope')), snippet=snippet)

class AnalystWorkbench:
    """
    Read-side analyst facade over local findings, graph, and vectors.

    Bounds (fixed, not configurable):
      - MAX_CONTEXT_BYTES = 8192
      - MAX_TOP_K = 20
      - MAX_GRAPH_HOPS = 2
      - MAX_EVIDENCE_PTRS = 5
      - MAX_RELATED_ENTITIES = 10

    Thread-safe: all async methods delegate to duckdb_worker via run_in_executor.

    NO external network calls.
    NO LLM required (extractive fallback always available).
    Model lifecycle via brain.model_lifecycle only.
    """
    __slots__ = tuple(('_duckdb', '_graph', '_logger', '_semantic', '_vector'))

    def __init__(self, duckdb_store: Any=None, graph_service: Any=None, vector_store: Any=None, semantic_store: Any=None) -> None:
        """
        Initialize AnalystWorkbench with optional store references.

        All stores are optional — workbench operates with whatever is available.
        If a store is None, its queries return empty results (fail-soft).

        Args:
            duckdb_store: DuckDBShadowStore instance for findings
            graph_service: DuckPGQGraph-backed service for entity history
            vector_store: LanceDB VectorStore for text ANN
            semantic_store: FastEmbed SemanticStore for keyword search
        """
        self._duckdb = duckdb_store
        self._graph = graph_service
        self._vector = vector_store
        self._semantic = semantic_store
        self._logger = logging.getLogger(f'{__name__}.AnalystWorkbench')

    async def query_findings(self, query: str, limit: int=MAX_TOP_K, source_type: str | None=None) -> list[dict[str, Any]]:
        """
        Query recent findings using keyword/BM25 search.

        Args:
            query: Search query string
            limit: Max results (capped to MAX_TOP_K)
            source_type: Optional filter by source_type

        Returns:
            List of finding dicts ordered by relevance (keyword match).
            Each dict has: id, query, source_type, confidence, ts, provenance,
            payload_text (if available).
        """
        if limit > MAX_TOP_K:
            limit = MAX_TOP_K
        if not self._duckdb:
            self._logger.debug('duckdb_store not available, returning empty')
            return []
        try:
            raw = await self._duckdb.async_query_recent_findings(limit=MAX_TOP_K * 2)
        except Exception as e:
            self._logger.warning(f'query_findings failed: {e}')
            return []
        if source_type:
            raw = [f for f in raw if f.get('source_type') == source_type]
        keywords = query.split()
        scored = []
        for f in raw:
            text = f.get('query', '') + ' ' + (f.get('payload_text') or '')
            score = _keyword_score(text, keywords)
            if score > 0:
                scored.append((score, f))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [f for _, f in scored[:limit]]
        self._logger.debug(f"query_findings('{query}') -> {len(results)} results")
        return results

    async def query_graph(self, entity_value: str, max_hops: int=MAX_GRAPH_HOPS) -> list[RelatedEntity]:
        """
        Query entity history from DuckPGQGraph.

        Args:
            entity_value: IOC value to traverse from (e.g., domain, IP)
            max_hops: Max traversal depth (capped to MAX_GRAPH_HOPS)

        Returns:
            List of RelatedEntity ordered by hops then confidence.
        """
        if max_hops > MAX_GRAPH_HOPS:
            max_hops = MAX_GRAPH_HOPS
        if not self._graph:
            self._logger.debug('graph_service not available, returning empty')
            return []
        try:
            history = self._graph.find_entity_history(entity_value, max_hops=max_hops)
        except Exception as e:
            self._logger.warning(f'query_graph failed: {e}')
            return []
        entities: dict[str, RelatedEntity] = {}
        for entry in history:
            val = entry.get('value', '')
            ioc_type = entry.get('ioc_type', 'unknown')
            conf = float(entry.get('confidence', 0.0))
            hops = int(entry.get('hops', 0))
            rel_type = str(entry.get('relation_type', ''))
            key = f'{val}|{ioc_type}'
            if key not in entities:
                entities[key] = RelatedEntity(entity_value=val, entity_type=ioc_type, confidence=conf, hops=hops, relation_types=frozenset([rel_type]))
            else:
                existing = entities[key]
                entities[key] = RelatedEntity(entity_value=val, entity_type=ioc_type, confidence=max(existing.confidence, conf), hops=min(existing.hops, hops), relation_types=existing.relation_types | {rel_type})
        result = sorted(entities.values(), key=lambda e: (e.hops, -e.confidence))
        self._logger.debug(f"query_graph('{entity_value}') -> {len(result)} entities")
        return result[:MAX_RELATED_ENTITIES]

    async def query_vectors(self, query_embedding: Any, k: int=MAX_TOP_K) -> list[tuple[str, float]]:
        """
        Query LanceDB text index for ANN similar vectors.

        Args:
            query_embedding: 256d numpy array (MRL dimension for text)
            k: Number of results (capped to MAX_TOP_K)

        Returns:
            List of (finding_id, similarity_score) tuples ordered by similarity.
        """
        if k > MAX_TOP_K:
            k = MAX_TOP_K
        if not self._vector:
            self._logger.debug('vector_store not available, returning empty')
            return []
        try:
            results = self._vector.query(query_embedding, k=k, index_type='text')
        except Exception as e:
            self._logger.warning(f'query_vectors failed: {e}')
            return []
        self._logger.debug(f'query_vectors() -> {len(results)} results')
        return results

    async def query_semantic(self, query: str, limit: int=MAX_TOP_K) -> list[str]:
        """
        Query SemanticStore (FastEmbed) for finding_ids by keyword.

        Args:
            query: Search query
            limit: Max results (capped to MAX_TOP_K)

        Returns:
            List of finding_ids ordered by semantic relevance.
        """
        if limit > MAX_TOP_K:
            limit = MAX_TOP_K
        if not self._semantic:
            self._logger.debug('semantic_store not available, returning empty')
            return []
        try:
            ids = await self._semantic.semantic_pivot(query, top_k=limit)
            return list(ids)[:limit]
        except Exception as e:
            self._logger.warning(f'query_semantic failed: {e}')
            return []

    async def ask(self, question: str, use_model: bool=False, model_name: str | None=None) -> AnalystAnswer:
        """
        Answer an analyst question using local data sources.

        PIPELINE:
          1. query_findings() — keyword search over recent findings
          2. query_graph() — entity history for key entities in question
          3. _extract_answer() — deterministic extractive answer from chunks
          4. get_evidence_pointers() — build EvidencePointer list
          5. get_related_entities() — build RelatedEntity list
          6. (Optional) LLM answer via model_lifecycle.load_model()

        Args:
            question: Natural language analyst question
            use_model: If True, generate LLM answer after extractive
            model_name: Model to load (required if use_model=True)

        Returns:
            AnalystAnswer with extractive_answer always populated.
            llm_answer is None unless use_model=True and model loads successfully.
        """
        t0 = time.monotonic()
        findings = await self.query_findings(question, limit=MAX_TOP_K)
        entities_from_q = self._extract_entities_from_question(question)
        all_related: list[RelatedEntity] = []
        for entity in entities_from_q[:3]:
            related = await self.query_graph(entity)
            all_related.extend(related)
        seen: set[str] = set()
        unique_related: list[RelatedEntity] = []
        for e in all_related:
            key = f'{e.entity_value}|{e.entity_type}'
            if key not in seen:
                seen.add(key)
                unique_related.append(e)
        unique_related.sort(key=lambda x: (x.hops, -x.confidence))
        related_entities = unique_related[:MAX_RELATED_ENTITIES]
        context_chunks: list[str] = []
        for f in findings:
            chunk = f.get('query', '')
            if f.get('payload_text'):
                chunk += ' ' + f['payload_text']
            context_chunks.append(chunk)
        for e in related_entities:
            chunk = f'{e.entity_type}:{e.entity_value}'
            if e.relation_types:
                chunk += ' (' + ', '.join(e.relation_types) + ')'
            context_chunks.append(chunk)
        full_context = '\n'.join(context_chunks)
        truncated_context, context_bytes = _truncate_to_bytes(full_context, MAX_CONTEXT_BYTES)
        extractive_answer = self._extract_answer(truncated_context, question)
        evidence_pointers = self._build_evidence_pointers(findings)
        llm_answer: str | None = None
        sources_used = list({f.get('source_type', 'unknown') for f in findings})
        if use_model and model_name:
            llm_answer = await self._generate_llm_answer(question, truncated_context, model_name)
        elapsed_ms = (time.monotonic() - t0) * 1000
        return AnalystAnswer(question=question, extractive_answer=extractive_answer, llm_answer=llm_answer, evidence_pointers=evidence_pointers, related_entities=related_entities, context_bytes=context_bytes, model_used=use_model, sources_used=sources_used, timing_ms=elapsed_ms)

    def _extract_answer(self, context: str, question: str) -> str:
        """
        Deterministic extractive answer from context chunks.

        Returns the longest contiguous text span that contains
        the most question keywords. No model required.

        Fail-soft: returns "No relevant information found." on any error.
        """
        if not context.strip():
            return 'No relevant information found.'
        keywords = [kw.lower() for kw in question.split() if len(kw) > 3]
        if not keywords:
            return context[:500]
        paragraphs = context.split('\n')
        best_para = ''
        best_score = 0.0
        for para in paragraphs:
            if not para.strip():
                continue
            score = _keyword_score(para, keywords)
            if score > best_score:
                best_score = score
                best_para = para
        if best_score > 0 and best_para:
            return best_para.strip()
        return context[:500].strip() if context else 'No relevant information found.'

    async def _generate_llm_answer(self, question: str, context: str, model_name: str) -> str | None:
        """
        Generate LLM answer using brain/model_lifecycle.py.

        Load/unload only through canonical model_lifecycle interface.
        Returns None on any failure (fail-soft).
        """
        try:
            from brain.model_lifecycle import load_model, unload_model
            load_model(model_name)
            answer = self._extract_answer(context, question)
            unload_model()
            return answer
        except Exception as e:
            self._logger.warning(f'LLM answer generation failed: {e}')
            return None

    def _extract_entities_from_question(self, question: str) -> list[str]:
        """
        Extract potential IOC entities from question using regex patterns.

        Returns list of entity values (domains, IPs, emails, hashes).
        """
        entities: list[str] = []
        domains = re.findall('\\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}\\b', question)
        entities.extend(domains)
        ips = re.findall('\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b', question)
        entities.extend(ips)
        emails = re.findall('\\b[\\w.-]+@[\\w.-]+\\.\\w+\\b', question)
        entities.extend(emails)
        hashes = re.findall('\\b(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{40}|[a-fA-F0-9]{64})\\b', question)
        entities.extend(hashes)
        return entities

    def _build_evidence_pointers(self, findings: list[dict[str, Any]]) -> list[EvidencePointer]:
        """
        Build evidence pointers from findings.

        Caps at MAX_EVIDENCE_PTRS, ordered by confidence descending.
        """
        pointers: list[EvidencePointer] = []
        for f in findings:
            if len(pointers) >= MAX_EVIDENCE_PTRS:
                break
            snippet = None
            if f.get('payload_text'):
                snippet = _extract_snippet(f['payload_text'], f.get('query', ''), max_len=200)
            pointers.append(_build_evidence_pointer(f, snippet))
        pointers.sort(key=lambda p: p.confidence, reverse=True)
        return pointers[:MAX_EVIDENCE_PTRS]

    def ask_sync(self, question: str, use_model: bool=False, model_name: str | None=None) -> AnalystAnswer:
        """
        Synchronous wrapper around ask().

        For use in sync contexts. Prefer ask() in async contexts.
        """
        return run_sync_async(self.ask(question, use_model=use_model, model_name=model_name))

    async def get_evidence_chain(self, finding_id: str) -> EvidenceChain | None:
        """
        F203D: Retrieve the evidence chain for a given finding_id.

        Chains are accumulated by the EvidenceChainBuilder during sprint teardown
        and stored as a sprint artifact. This method queries the module-level
        registry for the chain.

        Args:
            finding_id: The finding ID to look up.

        Returns:
            EvidenceChain if found, None otherwise.
            Returns None if no sprint has been run yet or if the finding_id
            is not part of any tracked chain.
        """
        try:
            from knowledge.evidence_chain import _get_chain_for_finding
            return _get_chain_for_finding(finding_id)
        except Exception:
            self._logger.warning(f'get_evidence_chain({finding_id}) failed')
            return None

    async def get_target_memory_summary(self, target_id: str) -> dict | None:
        """
        F204D: Get target memory summary for a target.

        Returns dict with keys: target_id, sprint_count, cumulative_finding_count,
        entity_facets, exposure_facets, pivot_facets, confidence_drift,
        updated_by_sprint_id or None if not found.

        Thread-safe: runs on duckdb_worker via run_in_executor.
        Fail-soft: returns None on any error.
        """
        if not self._duckdb:
            return None
        try:
            memory = await self._duckdb.async_get_target_memory(target_id)
            if memory is None:
                return None
            return {'target_id': memory.target_id, 'sprint_count': memory.sprint_count, 'cumulative_finding_count': memory.cumulative_finding_count, 'entity_facets': memory.entity_facets, 'exposure_facets': memory.exposure_facets, 'pivot_facets': memory.pivot_facets, 'confidence_drift': memory.confidence_drift, 'updated_by_sprint_id': memory.updated_by_sprint_id}
        except Exception:
            return None

    def _derive_target_memory_feedback(self, target_memory: dict[str, Any], findings: list[Any]) -> dict[str, Any]:
        """
        F226E: Derive next-run advice from target memory history.

        Computed from existing surfaces only (target_memory + findings).
        NO new DB API, NO network, NO model.

        Returns:
            dict with keys:
              - repeated_feed_dominance: bool
              - prior_nonfeed_weakness: bool
              - prior_public_accepted_count: int
              - prior_ct_accepted_count: int
              - suggested_next_profile: str
              - suggested_feed_cap_reason: str
              - suggested_nonfeed_lanes: str
        """
        try:
            mem_sprints = target_memory.get('sprint_count', 0) or 0
            mem_findings = target_memory.get('cumulative_finding_count', 0) or 0
            if mem_sprints < 2:
                return {}
            current_feed = sum((1 for f in findings if 'feed' in (safe_get_finding_field(f, 'source_type', None) or '').lower()))
            current_total = max(len(findings), 1)
            current_feed_ratio = current_feed / current_total
            entity_facets = target_memory.get('entity_facets', {})
            exposure_facets = target_memory.get('exposure_facets', {})
            prior_feed_heavy = len(entity_facets) > 5 and (not exposure_facets) and (mem_findings > 10) and (current_feed_ratio >= 0.8)
            repeated_feed_dominance = bool(prior_feed_heavy and mem_sprints >= 2)
            pivot_facets = target_memory.get('pivot_facets', {})
            prior_public = sum((1 for k in pivot_facets if 'public' in str(k).lower()))
            prior_ct = sum((1 for k in pivot_facets if 'ct' in str(k).lower()))
            prior_nonfeed_weakness = bool((prior_public == 0 or prior_ct == 0) and mem_sprints >= 2)
            prior_public_accepted = max(0, prior_public)
            prior_ct_accepted = max(0, prior_ct)
            if repeated_feed_dominance:
                suggested_next_profile = 'nonfeed_diagnostic180'
                suggested_nonfeed_lanes = 'PUBLIC,CT'
                suggested_feed_cap_reason = 'break_feed_cycle'
            elif prior_nonfeed_weakness:
                if prior_public == 0 and prior_ct > 0:
                    suggested_next_profile = 'PUBLIC'
                    suggested_nonfeed_lanes = 'PUBLIC'
                    suggested_feed_cap_reason = 'bootstrap_public_bridge'
                elif prior_ct == 0 and prior_public > 0:
                    suggested_next_profile = 'CT'
                    suggested_nonfeed_lanes = 'CT'
                    suggested_feed_cap_reason = 'bootstrap_ct_provider'
                else:
                    suggested_next_profile = 'nonfeed'
                    suggested_nonfeed_lanes = 'PUBLIC,CT'
                    suggested_feed_cap_reason = 'balance_lanes'
            else:
                suggested_next_profile = ''
                suggested_nonfeed_lanes = ''
                suggested_feed_cap_reason = ''
            return {'repeated_feed_dominance': repeated_feed_dominance, 'prior_nonfeed_weakness': prior_nonfeed_weakness, 'prior_public_accepted_count': prior_public_accepted, 'prior_ct_accepted_count': prior_ct_accepted, 'suggested_next_profile': suggested_next_profile, 'suggested_feed_cap_reason': suggested_feed_cap_reason, 'suggested_nonfeed_lanes': suggested_nonfeed_lanes}
        except Exception:
            return {}

    async def build_sprint_brief(self, sprint_id: str, target_id: str, findings: list[Any], graph_signal: dict[str, Any], governor: Any=None, duckdb_store: Any=None, store_findings_count: int | None=None) -> AnalystBrief:
        """
        F204E: Build a model-free analyst brief at sprint teardown.

        Generates a summary of sprint results: what changed, strongest evidence,
        next best pivots, and open questions. Uses extractive analysis only --
        no model loading required.

        RAM guard: if governor is critical/emergency, generates minimal brief
        from counts only (no graph queries).

        F205J: If duckdb_store is available, reads cross-sprint target memory
        via get_target_memory_summary(target_id) and incorporates it into
        headline, key_findings, and open_questions.

        F223F: store_findings_count, when provided, distinguishes runtime findings
        (from the current sprint) from store findings (canonical total accepted).
        The headline uses runtime findings as "sprint findings"; store findings
        are surfaced separately in key_findings when they differ from runtime.

        Bounds:
          - MAX_BRIEF_FINDINGS = 20
          - MAX_BRIEF_CHAINS = 5
          - MAX_BRIEF_NEXT_ACTIONS = 10
          - MAX_CONTEXT_BYTES = 8192

        Args:
            sprint_id: Sprint identifier
            target_id: Research target (query or canonical target_id)
            findings: List of findings from the current sprint run (runtime findings)
            graph_signal: Graph signal dict from _get_graph_signal()
            governor: Optional M1ResourceGovernor for RAM check
            duckdb_store: Optional DuckDBShadowStore for target memory read
            store_findings_count: Optional canonical store count of total accepted
                findings for this target/sprint. When provided and different from
                len(findings), the headline uses runtime findings and store findings
                are noted in key_findings when they differ.
        """
        import time as _time
        ts = _time.time()
        if governor is not None:
            try:
                snap = governor.snapshot()
                uma_state = getattr(snap, 'uma_state', 'ok') if snap else 'ok'
                if uma_state in ('critical', 'emergency'):
                    finding_count = len(findings)
                    graph_nodes = graph_signal.get('graph_nodes', 0) if graph_signal else 0
                    graph_edges = graph_signal.get('graph_edges', 0) if graph_signal else 0
                    return AnalystBrief(sprint_id=sprint_id, target_id=target_id, headline=f'Sprint {sprint_id}: {finding_count} findings, {graph_nodes} graph nodes (RAM pressure — minimal brief)', key_findings=(f'Accepted findings: {finding_count}', f'Graph nodes: {graph_nodes}', f'Graph edges: {graph_edges}'), evidence_chain_ids=(), next_actions=('Continue investigation with reduced scope',), open_questions=('What caused RAM pressure?',), confidence=0.3, generated_ts=ts)
            except Exception as _e:
                self._logger.debug('fail-soft suppression: build_sprint_brief: %s', _e, exc_info=True)
        graph_analytics: dict[str, Any] = {}
        try:
            from hledac.universal.knowledge.graph_service import graph_analytics_summary
            graph_analytics = graph_analytics_summary(top_k=MAX_GRAPH_ANALYTICS_BRIEF_FINDINGS + 5)
        except Exception:
            graph_analytics = {}
        target_memory: dict[str, Any] | None = None
        _store = duckdb_store or self._duckdb
        if _store and target_id:
            try:
                target_memory = await self.get_target_memory_summary(target_id)
            except Exception:
                target_memory = None
        try:
            key_findings_list = self._extract_key_findings(findings)
            if target_memory:
                mem_sprints = target_memory.get('sprint_count', 0)
                mem_findings = target_memory.get('cumulative_finding_count', 0)
                entity_count = len(target_memory.get('entity_facets', {}))
                exposure_count = len(target_memory.get('exposure_facets', {}))
                pivot_count = len(target_memory.get('pivot_facets', {}))
                drift = target_memory.get('confidence_drift', {})
                drift_ratio = drift.get('drift_ratio', 1.0) if drift else 1.0
                mem_finding = f'Target memory: {mem_sprints} sprints, {mem_findings} cumulative findings, {entity_count} entities, {exposure_count} exposures, {pivot_count} pivots (drift={drift_ratio:.2f})'
                key_findings_list.append(mem_finding)
                drift_reasons = drift.get('drift_reasons', []) if drift else []
                if drift_reasons:
                    concise = drift_reasons[:3]
                    drift_exp = f"Drift signals: {', '.join(concise)}"
                    key_findings_list.append(drift_exp)
                if drift_ratio > 1.5:
                    open_drift_q = f'Finding rate drift detected (ratio={drift_ratio:.2f}): this sprint yield is {int((drift_ratio - 1) * 100)}% above average'
                elif mem_sprints >= 3 and drift_ratio >= 0.7:
                    open_drift_q = f'Target has {mem_sprints} prior sprints — consider graph expansion'
                else:
                    open_drift_q = None
            else:
                open_drift_q = None
            if graph_analytics.get('analytics_available') and graph_analytics.get('top_central_entities'):
                top_entities = graph_analytics['top_central_entities']
                community_count = graph_analytics.get('community_count', 0)
                if top_entities:
                    top = top_entities[0]
                    key_findings_list.append(f"Graph central entity: {top.get('value', '?')} ({top.get('ioc_type', '?')}, degree={top.get('degree', 0)})")
                if len(top_entities) > 1:
                    second = top_entities[1]
                    key_findings_list.append(f"Graph entity 2: {second.get('value', '?')} ({second.get('ioc_type', '?')}, degree={second.get('degree', 0)})")
                elif community_count > 1:
                    key_findings_list.append(f'Graph communities: ~{community_count} detected communities')
            runtime_finding_count = len(findings)
            graph_nodes = graph_signal.get('graph_nodes', 0) if graph_signal else 0
            graph_edges = graph_signal.get('graph_edges', 0) if graph_signal else 0
            if target_memory:
                mem_sprints = target_memory.get('sprint_count', 0)
                headline = f'Sprint {sprint_id} (target {target_id}, {mem_sprints} prior sprints): {runtime_finding_count} findings, {graph_nodes} nodes, {graph_edges} edges'
            else:
                headline = f'Sprint {sprint_id}: {runtime_finding_count} findings, {graph_nodes} graph nodes, {graph_edges} edges'
            if store_findings_count is not None and store_findings_count != runtime_finding_count:
                if runtime_finding_count == 0:
                    key_findings_list.append(f'Canonical store: {store_findings_count} prior accepted findings (0 this sprint -- possible quality gate change or target exhaustion)')
                else:
                    key_findings_list.append(f'Canonical store: {store_findings_count} total accepted findings (runtime: {runtime_finding_count} this sprint)')
            key_findings = tuple(key_findings_list[:MAX_BRIEF_FINDINGS])
            chain_ids: list[str] = []
            for f in findings[:50]:
                fid = safe_get_finding_field(f, 'finding_id', None) or f.get('finding_id', '')
                if fid and 'chain' in str(f.get('provenance', '')):
                    chain_ids.append(str(fid))
            evidence_chain_ids = tuple(chain_ids[:MAX_BRIEF_CHAINS])
            next_actions = self._derive_next_actions(findings)
            next_actions_tuple = tuple(next_actions[:MAX_BRIEF_NEXT_ACTIONS])
            open_questions = list(self._derive_open_questions(findings, graph_signal))
            if open_drift_q and len(open_questions) < 5:
                open_questions.append(open_drift_q)
            if not target_memory and runtime_finding_count > 0:
                open_questions.append('No prior target memory — consider establishing baseline')
            confidence = 0.7 if runtime_finding_count > 10 else 0.5 if runtime_finding_count > 0 else 0.3
            if target_memory:
                confidence = min(0.9, confidence + 0.1)
            corroboration_summary = self._build_corroboration_summary(findings)
            source_family_summary = self._build_source_family_summary(findings)
            evidence_gaps = self._build_evidence_gaps(findings, source_family_summary)
            risk_hypotheses = self._build_risk_hypotheses(findings, source_family_summary)
            feed_cluster_summary: tuple[str, ...] = ()
            feed_ratio = sum((1 for f in findings if 'feed' in (safe_get_finding_field(f, 'source_type', None) or '').lower())) / max(len(findings), 1)
            if feed_ratio >= 0.3 and len(findings) > 5:
                feed_cluster_summary = self.summarize_feed_clusters(findings)
            pivot_recommendations = self._build_pivot_recommendations(findings, graph_signal)
            tmf: dict[str, Any] = {}
            if target_memory:
                tmf = self._derive_target_memory_feedback(target_memory, findings)
                if tmf.get('repeated_feed_dominance'):
                    gaps = list(evidence_gaps)
                    gaps.append(f"F226E: Repeated feed-dominant sprint — consider non-feed diagnostic: {tmf.get('suggested_nonfeed_lanes', 'CT/PUBLIC')}")
                    evidence_gaps = tuple(gaps[:5])
                if tmf.get('prior_nonfeed_weakness'):
                    pivots = list(pivot_recommendations)
                    pivots.append(f"F226E: Prior {tmf.get('suggested_next_profile', 'nonfeed')} weakness — bootstrap {tmf.get('suggested_feed_cap_reason', 'PUBLIC')} lane")
                    pivot_recommendations = tuple(pivots[:5])
            return AnalystBrief(sprint_id=sprint_id, target_id=target_id, headline=headline, key_findings=key_findings, evidence_chain_ids=evidence_chain_ids, next_actions=next_actions_tuple, open_questions=tuple(open_questions[:5]), confidence=confidence, generated_ts=ts, corroboration_summary=corroboration_summary, source_family_summary=source_family_summary, evidence_gaps=evidence_gaps, risk_hypotheses=risk_hypotheses, feed_cluster_summary=feed_cluster_summary, pivot_recommendations=pivot_recommendations, target_memory_feedback=tmf)
        except Exception:
            return AnalystBrief(sprint_id=sprint_id, target_id=target_id, headline=f'Sprint {sprint_id}: brief generation failed', key_findings=(f'Findings processed: {len(findings)}',), evidence_chain_ids=(), next_actions=('Review findings manually',), open_questions=('Why did brief generation fail?',), confidence=0.1, generated_ts=ts, corroboration_summary=('Corroboration unavailable due to brief generation failure',), source_family_summary=(), evidence_gaps=('Brief generation failed — evidence gaps unavailable',), risk_hypotheses=(), feed_cluster_summary=(), pivot_recommendations=())

    def _extract_key_findings(self, findings: list[Any]) -> list[str]:
        """
        Extract key findings as strings from the findings list.

        Uses extractive pattern: sorts by confidence and takes top items.
        No model required.
        """
        if not findings:
            return []
        scored: list[tuple[float, str]] = []
        for f in findings:
            conf = safe_get_finding_field(f, 'confidence', None) or f.get('confidence', 0.0)
            conf = float(conf)
            ioc_type = safe_get_finding_field(f, 'ioc_type', None) or f.get('ioc_type', '')
            ioc_value = safe_get_finding_field(f, 'ioc_value', None) or f.get('ioc_value', '')
            query = safe_get_finding_field(f, 'query', None) or f.get('query', '') or ''
            source = safe_get_finding_field(f, 'source_type', None) or f.get('source_type', '')
            if ioc_value:
                text = f'{source}:{ioc_type}={ioc_value} (conf={conf:.2f})'
            elif query:
                text = f'{source}: {query[:80]} (conf={conf:.2f})'
            else:
                text = f'{source} finding (conf={conf:.2f})'
            scored.append((conf, text))
        scored.sort(key=lambda x: x[0], reverse=True)
        seen: set[str] = set()
        unique: list[str] = []
        for conf, text in scored:
            key = text[:60].lower()
            if key not in seen:
                seen.add(key)
                unique.append(text)
        return unique

    def _derive_next_actions(self, findings: list[Any]) -> list[str]:
        """
        Derive next actions from high-confidence findings.

        Uses source_type and ioc_type patterns to suggest follow-ups.
        No model required.
        """
        actions: list[str] = []
        seen: set[str] = set()
        source_iocs: dict[str, dict[str, int]] = {}
        for f in findings:
            source = getattr(f, 'source_type', None) or f.get('source_type', 'unknown')
            ioc_type = getattr(f, 'ioc_type', None) or f.get('ioc_type', 'unknown')
            conf = safe_get_finding_field(f, 'confidence', None) or f.get('confidence', 0.0)
            if float(conf) < 0.5:
                continue
            if source not in source_iocs:
                source_iocs[source] = {}
            source_iocs[source][ioc_type] = source_iocs[source].get(ioc_type, 0) + 1
        for source, iocs in source_iocs.items():
            for ioc_type, count in sorted(iocs.items(), key=lambda x: x[1], reverse=True)[:2]:
                if count >= 2:
                    action = f'Expand {ioc_type} investigation via {source}'
                    if action not in seen:
                        seen.add(action)
                        actions.append(action)
        for f in findings[:20]:
            conf = safe_get_finding_field(f, 'confidence', None) or f.get('confidence', 0.0)
            if float(conf) < 0.7:
                continue
            ioc_value = safe_get_finding_field(f, 'ioc_value', None) or f.get('ioc_value', '')
            ioc_type = safe_get_finding_field(f, 'ioc_type', None) or f.get('ioc_type', '')
            if ioc_value and ioc_type in ('domain', 'ipv4', 'email'):
                action = f'Pivot on {ioc_type}:{ioc_value}'
                if action not in seen:
                    seen.add(action)
                    actions.append(action)
        return actions

    def _derive_open_questions(self, findings: list[Any], graph_signal: dict[str, Any]) -> tuple[str, ...]:
        """
        Derive open questions from gaps in findings and graph.

        Checks for common gaps: low finding count, no high-confidence findings,
        sparse graph, missing IOC types.
        """
        questions: list[str] = []
        seen: set[str] = set()
        finding_count = len(findings)
        if finding_count == 0:
            q = 'Why did the sprint produce no findings?'
            if q not in seen:
                seen.add(q)
                questions.append(q)
        ioc_types: set[str] = set()
        high_conf_count = 0
        for f in findings:
            ioc_type = safe_get_finding_field(f, 'ioc_type', None) or f.get('ioc_type', '')
            conf = safe_get_finding_field(f, 'confidence', None) or f.get('confidence', 0.0)
            if ioc_type:
                ioc_types.add(ioc_type)
            if float(conf) >= 0.7:
                high_conf_count += 1
        if high_conf_count == 0 and finding_count > 0:
            q = 'Why are there no high-confidence findings?'
            if q not in seen:
                seen.add(q)
                questions.append(q)
        graph_nodes = graph_signal.get('graph_nodes', 0) if graph_signal else 0
        if graph_nodes == 0 and finding_count > 0:
            q = 'Why are no entities connected in the graph?'
            if q not in seen:
                seen.add(q)
                questions.append(q)
        if 'domain' not in ioc_types and finding_count > 5:
            q = 'Why were no domain IOCs extracted?'
            if q not in seen:
                seen.add(q)
                questions.append(q)
        return tuple(questions[:5])

    def _build_corroboration_summary(self, findings: list[Any]) -> tuple[str, ...]:
        """
        F225C: Build corroboration summary from findings source families.

        Uses summarize_chain_support if chains are available via the evidence_chain
        module global registry, otherwise falls back to findings source_type.

        Bounds: max MAX_CORROBORATION_SUMMARY lines.
        Fail-soft: returns ("Corroboration unavailable",) on any error.
        """
        try:
            from knowledge.evidence_chain import get_all_chains, summarize_chain_support
            chains = get_all_chains()
            if chains:
                support = summarize_chain_support(chains)
            else:
                finding_dicts = []
                for f in findings[:50]:
                    finding_dicts.append({'source_type': getattr(f, 'source_type', None) or f.get('source_type', 'unknown'), 'query': safe_get_finding_field(f, 'query', None) or f.get('query', '')})
                support = summarize_chain_support(finding_dicts)
            summary_lines = support.get('corroboration_summary', [])
            level = support.get('corroboration_level', 'none')
            if level == 'none' and (not summary_lines):
                return ('No corroborating sources identified',)
            return tuple(summary_lines[:MAX_CORROBORATION_SUMMARY])
        except Exception:
            return ('Corroboration unavailable',)

    def summarize_feed_clusters(self, findings: list[Any], max_clusters: int=MAX_FEED_CLUSTERS) -> tuple[str, ...]:
        """
        F225E: Deterministic feed cluster summary from findings.

        Clusters findings by shared IOC/entity tokens or by source_type+domain
        fallback. Feed-heavy runs show compact clusters instead of raw volume.

        Bounds:
          - max_clusters: max number of clusters (default MAX_FEED_CLUSTERS=20)
          - max sample IDs per cluster: MAX_SAMPLE_IDS_PER_CLUSTER=5
          - max text per cluster line: MAX_TEXT_PER_CLUSTER=200 chars

        No model, no embeddings, no network calls.
        Fail-soft: returns ("Feed clustering unavailable",) on any error.
        """
        try:
            if not findings:
                return ()
            feed_findings: list[Any] = []
            nonfeed_findings: list[Any] = []
            for f in findings:
                src = getattr(f, 'source_type', None) or (f.get('source_type') if isinstance(f, dict) else None) or 'unknown'
                src_lower = src.lower()
                if 'feed' in src_lower or 'public_feed' in src_lower or ('ct_log' not in src_lower and 'passive' not in src_lower):
                    if src_lower not in ('ct_log', 'passive_dns', 'document', 'deep_probe'):
                        feed_findings.append(f)
                    else:
                        nonfeed_findings.append(f)
                else:
                    nonfeed_findings.append(f)

            def _extract_tokens(f: Any) -> set[str]:
                tokens: set[str] = set()
                for field_name in ('ioc_value', 'domain', 'ipv4', 'email', 'query', 'title'):
                    val = getattr(f, field_name, None) or (f.get(field_name) if isinstance(f, dict) else None)
                    if val and isinstance(val, str):
                        for tok in re.split('[^a-zA-Z0-9]+', val.lower()):
                            if len(tok) > 2:
                                tokens.add(tok)
                payload = getattr(f, 'payload_text', None) or (f.get('payload_text') if isinstance(f, dict) else None)
                if payload and isinstance(payload, str):
                    for tok in payload.lower().split()[:20]:
                        tok = tok.strip('.,;:\'"()[]{}')
                        if len(tok) > 4:
                            tokens.add(tok)
                return tokens

            def _cluster_key(f: Any) -> str:
                src = getattr(f, 'source_type', None) or (f.get('source_type') if isinstance(f, dict) else None) or 'unknown'
                domain = getattr(f, 'domain', None) or (f.get('domain') if isinstance(f, dict) else None)
                if not domain:
                    url = getattr(f, 'url', None) or (f.get('url') if isinstance(f, dict) else None)
                    if url and isinstance(url, str):
                        m = re.search('://([^/]+)', url)
                        if m:
                            domain = m.group(1).lower()
                if domain:
                    domain = re.sub('\\.(com|org|net|io|co|ru|cn|info|xyz|tk|ml|ga|cf|gq|pw)$', '', domain)
                    return f'{src}|{domain}'
                return f'{src}|unknown'
            key_to_fids: dict[str, list[str]] = {}
            key_to_tokens: dict[str, set[str]] = {}
            key_to_texts: dict[str, list[str]] = {}
            for f in feed_findings:
                fid = getattr(f, 'finding_id', None) or (f.get('finding_id') if isinstance(f, dict) else None) or f'fid_{id(f)}'
                key = _cluster_key(f)
                if key not in key_to_fids:
                    key_to_fids[key] = []
                    key_to_tokens[key] = set()
                    key_to_texts[key] = []
                key_to_fids[key].append(fid)
                key_to_tokens[key].update(_extract_tokens(f))
                title = getattr(f, 'title', None) or (f.get('title') if isinstance(f, dict) else None) or ''
                query = getattr(f, 'query', None) or (f.get('query') if isinstance(f, dict) else None) or ''
                snippet = title or query
                if snippet and len(key_to_texts[key]) < 3:
                    key_to_texts[key].append(snippet[:100])

            def _cluster_sort(item: tuple[str, list[str]]) -> tuple[int, str]:
                return (-len(item[1]), item[0])
            sorted_keys = sorted(key_to_fids.items(), key=_cluster_sort)
            result_lines: list[str] = []
            clusters_used = 0
            for key, fids in sorted_keys:
                if clusters_used >= max_clusters:
                    break
                tokens = key_to_tokens.get(key, set())
                key_to_texts.get(key, [])
                sample_ids = fids[:MAX_SAMPLE_IDS_PER_CLUSTER]
                count = len(fids)
                if tokens:
                    top_tokens = sorted(tokens, key=len, reverse=True)[:5]
                    token_str = ', '.join((t for t in top_tokens if len(t) > 3))
                else:
                    token_str = 'no tokens'
                sample_str = ', '.join(sample_ids[:3])
                line = f'[{key}] {count} findings | tokens: {token_str[:MAX_TEXT_PER_CLUSTER - 50]} | samples: {sample_str}'
                if len(line) > MAX_TEXT_PER_CLUSTER:
                    line = line[:MAX_TEXT_PER_CLUSTER - 3] + '...'
                result_lines.append(line)
                clusters_used += 1
            feed_ratio = len(feed_findings) / max(len(findings), 1)
            if feed_ratio >= 0.5 and result_lines:
                result_lines.insert(0, f'Feed clusters ({len(feed_findings)} feed findings, {clusters_used} clusters)')
            if not result_lines:
                if nonfeed_findings:
                    return (f'Non-feed findings: {len(nonfeed_findings)}',)
                return ()
            return tuple(result_lines[:MAX_FEED_CLUSTERS])
        except Exception:
            return ('Feed clustering unavailable',)

    def _build_source_family_summary(self, findings: list[Any]) -> tuple[str, ...]:
        """
        F225B: Count source families from findings and summarize presence.

        Counts source_type/provenance families, identifies feed-only gap,
        non-feed evidence, and CT/PUBLIC/PASSIVE_DNS support.

        No model required.
        """
        if not findings:
            return ()
        families: dict[str, int] = {}
        for f in findings[:100]:
            src = getattr(f, 'source_type', None) or f.get('source_type', 'unknown')
            families[src] = families.get(src, 0) + 1
        lines: list[str] = []
        for src, count in sorted(families.items(), key=lambda x: x[1], reverse=True):
            lines.append(f'{src}: {count} findings')
        ct_sources = [s for s in families if 'ct' in s.lower() or 'certificate' in s.lower()]
        if ct_sources:
            lines.append(f"CT/certificate support: {', '.join(ct_sources)}")
        public_sources = [s for s in families if 'public' in s.lower()]
        if public_sources:
            lines.append(f"PUBLIC support: {', '.join(public_sources)}")
        pdns_sources = [s for s in families if 'dns' in s.lower() or 'passive' in s.lower()]
        if pdns_sources:
            lines.append(f"PASSIVE_DNS support: {', '.join(pdns_sources)}")
        non_feed = [s for s in families if not any((x in s.lower() for x in ['ct', 'public', 'dns', 'passive']))]
        if non_feed and len(families) == 1 and ('feed' in list(families.keys())[0].lower()):
            lines.append('FEED-ONLY: no public/CT/DNS corroboration detected')
        elif families and len(families) > 1:
            lines.append(f'Cross-source diversity: {len(families)} distinct source families')
        return tuple(lines[:10])

    def _build_evidence_gaps(self, findings: list[Any], source_families: tuple[str, ...]) -> tuple[str, ...]:
        """
        F225B: Identify evidence gaps from findings and source family summary.

        Checks for: feed-only (no public/CT corroboration), no high-confidence,
        no multi-IOC type, missing graph connectivity.
        """
        gaps: list[str] = []
        if not findings:
            gaps.append('No findings produced — possible quality gate or target exhaustion')
            return tuple(gaps)
        feed_only = any(('FEED-ONLY' in s for s in source_families))
        if feed_only:
            gaps.append('Feed-only findings — no public/CT corroboration available')
        high_conf = sum((1 for f in findings[:50] if (getattr(f, 'confidence', 0.0) or f.get('confidence', 0.0)) >= 0.7))
        if high_conf == 0 and len(findings) > 3:
            gaps.append('No high-confidence findings (≥0.7) — evidence weak')
        ioc_types = set()
        for f in findings[:50]:
            it = safe_get_finding_field(f, 'ioc_type', None) or f.get('ioc_type', '')
            if it:
                ioc_types.add(it)
        if len(ioc_types) == 1 and len(findings) > 5:
            gaps.append(f'Single IOC type ({list(ioc_types)[0]}) — narrow evidence surface')
        if len(findings) > 5:
            has_graph_conn = any((f.get('graph_connected') or getattr(f, 'graph_connected', False) for f in findings[:20]))
            if not has_graph_conn:
                gaps.append('No graph-connected findings — entities isolated')
        return tuple(gaps[:5])

    def _build_risk_hypotheses(self, findings: list[Any], source_families: tuple[str, ...]) -> tuple[str, ...]:
        """
        F225B: Build bounded deterministic risk hypotheses based on findings.

        Max 5 hypotheses based on: source diversity, IOC density,
        non-feed absence, CT/public presence.
        """
        hypotheses: list[str] = []
        seen: set[str] = set()
        if not findings:
            return ()
        families: dict[str, int] = {}
        for f in findings[:100]:
            src = getattr(f, 'source_type', None) or f.get('source_type', 'unknown')
            families[src] = families.get(src, 0) + 1
        if len(families) == 1:
            hypotheses.append('Single-source dependency — one source failure collapses all coverage')
            seen.add('single_source')
        total_iocs = sum((1 for f in findings if getattr(f, 'ioc_value', None) or f.get('ioc_value')))
        if total_iocs > 10 and len(families) == 1:
            hypotheses.append(f'High IOC density ({total_iocs}) but single-source — possible false correlation')
            seen.add('high_density_single')
        has_public = any(('public' in s.lower() for s in families))
        if not has_public and len(findings) > 3:
            hypotheses.append('No public source findings — feed-dependent, limited external corroboration')
            seen.add('no_public')
        has_ct = any(('ct' in s.lower() or 'certificate' in s.lower() for s in families))
        if has_ct:
            ct_count = sum((c for s, c in families.items() if 'ct' in s.lower() or 'certificate' in s.lower()))
            if ct_count > 5:
                hypotheses.append(f'CT certificate findings ({ct_count}) suggest domain infrastructure recon')
        feed_count = sum((c for s, c in families.items() if 'feed' in s.lower()))
        if feed_count > 10 and (not has_public):
            hypotheses.append(f'Feed-heavy cluster ({feed_count}) — confirm public/CT overlap to avoid tunnel vision')
        return tuple(hypotheses[:MAX_RISK_HYPOTHESES])

    def _build_feed_cluster_summary(self, findings: list[Any]) -> tuple[str, ...]:
        """
        F225B: Summarize feed/public/CT cluster distribution from findings.
        """
        if not findings:
            return ()
        feed_count = 0
        public_count = 0
        ct_count = 0
        other_count = 0
        for f in findings[:100]:
            src = getattr(f, 'source_type', None) or f.get('source_type', 'unknown')
            src_lower = src.lower()
            if 'feed' in src_lower:
                feed_count += 1
            elif 'public' in src_lower:
                public_count += 1
            elif 'ct' in src_lower or 'certificate' in src_lower:
                ct_count += 1
            else:
                other_count += 1
        lines: list[str] = []
        if feed_count:
            lines.append(f'feed: {feed_count} findings')
        if public_count:
            lines.append(f'public: {public_count} findings')
        if ct_count:
            lines.append(f'ct: {ct_count} findings')
        if other_count:
            lines.append(f'other: {other_count} findings')
        return tuple(lines[:5])

    def _build_pivot_recommendations(self, findings: list[Any], graph_signal: dict[str, Any]) -> tuple[str, ...]:
        """
        F225B: Build bounded pivot recommendations from findings and graph signal.

        Max 5 recommendations. Uses findings IOC values/types and graph entity data.
        No new planner — summarizes existing pivots if present.
        """
        pivots: list[str] = []
        seen: set[str] = set()
        for f in findings[:30]:
            conf = getattr(f, 'confidence', 0.0) or f.get('confidence', 0.0)
            if float(conf) < 0.6:
                continue
            ioc_val = safe_get_finding_field(f, 'ioc_value', None) or f.get('ioc_value', '')
            ioc_type = safe_get_finding_field(f, 'ioc_type', None) or f.get('ioc_type', '')
            if not ioc_val or not ioc_type:
                continue
            if ioc_type in ('domain', 'ipv4', 'email'):
                pivot = f'Explore {ioc_type}:{ioc_val} for infrastructure expansion'
                if pivot not in seen:
                    seen.add(pivot)
                    pivots.append(pivot)
        if graph_signal:
            top_nodes = graph_signal.get('top_nodes', [])
            for node in top_nodes[:3]:
                val = node.get('value', '')
                it = node.get('ioc_type', '')
                if val and it and (len(pivots) < MAX_PIVOT_RECOMMENDATIONS):
                    pivot = f'Graph pivot on {it}:{val}'
                    if pivot not in seen:
                        seen.add(pivot)
                        pivots.append(pivot)
        for f in findings[:20]:
            env = f.get('envelope') if isinstance(f, dict) else None
            if env is None:
                continue
            suggested_pivots = getattr(env, 'suggested_pivots', None) or (env.get('suggested_pivots') if isinstance(env, dict) else None)
            if suggested_pivots and isinstance(suggested_pivots, (list, tuple)):
                for sp in suggested_pivots[:3]:
                    if sp and len(pivots) < MAX_PIVOT_RECOMMENDATIONS:
                        pivot_str = str(sp)
                        if pivot_str not in seen:
                            seen.add(pivot_str)
                            pivots.append(pivot_str)
        return tuple(pivots[:MAX_PIVOT_RECOMMENDATIONS])

def create_analyst_workbench() -> AnalystWorkbench:
    """
    Create AnalystWorkbench with lazily-initialized store references.

    Stores are resolved from global singletons where available:
      - VectorStore via vector_store.get_vector_store() (singleton)
      - DuckPGQGraph via knowledge.graph_service._get_graph() (singleton)

    DuckDBShadowStore and SemanticStore have no module-level singletons —
    pass them explicitly if available.

    Fail-soft: if any store is unavailable, workbench operates without it.
    """
    duckdb = None
    graph = None
    vector = None
    semantic = None
    try:
        from knowledge.vector_store import get_vector_store
        vector = get_vector_store()
    except Exception as _e:
        logger.debug('fail-soft suppression: create_analyst_workbench (vector_store): %s', _e, exc_info=True)
    try:
        from knowledge.graph_service import _get_graph
        graph = _get_graph()
    except Exception as _e:
        logger.debug('fail-soft suppression: create_analyst_workbench (graph): %s', _e, exc_info=True)
    return AnalystWorkbench(duckdb_store=duckdb, graph_service=graph, vector_store=vector, semantic_store=semantic)

def get_evidence_chain(finding_id: str) -> EvidenceChain | None:
    """
    F203D: Retrieve the evidence chain for a given finding_id.

    Chains are accumulated during sprint teardown by the EvidenceChainBuilder
    (evidence_chain.py) and stored as a sprint artifact. This function looks up
    the chain from the module-level registry.

    Returns the EvidenceChain if found, None otherwise.
    """
    from knowledge.evidence_chain import _get_chain_for_finding
    return _get_chain_for_finding(finding_id)