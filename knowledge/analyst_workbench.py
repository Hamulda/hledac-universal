# hledac/universal/knowledge/analyst_workbench.py
# Sprint F202F — Local Graph/RAG Analyst Workbench
# Zero external network / Zero LLM required for extractive fallback
# Model lifecycle via brain/model_lifecycle.py only
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
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "AnalystWorkbench",
    "AnalystAnswer",
    "AnalystBrief",
    "EvidencePointer",
    "RelatedEntity",
    "create_analyst_workbench",
    "get_evidence_chain",
]

# ============================================================================
# BOUNDS — never change these without F202F sprint review
# ============================================================================
MAX_CONTEXT_BYTES: int = 8192  # 8KB max context per answer
MAX_TOP_K: int = 20  # Max results from any single source
MAX_GRAPH_HOPS: int = 2  # Entity history max hops
MAX_EVIDENCE_PTRS: int = 5  # Max evidence pointers per answer
MAX_RELATED_ENTITIES: int = 10  # Max related entities per answer

# Evidence envelope max from F202A
MAX_ENVELOPE_SIZE: int = 4096

# Sprint F204E: Analyst Brief bounds
MAX_BRIEF_FINDINGS: int = 20  # Max key findings in brief
MAX_BRIEF_CHAINS: int = 5     # Max evidence chain IDs in brief
MAX_BRIEF_NEXT_ACTIONS: int = 10  # Max next actions in brief

# Sprint F206G: Graph analytics bounds
MAX_GRAPH_ANALYTICS_BRIEF_FINDINGS: int = 2  # Max graph analytics findings in brief


# ============================================================================
# Result DTOs
# ============================================================================


@dataclass(frozen=True, slots=True)
class AnalystBrief:
    """
    Sprint F204E: Analyst brief produced at sprint teardown.

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
@dataclass(frozen=True, slots=True)
class EvidencePointer:
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
    snippet: Optional[str] = None


@dataclass(frozen=True, slots=True)
class RelatedEntity:
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


@dataclass
class AnalystAnswer:
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
    llm_answer: Optional[str] = None
    evidence_pointers: list[EvidencePointer] = field(default_factory=list)
    related_entities: list[RelatedEntity] = field(default_factory=list)
    context_bytes: int = 0
    model_used: bool = False
    sources_used: list[str] = field(default_factory=list)
    timing_ms: float = 0.0


# ============================================================================
# Helpers
# ============================================================================
def _truncate_to_bytes(text: str, max_bytes: int = MAX_CONTEXT_BYTES) -> tuple[str, int]:
    """
    Truncate text to max_bytes UTF-8.

    Returns (truncated_text, actual_bytes).
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, len(encoded)
    # Binary search for truncation point
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, max_bytes


def _extract_snippet(payload_text: Optional[str], query: str, max_len: int = 200) -> Optional[str]:
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
        # Try first keyword
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
        snippet = "..." + snippet
    if end < len(payload_text):
        snippet = snippet + "..."
    return snippet[:max_len]


def _keyword_score(text: str, keywords: list[str]) -> float:
    """
    Score text by keyword overlap.

    Returns score in [0.0, 1.0] based on keyword match ratio.
    """
    if not keywords or not text:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for kw in keywords if kw.lower() in text_lower)
    return matches / len(keywords)


def _build_evidence_pointer(
    finding: dict[str, Any],
    snippet: Optional[str] = None,
) -> EvidencePointer:
    """Build EvidencePointer from a finding dict."""
    return EvidencePointer(
        finding_id=str(finding.get("id", finding.get("finding_id", ""))),
        source_type=str(finding.get("source_type", "unknown")),
        query=str(finding.get("query", "")),
        confidence=float(finding.get("confidence", 0.0)),
        ts=float(finding.get("ts", 0.0)),
        provenance=tuple(finding.get("provenance", [])),
        envelope_available=bool(finding.get("envelope")),
        snippet=snippet,
    )


# ============================================================================
# Main Facade
# ============================================================================
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

    def __init__(
        self,
        duckdb_store: Any = None,
        graph_service: Any = None,
        vector_store: Any = None,
        semantic_store: Any = None,
    ) -> None:
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
        self._logger = logging.getLogger(f"{__name__}.AnalystWorkbench")

    # -------------------------------------------------------------------------
    # Finding queries
    # -------------------------------------------------------------------------

    async def query_findings(
        self,
        query: str,
        limit: int = MAX_TOP_K,
        source_type: Optional[str] = None,
    ) -> list[dict[str, Any]]:
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
            self._logger.debug("duckdb_store not available, returning empty")
            return []

        try:
            raw = await self._duckdb.async_query_recent_findings(limit=MAX_TOP_K * 2)
        except Exception as e:
            self._logger.warning(f"query_findings failed: {e}")
            return []

        # Filter by source_type if specified
        if source_type:
            raw = [f for f in raw if f.get("source_type") == source_type]

        # Score by keyword match
        keywords = query.split()
        scored = []
        for f in raw:
            text = f.get("query", "") + " " + (f.get("payload_text") or "")
            score = _keyword_score(text, keywords)
            if score > 0:
                scored.append((score, f))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [f for _, f in scored[:limit]]

        self._logger.debug(f"query_findings('{query}') -> {len(results)} results")
        return results

    # -------------------------------------------------------------------------
    # Graph queries
    # -------------------------------------------------------------------------

    async def query_graph(
        self,
        entity_value: str,
        max_hops: int = MAX_GRAPH_HOPS,
    ) -> list[RelatedEntity]:
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
            self._logger.debug("graph_service not available, returning empty")
            return []

        try:
            history = self._graph.find_entity_history(entity_value, max_hops=max_hops)
        except Exception as e:
            self._logger.warning(f"query_graph failed: {e}")
            return []

        entities: dict[str, RelatedEntity] = {}
        for entry in history:
            val = entry.get("value", "")
            ioc_type = entry.get("ioc_type", "unknown")
            conf = float(entry.get("confidence", 0.0))
            hops = int(entry.get("hops", 0))
            rel_type = str(entry.get("relation_type", ""))

            key = f"{val}|{ioc_type}"
            if key not in entities:
                entities[key] = RelatedEntity(
                    entity_value=val,
                    entity_type=ioc_type,
                    confidence=conf,
                    hops=hops,
                    relation_types=frozenset([rel_type]),
                )
            else:
                existing = entities[key]
                entities[key] = RelatedEntity(
                    entity_value=val,
                    entity_type=ioc_type,
                    confidence=max(existing.confidence, conf),
                    hops=min(existing.hops, hops),
                    relation_types=existing.relation_types | {rel_type},
                )

        result = sorted(entities.values(), key=lambda e: (e.hops, -e.confidence))
        self._logger.debug(
            f"query_graph('{entity_value}') -> {len(result)} entities"
        )
        return result[:MAX_RELATED_ENTITIES]

    # -------------------------------------------------------------------------
    # Vector queries
    # -------------------------------------------------------------------------

    async def query_vectors(
        self,
        query_embedding: Any,  # np.ndarray
        k: int = MAX_TOP_K,
    ) -> list[tuple[str, float]]:
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
            self._logger.debug("vector_store not available, returning empty")
            return []

        try:
            results = self._vector.query(query_embedding, k=k, index_type="text")
        except Exception as e:
            self._logger.warning(f"query_vectors failed: {e}")
            return []

        self._logger.debug(f"query_vectors() -> {len(results)} results")
        return results

    # -------------------------------------------------------------------------
    # Semantic keyword query
    # -------------------------------------------------------------------------

    async def query_semantic(
        self,
        query: str,
        limit: int = MAX_TOP_K,
    ) -> list[str]:
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
            self._logger.debug("semantic_store not available, returning empty")
            return []

        try:
            # semantic_pivot returns list of finding_ids
            ids = await self._semantic.semantic_pivot(query, top_k=limit)
            return list(ids)[:limit]
        except Exception as e:
            self._logger.warning(f"query_semantic failed: {e}")
            return []

    # -------------------------------------------------------------------------
    # Core analyst pipeline
    # -------------------------------------------------------------------------

    async def ask(
        self,
        question: str,
        use_model: bool = False,
        model_name: Optional[str] = None,
    ) -> AnalystAnswer:
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

        # Phase 1: Finding retrieval
        findings = await self.query_findings(question, limit=MAX_TOP_K)

        # Phase 2: Graph traversal for key entities
        entities_from_q = self._extract_entities_from_question(question)
        all_related: list[RelatedEntity] = []
        for entity in entities_from_q[:3]:  # Max 3 entities from question
            related = await self.query_graph(entity)
            all_related.extend(related)

        # Deduplicate and cap
        seen: set[str] = set()
        unique_related: list[RelatedEntity] = []
        for e in all_related:
            key = f"{e.entity_value}|{e.entity_type}"
            if key not in seen:
                seen.add(key)
                unique_related.append(e)
        unique_related.sort(key=lambda x: (x.hops, -x.confidence))
        related_entities = unique_related[:MAX_RELATED_ENTITIES]

        # Phase 3: Build context chunks
        context_chunks: list[str] = []
        for f in findings:
            chunk = f.get("query", "")
            if f.get("payload_text"):
                chunk += " " + f["payload_text"]
            context_chunks.append(chunk)

        # Add entity info
        for e in related_entities:
            chunk = f"{e.entity_type}:{e.entity_value}"
            if e.relation_types:
                chunk += " (" + ", ".join(e.relation_types) + ")"
            context_chunks.append(chunk)

        # Phase 4: Truncate to MAX_CONTEXT_BYTES
        full_context = "\n".join(context_chunks)
        truncated_context, context_bytes = _truncate_to_bytes(
            full_context, MAX_CONTEXT_BYTES
        )

        # Phase 5: Extractive answer (no model required)
        extractive_answer = self._extract_answer(truncated_context, question)

        # Phase 6: Evidence pointers
        evidence_pointers = self._build_evidence_pointers(findings)

        # Phase 7: Optional LLM answer
        llm_answer: Optional[str] = None
        sources_used = list(set(f.get("source_type", "unknown") for f in findings))
        if use_model and model_name:
            llm_answer = await self._generate_llm_answer(
                question, truncated_context, model_name
            )

        elapsed_ms = (time.monotonic() - t0) * 1000

        return AnalystAnswer(
            question=question,
            extractive_answer=extractive_answer,
            llm_answer=llm_answer,
            evidence_pointers=evidence_pointers,
            related_entities=related_entities,
            context_bytes=context_bytes,
            model_used=use_model,
            sources_used=sources_used,
            timing_ms=elapsed_ms,
        )

    def _extract_answer(self, context: str, question: str) -> str:
        """
        Deterministic extractive answer from context chunks.

        Returns the longest contiguous text span that contains
        the most question keywords. No model required.

        Fail-soft: returns "No relevant information found." on any error.
        """
        if not context.strip():
            return "No relevant information found."

        keywords = [kw.lower() for kw in question.split() if len(kw) > 3]
        if not keywords:
            return context[:500]

        # Find best paragraph
        paragraphs = context.split("\n")
        best_para = ""
        best_score = 0.0

        for para in paragraphs:
            if not para.strip():
                continue
            score = _keyword_score(para, keywords)
            if score > best_score:
                best_score = score
                best_para = para

        if best_score > 0 and best_para:
            # Truncate if too long (already truncated via [:MAX_CONTEXT_BYTES] on chars)
            return best_para.strip()

        return context[:500].strip() if context else "No relevant information found."

    async def _generate_llm_answer(
        self,
        question: str,
        context: str,
        model_name: str,
    ) -> Optional[str]:
        """
        Generate LLM answer using brain/model_lifecycle.py.

        Load/unload only through canonical model_lifecycle interface.
        Returns None on any failure (fail-soft).
        """
        try:
            from brain.model_lifecycle import load_model, unload_model

            # Load model
            load_model(model_name)

            # Generate answer (simplified — actual implementation
            # would use the loaded model's generate interface)
            # This is a placeholder that would be replaced with actual
            # MLX generate call once model is loaded
            answer = self._extract_answer(context, question)

            # Unload model
            unload_model()

            return answer
        except Exception as e:
            self._logger.warning(f"LLM answer generation failed: {e}")
            return None

    def _extract_entities_from_question(self, question: str) -> list[str]:
        """
        Extract potential IOC entities from question using regex patterns.

        Returns list of entity values (domains, IPs, emails, hashes).
        """
        entities: list[str] = []

        # Domain pattern
        domains = re.findall(
            r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b",
            question,
        )
        entities.extend(domains)

        # IP v4 pattern
        ips = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", question)
        entities.extend(ips)

        # Email pattern
        emails = re.findall(r"\b[\w.-]+@[\w.-]+\.\w+\b", question)
        entities.extend(emails)

        # MD5/SHA hash patterns
        hashes = re.findall(
            r"\b(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{40}|[a-fA-F0-9]{64})\b", question
        )
        entities.extend(hashes)

        return entities

    def _build_evidence_pointers(
        self,
        findings: list[dict[str, Any]],
    ) -> list[EvidencePointer]:
        """
        Build evidence pointers from findings.

        Caps at MAX_EVIDENCE_PTRS, ordered by confidence descending.
        """
        pointers: list[EvidencePointer] = []
        for f in findings:
            if len(pointers) >= MAX_EVIDENCE_PTRS:
                break
            snippet = None
            if f.get("payload_text"):
                snippet = _extract_snippet(
                    f["payload_text"], f.get("query", ""), max_len=200
                )
            pointers.append(_build_evidence_pointer(f, snippet))

        pointers.sort(key=lambda p: p.confidence, reverse=True)
        return pointers[:MAX_EVIDENCE_PTRS]

    # -------------------------------------------------------------------------
    # Convenience sync wrapper
    # -------------------------------------------------------------------------

    def ask_sync(
        self,
        question: str,
        use_model: bool = False,
        model_name: Optional[str] = None,
    ) -> AnalystAnswer:
        """
        Synchronous wrapper around ask().

        For use in sync contexts. Prefer ask() in async contexts.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            # We're in an async context — delegate
            return loop.run_until_complete(
                self.ask(question, use_model=use_model, model_name=model_name)
            )
        except RuntimeError:
            # No running loop — create new one
            return asyncio.run(
                self.ask(question, use_model=use_model, model_name=model_name)
            )

    # -------------------------------------------------------------------------
    # F203D: Evidence Chain lookup
    # -------------------------------------------------------------------------

    async def get_evidence_chain(self, finding_id: str) -> "EvidenceChain | None":
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
        # Import here to avoid circular imports
        try:
            from knowledge.evidence_chain import _get_chain_for_finding

            return _get_chain_for_finding(finding_id)
        except Exception:
            self._logger.warning(f"get_evidence_chain({finding_id}) failed")
            return None

    # -------------------------------------------------------------------------
    # F204D: Target memory read helper
    # -------------------------------------------------------------------------

    async def get_target_memory_summary(self, target_id: str) -> Optional[dict]:
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
            return {
                "target_id": memory.target_id,
                "sprint_count": memory.sprint_count,
                "cumulative_finding_count": memory.cumulative_finding_count,
                "entity_facets": memory.entity_facets,
                "exposure_facets": memory.exposure_facets,
                "pivot_facets": memory.pivot_facets,
                "confidence_drift": memory.confidence_drift,
                "updated_by_sprint_id": memory.updated_by_sprint_id,
            }
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # F204E: Sprint Brief Generation
    # -------------------------------------------------------------------------

    async def build_sprint_brief(
        self,
        sprint_id: str,
        target_id: str,
        findings: list[Any],
        graph_signal: dict[str, Any],
        governor: Any = None,
        duckdb_store: Any = None,
    ) -> AnalystBrief:
        """
        F204E: Build a model-free analyst brief at sprint teardown.

        Generates a summary of sprint results: what changed, strongest evidence,
        next best pivots, and open questions. Uses extractive analysis only —
        no model loading required.

        RAM guard: if governor is critical/emergency, generates minimal brief
        from counts only (no graph queries).

        F205J: If duckdb_store is available, reads cross-sprint target memory
        via get_target_memory_summary(target_id) and incorporates it into
        headline, key_findings, and open_questions.

        Bounds:
          - MAX_BRIEF_FINDINGS = 20
          - MAX_BRIEF_CHAINS = 5
          - MAX_BRIEF_NEXT_ACTIONS = 10
          - MAX_CONTEXT_BYTES = 8192

        Args:
            sprint_id: Sprint identifier
            target_id: Research target (query or canonical target_id)
            findings: List of findings from the sprint
            graph_signal: Graph signal dict from _get_graph_signal()
            governor: Optional M1ResourceGovernor for RAM check
            duckdb_store: Optional DuckDBShadowStore for target memory read

        Returns:
            AnalystBrief with all fields populated or minimal fallback
        """
        import time as _time

        ts = _time.time()

        # RAM guard: if governor critical/emergency, generate minimal brief
        if governor is not None:
            try:
                snap = governor.snapshot()
                uma_state = getattr(snap, "uma_state", "ok") if snap else "ok"
                if uma_state in ("critical", "emergency"):
                    finding_count = len(findings)
                    graph_nodes = graph_signal.get("graph_nodes", 0) if graph_signal else 0
                    graph_edges = graph_signal.get("graph_edges", 0) if graph_signal else 0
                    return AnalystBrief(
                        sprint_id=sprint_id,
                        target_id=target_id,
                        headline=f"Sprint {sprint_id}: {finding_count} findings, {graph_nodes} graph nodes (RAM pressure — minimal brief)",
                        key_findings=(
                            f"Accepted findings: {finding_count}",
                            f"Graph nodes: {graph_nodes}",
                            f"Graph edges: {graph_edges}",
                        ),
                        evidence_chain_ids=(),
                        next_actions=("Continue investigation with reduced scope",),
                        open_questions=("What caused RAM pressure?",),
                        confidence=0.3,
                        generated_ts=ts,
                    )
            except Exception:
                pass  # Fall through to normal path

        # F206G: Read graph analytics summary (bounded, fail-soft)
        graph_analytics: dict[str, Any] = {}
        try:
            from hledac.universal.knowledge.graph_service import graph_analytics_summary
            graph_analytics = graph_analytics_summary(top_k=MAX_GRAPH_ANALYTICS_BRIEF_FINDINGS + 5)
        except Exception:
            graph_analytics = {}

        # F205J: Read target memory fail-soft
        target_memory: dict[str, Any] | None = None
        _store = duckdb_store or self._duckdb
        if _store and target_id:
            try:
                target_memory = await self.get_target_memory_summary(target_id)
            except Exception:
                target_memory = None

        # Normal path: extractive analysis from findings + graph + target memory
        try:
            # Extract key findings from findings (extractive, no model)
            key_findings_list = self._extract_key_findings(findings)

            # F205J: Append target memory facets if available
            # F206H: Include explainable drift (entity_delta, exposure_delta, pivot_delta)
            if target_memory:
                mem_sprints = target_memory.get("sprint_count", 0)
                mem_findings = target_memory.get("cumulative_finding_count", 0)
                entity_count = len(target_memory.get("entity_facets", {}))
                exposure_count = len(target_memory.get("exposure_facets", {}))
                pivot_count = len(target_memory.get("pivot_facets", {}))
                drift = target_memory.get("confidence_drift", {})
                drift_ratio = drift.get("drift_ratio", 1.0) if drift else 1.0

                mem_finding = (
                    f"Target memory: {mem_sprints} sprints, {mem_findings} cumulative findings, "
                    f"{entity_count} entities, {exposure_count} exposures, {pivot_count} pivots "
                    f"(drift={drift_ratio:.2f})"
                )
                key_findings_list.append(mem_finding)

                # F206H: Append concise drift explanation if available
                drift_reasons = drift.get("drift_reasons", []) if drift else []
                if drift_reasons:
                    # Concise: first 3 reasons max
                    concise = drift_reasons[:3]
                    drift_exp = f"Drift signals: {', '.join(concise)}"
                    key_findings_list.append(drift_exp)

                # High-drift open question
                if drift_ratio > 1.5:
                    open_drift_q = (
                        f"Finding rate drift detected (ratio={drift_ratio:.2f}): "
                        f"this sprint yield is {int((drift_ratio - 1) * 100)}% above average"
                    )
                elif mem_sprints >= 3 and drift_ratio >= 0.7:
                    open_drift_q = (
                        f"Target has {mem_sprints} prior sprints — consider graph expansion"
                    )
                else:
                    open_drift_q = None
            else:
                open_drift_q = None

            # F206G: Append up to 2 graph analytics findings (bounded)
            if graph_analytics.get("analytics_available") and graph_analytics.get("top_central_entities"):
                top_entities = graph_analytics["top_central_entities"]
                community_count = graph_analytics.get("community_count", 0)
                # Add top entity finding
                if top_entities:
                    top = top_entities[0]
                    key_findings_list.append(
                        f"Graph central entity: {top.get('value', '?')} "
                        f"({top.get('ioc_type', '?')}, degree={top.get('degree', 0)})"
                    )
                # Add second entity or community finding
                if len(top_entities) > 1:
                    second = top_entities[1]
                    key_findings_list.append(
                        f"Graph entity 2: {second.get('value', '?')} "
                        f"({second.get('ioc_type', '?')}, degree={second.get('degree', 0)})"
                    )
                elif community_count > 1:
                    key_findings_list.append(
                        f"Graph communities: ~{community_count} detected communities"
                    )

            key_findings = tuple(key_findings_list[:MAX_BRIEF_FINDINGS])

            # Build headline from finding counts + memory context
            finding_count = len(findings)
            graph_nodes = graph_signal.get("graph_nodes", 0) if graph_signal else 0
            graph_edges = graph_signal.get("graph_edges", 0) if graph_signal else 0
            if target_memory:
                mem_sprints = target_memory.get("sprint_count", 0)
                headline = (
                    f"Sprint {sprint_id} (target {target_id}, {mem_sprints} prior sprints): "
                    f"{finding_count} findings, {graph_nodes} nodes, {graph_edges} edges"
                )
            else:
                headline = (
                    f"Sprint {sprint_id}: {finding_count} findings, "
                    f"{graph_nodes} graph nodes, {graph_edges} edges"
                )

            # Extract evidence chain IDs from findings (first MAX_BRIEF_CHAINS)
            chain_ids: list[str] = []
            for f in findings[:50]:  # Check first 50 findings for chain IDs
                fid = getattr(f, "finding_id", None) or f.get("finding_id", "")
                if fid and "chain" in str(f.get("provenance", "")):
                    chain_ids.append(str(fid))
            evidence_chain_ids = tuple(chain_ids[:MAX_BRIEF_CHAINS])

            # Generate next actions from high-confidence findings
            next_actions = self._derive_next_actions(findings)
            next_actions_tuple = tuple(next_actions[:MAX_BRIEF_NEXT_ACTIONS])

            # Derive open questions from gaps + target memory
            open_questions = list(self._derive_open_questions(findings, graph_signal))
            if open_drift_q and len(open_questions) < 5:
                open_questions.append(open_drift_q)
            if not target_memory and finding_count > 0:
                open_questions.append("No prior target memory — consider establishing baseline")

            # Confidence based on finding density and memory
            confidence = 0.7 if finding_count > 10 else 0.5 if finding_count > 0 else 0.3
            if target_memory:
                confidence = min(0.9, confidence + 0.1)  # Memory boost

            return AnalystBrief(
                sprint_id=sprint_id,
                target_id=target_id,
                headline=headline,
                key_findings=key_findings,
                evidence_chain_ids=evidence_chain_ids,
                next_actions=next_actions_tuple,
                open_questions=tuple(open_questions[:5]),
                confidence=confidence,
                generated_ts=ts,
            )
        except Exception:
            # Fallback: minimal brief on any error
            return AnalystBrief(
                sprint_id=sprint_id,
                target_id=target_id,
                headline=f"Sprint {sprint_id}: brief generation failed",
                key_findings=(f"Findings processed: {len(findings)}",),
                evidence_chain_ids=(),
                next_actions=("Review findings manually",),
                open_questions=("Why did brief generation fail?",),
                confidence=0.1,
                generated_ts=ts,
            )

    def _extract_key_findings(self, findings: list[Any]) -> list[str]:
        """
        Extract key findings as strings from the findings list.

        Uses extractive pattern: sorts by confidence and takes top items.
        No model required.
        """
        if not findings:
            return []

        # Sort by confidence (descending)
        scored: list[tuple[float, str]] = []
        for f in findings:
            conf = getattr(f, "confidence", None) or f.get("confidence", 0.0)
            conf = float(conf)
            # Extract a meaningful string representation
            ioc_type = getattr(f, "ioc_type", None) or f.get("ioc_type", "")
            ioc_value = getattr(f, "ioc_value", None) or f.get("ioc_value", "")
            query = getattr(f, "query", None) or f.get("query", "") or ""
            source = getattr(f, "source_type", None) or f.get("source_type", "")

            if ioc_value:
                text = f"{source}:{ioc_type}={ioc_value} (conf={conf:.2f})"
            elif query:
                text = f"{source}: {query[:80]} (conf={conf:.2f})"
            else:
                text = f"{source} finding (conf={conf:.2f})"

            scored.append((conf, text))

        scored.sort(key=lambda x: x[0], reverse=True)

        # De-duplicate similar texts
        seen: set[str] = set()
        unique: list[str] = []
        for conf, text in scored:
            # Simple dedup: first 60 chars as key
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

        # Patterns for next actions based on findings
        source_iocs: dict[str, dict[str, int]] = {}
        for f in findings:
            source = getattr(f, "source_type", None) or f.get("source_type", "unknown")
            ioc_type = getattr(f, "ioc_type", None) or f.get("ioc_type", "unknown")
            conf = getattr(f, "confidence", None) or f.get("confidence", 0.0)
            if float(conf) < 0.5:
                continue
            if source not in source_iocs:
                source_iocs[source] = {}
            source_iocs[source][ioc_type] = source_iocs[source].get(ioc_type, 0) + 1

        # Generate actions from patterns
        for source, iocs in source_iocs.items():
            for ioc_type, count in sorted(iocs.items(), key=lambda x: x[1], reverse=True)[:2]:
                if count >= 2:
                    action = f"Expand {ioc_type} investigation via {source}"
                    if action not in seen:
                        seen.add(action)
                        actions.append(action)

        # Add pivot suggestions based on high-confidence IOCs
        for f in findings[:20]:
            conf = getattr(f, "confidence", None) or f.get("confidence", 0.0)
            if float(conf) < 0.7:
                continue
            ioc_value = getattr(f, "ioc_value", None) or f.get("ioc_value", "")
            ioc_type = getattr(f, "ioc_type", None) or f.get("ioc_type", "")
            if ioc_value and ioc_type in ("domain", "ipv4", "email"):
                action = f"Pivot on {ioc_type}:{ioc_value}"
                if action not in seen:
                    seen.add(action)
                    actions.append(action)

        return actions

    def _derive_open_questions(
        self, findings: list[Any], graph_signal: dict[str, Any]
    ) -> tuple[str, ...]:
        """
        Derive open questions from gaps in findings and graph.

        Checks for common gaps: low finding count, no high-confidence findings,
        sparse graph, missing IOC types.
        """
        questions: list[str] = []
        seen: set[str] = set()

        finding_count = len(findings)
        if finding_count == 0:
            q = "Why did the sprint produce no findings?"
            if q not in seen:
                seen.add(q)
                questions.append(q)

        # Check for missing IOC types
        ioc_types: set[str] = set()
        high_conf_count = 0
        for f in findings:
            ioc_type = getattr(f, "ioc_type", None) or f.get("ioc_type", "")
            conf = getattr(f, "confidence", None) or f.get("confidence", 0.0)
            if ioc_type:
                ioc_types.add(ioc_type)
            if float(conf) >= 0.7:
                high_conf_count += 1

        if high_conf_count == 0 and finding_count > 0:
            q = "Why are there no high-confidence findings?"
            if q not in seen:
                seen.add(q)
                questions.append(q)

        # Check graph signal
        graph_nodes = graph_signal.get("graph_nodes", 0) if graph_signal else 0
        if graph_nodes == 0 and finding_count > 0:
            q = "Why are no entities connected in the graph?"
            if q not in seen:
                seen.add(q)
                questions.append(q)

        # Check for domain coverage
        if "domain" not in ioc_types and finding_count > 5:
            q = "Why were no domain IOCs extracted?"
            if q not in seen:
                seen.add(q)
                questions.append(q)

        return tuple(questions[:5])  # Max 5 open questions


# ============================================================================
# Factory
# ============================================================================
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
    except Exception:
        pass

    try:
        from knowledge.graph_service import _get_graph

        graph = _get_graph()
    except Exception:
        pass

    return AnalystWorkbench(
        duckdb_store=duckdb,
        graph_service=graph,
        vector_store=vector,
        semantic_store=semantic,
    )


# ============================================================================
# F203D: Evidence Chain Lookup
# ============================================================================

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
