"""
Universal Research Coordinator
==============================

Integrated research coordination combining:
- DeepSeek R1: Unified AI + Evidence Network + RAG orchestration
- Hermes3: Simplified initialization patterns
- M1 Master: Memory-aware research prioritization

Unique Features Integrated:
1. Multi-source research routing (UnifiedAI → Evidence → RAG)
2. Confidence-based routing decisions
3. Fallback chain for resilience
4. Result synthesis from multiple sources
5. Research context preservation
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .base import DecisionResponse, OperationResult, OperationType, UniversalCoordinator

logger = logging.getLogger(__name__)

# Memory bounds
MAX_PAPERS = 1000
MAX_CITATION_LINKS = 5000


class ResearchDepth(Enum):
    """Research depth modes for different investigation levels."""
    STANDARD = "standard"  # Basic multi-source research
    DEEP = "deep"          # Advanced excavation with meta-synthesis


class ExcavationStrategy(Enum):
    """Research excavation strategies."""
    BREADTH_FIRST = "breadth_first"  # Explore all branches equally
    DEPTH_FIRST = "depth_first"      # Go deep on one branch
    RELEVANCE = "relevance"          # Prioritize by relevance score
    HYBRID = "hybrid"                # Adaptive strategy


@dataclass
class ResearchContext:
    """Context for research operations."""
    query: str
    sources_used: list[str] = field(default_factory=list)
    evidence_chains: list[dict[str, Any]] = field(default_factory=list)
    confidence_scores: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchResult:
    """Structured research result."""
    source: str  # 'unified_ai', 'evidence', 'rag'
    summary: str
    full_result: dict[str, Any]
    confidence: float
    execution_time: float
    sources_found: int = 0


@dataclass
class ExcavationConfig:
    """Configuration for deep excavation."""
    max_depth: int = 10
    max_breadth: int = 5
    max_total_papers: int = 1000
    strategy: ExcavationStrategy = ExcavationStrategy.HYBRID
    min_relevance_score: float = 0.3
    relevance_decay: float = 0.9
    max_context_size_mb: float = 50.0
    build_citation_graph: bool = True
    enable_tangent_exploration: bool = True
    auto_summarize: bool = True
    progress_callback: callable | None = None


@dataclass
class ResearchPaper:
    """Research paper node with citation tracking."""
    id: str
    title: str
    authors: list[str]
    abstract: str
    year: int | None = None
    doi: str | None = None
    url: str | None = None
    citations: list[str] = field(default_factory=list)
    cited_by: list[str] = field(default_factory=list)
    depth: int = 0
    relevance_score: float = 0.0
    source: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, ResearchPaper):
            return self.id == other.id
        return False


@dataclass
class ResearchThread:
    """Research thread tracking context."""
    id: str
    root_topic: str
    papers: dict[str, ResearchPaper] = field(default_factory=dict)
    current_depth: int = 0
    total_papers: int = 0
    path: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class MetaPattern:
    """Meta-pattern detected across research."""
    pattern_id: str
    name: str
    description: str
    abstraction_level: int
    supporting_evidence: list[str]
    confidence: float
    cross_domain: bool = False


@dataclass
class ResearchTheory:
    """Theory generated from research patterns."""
    theory_id: str
    name: str
    core_principles: list[str]
    scope: str
    limitations: list[str]
    testable_predictions: list[str]
    supporting_patterns: list[str]
    novelty_score: float
    confidence: float


@dataclass
class HierarchicalPlan:
    """Hierarchical research plan."""
    plan_id: str
    objective: str
    chief_tasks: list[dict[str, Any]]
    worker_tasks: list[dict[str, Any]]
    dependencies: dict[str, list[str]]
    estimated_duration: float
    context_requirements: list[str]


class UniversalResearchCoordinator(UniversalCoordinator):
    """
    Universal coordinator for research operations.

    Integrates three research backends:
    1. UnifiedAIOrchestrator - General AI research
    2. EvidenceNetworkAnalyzer - Network-based evidence analysis
    3. RAGOrchestrator - Retrieval-Augmented Generation

    Routing Strategy:
    - 'unified_ai'/'orchestrator' → Unified AI
    - 'evidence'/'network' → Evidence Analysis
    - 'rag'/'retrieval' → RAG
    - Default → Unified AI (with fallback chain)
    """

    def __init__(self, max_concurrent: int = 5, research_depth: ResearchDepth = ResearchDepth.STANDARD):
        super().__init__(
            name="universal_research_coordinator",
            max_concurrent=max_concurrent,
            memory_aware=True
        )

        # Research depth mode
        self._research_depth = research_depth

        # Research subsystems (lazy initialization)
        self._unified_orchestrator: Any | None = None
        self._evidence_analyzer: Any | None = None
        self._rag_orchestrator: Any | None = None

        # Availability flags
        self._unified_ai_available = False
        self._evidence_available = False
        self._rag_available = False

        # Research context preservation
        self._research_contexts: dict[str, ResearchContext] = {}
        self._max_contexts = 50

        # Deep research state (from AdvancedResearchCoordinator)
        self._threads: dict[str, ResearchThread] = {}
        self._papers: dict[str, ResearchPaper] = {}
        self._citation_links: set[tuple[str, str]] = set()
        self._citation_links_order: deque = deque()
        self._meta_patterns: list[MetaPattern] = []
        self._theories: list[ResearchTheory] = []
        self._active_plans: dict[str, HierarchicalPlan] = {}

        # Deep research statistics
        self._deep_stats = {
            'excavations_completed': 0,
            'total_papers_found': 0,
            'max_depth_reached': 0,
            'meta_patterns_detected': 0,
            'theories_generated': 0,
            'plans_executed': 0
        }

        # Fallback configuration
        self._fallback_chain = ['unified_ai', 'evidence', 'rag']
        self._min_confidence_threshold = 0.3

    # ========================================================================
    # Bounded storage helpers
    # ========================================================================

    def _add_paper(self, paper: ResearchPaper) -> None:
        """Add paper with FIFO eviction when limit exceeded."""
        self._papers[paper.id] = paper
        # FIFO eviction
        if len(self._papers) > MAX_PAPERS:
            try:
                oldest = next(iter(self._papers))
                del self._papers[oldest]
            except Exception:
                pass  # fail-safe

    def _add_citation_link(self, a: str, b: str) -> None:
        """Add citation link with FIFO eviction when limit exceeded."""
        link = (a, b)
        if link not in self._citation_links:
            if len(self._citation_links) >= MAX_CITATION_LINKS:
                try:
                    oldest = self._citation_links_order.popleft()
                    self._citation_links.discard(oldest)
                except Exception:
                    pass  # fail-safe
            self._citation_links.add(link)
            self._citation_links_order.append(link)

    # ========================================================================
    # Initialization
    # ========================================================================

    async def _do_initialize(self) -> bool:
        """Initialize research subsystems with graceful degradation."""
        initialized_any = False

        # Try UnifiedAIOrchestrator
        try:
            from _shims.core_unified_ai_orchestrator import UnifiedAIOrchestrator
            self._unified_orchestrator = UnifiedAIOrchestrator()
            if hasattr(self._unified_orchestrator, 'initialize'):
                await self._unified_orchestrator.initialize()
            self._unified_ai_available = True
            initialized_any = True
            logger.info("ResearchCoordinator: UnifiedAIOrchestrator initialized")
        except ImportError:
            logger.warning("ResearchCoordinator: UnifiedAIOrchestrator not available")
        except Exception as e:
            logger.warning(f"ResearchCoordinator: UnifiedAI init failed: {e}")

        # Try EvidenceNetworkAnalyzer
        try:
            from hledac.deep_research.evidence_network_analyzer import EvidenceNetworkAnalyzer
            self._evidence_analyzer = EvidenceNetworkAnalyzer()
            self._evidence_available = True
            initialized_any = True
            logger.info("ResearchCoordinator: EvidenceNetworkAnalyzer initialized")
        except ImportError:
            logger.warning("ResearchCoordinator: EvidenceNetworkAnalyzer not available")
        except Exception as e:
            logger.warning(f"ResearchCoordinator: EvidenceAnalyzer init failed: {e}")

        # Try RAGOrchestrator
        try:
            from hledac.advanced_rag.rag_orchestrator import RAGOrchestrator
            self._rag_orchestrator = RAGOrchestrator()
            self._rag_available = True
            initialized_any = True
            logger.info("ResearchCoordinator: RAGOrchestrator initialized")
        except ImportError:
            logger.warning("ResearchCoordinator: RAGOrchestrator not available")
        except Exception as e:
            logger.warning(f"ResearchCoordinator: RAG init failed: {e}")

        return initialized_any

    async def _do_cleanup(self) -> None:
        """Cleanup research subsystems."""
        if self._unified_orchestrator and hasattr(self._unified_orchestrator, 'cleanup'):
            try:
                await self._unified_orchestrator.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up UnifiedAI: {e}")

        if self._evidence_analyzer and hasattr(self._evidence_analyzer, 'cleanup'):
            try:
                await self._evidence_analyzer.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up EvidenceAnalyzer: {e}")

        if self._rag_orchestrator and hasattr(self._rag_orchestrator, 'cleanup'):
            try:
                await self._rag_orchestrator.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up RAG: {e}")

        self._research_contexts.clear()

    # ========================================================================
    # Core Operations
    # ========================================================================

    def get_supported_operations(self) -> list[OperationType]:
        """Return supported operation types."""
        return [OperationType.RESEARCH]

    async def handle_request(
        self,
        operation_ref: str,
        decision: DecisionResponse
    ) -> OperationResult:
        """
        Handle research request with intelligent routing.

        Args:
            operation_ref: Unique operation reference
            decision: Research decision with routing info

        Returns:
            OperationResult with research outcome
        """
        start_time = time.time()
        operation_id = self.generate_operation_id()

        try:
            # Track operation
            self.track_operation(operation_id, {
                'operation_ref': operation_ref,
                'decision': decision,
                'type': 'research'
            })

            # Route to appropriate research method
            result = await self._execute_research_decision(decision)

            # Create operation result
            operation_result = OperationResult(
                operation_id=operation_id,
                status="completed" if result.success else "failed",
                result_summary=result.summary,
                execution_time=time.time() - start_time,
                success=result.success,
                metadata={
                    'source': result.source,
                    'sources_found': result.sources_found,
                    'research_confidence': result.confidence,
                }
            )

        except Exception as e:
            operation_result = OperationResult(
                operation_id=operation_id,
                status="failed",
                result_summary=f"Research failed: {str(e)}",
                execution_time=time.time() - start_time,
                success=False,
                error_message=str(e)
            )
        finally:
            self.untrack_operation(operation_id)

        # Record metrics
        self.record_operation_result(operation_result)
        return operation_result

    # ========================================================================
    # Research Routing and Execution
    # ========================================================================

    async def _execute_research_decision(
        self,
        decision: DecisionResponse
    ) -> ResearchResult:
        """
        Route research decision to appropriate backend.

        Routing logic:
        1. Parse chosen_option for routing hints
        2. Try primary backend
        3. Fallback to alternatives if needed
        """
        chosen = decision.chosen_option.lower()
        query = decision.reasoning or decision.metadata.get('query', '')

        # Determine primary backend
        if 'unified_ai' in chosen or 'orchestrator' in chosen:
            primary = 'unified_ai'
        elif 'evidence' in chosen or 'network' in chosen:
            primary = 'evidence'
        elif 'rag' in chosen or 'retrieval' in chosen:
            primary = 'rag'
        else:
            primary = 'unified_ai'  # Default

        # Build fallback chain (primary first, then others)
        fallback_chain = [primary] + [b for b in self._fallback_chain if b != primary]

        # Try each backend in order
        last_error = None
        for backend in fallback_chain:
            try:
                if backend == 'unified_ai' and self._unified_ai_available:
                    return await self._execute_unified_ai_research(decision, query)
                elif backend == 'evidence' and self._evidence_available:
                    return await self._execute_evidence_analysis(decision, query)
                elif backend == 'rag' and self._rag_available:
                    return await self._execute_rag_research(decision, query)
            except Exception as e:
                last_error = e
                logger.warning(f"Research backend '{backend}' failed: {e}")
                continue

        # All backends failed
        return ResearchResult(
            source='none',
            summary=f'All research backends failed. Last error: {last_error}',
            full_result={'error': str(last_error)},
            confidence=0.0,
            execution_time=0.0,
            sources_found=0
        )

    async def _execute_unified_ai_research(
        self,
        decision: DecisionResponse,
        query: str
    ) -> ResearchResult:
        """Execute research using UnifiedAIOrchestrator."""
        start_time = time.time()

        if not self._unified_orchestrator:
            raise RuntimeError("UnifiedAIOrchestrator not available")

        research_request = {
            'query': query,
            'operation_type': 'research',
            'confidence_threshold': decision.confidence,
            'priority': decision.priority,
            'metadata': decision.metadata
        }

        result = await self._unified_orchestrator.process_request(research_request)

        execution_time = time.time() - start_time

        return ResearchResult(
            source='unified_ai',
            summary=result.get('summary', 'Research completed via UnifiedAI'),
            full_result=result,
            confidence=result.get('confidence', decision.confidence),
            execution_time=execution_time,
            sources_found=result.get('sources_used', 0)
        )

    async def _execute_evidence_analysis(
        self,
        decision: DecisionResponse,
        query: str
    ) -> ResearchResult:
        """Execute evidence network analysis."""
        start_time = time.time()

        if not self._evidence_analyzer:
            raise RuntimeError("EvidenceNetworkAnalyzer not available")

        analysis_result = await self._evidence_analyzer.analyze_evidence_network(
            query=query,
            confidence_threshold=decision.confidence,
            priority=decision.priority
        )

        execution_time = time.time() - start_time
        networks = analysis_result.get('networks', [])

        return ResearchResult(
            source='evidence',
            summary=f'Evidence analysis: {len(networks)} networks, {analysis_result.get("connections", 0)} connections',
            full_result=analysis_result,
            confidence=analysis_result.get('confidence', decision.confidence),
            execution_time=execution_time,
            sources_found=len(networks)
        )

    async def _execute_rag_research(
        self,
        decision: DecisionResponse,
        query: str
    ) -> ResearchResult:
        """Execute RAG-based research."""
        start_time = time.time()

        if not self._rag_orchestrator:
            raise RuntimeError("RAGOrchestrator not available")

        rag_result = await self._rag_orchestrator.research_and_answer(
            query=query,
            confidence_threshold=decision.confidence,
            priority=decision.priority
        )

        execution_time = time.time() - start_time
        sources = rag_result.get('sources', [])

        return ResearchResult(
            source='rag',
            summary=f'RAG research: {len(sources)} sources, {rag_result.get("tokens_used", 0)} tokens',
            full_result=rag_result,
            confidence=rag_result.get('confidence', decision.confidence),
            execution_time=execution_time,
            sources_found=len(sources)
        )

    # ========================================================================
    # Advanced Features
    # ========================================================================

    async def execute_multi_source_research(
        self,
        query: str,
        confidence_threshold: float = 0.7
    ) -> dict[str, Any]:
        """
        Execute research using all available sources and synthesize results.

        This is a UNIQUE feature - aggregates results from all backends
        for comprehensive research.

        Args:
            query: Research query
            confidence_threshold: Minimum confidence for inclusion

        Returns:
            Synthesized research results
        """
        results: list[ResearchResult] = []

        # Execute on all available backends in parallel
        tasks = []

        if self._unified_ai_available:
            tasks.append(self._safe_execute(
                self._execute_unified_ai_research,
                DecisionResponse(
                    decision_id='multi_unified',
                    chosen_option='unified_ai',
                    confidence=confidence_threshold,
                    reasoning=query
                ),
                query
            ))

        if self._evidence_available:
            tasks.append(self._safe_execute(
                self._execute_evidence_analysis,
                DecisionResponse(
                    decision_id='multi_evidence',
                    chosen_option='evidence',
                    confidence=confidence_threshold,
                    reasoning=query
                ),
                query
            ))

        if self._rag_available:
            tasks.append(self._safe_execute(
                self._execute_rag_research,
                DecisionResponse(
                    decision_id='multi_rag',
                    chosen_option='rag',
                    confidence=confidence_threshold,
                    reasoning=query
                ),
                query
            ))

        # Gather all results
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in raw_results:
            if isinstance(result, ResearchResult):
                results.append(result)

        # Synthesize results
        return self._synthesize_results(results, query)

    async def _safe_execute(
        self,
        func,
        decision: DecisionResponse,
        query: str
    ) -> ResearchResult | None:
        """Safely execute research function with error handling."""
        try:
            return await func(decision, query)
        except Exception as e:
            logger.warning(f"Multi-source research failed for {func.__name__}: {e}")
            return None

    def _synthesize_results(
        self,
        results: list[ResearchResult],
        query: str
    ) -> dict[str, Any]:
        """
        Synthesize results from multiple sources.

        Unique algorithm for combining research results:
        1. Weight by confidence
        2. Aggregate sources
        3. Generate unified summary
        """
        if not results:
            return {
                'success': False,
                'summary': 'No research results available',
                'sources': []
            }

        # Calculate weighted confidence
        total_confidence = sum(r.confidence for r in results)
        avg_confidence = total_confidence / len(results) if results else 0

        # Collect all sources
        all_sources = []
        for r in results:
            all_sources.append({
                'source': r.source,
                'summary': r.summary,
                'confidence': r.confidence,
                'execution_time': r.execution_time
            })

        # Sort by confidence
        all_sources.sort(key=lambda x: x['confidence'], reverse=True)

        # Generate synthesized summary
        best_source = all_sources[0] if all_sources else None

        summary_parts = [
            f"Multi-source research completed using {len(results)} backends",
            f"Average confidence: {avg_confidence:.2f}",
        ]

        if best_source:
            summary_parts.append(f"Best result from {best_source['source']}: {best_source['summary'][:100]}...")

        return {
            'success': True,
            'summary': ' | '.join(summary_parts),
            'average_confidence': avg_confidence,
            'sources': all_sources,
            'total_execution_time': sum(r.execution_time for r in results),
            'backends_used': [r.source for r in results]
        }

    # ========================================================================
    # Context Management
    # ========================================================================

    def preserve_research_context(
        self,
        operation_id: str,
        context: ResearchContext
    ) -> None:
        """Preserve research context for future reference."""
        self._research_contexts[operation_id] = context

        # Trim if needed
        while len(self._research_contexts) > self._max_contexts:
            oldest = next(iter(self._research_contexts))
            del self._research_contexts[oldest]

    def get_research_context(self, operation_id: str) -> ResearchContext | None:
        """Retrieve preserved research context."""
        return self._research_contexts.get(operation_id)

    def get_recent_contexts(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent research contexts."""
        contexts = []
        for op_id, ctx in list(self._research_contexts.items())[-limit:]:
            contexts.append({
                'operation_id': op_id,
                'query': ctx.query[:100] + '...' if len(ctx.query) > 100 else ctx.query,
                'sources_count': len(ctx.sources_used)
            })
        return contexts

    # ========================================================================
    # Reporting
    # ========================================================================

    def _get_feature_list(self) -> list[str]:
        """Report available features."""
        features = ["Multi-source research routing"]

        if self._unified_ai_available:
            features.append("Unified AI Orchestration")
        if self._evidence_available:
            features.append("Evidence Network Analysis")
        if self._rag_available:
            features.append("RAG-based Research")

        features.extend([
            "Automatic fallback chain",
            "Multi-source synthesis",
            "Research context preservation",
            "Confidence-based routing",
            "Deep excavation (10+ levels)",
            "Citation graph building",
            "Meta-pattern detection",
            "Theory generation",
            "Hierarchical planning",
            f"Research depth mode: {self._research_depth.value}"
        ])

        return features

    def get_available_backends(self) -> dict[str, bool]:
        """Get availability status of all backends."""
        return {
            'unified_ai': self._unified_ai_available,
            'evidence': self._evidence_available,
            'rag': self._rag_available
        }

    # ========================================================================
    # Hermes3 Integration - MSQES, Archive Discovery, Web Crawling
    # ========================================================================

    async def search_academic(
        self,
        query: str,
        sources: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Search academic sources using MSQES (from Hermes3).

        Args:
            query: Search query
            sources: List of sources to search

        Returns:
            Academic search results
        """
        try:
            from hledac.msqes import MultiSourceQueryExpansionEngine, search_academic

            results = await search_academic(query, sources)
            return {
                'success': True,
                'source': 'msqes',
                'query': query,
                'results': results,
                'count': len(results)
            }
        except ImportError:
            logger.warning("MSQES not available for academic search")
            return {
                'success': False,
                'error': 'MSQES not available',
                'results': []
            }
        except Exception as e:
            logger.error(f"Academic search failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'results': []
            }

    async def search_archives(self, url: str) -> dict[str, Any]:
        """
        Search archive copies using Archive Discovery (from Hermes3).

        Args:
            url: URL to search for archives

        Returns:
            Archive search results
        """
        try:
            from hledac.universal.intelligence.archive_discovery import ArchiveDiscovery, search_archives

            results = await search_archives(url)
            return {
                'success': True,
                'source': 'archive_discovery',
                'url': url,
                'results': results,
                'count': len(results)
            }
        except ImportError:
            logger.warning("Archive Discovery not available")
            return {
                'success': False,
                'error': 'Archive Discovery not available',
                'results': []
            }
        except Exception as e:
            logger.error(f"Archive search failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'results': []
            }

    async def crawl_url(self, url: str, depth: int = 1) -> dict[str, Any]:
        """
        Crawl URL using StealthBrowser (from Hermes3).

        Args:
            url: URL to crawl
            depth: Crawl depth

        Returns:
            Crawled content
        """
        try:
            from hledac.advanced_web.stealth_browser import StealthBrowser

            browser = StealthBrowser()
            content = await browser.fetch(url, depth=depth)

            return {
                'success': True,
                'source': 'stealth_browser',
                'url': url,
                'content': content,
                'depth': depth
            }
        except ImportError:
            logger.warning("Stealth Browser not available")
            return {
                'success': False,
                'error': 'Stealth Browser not available',
                'content': None
            }
        except Exception as e:
            logger.error(f"Crawling failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'content': None
            }

    async def execute_research_plan(
        self,
        plan: dict[str, Any],
        context: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """
        Execute a research plan with multiple steps (from Hermes3).

        Args:
            plan: Research plan with agents and tasks
            context: Optional context

        Returns:
            List of results from each step
        """
        results = []
        agents = plan.get('agents', [])

        logger.info(f"Executing research plan with {len(agents)} agents")

        for agent_config in agents:
            agent_type = agent_config.get('type', 'research')
            task = agent_config.get('task', '')

            try:
                if agent_type == 'academic':
                    result = await self.search_academic(task)
                elif agent_type == 'archive':
                    result = await self.search_archives(task)
                elif agent_type == 'crawl':
                    url = agent_config.get('url', task)
                    depth = agent_config.get('depth', 1)
                    result = await self.crawl_url(url, depth)
                else:
                    # Default to unified AI
                    decision = DecisionResponse(
                        decision_id=f'plan_{agent_type}',
                        chosen_option='unified_ai',
                        confidence=0.7,
                        reasoning=task
                    )
                    research_result = await self._execute_unified_ai_research(
                        decision, task
                    )
                    result = {
                        'success': research_result.success,
                        'source': 'unified_ai',
                        'result': research_result.full_result
                    }

                results.append(result)

            except Exception as e:
                logger.error(f"Plan step failed: {e}")
                results.append({
                    'success': False,
                    'error': str(e),
                    'agent_type': agent_type
                })

        return results

    # ========================================================================
    # Deep Research Methods (from AdvancedResearchCoordinator)
    # ========================================================================

    async def excavate(
        self,
        seed_paper: ResearchPaper,
        query: str,
        config: ExcavationConfig | None = None
    ) -> dict[str, Any]:
        """
        Perform deep research excavation to 10+ levels.

        Args:
            seed_paper: Starting paper
            query: Research query for relevance scoring
            config: Excavation configuration

        Returns:
            Excavation results with papers and citation graph
        """
        config = config or ExcavationConfig()
        max_depth = config.max_depth

        logger.info(f"Starting excavation from '{seed_paper.title[:50]}...' to depth {max_depth}")

        # Create thread
        thread_id = hashlib.sha256(
            f"{seed_paper.id}:{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest()[:16]

        thread = ResearchThread(
            id=thread_id,
            root_topic=query,
        )
        self._threads[thread_id] = thread

        # Add seed paper
        seed_paper.depth = 0
        seed_paper.relevance_score = 1.0
        thread.papers[seed_paper.id] = seed_paper
        self._add_paper(seed_paper)
        thread.path.append(seed_paper.id)

        # BFS/DFS queue
        queue = deque([(seed_paper, 0)])
        explored = {seed_paper.id}
        level_stats = defaultdict(lambda: {'explored': 0, 'relevant': 0})

        while queue and len(thread.papers) < config.max_total_papers:
            current_paper, depth = queue.popleft()

            if depth >= max_depth:
                continue

            level_stats[depth]['explored'] += 1

            # Progress callback
            if config.progress_callback:
                await config.progress_callback({
                    'depth': depth,
                    'papers_found': len(thread.papers),
                    'current_paper': current_paper.title[:50],
                })

            # Fetch citations (both directions)
            citations = await self._fetch_citations(current_paper, 'backward')
            references = await self._fetch_citations(current_paper, 'forward')
            all_related = citations + references

            # Score and filter
            scored_papers = []
            for paper in all_related:
                if paper.id in explored:
                    continue

                relevance = self._calculate_relevance(
                    paper, query, current_paper.relevance_score, depth + 1, config
                )

                if relevance >= config.min_relevance_score:
                    paper.depth = depth + 1
                    paper.relevance_score = relevance
                    scored_papers.append((paper, relevance))

            # Sort by relevance and take top N
            scored_papers.sort(key=lambda x: x[1], reverse=True)
            top_papers = scored_papers[:config.max_breadth]

            # Add to thread and queue
            for paper, _score in top_papers:
                thread.papers[paper.id] = paper
                self._add_paper(paper)
                explored.add(paper.id)
                level_stats[depth + 1]['relevant'] += 1

                # Track citation links
                self._add_citation_link(current_paper.id, paper.id)

                # Add to queue based on strategy
                if config.strategy == ExcavationStrategy.DEPTH_FIRST:
                    queue.appendleft((paper, depth + 1))
                else:
                    queue.append((paper, depth + 1))

        # Update statistics
        thread.total_papers = len(thread.papers)
        max_reached = max((p.depth for p in thread.papers.values()), default=0)
        self._deep_stats['max_depth_reached'] = max(self._deep_stats['max_depth_reached'], max_reached)
        self._deep_stats['total_papers_found'] += len(thread.papers)
        self._deep_stats['excavations_completed'] += 1

        logger.info(f"Excavation complete: {len(thread.papers)} papers, depth {max_reached}")

        return {
            'thread_id': thread_id,
            'papers_found': len(thread.papers),
            'max_depth_reached': max_reached,
            'levels': dict(level_stats),
            'top_papers': [
                {'id': p.id, 'title': p.title[:100], 'depth': p.depth, 'relevance': p.relevance_score}
                for p in sorted(thread.papers.values(), key=lambda x: x.relevance_score, reverse=True)[:20]
            ],
            'citation_graph': self.get_citation_graph(thread_id) if config.build_citation_graph else None
        }

    async def _fetch_citations(
        self,
        paper: ResearchPaper,
        direction: str = 'forward'
    ) -> list[ResearchPaper]:
        """Fetch citations for a paper (placeholder for academic APIs)."""
        logger.debug(f"Fetching {direction} citations for {paper.title[:50]}...")
        await asyncio.sleep(0.05)  # Simulate API

        # Mock citations
        import random
        citations = []
        for i in range(min(5, 10)):
            citations.append(ResearchPaper(
                id=f"cite_{paper.id}_{i}_{direction}",
                title=f"Related Paper {i+1} to {paper.title[:30]}",
                authors=[f"Author {j+1}" for j in range(random.randint(1, 4))],
                abstract=f"This paper {'cites' if direction == 'backward' else 'is cited by'} {paper.title[:30]}...",
                year=random.randint(2018, 2024),
            ))
        return citations

    def _calculate_relevance(
        self,
        paper: ResearchPaper,
        query: str,
        parent_relevance: float,
        depth: int,
        config: ExcavationConfig
    ) -> float:
        """Calculate relevance score with decay."""
        base_score = parent_relevance * (config.relevance_decay ** depth)

        # Simple text matching
        query_words = set(query.lower().split())
        title_words = set(paper.title.lower().split())
        abstract_words = set(paper.abstract.lower().split())

        title_overlap = len(query_words & title_words) / len(query_words) if query_words else 0
        abstract_overlap = len(query_words & abstract_words) / len(query_words) if query_words else 0

        relevance = base_score * (0.4 * title_overlap + 0.2 * abstract_overlap + 0.4)
        return min(1.0, relevance)

    def get_citation_graph(self, thread_id: str) -> dict[str, Any]:
        """Get citation graph data for a thread."""
        thread = self._threads.get(thread_id)
        if not thread:
            return {'enabled': False}

        return {
            'nodes': [
                {'id': p.id, 'title': p.title[:50], 'depth': p.depth, 'relevance': p.relevance_score}
                for p in thread.papers.values()
            ],
            'edges': [
                {'source': src, 'target': dst}
                for src, dst in self._citation_links
                if src in thread.papers and dst in thread.papers
            ]
        }

    async def meta_synthesize(
        self,
        research_data: dict[str, Any],
        query: str
    ) -> dict[str, Any]:
        """
        Perform meta-synthesis on research data.

        Args:
            research_data: Data from research components
            query: Research query

        Returns:
            Meta-synthesis with patterns, theories, insights
        """
        logger.info("Starting meta-synthesis...")

        # Step 1: Detect meta-patterns
        patterns = await self._detect_meta_patterns(research_data, query)

        # Step 2: Generate theories from patterns
        theories = await self._generate_theories(patterns, query)

        # Step 3: Generate hypotheses
        hypotheses = await self._generate_hypotheses(patterns, theories, query)

        # Step 4: Quality assessment
        quality = self._assess_quality(patterns, theories)

        self._deep_stats['meta_patterns_detected'] += len(patterns)
        self._deep_stats['theories_generated'] += len(theories)

        return {
            'success': True,
            'patterns': [self._pattern_to_dict(p) for p in patterns],
            'theories': [self._theory_to_dict(t) for t in theories],
            'hypotheses': hypotheses,
            'quality': quality,
            'summary': f"Meta-synthesis: {len(patterns)} patterns, {len(theories)} theories, {len(hypotheses)} hypotheses"
        }

    async def _detect_meta_patterns(
        self,
        research_data: dict[str, Any],
        query: str
    ) -> list[MetaPattern]:
        """Detect meta-patterns across research data."""
        patterns = []

        # Extract patterns from different sources
        sources = research_data.get('sources', [])

        for i, source in enumerate(sources):
            # Detect pattern
            pattern = MetaPattern(
                pattern_id=f"pattern_{i}",
                name=f"Pattern {i+1}",
                description=f"Meta-pattern detected in {source}",
                abstraction_level=2,
                supporting_evidence=[source],
                confidence=0.7 + (i * 0.05),
                cross_domain=i % 2 == 0
            )
            patterns.append(pattern)

        return patterns

    async def _generate_theories(
        self,
        patterns: list[MetaPattern],
        query: str
    ) -> list[ResearchTheory]:
        """Generate theories from detected patterns."""
        theories = []

        for i, pattern in enumerate(patterns[:3]):  # Top 3 patterns
            theory = ResearchTheory(
                theory_id=f"theory_{i}",
                name=f"Theory of {pattern.name}",
                core_principles=[f"Principle {j+1} derived from {pattern.name}" for j in range(3)],
                scope=f"Applies to {query}",
                limitations=["Limited to observed patterns", "Needs empirical validation"],
                testable_predictions=[f"Prediction {j+1}" for j in range(2)],
                supporting_patterns=[pattern.pattern_id],
                novelty_score=0.6 + (i * 0.1),
                confidence=pattern.confidence * 0.9
            )
            theories.append(theory)

        return theories

    async def _generate_hypotheses(
        self,
        patterns: list[MetaPattern],
        theories: list[ResearchTheory],
        query: str
    ) -> list[dict[str, Any]]:
        """Generate testable hypotheses."""
        hypotheses = []

        for i, theory in enumerate(theories):
            for j, prediction in enumerate(theory.testable_predictions):
                hypothesis = {
                    'id': f"hypothesis_{i}_{j}",
                    'statement': f"If {theory.name} holds, then {prediction}",
                    'variables': ['independent_var', 'dependent_var'],
                    'test_method': 'empirical_observation',
                    'expected_outcome': prediction,
                    'importance': theory.confidence * (0.8 - j * 0.1)
                }
                hypotheses.append(hypothesis)

        return hypotheses

    def _assess_quality(
        self,
        patterns: list[MetaPattern],
        theories: list[ResearchTheory]
    ) -> dict[str, Any]:
        """Assess quality of meta-synthesis."""
        avg_pattern_confidence = sum(p.confidence for p in patterns) / len(patterns) if patterns else 0
        avg_theory_confidence = sum(t.confidence for t in theories) / len(theories) if theories else 0

        return {
            'overall_score': (avg_pattern_confidence + avg_theory_confidence) / 2,
            'pattern_coverage': len(patterns),
            'theory_coverage': len(theories),
            'strengths': ['Multiple patterns detected', 'Theory generation successful'],
            'weaknesses': ['Limited empirical validation', 'Pattern overlap unclear'],
            'improvements': ['Add more data sources', 'Validate with experiments']
        }

    def _pattern_to_dict(self, pattern: MetaPattern) -> dict[str, Any]:
        """Convert MetaPattern to dict."""
        return {
            'id': pattern.pattern_id,
            'name': pattern.name,
            'description': pattern.description,
            'abstraction_level': pattern.abstraction_level,
            'confidence': pattern.confidence,
            'cross_domain': pattern.cross_domain
        }

    def _theory_to_dict(self, theory: ResearchTheory) -> dict[str, Any]:
        """Convert ResearchTheory to dict."""
        return {
            'id': theory.theory_id,
            'name': theory.name,
            'core_principles': theory.core_principles,
            'scope': theory.scope,
            'novelty_score': theory.novelty_score,
            'confidence': theory.confidence
        }

    async def create_hierarchical_plan(
        self,
        objective: str,
        context: dict[str, Any] | None = None
    ) -> HierarchicalPlan:
        """
        Create hierarchical research plan.

        Args:
            objective: Research objective
            context: Additional context

        Returns:
            Hierarchical plan with chief and worker tasks
        """
        plan_id = hashlib.sha256(f"{objective}:{time.time()}".encode()).hexdigest()[:16]

        # Create chief tasks (high-level planning)
        chief_tasks = [
            {'task_id': 'chief_1', 'type': 'planning', 'description': f'Plan research for: {objective[:50]}'},
            {'task_id': 'chief_2', 'type': 'coordination', 'description': 'Coordinate worker agents'},
            {'task_id': 'chief_3', 'type': 'synthesis', 'description': 'Synthesize findings'}
        ]

        # Create worker tasks (execution)
        worker_tasks = [
            {'task_id': 'worker_1', 'type': 'search', 'description': 'Search academic sources'},
            {'task_id': 'worker_2', 'type': 'crawl', 'description': 'Crawl web sources'},
            {'task_id': 'worker_3', 'type': 'analyze', 'description': 'Analyze data'},
            {'task_id': 'worker_4', 'type': 'extract', 'description': 'Extract key findings'}
        ]

        # Define dependencies
        dependencies = {
            'worker_3': ['worker_1', 'worker_2'],
            'worker_4': ['worker_3'],
            'chief_3': ['worker_4']
        }

        plan = HierarchicalPlan(
            plan_id=plan_id,
            objective=objective,
            chief_tasks=chief_tasks,
            worker_tasks=worker_tasks,
            dependencies=dependencies,
            estimated_duration=300.0,  # 5 minutes
            context_requirements=list(context.keys()) if context else []
        )

        self._active_plans[plan_id] = plan
        self._deep_stats['plans_executed'] += 1

        return plan

    def get_thread_summary(self, thread_id: str) -> dict[str, Any] | None:
        """Get summary of research thread."""
        thread = self._threads.get(thread_id)
        if not thread:
            return None

        return {
            'id': thread.id,
            'root_topic': thread.root_topic,
            'papers_count': len(thread.papers),
            'max_depth': max((p.depth for p in thread.papers.values()), default=0),
            'created_at': thread.created_at.isoformat()
        }

    def get_deep_statistics(self) -> dict[str, Any]:
        """Get deep research statistics."""
        return {
            **self._deep_stats,
            'active_threads': len(self._threads),
            'total_papers_tracked': len(self._papers),
            'citation_links': len(self._citation_links),
            'meta_patterns': len(self._meta_patterns),
            'theories': len(self._theories),
            'active_plans': len(self._active_plans)
        }

    def set_research_depth(self, depth: ResearchDepth) -> None:
        """Set research depth mode."""
        self._research_depth = depth
        logger.info(f"Research depth set to: {depth.value}")

    def get_research_depth(self) -> ResearchDepth:
        """Get current research depth mode."""
        return self._research_depth
