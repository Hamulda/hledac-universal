"""
EnhancedResearch - Dormant Canonical Provider Candidate
=====================================================

CLASSIFICATION (Sprint F11 Containment):
----------------------------------------
Tento modul obsahuje DVA odlišné surface:

1. UnifiedResearchEngine (PROVIDER CANDIDATE):
   - Dormant canonical provider candidate pro deep research
   - Úzký, typed, lazy provider seam: deep_research()
   - M1-friendly: lazy loading, bounded concurrency, chunked processing
   - Aktivace: PO triádě, source plane, transport plane, session seams,
     security gate, minimal grounding seam
   - Stav: DORMANT - není v hot path, čeká na F11 připojení

2. EnhancedResearchOrchestrator (ORCHESTRATOR RESIDUE):
   - NON-CANONICAL - rozšiřuje UniversalResearchOrchestrator
   - Obsahuje workflow engine, predictive planner, performance monitoring
   - Public methods jsou helper/non-canonical surfaces
   - Stav: DEPRECATED pro nový runtime - pouze backward compat

PUBLIC ENTRYPOINTS CLASSIFICATION:
----------------------------------
Provider Candidate Seam (canonical):
  - UnifiedResearchEngine.deep_research(query, depth, query_type, max_results)
  - UnifiedResearchEngine.__init__() s UnifiedResearchConfig

Non-Canonical Helpers (NEPOUŽÍVAT pro nový runtime):
  - enhanced_research() - convenience wrapper
  - deep_research() - convenience function
  - create_unified_research_engine() - factory

Orchestrator Residue (non-canonical, backward compat only):
  - EnhancedResearchOrchestrator (plně orchestrátor, ne provider)

DEPENDENCY MATRIX:
------------------
F10/F9 surfaces: ŽÁDNÉ přímé závislosti
- UnifiedResearchEngine používá: intelligence.* (lazy), utils.ranking, knowledge.rag_engine, layers.stealth_layer
- EnhancedResearchOrchestrator používá: types.UniversalResearchOrchestrator, utils.WorkflowEngine

ADMISSION BLOCKERS (před F11 připojením):
------------------------------------------
1. Triáda: PARTIAL — analyzer + capability router + tool registry EXISTUJÍ (Sprint F11),
   ale DeepResearch NENÍ napojen na triad admission seam
2. Source plane: EXISTS — SourceFamily (line 129) + SourcePlan (line 2348) +
   _build_source_plan() (line 2379) jsou definované
3. Transport plane (FetchCoordinator): EXISTS (FetchCoordinator class exists),
   exists, not wired to DeepResearch runtime path
4. Session seams (BudgetManager, EvidenceLog): exists, not wired to DeepResearch
5. Security gate (SecurityGate, privacy layer): exists, not wired to DeepResearch
6. Minimal grounding seam (ProviderRequest/ProviderResult handoff): exists, not wired to DeepResearch

M1 8GB Optimized: Lazy loading, chunked processing, aggressive memory management
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from .types import (
    UniversalResearchOrchestrator,
    ResearchConfig,
    ResearchMode,
    ResearchResult,
    ExecutionContext,
    RunCorrelation,
    CanonicalGroundingHints,
)
from .utils import WorkflowEngine, Workflow, Task, TaskType, PredictivePlanner, PerformanceMonitor

# Extended imports for research enhancements (from universal)
from .utils.ranking import ReciprocalRankFusion, RRFConfig, RankedResult as SearchResult
from .knowledge.rag_engine import RAGConfig, Document
from .utils.query_expansion import QueryExpander as IntelligentWordlistGenerator, ExpansionConfig as WordlistConfig
from .layers.stealth_layer import BehaviorSimulator, SimulationConfig, BehaviorPattern

# Intelligence tools (lazy loaded)
try:
    from .intelligence import (
        AcademicSearchEngine,
        ArchiveDiscovery,
        ArchiveResurrector,
        StealthCrawler,
        StealthWebScraper,
        UnifiedWebIntelligence,
        DataLeakHunter,
        TemporalAnalyzer,
        search_academic,
        search_archives,
        quick_scrape,
    )
    INTELLIGENCE_AVAILABLE = True
except ImportError:
    INTELLIGENCE_AVAILABLE = False

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS AND CONFIGURATION
# =============================================================================

class ResearchDepth(Enum):
    """Research depth levels - each adds more tools and thoroughness."""
    BASIC = auto()       # Web + Academic search
    ADVANCED = auto()    # + Archives + Stealth crawling
    EXHAUSTIVE = auto()  # + Data leaks + Temporal analysis + Full OSINT


class QueryType(Enum):
    """Types of queries for intelligent routing."""
    ACADEMIC = "academic"           # Research papers, citations
    TECHNICAL = "technical"         # Code, documentation, APIs
    NEWS = "news"                   # Current events, recent developments
    HISTORICAL = "historical"       # Past events, archives
    PERSON = "person"               # OSINT on individuals
    ORGANIZATION = "organization"   # Company, institution research
    SECURITY = "security"           # Vulnerabilities, breaches
    GENERAL = "general"             # Broad information gathering


class SourceFamily(Enum):
    """Source families for research — defines which engines/tools are used.

    PROVIDER-OWNED INTERNAL SEAM: This enum is an internal planning artifact,
    NOT a public authority surface. It is used by _build_source_plan() to
    determine which lazy-loaded engines to route to.

    LOCAL_CORPUS is a CONSUMER SEAM — DeepResearch does NOT own the search plane.
    It merely declares that it WOULD consume local corpus results if the plane
    existed. This is NOT a new retrieval authority (per F8 invariant).
    """
    WEB = "web"                       # StealthCrawler, UnifiedWebIntelligence
    ACADEMIC = "academic"             # AcademicSearchEngine (ArXiv, CrossRef, Semantic)
    ARCHIVE = "archive"               # ArchiveDiscovery, ArchiveResurrector
    SECURITY = "security"             # DataLeakHunter, StealthWebScraper
    TEMPORAL = "temporal"             # TemporalAnalyzer (EXHAUSTIVE only)
    OSINT = "osint"                   # DataLeakHunter + cross-reference (EXHAUSTIVE)
    LOCAL_CORPUS = "local_corpus"     # LOCAL CONSUMER SEAM — search plane consumer, NOT owner


@dataclass
class UnifiedResearchConfig:
    """Configuration for unified research engine.

    M1 8GB Optimized: All settings tuned for memory-constrained environments.

    Attributes:
        depth: Research depth level (BASIC/ADVANCED/EXHAUSTIVE)
        max_memory_mb: Maximum memory usage in MB
        enable_parallel: Enable parallel tool execution
        max_concurrent_tools: Maximum concurrent tools (M1: 2-3)
        chunk_size: Results processing chunk size
        enable_rrf: Enable Reciprocal Rank Fusion
        rrf_k: RRF fusion parameter
        enable_deduplication: Enable result deduplication
        enable_temporal_analysis: Enable time-series analysis
        enable_data_leak_check: Enable breach monitoring
        cache_results: Cache intermediate results
        cache_ttl_seconds: Cache time-to-live
    """
    # Depth and scope
    depth: ResearchDepth = ResearchDepth.ADVANCED

    # M1 8GB Optimization
    max_memory_mb: int = 4096  # Stay well under 8GB limit
    enable_parallel: bool = True
    max_concurrent_tools: int = 2  # Conservative for M1
    chunk_size: int = 50  # Process results in chunks

    # Result fusion
    enable_rrf: bool = True
    rrf_k: int = 60

    # Quality controls
    enable_deduplication: bool = True
    dedup_threshold: float = 0.85

    # Feature toggles by depth
    enable_temporal_analysis: bool = True
    enable_data_leak_check: bool = True
    enable_archive_search: bool = True
    enable_stealth_crawling: bool = True

    # Caching
    cache_results: bool = True
    cache_ttl_seconds: int = 3600

    # Sources configuration
    academic_sources: List[str] = field(default_factory=lambda: [
        'arxiv', 'crossref', 'semantic_scholar'
    ])
    archive_sources: List[str] = field(default_factory=lambda: [
        'wayback', 'archive_today'
    ])

    def should_use_tool(self, tool_name: str) -> bool:
        """Check if a tool should be used based on depth config."""
        tool_depth_map = {
            'academic': ResearchDepth.BASIC,
            'web': ResearchDepth.BASIC,
            'stealth_crawler': ResearchDepth.ADVANCED,
            'archives': ResearchDepth.ADVANCED,
            'temporal': ResearchDepth.EXHAUSTIVE,
            'data_leak': ResearchDepth.EXHAUSTIVE,
            'osint': ResearchDepth.EXHAUSTIVE,
        }
        required_depth = tool_depth_map.get(tool_name, ResearchDepth.BASIC)
        return self.depth.value >= required_depth.value


@dataclass
class ResearchFinding:
    """A single research finding with rich metadata."""
    id: str
    title: str
    content: str
    url: Optional[str]
    source: str  # Tool that found it
    source_type: str  # academic, web, archive, etc.
    timestamp: datetime
    relevance_score: float = 0.0
    credibility_score: float = 0.5
    temporal_relevance: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content[:500] if self.content else '',
            'url': self.url,
            'source': self.source,
            'source_type': self.source_type,
            'timestamp': self.timestamp.isoformat(),
            'relevance_score': self.relevance_score,
            'credibility_score': self.credibility_score,
            'metadata': self.metadata,
        }


@dataclass
class UnifiedResearchResult:
    """Complete result from unified research."""
    query: str
    depth: ResearchDepth
    query_type: QueryType

    # Results
    findings: List[ResearchFinding] = field(default_factory=list)
    fused_results: List[Dict[str, Any]] = field(default_factory=list)

    # Correlation context (canonical RunCorrelation for cross-component tracing)
    # This enables F11 activation path: correlation flows through result
    # so downstream components (EvidenceLog, BudgetManager) can correlate.
    correlation: Optional["RunCorrelation"] = None

    # Analysis
    temporal_analysis: Optional[Dict[str, Any]] = None
    cross_references: List[Dict[str, Any]] = field(default_factory=list)
    validation_report: Optional[Dict[str, Any]] = None

    # Sources and metadata
    sources_used: List[str] = field(default_factory=list)
    total_sources_found: int = 0
    unique_sources: int = 0

    # Performance
    execution_time_seconds: float = 0.0
    memory_peak_mb: float = 0.0
    tools_executed: List[str] = field(default_factory=list)

    # Quality metrics
    confidence_score: float = 0.0
    coverage_score: float = 0.0

    # Timestamp
    completed_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'query': self.query,
            'depth': self.depth.name,
            'query_type': self.query_type.value,
            'findings_count': len(self.findings),
            'sources_used': self.sources_used,
            'total_sources': self.total_sources_found,
            'unique_sources': self.unique_sources,
            'execution_time': self.execution_time_seconds,
            'confidence': self.confidence_score,
            'coverage': self.coverage_score,
            'completed_at': self.completed_at.isoformat(),
        }


@dataclass
class EnhancedResearchConfig:
    """Configuration for enhanced research workflow with advanced features.

    DEPRECATED: Use UnifiedResearchConfig instead for new code.

    Attributes:
        enable_fusion: Enable Reciprocal Rank Fusion for multi-source results
        rrf_k: RRF fusion parameter (default: 60)
        enable_rag: Enable Hybrid RAG for context retrieval
        rag_top_k: Number of top documents to retrieve (default: 5)
        enable_expansion: Enable query expansion for broader coverage
        max_query_variations: Maximum number of query variations (default: 10)
        enable_stealth: Enable behavior simulation for stealth access
        behavior_pattern: Behavior pattern for stealth mode (default: RESEARCHER)
        sources: List of research sources to use
    """
    # RRF Configuration
    enable_fusion: bool = True
    rrf_k: int = 60

    # RAG Configuration
    enable_rag: bool = True
    rag_top_k: int = 5

    # Query expansion configuration
    enable_expansion: bool = True
    max_query_variations: int = 10

    # Behavior simulation configuration
    enable_stealth: bool = True
    behavior_pattern: BehaviorPattern = BehaviorPattern.RESEARCHER

    # Sources configuration
    sources: List[str] = field(default_factory=lambda: [
        'web', 'scholar', 'arxiv', 'semantic_scholar', 'news'
    ])


# =============================================================================
# UNIFIED RESEARCH ENGINE - MAIN IMPLEMENTATION
# =============================================================================

class UnifiedResearchEngine:
    """
    Unified Research Engine - Kompletní integrace všech výzkumných nástrojů.

    Integruje:
    1. Academic Search (ArXiv, CrossRef, Semantic Scholar)
    2. Archive Discovery (Wayback Machine, IPFS, GitHub history)
    3. Stealth Crawler (anti-detection crawling, CAPTCHA handling)
    4. Web Intelligence (deep web scanning, hidden API discovery)
    5. Data Leak Hunter (breach detection, credential exposure)
    6. Temporal Analysis (time-series analysis, trend detection)

    Features:
    - Smart Query Routing: Automatically selects best tools for query type
    - Depth Levels: BASIC → ADVANCED → EXHAUSTIVE
    - M1 8GB Optimization: Lazy loading, chunked processing, memory management
    - Parallel Execution: Concurrent tool execution with semaphore control
    - RRF Fusion: Reciprocal Rank Fusion for result combination
    - Deduplication: Multi-level deduplication engine

    M1 8GB Optimizations:
    - Lazy loading: Tools initialized only when needed
    - Chunked processing: Results processed in small batches
    - Context swap: Aggressive cleanup between phases
    - Memory limit: Strict <4GB limit with monitoring
    - Garbage collection: Explicit GC after each phase

    Example:
        >>> engine = UnifiedResearchEngine()
        >>> result = await engine.deep_research(
        ...     "quantum computing breakthroughs",
        ...     depth=ResearchDepth.EXHAUSTIVE
        ... )
        >>> print(f"Found {len(result.findings)} findings")
        >>> print(f"Confidence: {result.confidence_score:.2%}")
    """

    def __init__(
        self,
        config: Optional[UnifiedResearchConfig] = None,
        research_config: Optional[ResearchConfig] = None
    ):
        """
        Initialize Unified Research Engine.

        Args:
            config: Unified research configuration
            research_config: Base research configuration
        """
        self.config = config or UnifiedResearchConfig()
        self.research_config = research_config

        # Performance monitoring
        self._performance_monitor = PerformanceMonitor()
        self._start_time: Optional[float] = None

        # Lazy-loaded tool instances (initialized on first use)
        self._academic_engine: Optional[Any] = None
        self._archive_discovery: Optional[Any] = None
        self._archive_resurrector: Optional[Any] = None
        self._stealth_crawler: Optional[Any] = None
        self._stealth_scraper: Optional[Any] = None
        self._web_intelligence: Optional[Any] = None
        self._data_leak_hunter: Optional[Any] = None
        self._temporal_analyzer: Optional[Any] = None

        # RRF for result fusion
        self._rrf = ReciprocalRankFusion(RRFConfig(k=self.config.rrf_k))

        # Concurrency control (M1 optimized)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_tools)

        # Results cache
        self._cache: Dict[str, Tuple[Any, float]] = {}

        # Statistics
        self._stats = {
            'queries_processed': 0,
            'tools_initialized': 0,
            'total_findings': 0,
            'cache_hits': 0,
        }

        logger.info(f"UnifiedResearchEngine initialized (depth: {self.config.depth.name})")
        logger.info(f"M1 Optimized: max_concurrent={self.config.max_concurrent_tools}, "
                   f"chunk_size={self.config.chunk_size}")

    # ========================================================================
    # LAZY LOADING - M1 Memory Optimization
    # ========================================================================

    async def _get_academic_engine(self) -> Any:
        """Lazy load academic search engine."""
        if self._academic_engine is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError("Intelligence tools not available")
            self._academic_engine = AcademicSearchEngine(
                enable_expansion=True,
                enable_deduplication=True
            )
            self._stats['tools_initialized'] += 1
            logger.debug("AcademicSearchEngine initialized")
        return self._academic_engine

    async def _get_archive_discovery(self) -> Any:
        """Lazy load archive discovery."""
        if self._archive_discovery is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError("Intelligence tools not available")
            self._archive_discovery = ArchiveDiscovery()
            self._stats['tools_initialized'] += 1
            logger.debug("ArchiveDiscovery initialized")
        return self._archive_discovery

    async def _get_archive_resurrector(self) -> Any:
        """Lazy load archive resurrector."""
        if self._archive_resurrector is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError("Intelligence tools not available")
            self._archive_resurrector = ArchiveResurrector()
            await self._archive_resurrector.initialize()
            self._stats['tools_initialized'] += 1
            logger.debug("ArchiveResurrector initialized")
        return self._archive_resurrector

    async def _get_stealth_crawler(self) -> Any:
        """Lazy load stealth crawler."""
        if self._stealth_crawler is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError("Intelligence tools not available")
            self._stealth_crawler = StealthCrawler()
            self._stats['tools_initialized'] += 1
            logger.debug("StealthCrawler initialized")
        return self._stealth_crawler

    async def _get_stealth_scraper(self) -> Any:
        """Lazy load stealth web scraper."""
        if self._stealth_scraper is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError("Intelligence tools not available")
            self._stealth_scraper = StealthWebScraper()
            await self._stealth_scraper.initialize()
            self._stats['tools_initialized'] += 1
            logger.debug("StealthWebScraper initialized")
        return self._stealth_scraper

    async def _get_web_intelligence(self) -> Any:
        """Lazy load web intelligence."""
        if self._web_intelligence is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError("Intelligence tools not available")
            self._web_intelligence = UnifiedWebIntelligence()
            self._stats['tools_initialized'] += 1
            logger.debug("UnifiedWebIntelligence initialized")
        return self._web_intelligence

    async def _get_data_leak_hunter(self) -> Any:
        """Lazy load data leak hunter."""
        if self._data_leak_hunter is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError("Intelligence tools not available")
            self._data_leak_hunter = DataLeakHunter()
            await self._data_leak_hunter.initialize()
            self._stats['tools_initialized'] += 1
            logger.debug("DataLeakHunter initialized")
        return self._data_leak_hunter

    async def _get_temporal_analyzer(self) -> Any:
        """Lazy load temporal analyzer."""
        if self._temporal_analyzer is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError("Intelligence tools not available")
            self._temporal_analyzer = TemporalAnalyzer()
            self._stats['tools_initialized'] += 1
            logger.debug("TemporalAnalyzer initialized")
        return self._temporal_analyzer

    # ========================================================================
    # SMART QUERY ROUTING
    # ========================================================================

    def _classify_query(self, query: str) -> QueryType:
        """
        Classify query type for intelligent tool selection.

        Uses keyword matching and heuristics to determine the best
        query type for routing to appropriate tools.
        """
        query_lower = query.lower()

        # Academic indicators
        academic_keywords = [
            'paper', 'research', 'study', 'journal', 'arxiv', 'doi',
            'citation', 'publication', 'conference', 'thesis', 'dissertation',
            'peer-reviewed', 'methodology', 'hypothesis', 'experiment'
        ]
        if any(kw in query_lower for kw in academic_keywords):
            return QueryType.ACADEMIC

        # Technical indicators
        technical_keywords = [
            'api', 'code', 'github', 'documentation', 'sdk', 'library',
            'framework', 'tutorial', 'how to', 'implementation', 'algorithm'
        ]
        if any(kw in query_lower for kw in technical_keywords):
            return QueryType.TECHNICAL

        # News indicators
        news_keywords = [
            'news', 'latest', 'recent', 'today', 'yesterday', 'this week',
            'breaking', 'update', 'announcement', 'launch', 'release'
        ]
        if any(kw in query_lower for kw in news_keywords):
            return QueryType.NEWS

        # Historical indicators
        historical_keywords = [
            'history', 'archived', 'past', 'wayback', 'old', 'former',
            'vintage', 'retro', 'legacy', 'deprecated', 'original'
        ]
        if any(kw in query_lower for kw in historical_keywords):
            return QueryType.HISTORICAL

        # Person indicators
        person_keywords = [
            'person', 'people', 'biography', 'profile', 'who is', 'founder',
            'ceo', 'author', 'researcher', 'developer', 'contact'
        ]
        if any(kw in query_lower for kw in person_keywords):
            return QueryType.PERSON

        # Organization indicators
        org_keywords = [
            'company', 'organization', 'corp', 'inc', 'ltd', 'startup',
            'enterprise', 'business', 'firm', 'agency', 'institute'
        ]
        if any(kw in query_lower for kw in org_keywords):
            return QueryType.ORGANIZATION

        # Security indicators
        security_keywords = [
            'vulnerability', 'exploit', 'breach', 'hack', 'security',
            'cve', 'malware', 'ransomware', 'phishing', 'leak'
        ]
        if any(kw in query_lower for kw in security_keywords):
            return QueryType.SECURITY

        return QueryType.GENERAL

    def _select_tools_for_query(self, query_type: QueryType) -> List[str]:
        """
        Select appropriate tools based on query type and depth.

        Returns list of tool names to execute.
        """
        tools = []

        # Basic tools (always used)
        if self.config.should_use_tool('academic'):
            tools.append('academic')
        if self.config.should_use_tool('web'):
            tools.append('stealth_crawler')

        # Advanced tools
        if self.config.depth.value >= ResearchDepth.ADVANCED.value:
            if self.config.should_use_tool('archives'):
                tools.append('archives')

        # Exhaustive tools
        if self.config.depth.value >= ResearchDepth.EXHAUSTIVE.value:
            if self.config.should_use_tool('temporal'):
                tools.append('temporal')
            if self.config.should_use_tool('data_leak'):
                tools.append('data_leak')
            if self.config.should_use_tool('osint'):
                tools.append('osint')

        # Query-type specific additions
        if query_type == QueryType.ACADEMIC:
            if 'academic' not in tools:
                tools.append('academic')
        elif query_type == QueryType.HISTORICAL:
            if 'archives' not in tools:
                tools.append('archives')
        elif query_type == QueryType.SECURITY:
            if 'data_leak' not in tools and self.config.depth.value >= ResearchDepth.ADVANCED.value:
                tools.append('data_leak')

        return tools

    # ========================================================================
    # MAIN RESEARCH METHOD
    # ========================================================================

    async def deep_research(
        self,
        query: str,
        depth: Optional[ResearchDepth] = None,
        query_type: Optional[QueryType] = None,
        max_results: int = 50,
        correlation: Optional[RunCorrelation] = None,
        grounding_hints: Optional[CanonicalGroundingHints] = None,
    ) -> UnifiedResearchResult:
        """
        Execute deep research across all integrated tools.

        This is the main entry point for comprehensive research.

        Args:
            query: Research query
            depth: Research depth (overrides config)
            query_type: Query type (auto-detected if not provided)
            max_results: Maximum results to return
            correlation: Optional RunCorrelation for cross-component tracing.
                When provided, enables F11 activation path by flowing
                correlation through result to downstream components.

        Returns:
            UnifiedResearchResult with all findings and analysis
        """
        self._start_time = time.time()

        # Use provided or default depth
        research_depth = depth or self.config.depth

        # Classify query if not provided
        detected_type = query_type or self._classify_query(query)

        logger.info(f"Starting deep research: '{query}'")
        logger.info(f"Depth: {research_depth.name}, Type: {detected_type.value}")

        # Initialize result with correlation context
        result = UnifiedResearchResult(
            query=query,
            depth=research_depth,
            query_type=detected_type,
            correlation=correlation,
        )

        # Select tools
        tools_to_use = self._select_tools_for_query(detected_type)
        logger.info(f"Selected tools: {tools_to_use}")

        # Execute tools (parallel where possible)
        all_findings: List[ResearchFinding] = []

        try:
            # Phase 1: Search (Academic + Web) - can run in parallel
            search_tasks = []

            if 'academic' in tools_to_use:
                search_tasks.append(self._task_search(query, 'academic'))

            if 'stealth_crawler' in tools_to_use:
                search_tasks.append(self._task_search(query, 'web'))

            # Execute search tasks with semaphore control
            async with self._semaphore:
                search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

            for findings in search_results:
                if isinstance(findings, list):
                    all_findings.extend(findings)

            # M1: Context swap - cleanup after search phase
            self._context_swap()

            # Phase 2: Cross-reference (Archives + Data Leaks) - EXHAUSTIVE only
            if research_depth == ResearchDepth.EXHAUSTIVE:
                cross_ref_tasks = []

                if 'archives' in tools_to_use:
                    cross_ref_tasks.append(self._task_cross_reference(query, all_findings))

                if 'data_leak' in tools_to_use:
                    cross_ref_tasks.append(self._task_data_leak_check(query))

                if cross_ref_tasks:
                    async with self._semaphore:
                        cross_results = await asyncio.gather(*cross_ref_tasks, return_exceptions=True)

                    for findings in cross_results:
                        if isinstance(findings, list):
                            all_findings.extend(findings)

                # M1: Context swap
                self._context_swap()

            # Phase 3: Analyze (Temporal analysis)
            if 'temporal' in tools_to_use and len(all_findings) > 5:
                temporal_result = await self._task_analyze(query, all_findings)
                result.temporal_analysis = temporal_result

                # M1: Context swap
                self._context_swap()

            # Phase 4: Validate and enhance
            validation = await self._task_validate(query, all_findings)
            result.validation_report = validation

            # Phase 5: Synthesize with RRF fusion
            fused = await self._task_synthesize(query, all_findings)
            result.fused_results = fused

            # Deduplicate findings
            if self.config.enable_deduplication:
                all_findings = self._deduplicate_findings(all_findings)

            # Rank by relevance
            all_findings = self._rank_findings(all_findings, query)

            # Limit results
            result.findings = all_findings[:max_results]

            # Update metadata
            result.total_sources_found = len(all_findings)
            result.unique_sources = len(set(f.url for f in result.findings if f.url))
            result.sources_used = list(set(f.source for f in result.findings))
            result.tools_executed = tools_to_use

            # Calculate quality metrics
            result.confidence_score = self._calculate_confidence(result)
            result.coverage_score = min(1.0, len(result.findings) / max_results)

        except Exception as e:
            logger.error(f"Deep research error: {e}")
            result.findings = all_findings  # Return what we have

        finally:
            # Calculate execution time
            if self._start_time:
                result.execution_time_seconds = time.time() - self._start_time

            self._stats['queries_processed'] += 1
            self._stats['total_findings'] += len(result.findings)

            logger.info(f"Deep research completed in {result.execution_time_seconds:.2f}s")
            logger.info(f"Found {len(result.findings)} findings from {len(result.sources_used)} sources")

        return result

    # ========================================================================
    # TASK IMPLEMENTATIONS (replacing TODOs)
    # ========================================================================

    async def _task_search(self, query: str, source_type: str) -> List[ResearchFinding]:
        """
        Execute search task using academic or web sources.

        Args:
            query: Search query
            source_type: 'academic' or 'web'

        Returns:
            List of ResearchFinding objects
        """
        findings = []

        try:
            if source_type == 'academic':
                # Academic search
                engine = await self._get_academic_engine()
                result = await engine.search(query, max_results=20)

                for r in result.deduplicated_results:
                    finding = ResearchFinding(
                        id=hashlib.md5(f"{r.title}{r.url}".encode()).hexdigest()[:16],
                        title=r.title,
                        content=r.snippet,
                        url=r.url,
                        source='academic_search',
                        source_type='academic',
                        timestamp=datetime.now(),
                        relevance_score=r.relevance_score,
                        credibility_score=0.8 if r.source in ['arxiv', 'crossref'] else 0.6,
                        metadata={
                            'authors': r.metadata.get('authors', []),
                            'published': r.metadata.get('published', ''),
                            'citations': r.metadata.get('citation_count', 0),
                            'source_name': r.source,
                        }
                    )
                    findings.append(finding)

                logger.info(f"Academic search: {len(findings)} results")

            elif source_type == 'web':
                # Web search via stealth crawler
                crawler = await self._get_stealth_crawler()
                results = crawler.search(query, num_results=15)

                for r in results:
                    finding = ResearchFinding(
                        id=hashlib.md5(f"{r.title}{r.url}".encode()).hexdigest()[:16],
                        title=r.title,
                        content=r.snippet,
                        url=r.url,
                        source='stealth_crawler',
                        source_type='web',
                        timestamp=datetime.now(),
                        relevance_score=0.5,  # Will be re-ranked
                        credibility_score=0.5,
                        metadata={'rank': r.rank}
                    )
                    findings.append(finding)

                logger.info(f"Web search: {len(findings)} results")

        except Exception as e:
            logger.warning(f"Search task failed ({source_type}): {e}")

        return findings

    async def _task_analyze(
        self,
        query: str,
        findings: List[ResearchFinding]
    ) -> Dict[str, Any]:
        """
        Perform temporal and content analysis on findings.

        Args:
            query: Research query
            findings: List of findings to analyze

        Returns:
            Analysis results dictionary
        """
        try:
            analyzer = await self._get_temporal_analyzer()

            # Extract timestamps from findings
            timestamps = []
            values = []

            for i, f in enumerate(findings):
                # Use finding timestamp or metadata
                ts = f.temporal_relevance or f.timestamp
                timestamps.append(ts)
                # Use relevance as value for trend analysis
                values.append(f.relevance_score)

            if len(timestamps) < 5:
                return {'error': 'Insufficient data for temporal analysis'}

            # Run temporal analysis
            analysis = analyzer.analyze(
                query=query,
                timestamps=timestamps,
                values=values,
                analysis_types=['trend', 'patterns', 'scenarios']
            )

            return {
                'trend_direction': analysis.trend.direction.value if analysis.trend else None,
                'trend_confidence': analysis.trend.confidence if analysis.trend else 0,
                'patterns_detected': len(analysis.patterns),
                'scenarios_generated': len(analysis.scenarios),
                'overall_confidence': analysis.overall_confidence,
                'insights': analysis.insights,
                'recommendations': analysis.recommendations,
            }

        except Exception as e:
            logger.warning(f"Analysis task failed: {e}")
            return {'error': str(e)}

    async def _task_synthesize(
        self,
        query: str,
        findings: List[ResearchFinding]
    ) -> List[Dict[str, Any]]:
        """
        Synthesize findings using RRF fusion and knowledge extraction.

        Args:
            query: Research query
            findings: List of findings to synthesize

        Returns:
            Fused and ranked results
        """
        if not findings:
            return []

        try:
            # Group findings by source for RRF
            source_results: Dict[str, List[SearchResult]] = {}

            for i, f in enumerate(findings):
                source = f.source_type
                if source not in source_results:
                    source_results[source] = []

                source_results[source].append(SearchResult(
                    id=f.id,
                    title=f.title,
                    content=f.content,
                    url=f.url,
                    source=source,
                    score=f.relevance_score,
                    rank=i + 1,
                    metadata=f.metadata
                ))

            # Apply RRF fusion
            if self.config.enable_rrf and len(source_results) > 1:
                fused = await self._rrf.fuse(source_results)
            else:
                # Simple flatten if fusion disabled
                fused = []
                for source, results in source_results.items():
                    fused.extend(results)
                fused.sort(key=lambda x: x.score, reverse=True)

            # Convert back to dict format
            return [
                {
                    'id': r.id,
                    'title': r.title,
                    'content': r.content[:500] if r.content else '',
                    'url': r.url,
                    'source': r.source,
                    'score': r.score,
                    'rank': r.rank,
                    'metadata': r.metadata
                }
                for r in fused[:50]  # Limit fused results
            ]

        except Exception as e:
            logger.warning(f"Synthesis task failed: {e}")
            # Return simple ranking as fallback
            return [
                {
                    'id': f.id,
                    'title': f.title,
                    'content': f.content[:500] if f.content else '',
                    'url': f.url,
                    'source': f.source_type,
                    'score': f.relevance_score,
                }
                for f in sorted(findings, key=lambda x: x.relevance_score, reverse=True)[:50]
            ]

    async def _task_cross_reference(
        self,
        query: str,
        existing_findings: List[ResearchFinding]
    ) -> List[ResearchFinding]:
        """
        Cross-reference findings with archive sources.

        Args:
            query: Research query
            existing_findings: Current findings to cross-reference

        Returns:
            Additional findings from archives
        """
        cross_ref_findings = []

        try:
            # Get top URLs to check in archives
            urls_to_check = [
                f.url for f in existing_findings[:10]
                if f.url and f.source_type == 'web'
            ]

            if not urls_to_check:
                return []

            resurrector = await self._get_archive_resurrector()

            for url in urls_to_check:
                try:
                    res_result = await resurrector.resurrect(url)

                    if res_result.success and res_result.best_snapshot:
                        finding = ResearchFinding(
                            id=f"arch_{res_result.request_id}",
                            title=res_result.title or f"Archive: {url}",
                            content=res_result.content[:1000] if res_result.content else '',
                            url=res_result.best_snapshot.archived_url,
                            source='archive_resurrector',
                            source_type='archive',
                            timestamp=res_result.best_snapshot.timestamp,
                            relevance_score=0.6,
                            credibility_score=0.7,
                            metadata={
                                'original_url': url,
                                'snapshot_timestamp': res_result.best_snapshot.timestamp.isoformat(),
                                'content_type': res_result.best_snapshot.content_type.value,
                                'quality_score': res_result.best_snapshot.quality_score,
                            }
                        )
                        cross_ref_findings.append(finding)

                except Exception as e:
                    logger.debug(f"Cross-reference failed for {url}: {e}")

            logger.info(f"Cross-reference: {len(cross_ref_findings)} archive findings")

        except Exception as e:
            logger.warning(f"Cross-reference task failed: {e}")

        return cross_ref_findings

    async def _task_data_leak_check(self, query: str) -> List[ResearchFinding]:
        """
        Check for data leaks related to query.

        Args:
            query: Research query (may contain emails, domains, etc.)

        Returns:
            Leak findings if any
        """
        leak_findings = []

        try:
            # Extract potential targets from query
            import re

            # Email pattern
            emails = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', query)

            # Domain pattern
            domains = re.findall(r'(?:https?://)?([\w\.-]+\.\w{2,})', query)

            if not emails and not domains:
                return []

            hunter = await self._get_data_leak_hunter()

            # Check emails
            for email in emails[:3]:  # Limit checks
                alerts = await hunter.check_target(email, 'email')

                for alert in alerts:
                    finding = ResearchFinding(
                        id=f"leak_{alert.alert_id}",
                        title=f"Data Leak: {alert.breach_name}",
                        content=f"Target found in breach: {alert.breach_name}",
                        url=alert.url,
                        source='data_leak_hunter',
                        source_type='security',
                        timestamp=alert.timestamp,
                        relevance_score=0.9 if alert.severity.value in ['high', 'critical'] else 0.7,
                        credibility_score=0.8,
                        metadata={
                            'target': alert.target,
                            'breach_name': alert.breach_name,
                            'severity': alert.severity.value,
                            'leaked_data_types': alert.leaked_data.get('compromised_data', []),
                        }
                    )
                    leak_findings.append(finding)

            logger.info(f"Data leak check: {len(leak_findings)} alerts")

        except Exception as e:
            logger.warning(f"Data leak check failed: {e}")

        return leak_findings

    async def _task_validate(
        self,
        query: str,
        findings: List[ResearchFinding]
    ) -> Dict[str, Any]:
        """
        Validate findings across multiple sources.

        Args:
            query: Research query
            findings: Findings to validate

        Returns:
            Validation report
        """
        if not findings:
            return {'valid': False, 'reason': 'No findings to validate'}

        try:
            # Group by URL for cross-validation
            url_groups: Dict[str, List[ResearchFinding]] = {}
            for f in findings:
                if f.url:
                    url_groups.setdefault(f.url, []).append(f)

            # Calculate validation metrics
            validated_count = 0
            cross_validated = []

            for url, group in url_groups.items():
                if len(group) > 1:  # Found by multiple sources
                    validated_count += 1
                    cross_validated.append({
                        'url': url,
                        'sources': [f.source for f in group],
                        'agreement_score': len(group) / len(set(f.source for f in findings))
                    })

            # Calculate overall validity
            total_with_url = len([f for f in findings if f.url])
            validation_rate = validated_count / total_with_url if total_with_url > 0 else 0

            return {
                'valid': validation_rate > 0.1,  # At least 10% cross-validated
                'validation_rate': validation_rate,
                'total_findings': len(findings),
                'cross_validated_count': validated_count,
                'cross_validated_urls': cross_validated[:10],
                'source_diversity': len(set(f.source for f in findings)),
                'high_credibility_count': len([f for f in findings if f.credibility_score > 0.7]),
            }

        except Exception as e:
            logger.warning(f"Validation task failed: {e}")
            return {'valid': False, 'error': str(e)}

    async def _task_enhance(self, query: str, context: Dict[str, Any]) -> str:
        """
        Generate enhanced/reformulated query based on context.

        Args:
            query: Original query
            context: Research context

        Returns:
            Enhanced query string
        """
        # Simple enhancement - could use LLM in production
        enhancements = []

        # Add context-based terms
        if 'key_terms' in context:
            enhancements.extend(context['key_terms'][:2])

        # Add year for recent results
        if 'temporal_analysis' in context:
            current_year = datetime.now().year
            enhancements.append(str(current_year))

        if enhancements:
            return f"{query} {' '.join(enhancements)}"

        return query

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def _deduplicate_findings(self, findings: List[ResearchFinding]) -> List[ResearchFinding]:
        """Deduplicate findings based on URL and content similarity."""
        seen_urls: Set[str] = set()
        seen_hashes: Set[str] = set()
        unique: List[ResearchFinding] = []

        for f in findings:
            # URL-based dedup
            if f.url:
                normalized_url = f.url.lower().rstrip('/')
                if normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)

            # Content hash-based dedup
            content_hash = hashlib.md5(f.content[:200].lower().encode()).hexdigest()[:16]
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)

            unique.append(f)

        return unique

    def _rank_findings(
        self,
        findings: List[ResearchFinding],
        query: str
    ) -> List[ResearchFinding]:
        """Rank findings by relevance to query."""
        query_terms = set(query.lower().split())

        for f in findings:
            # Calculate term overlap
            title_terms = set(f.title.lower().split())
            content_terms = set(f.content.lower().split())

            title_matches = len(query_terms & title_terms)
            content_matches = len(query_terms & content_terms)

            # Update relevance score
            f.relevance_score = (
                f.relevance_score * 0.3 +  # Original score
                (title_matches / len(query_terms)) * 0.4 +  # Title match
                (content_matches / len(query_terms)) * 0.2 +  # Content match
                f.credibility_score * 0.1  # Credibility bonus
            )

        return sorted(findings, key=lambda x: x.relevance_score, reverse=True)

    def _calculate_confidence(self, result: UnifiedResearchResult) -> float:
        """Calculate overall confidence score."""
        if not result.findings:
            return 0.0

        # Factor 1: Number of sources
        source_factor = min(1.0, len(result.sources_used) / 3)

        # Factor 2: Average credibility
        avg_credibility = sum(f.credibility_score for f in result.findings) / len(result.findings)

        # Factor 3: Cross-validation
        validation_factor = result.validation_report.get('validation_rate', 0) if result.validation_report else 0

        # Weighted average
        confidence = (
            source_factor * 0.3 +
            avg_credibility * 0.4 +
            validation_factor * 0.3
        )

        return min(1.0, confidence)

    def _context_swap(self) -> None:
        """M1 Optimization: Aggressive cleanup between phases."""
        gc.collect()
        logger.debug("Context swap: garbage collection completed")

    async def cleanup(self) -> None:
        """Cleanup all resources."""
        logger.info("Cleaning up UnifiedResearchEngine...")

        # Cleanup tools
        tools = [
            self._academic_engine,
            self._archive_discovery,
            self._archive_resurrector,
            self._stealth_crawler,
            self._stealth_scraper,
            self._web_intelligence,
            self._data_leak_hunter,
        ]

        for tool in tools:
            if tool and hasattr(tool, 'cleanup'):
                try:
                    await tool.cleanup()
                except Exception as e:
                    logger.debug(f"Cleanup error: {e}")

        # Clear cache
        self._cache.clear()

        # Final GC
        self._context_swap()

        logger.info("UnifiedResearchEngine cleanup completed")

    def get_statistics(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return {
            **self._stats,
            'config': {
                'depth': self.config.depth.name,
                'max_concurrent': self.config.max_concurrent_tools,
                'parallel_enabled': self.config.enable_parallel,
            },
            'tools_initialized': self._stats['tools_initialized'],
        }


class EnhancedResearchOrchestrator(UniversalResearchOrchestrator):
    """
    Rozšířený orchestrátor s workflow a prediktivním plánováním.
    
    Rozšiřuje UniversalResearchOrchestrator o:
    - DAG-based workflow execution
    - Speculative execution
    - Performance monitoring
    - Quality validation
    - Query expansion for broader search coverage
    - Result fusion from multiple sources (RRF)
    - Hybrid RAG for context retrieval
    - Stealth behavior simulation for protected sources
    
    Example:
        >>> orchestrator = EnhancedResearchOrchestrator()
        >>> 
        >>> # Definovat workflow
        >>> workflow = orchestrator.create_research_workflow("My query")
        >>> 
        >>> # Vykonat s monitoringem
        >>> result = await orchestrator.execute_workflow(workflow)
        
        >>> # Or use enhanced research with all features
        >>> result = await orchestrator.research("machine learning", domain="academic")
    """
    
    def __init__(
        self,
        config: Optional[ResearchConfig] = None,
        enhanced_config: Optional[EnhancedResearchConfig] = None
    ):
        super().__init__(config)
        
        # Store enhanced configuration
        self.enhanced_config = enhanced_config or EnhancedResearchConfig()
        
        # Nové komponenty
        self.workflow_engine = WorkflowEngine(max_concurrency=3)
        self.predictive_planner = PredictivePlanner(min_confidence=0.7)
        self.performance_monitor = PerformanceMonitor()
        
        # Extended research enhancement components
        self._init_enhancement_components()
        
        # Statistics tracking
        self._stats: Dict[str, Any] = {
            'queries_expanded': 0,
            'sources_fused': 0,
            'documents_retrieved': 0,
            'stealth_operations': 0,
        }
        
        logger.info("EnhancedResearchOrchestrator initialized")
        logger.info("Features: Workflow Engine, Predictive Planning, Performance Monitoring")
        logger.info("Extended Features: Query Expansion, Result Fusion, Hybrid RAG, Stealth Mode")
    
    def _init_enhancement_components(self) -> None:
        """Initialize research enhancement components based on configuration."""
        cfg = self.enhanced_config
        
        # Reciprocal Rank Fusion for multi-source result fusion
        if cfg.enable_fusion:
            self.rrf = ReciprocalRankFusion(RRFConfig(k=cfg.rrf_k))
        else:
            self.rrf = None
        
        # Hybrid RAG for context retrieval
        if cfg.enable_rag:
            from .knowledge.rag_engine import RAGEngine, RAGConfig
            self.rag = RAGEngine(RAGConfig())
        else:
            self.rag = None
        
        # Query expansion with intelligent wordlist
        if cfg.enable_expansion:
            self.wordlist = IntelligentWordlistGenerator(WordlistConfig(
                max_variations=cfg.max_query_variations,
                domain_context='academic'
            ))
        else:
            self.wordlist = None
        
        # Behavior simulator for stealth operations
        if cfg.enable_stealth:
            self.behavior = BehaviorSimulator(SimulationConfig(
                pattern=cfg.behavior_pattern
            ))
        else:
            self.behavior = None
    
    async def expand_research_query(
        self,
        query: str,
        domain: Optional[str] = None
    ) -> List[str]:
        """
        Expand research query into multiple variations for broader coverage.
        
        Uses the IntelligentWordlistGenerator to create domain-specific query
        variations that can improve search coverage and recall.
        
        Args:
            query: Original research query
            domain: Domain context ('academic', 'medical', 'tech', 'legal')
            
        Returns:
            List of query variations including the original query
            
        Example:
            >>> variations = await orchestrator.expand_research_query(
            ...     "machine learning",
            ...     domain="academic"
            ... )
            >>> # Returns: ['machine learning', 'ML algorithms', 'neural networks', ...]
        """
        if not self.enhanced_config.enable_expansion or self.wordlist is None:
            return [query]
        
        variations = self.wordlist.generate(query)
        
        # Add domain-specific variations
        if domain:
            domain_variations = []
            for var in variations[:5]:  # Limit to avoid explosion
                domain_variations.extend(self.wordlist.generate_for_discovery(
                    [var],
                    modifiers=['paper', 'research', 'study', 'review']
                ))
            variations.extend(domain_variations)
        
        # Remove duplicates and limit
        unique = list(dict.fromkeys(variations))
        
        self._stats['queries_expanded'] += len(unique)
        
        logger.info(f"Expanded query '{query}' into {len(unique)} variations")
        return unique[:self.enhanced_config.max_query_variations]
    
    async def fuse_research_results(
        self,
        source_results: Dict[str, List[Dict[str, Any]]]
    ) -> List[SearchResult]:
        """
        Fuse results from multiple research sources using Reciprocal Rank Fusion.
        
        RRF combines ranked results from different sources without requiring
        score normalization, producing a single unified ranking.
        
        Args:
            source_results: Dict mapping source name to list of results.
                Each result should have: title, content, url, score
            
        Returns:
            Fused and ranked SearchResult objects
            
        Example:
            >>> sources = {
            ...     'web': [{'title': '...', 'content': '...', 'url': '...', 'score': 0.9}],
            ...     'scholar': [{'title': '...', 'content': '...', 'url': '...', 'score': 0.8}]
            ... }
            >>> fused = await orchestrator.fuse_research_results(sources)
        """
        cfg = self.enhanced_config
        
        if not cfg.enable_fusion or self.rrf is None:
            # Just flatten results without fusion
            all_results = []
            for source, results in source_results.items():
                for r in results:
                    all_results.append(SearchResult(
                        id=r.get('url', '') or r.get('title', ''),
                        title=r.get('title', ''),
                        content=r.get('content', ''),
                        url=r.get('url'),
                        source=source,
                        score=r.get('score', 0.0)
                    ))
            return all_results
        
        # Convert to SearchResult objects
        search_results: Dict[str, List[SearchResult]] = {}
        
        for source, results in source_results.items():
            search_results[source] = []
            for i, r in enumerate(results):
                result = SearchResult(
                    id=r.get('url', f'{source}_{i}'),
                    title=r.get('title', ''),
                    content=r.get('content', ''),
                    url=r.get('url'),
                    source=source,
                    score=r.get('score', 0.0),
                    rank=i + 1,
                    metadata=r.get('metadata', {})
                )
                search_results[source].append(result)
        
        # Apply RRF
        fused = await self.rrf.fuse(search_results)
        
        self._stats['sources_fused'] += len(source_results)
        
        logger.info(
            f"Fused {len(source_results)} sources into {len(fused)} unique results"
        )
        
        return fused
    
    async def retrieve_research_context(
        self,
        query: str,
        documents: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Retrieve relevant context from research documents using Hybrid RAG.
        
        Combines semantic search with keyword matching to find the most
        relevant text chunks from the provided documents.
        
        Args:
            query: Research query for context retrieval
            documents: List of documents (dict with 'content' and optional 'metadata')
            
        Returns:
            List of relevant text chunks ordered by relevance
            
        Example:
            >>> docs = [
            ...     {'id': '1', 'content': 'Machine learning is...', 'metadata': {...}},
            ...     {'id': '2', 'content': 'Deep learning models...', 'metadata': {...}}
            ... ]
            >>> context = await orchestrator.retrieve_research_context(
            ...     "What is machine learning?",
            ...     docs
            ... )
        """
        cfg = self.enhanced_config
        
        if not cfg.enable_rag or self.rag is None:
            # Return first N documents as-is when RAG is disabled
            return [d.get('content', '') for d in documents[:cfg.rag_top_k]]
        
        # Convert to Document objects
        docs = []
        for i, d in enumerate(documents):
            doc = Document(
                id=d.get('id', f'doc_{i}'),
                content=d.get('content', ''),
                metadata=d.get('metadata', {})
            )
            docs.append(doc)
        
        # Retrieve relevant chunks using hybrid retrieval
        results = await self.rag.hybrid_retrieve(query, docs, top_k=cfg.rag_top_k)
        
        self._stats['documents_retrieved'] += len(results)
        
        return [r.chunk_text for r in results]
    
    async def stealth_research(
        self,
        query: str,
        url: str,
        scrape_func: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """
        Perform stealth research on protected or academic sites.
        
        Uses behavior simulation to mimic human browsing patterns,
        helping to avoid detection when accessing protected resources.
        
        Args:
            query: Research query
            url: URL to scrape
            scrape_func: Optional async function to scrape with behavior simulation.
                Should accept (url, behavior_simulator) arguments.
            
        Returns:
            Dictionary with scraped content, behavior statistics, and success status
            
        Example:
            >>> async def scrape(url, behavior):
            ...     # Custom scraping logic with behavior simulation
            ...     pass
            >>> 
            >>> result = await orchestrator.stealth_research(
            ...     "research paper",
            ...     "https://example.com/paper",
            ...     scrape_func=scrape
            ... )
        """
        cfg = self.enhanced_config
        
        if not cfg.enable_stealth or self.behavior is None:
            logger.warning("Stealth mode disabled, falling back to normal research")
            return await self.research(query, domain="academic")
        
        logger.info(f"Starting stealth research: {url}")
        
        # Simulate human behavior before accessing
        behavior_stats = await self.behavior.simulate_page_visit(
            num_scrolls=random.randint(2, 5),
            read_time=random.uniform(10, 20)
        )
        
        content = None
        if scrape_func:
            try:
                # Scrape with behavior simulation
                content = await scrape_func(url, self.behavior)
            except Exception as e:
                logger.error(f"Stealth scrape failed: {e}")
        
        self._stats['stealth_operations'] += 1
        
        return {
            'query': query,
            'url': url,
            'content': content,
            'behavior_simulation': behavior_stats,
            'success': content is not None
        }
    
    async def research(
        self,
        query: str,
        search_func: Optional[Callable] = None,
        domain: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute enhanced research workflow with all available features.
        
        This is the main research method that combines:
        1. Query expansion for broader coverage
        2. Multi-source search with result fusion
        3. Hybrid RAG for context retrieval
        4. Performance monitoring
        
        Args:
            query: Research query
            search_func: Optional async function to perform search.
                Should accept a query string and return results dict.
            domain: Domain context ('academic', 'medical', 'tech', 'legal')
            
        Returns:
            Comprehensive research results including:
            - Original and expanded queries
            - Fused and ranked results
            - Relevant context chunks
            - Statistics about the research process
            
        Example:
            >>> async def search(q):
            ...     return {'source': 'web', 'results': [...]}
            >>> 
            >>> result = await orchestrator.research(
            ...     "machine learning in healthcare",
            ...     search_func=search,
            ...     domain="medical"
            ... )
        """
        logger.info(f"Starting enhanced research for: {query}")
        
        start_time = self.performance_monitor.start_timer()
        
        # 1. Expand query for broader coverage
        queries = await self.expand_research_query(query, domain)
        
        # 2. Search using provided search function
        all_results: Dict[str, List[Dict[str, Any]]] = {}
        
        if search_func:
            # Limit to top variations to avoid overwhelming the search function
            for q in queries[:3]:
                try:
                    results = await search_func(q)
                    source = results.get('source', 'unknown')
                    if source not in all_results:
                        all_results[source] = []
                    all_results[source].extend(results.get('results', []))
                except Exception as e:
                    logger.warning(f"Search failed for '{q}': {e}")
        
        # 3. Fuse results from multiple sources
        fused_results = []
        if all_results:
            fused_results = await self.fuse_research_results(all_results)
        
        # 4. Retrieve context using Hybrid RAG
        context = []
        if fused_results:
            documents = [
                {
                    'id': r.id,
                    'content': r.content,
                    'metadata': {'title': r.title, 'url': r.url, 'source': r.source}
                }
                for r in fused_results[:20]  # Top 20 for context
            ]
            context = await self.retrieve_research_context(query, documents)
        
        # Record performance
        perf_stats = self.performance_monitor.record(
            tokens=sum(len(r.content.split()) for r in fused_results[:10]),
            start_time=start_time,
        )
        
        logger.info(f"Research completed in {perf_stats['duration']:.2f}s")
        
        # 5. Compile final results
        return {
            'query': query,
            'expanded_queries': queries,
            'fused_results': [
                {
                    'title': r.title,
                    'content': r.content[:500],  # Truncate for brevity
                    'url': r.url,
                    'source': r.source,
                    'score': r.score,
                    'rank': r.rank
                }
                for r in fused_results[:10]  # Top 10
            ],
            'context': context,
            'statistics': {
                'queries_expanded': len(queries),
                'sources_searched': len(all_results),
                'results_fused': len(fused_results),
                'context_chunks': len(context),
                'duration_seconds': perf_stats.get('duration', 0),
                'tokens_processed': perf_stats.get('tokens', 0),
            }
        }
    
    def create_research_workflow(
        self,
        query: str,
        mode: ResearchMode = None
    ) -> Workflow:
        """
        Vytvořit výzkumný workflow.
        
        Args:
            query: Výzkumný dotaz
            mode: Režim výzkumu
            
        Returns:
            Workflow definice
        """
        mode = mode or self.config.mode
        
        workflow = Workflow(
            id=f"research_{hash(query) % 10000}",
            name=f"Research: {query[:50]}",
            context={"query": query, "mode": mode.value},
        )
        
        # Task 1: Initial Search
        task_search = Task(
            id="search",
            name="Initial Search",
            func=self._task_search,
            params={"query": query},
        )
        workflow.add_task(task_search)
        
        # Task 2: OSINT Discovery (paralelní s archive check)
        task_osint = Task(
            id="osint",
            name="OSINT Discovery",
            func=self._task_osint,
            params={"query": query},
        )
        workflow.add_task(task_osint)
        
        # Task 3: Academic Search (závisí na search)
        task_academic = Task(
            id="academic",
            name="Academic Search",
            func=self._task_academic,
            params={"query": query},
            dependencies=["search"],
        )
        workflow.add_task(task_academic)
        
        # Task 4: Deep Read (závisí na OSINT)
        task_deep_read = Task(
            id="deep_read",
            name="Deep Read",
            func=self._task_deep_read,
            params={"urls": "${osint_result.urls}"},
            dependencies=["osint"],
            max_retries=2,
        )
        workflow.add_task(task_deep_read)
        
        # Task 5: Fact Check (závisí na academic a deep_read)
        task_fact_check = Task(
            id="fact_check",
            name="Fact Check",
            func=self._task_fact_check,
            params={},
            dependencies=["academic", "deep_read"],
        )
        workflow.add_task(task_fact_check)
        
        # Task 6: Synthesis (závisí na všem)
        task_synthesis = Task(
            id="synthesis",
            name="Synthesis",
            func=self._task_synthesis,
            params={"query": query},
            dependencies=["fact_check"],
        )
        workflow.add_task(task_synthesis)
        
        return workflow
    
    async def execute_workflow(
        self,
        workflow: Workflow,
        use_predictions: bool = True
    ) -> ResearchResult:
        """
        Vykonat workflow s prediktivním plánováním.
        
        Args:
            workflow: Workflow k vykonání
            use_predictions: Použít prediktivní plánování
            
        Returns:
            Výsledek výzkumu
        """
        logger.info(f"Executing workflow: {workflow.name}")
        
        start_time = self.performance_monitor.start_timer()
        
        if use_predictions:
            # Prediktivní vykonávání
            result = await self._execute_with_prediction(workflow)
        else:
            # Standardní vykonávání
            results = await self.workflow_engine.execute(workflow)
            result = self._compile_results(workflow, results)
        
        # Zaznamenat výkon
        perf_stats = self.performance_monitor.record(
            tokens=len(result.final_answer.split()),
            start_time=start_time,
        )
        
        logger.info(f"Workflow completed in {perf_stats['duration']:.2f}s")
        logger.info(f"Speedup: {perf_stats.get('speedup', 0):.1f}×")
        
        return result
    
    async def _execute_with_prediction(self, workflow: Workflow) -> ResearchResult:
        """Vykonat s prediktivním plánováním"""
        
        async def planner(ctx):
            # Jednoduchý plán z workflow
            return [
                {"action": task.id, "params": task.params}
                for task in workflow.tasks.values()
            ]
        
        async def executor(action, params, ctx):
            # Vykonat úkol
            if action in workflow.tasks:
                task = workflow.tasks[action]
                return await task.execute(ctx)
            return None
        
        # Prediktivní plánování
        predictive_result = await self.predictive_planner.plan_with_prediction(
            planner_func=planner,
            executor_func=executor,
            context=workflow.context,
        )
        
        # Zkompilovat výsledky
        return self._compile_results(workflow, predictive_result.get("results", {}))
    
    def _compile_results(
        self,
        workflow: Workflow,
        results: Dict[str, Any]
    ) -> ResearchResult:
        """Zkompilovat výsledky do ResearchResult"""
        
        # Získat syntézu
        synthesis = results.get("synthesis", "")
        
        # Získat zdroje
        sources = []
        for task_id, result in results.items():
            if isinstance(result, dict) and "url" in result:
                sources.append({
                    "url": result["url"],
                    "title": result.get("title", ""),
                    "type": task_id,
                })
        
        return ResearchResult(
            success=True,
            query=workflow.context.get("query", ""),
            mode=self.config.mode,
            final_answer=synthesis if synthesis else "Research completed",
            sources=sources,
            statistics={
                "workflow_duration": sum(
                    t.duration() or 0 for t in workflow.tasks.values()
                ),
                "tasks_completed": sum(
                    1 for t in workflow.tasks.values()
                    if t.status.value == "completed"
                ),
            },
        )
    
    # Task implementace
    async def _task_search(self, query: str, context: Dict) -> Dict:
        """Task: Initial Search using query expansion and RAG"""
        logger.info(f"Task: Search for '{query}'")

        results = []

        # Use query expansion if available
        if self.wordlist is not None:
            try:
                variations = self.wordlist.expand(query)
                logger.info(f"Query expanded into {len(variations)} variations")
            except Exception as e:
                logger.warning(f"Query expansion failed: {e}")
                variations = [query]
        else:
            variations = [query]

        # Use RAG for retrieval if available
        if self.rag is not None:
            try:
                for var in variations[:3]:  # Limit to avoid overload
                    rag_results = await self.rag.retrieve(var, top_k=5)
                    results.extend(rag_results)
            except Exception as e:
                logger.warning(f"RAG retrieval failed: {e}")

        # Use behavior simulator for stealth if enabled
        if self.behavior is not None and self.enhanced_config.enable_stealth:
            try:
                await self.behavior.simulate_access_pattern()
            except Exception as e:
                logger.debug(f"Behavior simulation skipped: {e}")

        return {
            "query": query,
            "variations": variations,
            "results_count": len(results),
            "results": results[:10],  # Top 10 results
        }

    async def _task_osint(self, query: str, context: Dict) -> Dict:
        """Task: OSINT Discovery using web intelligence"""
        logger.info(f"Task: OSINT for '{query}'")

        urls = []
        sources = {}

        # Try to use web intelligence if available in parent
        if hasattr(self, '_search_web'):
            try:
                web_results = await self._search_web(query)
                for result in web_results.get('results', []):
                    if 'url' in result:
                        urls.append(result['url'])
                    elif 'link' in result:
                        urls.append(result['link'])
                sources['web'] = len(web_results.get('results', []))
            except Exception as e:
                logger.warning(f"Web search failed: {e}")

        # Use archive discovery if available
        if hasattr(self, '_search_archives'):
            try:
                archive_results = await self._search_archives(query)
                for result in archive_results.get('results', []):
                    if 'url' in result:
                        urls.append(result['url'])
                sources['archives'] = len(archive_results.get('results', []))
            except Exception as e:
                logger.debug(f"Archive search skipped: {e}")

        # Deduplicate URLs
        unique_urls = list(dict.fromkeys(urls))[:20]  # Max 20 URLs

        return {
            "query": query,
            "urls": unique_urls,
            "count": len(unique_urls),
            "sources": sources,
        }

    async def _task_academic(self, query: str, context: Dict) -> Dict:
        """Task: Academic Search using academic search engine"""
        logger.info(f"Task: Academic search for '{query}'")

        papers = []

        # Lazy import academic search to save memory
        try:
            from .intelligence.academic_search import AcademicSearchEngine

            engine = AcademicSearchEngine()
            search_results = await engine.search(query, max_results=10)

            for result in search_results:
                papers.append({
                    "title": getattr(result, 'title', 'Unknown'),
                    "authors": getattr(result, 'authors', []),
                    "year": getattr(result, 'year', None),
                    "url": getattr(result, 'url', None),
                    "pdf_url": getattr(result, 'pdf_url', None),
                    "source": getattr(result, 'source', 'unknown'),
                    "score": getattr(result, 'score', 0.0),
                })
        except ImportError:
            logger.debug("Academic search engine not available")
        except Exception as e:
            logger.warning(f"Academic search failed: {e}")

        # Fallback: use RAG if available and no papers found
        if not papers and self.rag is not None:
            try:
                rag_results = await self.rag.retrieve(query, top_k=5)
                for doc in rag_results:
                    papers.append({
                        "title": getattr(doc, 'title', 'Document'),
                        "content": getattr(doc, 'content', '')[:500],
                        "source": "rag",
                    })
            except Exception as e:
                logger.debug(f"RAG fallback failed: {e}")

        return {
            "query": query,
            "papers": papers,
            "count": len(papers),
        }

    async def _task_deep_read(self, urls: List[str], context: Dict) -> Dict:
        """Task: Deep Read using RAG and content extraction"""
        logger.info(f"Task: Deep read {len(urls)} URLs")

        contents = []
        urls_read = []

        # Limit URLs for M1 8GB optimization
        urls = urls[:5]

        for url in urls:
            try:
                # Try to use RAG for content retrieval
                if self.rag is not None:
                    docs = await self.rag.retrieve(f"site:{url}", top_k=3)
                    for doc in docs:
                        content = {
                            "url": url,
                            "title": getattr(doc, 'title', ''),
                            "content": getattr(doc, 'content', '')[:2000],  # Limit content size
                            "source": getattr(doc, 'source', 'unknown'),
                        }
                        contents.append(content)
                        if url not in urls_read:
                            urls_read.append(url)

                # Simulate stealth delay if enabled
                if self.behavior is not None and self.enhanced_config.enable_stealth:
                    import asyncio
                    await asyncio.sleep(0.5)  # Be polite

            except Exception as e:
                logger.warning(f"Failed to read {url}: {e}")

        return {
            "urls_read": urls_read,
            "content": contents,
            "count": len(contents),
        }

    async def _task_fact_check(self, context: Dict) -> Dict:
        """Task: Fact Check using cross-referencing"""
        logger.info("Task: Fact check")

        claims_checked = 0
        verified = []

        # Get claims from context
        claims = context.get('claims', [])
        sources = context.get('sources', [])

        if not claims:
            return {"claims_checked": 0, "verified": [], "status": "no_claims"}

        for claim in claims[:5]:  # Limit for performance
            claims_checked += 1
            verification = {
                "claim": claim,
                "status": "unverified",
                "confidence": 0.0,
                "sources": [],
            }

            # Cross-reference with sources
            if sources and self.rag is not None:
                try:
                    # Search for claim in RAG
                    results = await self.rag.retrieve(claim, top_k=3)
                    if results:
                        scores = [getattr(r, 'score', 0) for r in results]
                        avg_score = sum(scores) / len(scores) if scores else 0

                        if avg_score > 0.8:
                            verification["status"] = "verified"
                            verification["confidence"] = avg_score
                        elif avg_score > 0.5:
                            verification["status"] = "partial"
                            verification["confidence"] = avg_score

                        verification["sources"] = [
                            getattr(r, 'source', 'unknown') for r in results[:3]
                        ]
                except Exception as e:
                    logger.debug(f"Fact check verification failed: {e}")

            verified.append(verification)

        return {
            "claims_checked": claims_checked,
            "verified": verified,
            "status": "completed",
        }

    async def _task_synthesis(self, query: str, context: Dict) -> str:
        """Task: Synthesis using RAG and result fusion"""
        logger.info(f"Task: Synthesis for '{query}'")

        # Collect all sources from context
        all_results = {}

        # Add search results
        if 'search_results' in context:
            all_results['search'] = context['search_results']

        # Add academic papers
        if 'papers' in context:
            all_results['academic'] = [
                {"title": p.get('title', ''), "content": p.get('abstract', '')}
                for p in context['papers']
            ]

        # Add deep read content
        if 'deep_read_content' in context:
            all_results['deep_read'] = context['deep_read_content']

        # Use RRF fusion if available and enabled
        if self.rrf is not None and self.enhanced_config.enable_fusion:
            try:
                fused = await self.fuse_research_results(all_results)
                top_results = fused[:5]

                # Build synthesis from top results
                synthesis_parts = [
                    f"## Synthesis for: {query}\n"
                ]

                for i, result in enumerate(top_results, 1):
                    title = getattr(result, 'title', 'Untitled')
                    content = getattr(result, 'content', '')[:500]
                    source = getattr(result, 'source', 'unknown')
                    score = getattr(result, 'score', 0)

                    synthesis_parts.append(
                        f"\n### Source {i} ({source}, score: {score:.2f})\n"
                        f"**{title}**\n{content}...\n"
                    )

                return "\n".join(synthesis_parts)

            except Exception as e:
                logger.warning(f"Fusion failed, using fallback: {e}")

        # Fallback synthesis
        synthesis = f"## Synthesis for: {query}\n\n"

        for source, results in all_results.items():
            synthesis += f"\n### From {source}:\n"
            for i, result in enumerate(results[:3], 1):
                if isinstance(result, dict):
                    title = result.get('title', result.get('url', 'Untitled'))
                    synthesis += f"{i}. {title}\n"

        return synthesis
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Získat statistiky výkonu"""
        return {
            "performance": self.performance_monitor.get_stats(),
            "predictions": self.predictive_planner.get_stats(),
        }
    
    def get_enhanced_statistics(self) -> Dict[str, Any]:
        """
        Get comprehensive statistics for all enhanced research features.
        
        Returns:
            Dictionary with statistics for query expansion, result fusion,
            RAG retrieval, and stealth operations.
        """
        return {
            **self._stats,
            'config': {
                'fusion_enabled': self.enhanced_config.enable_fusion,
                'rag_enabled': self.enhanced_config.enable_rag,
                'expansion_enabled': self.enhanced_config.enable_expansion,
                'stealth_enabled': self.enhanced_config.enable_stealth,
            },
            'performance': self.performance_monitor.get_stats(),
            'predictions': self.predictive_planner.get_stats(),
        }


# Convenience function for quick enhanced research
async def enhanced_research(
    query: str,
    search_func: Optional[Callable] = None,
    domain: str = 'academic',
    config: Optional[EnhancedResearchConfig] = None
) -> Dict[str, Any]:
    """
    Convenience helper — NON-CANONICAL, backward-compat only.

    This is a backward-compat convenience wrapper around EnhancedResearchOrchestrator,
    NOT a canonical runtime entrypoint. Uses EnhancedResearchOrchestrator.research()
    which is an orchestrator residue surface (deprecated).

    For new code, prefer deep_research_provider_seam() after F11 activation.

    Args:
        query: Research query
        search_func: Optional async search function
        domain: Domain context ('academic', 'medical', 'tech', 'legal')
        config: Optional enhanced research configuration

    Returns:
        Comprehensive research results

    Example:
        >>> results = await enhanced_research(
        ...     "machine learning in healthcare",
        ...     domain="medical"
        ... )
    """
    orchestrator = EnhancedResearchOrchestrator(enhanced_config=config)
    return await orchestrator.research(query, search_func, domain)


# =============================================================================
# UNIFIED RESEARCH ENGINE - CONVENIENCE FUNCTIONS
# =============================================================================

async def deep_research(
    query: str,
    depth: ResearchDepth = ResearchDepth.ADVANCED,
    max_results: int = 50
) -> UnifiedResearchResult:
    """
    Convenience helper — NON-CANONICAL.

    This is a backward-compat convenience wrapper, NOT a canonical runtime
    entrypoint. For new code, prefer deep_research_provider_seam() after
    F11 activation.

    Args:
        query: Research query
        depth: Research depth (BASIC/ADVANCED/EXHAUSTIVE)
        max_results: Maximum results to return

    Returns:
        UnifiedResearchResult with all findings

    Example:
        >>> result = await deep_research(
        ...     "quantum computing breakthroughs 2024",
        ...     depth=ResearchDepth.EXHAUSTIVE
        ... )
        >>> print(f"Found {len(result.findings)} findings")
        >>> print(f"Confidence: {result.confidence_score:.2%}")
    """
    engine = UnifiedResearchEngine(config=UnifiedResearchConfig(depth=depth))
    try:
        return await engine.deep_research(query, depth=depth, max_results=max_results)
    finally:
        await engine.cleanup()


def create_unified_research_engine(
    depth: ResearchDepth = ResearchDepth.ADVANCED,
    **kwargs
) -> UnifiedResearchEngine:
    """
    NON-CANONICAL factory function — backward-compat only.

    Creates UnifiedResearchEngine instance. For new code, prefer
    direct instantiation with UnifiedResearchConfig after F11 activation.

    Args:
        depth: Default research depth
        **kwargs: Additional config options

    Returns:
        Configured UnifiedResearchEngine instance

    Example:
        >>> engine = create_unified_research_engine(depth=ResearchDepth.EXHAUSTIVE)
        >>> result = await engine.deep_research("target query")
        >>> await engine.cleanup()
    """
    config = UnifiedResearchConfig(depth=depth, **kwargs)
    return UnifiedResearchEngine(config=config)


# =============================================================================
# SOURCE PLANE SEAM (Sprint F11 - Internal Provider-Owned)
# =============================================================================
# Malý, deterministic, read-only source planning seam.
# Provider-owned internal artifact — NOT a public authority surface.
#
# PURPOSE: Explicitně říká, které source families a enginy se použijí
#          pro danou query_type + depth combination.
#
# INTEGRATION: Voláno z deep_research() workflow před tool selection.
#              Reuseuje _classify_query() a _select_tools_for_query().
# =============================================================================

@dataclass(frozen=True)
class SourcePlan:
    """Immutable source plan — which families, engines, why, and conditions.

    PROVIDER-OWNED INTERNAL SEAM: Toto je read-only planning artifact,
    NOT a public DTO. Používá se interně v UnifiedResearchEngine
    pro transparentní rozhodování o source routing.

    Fields:
        families: List of SourceFamily values to activate
        engines: Concrete engine names that will be lazy-loaded
        reasoning: Why these families were selected (query_type + depth)
        conditions: Runtime conditions that trigger inclusion
        excluded: SourceFamily values explicitly excluded and why
    """
    families: Tuple[SourceFamily, ...]
    engines: Tuple[str, ...]
    reasoning: str
    conditions: Tuple[str, ...]
    excluded: Tuple[SourceFamily, ...] = field(default_factory=())

    def to_display_dict(self) -> Dict[str, Any]:
        """Human-readable dict for debugging/logging."""
        return {
            'families': [f.value for f in self.families],
            'engines': list(self.engines),
            'reasoning': self.reasoning,
            'conditions': list(self.conditions),
            'excluded': [f.value for f in self.excluded],
        }


def _build_source_plan(
    query_type: QueryType,
    depth: ResearchDepth,
    config: Optional[UnifiedResearchConfig] = None,
) -> SourcePlan:
    """
    Build deterministic source plan for query_type + depth combination.

    PROVIDER-OWNED INTERNAL SEAM — read-only, no side effects, no eager init.

    Tato funkce je internal seam pro UnifiedResearchEngine.
    Pro veřejné použití po F11 activation použij deep_research_provider_seam().

    LOCAL_CORPUS is a CONSUMER SEAM — declared here as a possible source
    but NOT wired at runtime. It would be consumed if the local corpus
    search plane existed and was populated. This is NOT an authority claim.

    Args:
        query_type: Detected or provided query type
        depth: Research depth level
        config: Optional config (uses defaults if not provided)

    Returns:
        Immutable SourcePlan s explicitním source routing

    Source Matrix:
        BASIC:     WEB + ACADEMIC (minimum viable coverage)
        ADVANCED:  + ARCHIVE (Wayback, archive resurrection)
        EXHAUSTIVE: + SECURITY + TEMPORAL + OSINT (full surface)
        LOCAL_CORPUS: CONSUMER SEAM (dormant, declared but not wired)

    Query-Type Routing:
        ACADEMIC:   always includes ACADEMIC family
        HISTORICAL: always includes ARCHIVE family
        SECURITY:   includes SECURITY family at ADVANCED+
        GENERAL:    minimal family set per depth
        PERSON:     may include LOCAL_CORPUS at EXHAUSTIVE (dormant)
        ORGANIZATION: may include LOCAL_CORPUS at ADVANCED+ (dormant)
    """
    cfg = config or UnifiedResearchConfig(depth=depth)

    # Determine base families by depth
    if depth == ResearchDepth.BASIC:
        base_families = [SourceFamily.WEB, SourceFamily.ACADEMIC]
        base_engines = ('stealth_crawler', 'academic')

    elif depth == ResearchDepth.ADVANCED:
        base_families = [SourceFamily.WEB, SourceFamily.ACADEMIC, SourceFamily.ARCHIVE]
        base_engines = ('stealth_crawler', 'academic', 'archives')

    else:  # EXHAUSTIVE
        base_families = [
            SourceFamily.WEB,
            SourceFamily.ACADEMIC,
            SourceFamily.ARCHIVE,
            SourceFamily.SECURITY,
            SourceFamily.TEMPORAL,
            SourceFamily.OSINT,
        ]
        base_engines = (
            'stealth_crawler', 'academic', 'archives',
            'data_leak', 'temporal', 'osint'
        )

    families = list(base_families)
    engines = list(base_engines)
    excluded: List[SourceFamily] = []
    conditions: List[str] = [f'depth={depth.name}']

    # Query-type specific routing
    if query_type == QueryType.ACADEMIC:
        if SourceFamily.ACADEMIC not in families:
            families.insert(0, SourceFamily.ACADEMIC)
            engines = ['academic'] + list(engines)
        conditions.append('query_type=ACADEMIC')

    elif query_type == QueryType.HISTORICAL:
        if SourceFamily.ARCHIVE not in families:
            families.insert(0, SourceFamily.ARCHIVE)
            engines = ['archives'] + list(engines)
        conditions.append('query_type=HISTORICAL')

    elif query_type == QueryType.SECURITY:
        if depth.value >= ResearchDepth.ADVANCED.value:
            if SourceFamily.SECURITY not in families:
                families.append(SourceFamily.SECURITY)
                engines = list(engines) + ['data_leak']
        conditions.append('query_type=SECURITY')

    elif query_type == QueryType.PERSON:
        # PERSON benefits from OSINT family at EXHAUSTIVE
        if depth == ResearchDepth.EXHAUSTIVE and SourceFamily.OSINT not in families:
            families.append(SourceFamily.OSINT)
            engines = list(engines) + ['osint']
        conditions.append('query_type=PERSON')

    elif query_type == QueryType.ORGANIZATION:
        # ORGANIZATION benefits from ARCHIVE at ADVANCED+
        if depth.value >= ResearchDepth.ADVANCED.value:
            if SourceFamily.ARCHIVE not in families:
                families.append(SourceFamily.ARCHIVE)
                engines = list(engines) + ['archives']
        conditions.append('query_type=ORGANIZATION')

    else:  # GENERAL, TECHNICAL, NEWS
        conditions.append(f'query_type={query_type.value}')

    # Apply config-level tool exclusions
    if config:
        if not cfg.should_use_tool('academic') and SourceFamily.ACADEMIC in families:
            families.remove(SourceFamily.ACADEMIC)
            engines = [e for e in engines if e != 'academic']
            excluded.append(SourceFamily.ACADEMIC)

        if not cfg.should_use_tool('archives') and SourceFamily.ARCHIVE in families:
            families.remove(SourceFamily.ARCHIVE)
            engines = [e for e in engines if e != 'archives']
            excluded.append(SourceFamily.ARCHIVE)

    # Build reasoning string
    reasoning = (
        f"depth={depth.name}, query_type={query_type.value}, "
        f"families={len(families)}, engines={len(engines)}"
    )

    return SourcePlan(
        families=tuple(families),
        engines=tuple(engines),
        reasoning=reasoning,
        conditions=tuple(conditions),
        excluded=tuple(excluded),
    )


# =============================================================================
# DEEP RESEARCH PROVIDER SEAM (Sprint F11 - Dormant Canonical)
# =============================================================================
# Úzký, typed, lazy provider seam pro UnifiedResearchEngine.
# Aktivace: PO triádě, source plane, transport plane, session seams,
# security gate, minimal grounding seam.
#
# STATE: DORMANT - není v hot path runtime
# USAGE: Pouze přes explicitní ProviderRequest/ProviderResult handoff
# =============================================================================

@dataclass
class DeepResearchRequest:
    """
    Request wrapper for deep research provider seam.

    NON-CANONICAL: Toto NENÍ ProviderRequest z types.py.
    Používá se pouze jako interní seam před F11 připojením.

    Canonical ProviderRequest/ProviderResult z types.py bude použito
    AŽ PO napojení na triádu a session seams.

    Migration direction:
        DeepResearchRequest.grounding_hints (raw Dict)
            → CanonicalGroundingHints (types.py:1702)
        via CanonicalGroundingHints.from_shim() classmethod.
        Currently discarded in to_engine_kwargs(); activation would
        wire this to engine's grounding parameter once F11 is ready.
    """
    query: str
    depth: ResearchDepth = ResearchDepth.ADVANCED
    query_type: Optional[QueryType] = None
    max_results: int = 50
    # Minimal grounding hints — internal, read-only.
    # Carries topic/domain hints for future retrieval alignment.
    # NOT a correlation field; correlation via seam parameter.
    grounding_hints: Optional[Dict[str, List[str]]] = None

    def to_engine_kwargs(self) -> Dict[str, Any]:
        """Convert to UnifiedResearchEngine.deep_research() kwargs."""
        kwargs = {
            'query': self.query,
            'depth': self.depth,
            'query_type': self.query_type,
            'max_results': self.max_results,
        }
        # GROUNDING SEAM TRUTH: grounding_hints flow to engine.
        # Migration direction (additive-only, non-activating):
        # raw grounding_hints Dict should become CanonicalGroundingHints
        # after F11 activation. Currently stored in kwargs for seam
        # propagation — engine receives but does not yet apply it (TBD).
        if self.grounding_hints:
            from .types import CanonicalGroundingHints
            _canonical_hints = CanonicalGroundingHints.from_shim(
                topic_hints=tuple(self.grounding_hints.get('topics', [])),
                domain_tags=tuple(self.grounding_hints.get('domains', [])),
            )
            # Propagation: engine.deep_research() receives grounding_hints kwarg
            # but engine implementation is TBD for F11 activation.
            # For now, store in kwargs so seam IS propagating (not discarding).
            if not _canonical_hints.is_empty():
                kwargs['grounding_hints'] = _canonical_hints
        return kwargs


@dataclass
class DeepResearchResponse:
    """
    Response wrapper for deep research provider seam.

    NON-CANONICAL: Toto NENÍ ProviderResult z types.py.
    Používá se pouze jako interní seam před F11 připojením.

    Canonical ProviderRequest/ProviderResult z types.py bude použito
    AŽ PO napojení na triádu a session seams.
    """
    findings: List[ResearchFinding]
    fused_results: List[Dict[str, Any]]
    confidence_score: float
    execution_time_seconds: float
    sources_used: List[str]
    tools_executed: List[str]

    @classmethod
    def from_unified_result(cls, result: UnifiedResearchResult) -> "DeepResearchResponse":
        """Create from UnifiedResearchResult."""
        return cls(
            findings=result.findings,
            fused_results=result.fused_results,
            confidence_score=result.confidence_score,
            execution_time_seconds=result.execution_time_seconds,
            sources_used=result.sources_used,
            tools_executed=result.tools_executed,
        )


# =============================================================================
# INTERNAL HINT TYPES (Sprint F11 Pre-activation)
# =============================================================================
# Read-only, optional, no side effects.
# Not exported — internal to enhanced_research.py seam only.
# =============================================================================

@dataclass(frozen=True)
class _BudgetHints:
    """Internal budget hints for DeepResearch session.

    Not a canonical contract — internal to enhanced_research.py.
    """
    stagnation_tolerance: int = 0  # Extra iterations before stagnation triggers
    confidence_boost: float = 0.0  # Adjust confidence threshold (delta)


@dataclass(frozen=True)
class _EvidenceHints:
    """Internal evidence/logging hints for DeepResearch session.

    Not a canonical contract — internal to enhanced_research.py.
    """
    log_level: str = "INFO"  # DEBUG, INFO, WARNING
    detail_depth: str = "standard"  # minimal, standard, verbose


@dataclass(frozen=True)
class _PolicyFlags:
    """Internal execution policy flags for DeepResearch session.

    Not a canonical contract — internal to enhanced_research.py.
    """
    skip_stagnation_check: bool = False
    force_exhaustive: bool = False


@dataclass
class DeepResearchGroundingShim:
    """
    Minimal internal grounding adapter for DeepResearch.

    INTERNAL / PROVIDER-OWNED — NOT a public canonical surface.

    Purpose:
        - Bridges local DeepResearch seam to future F11 activation path
        - Carries minimal grounding metadata (topic hints, domain tags)
        - Carries session context hints (budget, evidence, policy)
        - Does NOT pretend DeepResearch uses LLM-centric ProviderRequest/ProviderResult
        - Does NOT create new correlation world — reuses RunCorrelation

    Why this exists:
        - UnifiedResearchEngine.deep_research() has no grounding context
        - Future activation requires passing grounding hints to retrieval
        - This shim provides a bounded, typed vessel for that metadata
        - Full activation will replace this with triada-based grounding

    What this is NOT:
        - NOT ProviderRequest/ProviderResult replacement
        - NOT a new correlation mechanism
        - NOT public API

    Shrink wrap: This is intentionally minimal. Do NOT expand unless
    activation blockers are resolved.
    """
    topic_hints: List[str] = field(default_factory=list)
    domain_tags: List[str] = field(default_factory=list)
    correlation: Optional[RunCorrelation] = None
    # Session context hints (read-only, optional, no side effects)
    budget_hints: Optional[_BudgetHints] = None
    evidence_hints: Optional[_EvidenceHints] = None
    policy_flags: Optional[_PolicyFlags] = None

    def is_empty(self) -> bool:
        """Returns True if no grounding metadata is set."""
        return (
            len(self.topic_hints) == 0
            and len(self.domain_tags) == 0
            and self.correlation is None
            and self.budget_hints is None
            and self.evidence_hints is None
            and self.policy_flags is None
        )

    def merge(self, other: "DeepResearchGroundingShim") -> "DeepResearchGroundingShim":
        """Merge another shim into this one (deduplicates + concatenates)."""
        return DeepResearchGroundingShim(
            topic_hints=list(set(self.topic_hints + other.topic_hints)),
            domain_tags=list(set(self.domain_tags + other.domain_tags)),
            correlation=other.correlation or self.correlation,
            budget_hints=other.budget_hints or self.budget_hints,
            evidence_hints=other.evidence_hints or self.evidence_hints,
            policy_flags=other.policy_flags or self.policy_flags,
        )


async def deep_research_provider_seam(
    request: DeepResearchRequest,
    grounding: Optional[DeepResearchGroundingShim] = None,
) -> DeepResearchResponse:
    """
    Úzký provider seam pro deep research.

    DORMANT CANONICAL PROVIDER CANDIDATE - Sprint F11.

    Toto je jediný OFICIÁLNÍ entrypoint pro připojení na runtime.
    Používá se pouze po splnění admission blockers:
    1. Triáda: PARTIAL — analyzer + router + registry EXISTUJÍ, DeepResearch NE napojen
    2. Source plane: EXISTS — SourceFamily + SourcePlan + _build_source_plan()
    3. Transport plane (FetchCoordinator): exists, not wired to DeepResearch runtime
    4. Session seams (BudgetManager, EvidenceLog): exists, not wired to DeepResearch
    5. Security gate (SecurityGate, privacy layer): exists, not wired to DeepResearch
    6. Minimal grounding seam (ProviderRequest/ProviderResult): exists, not wired to DeepResearch

    Args:
        request: DeepResearchRequest s query a config
        grounding: Optional DeepResearchGroundingShim s minimal grounding metadata.
            When provided, its correlation is passed to engine.deep_research().

    Returns:
        DeepResearchResponse s výsledky

    Example:
        >>> req = DeepResearchRequest(
        ...     query="quantum computing breakthroughs",
        ...     depth=ResearchDepth.EXHAUSTIVE
        ... )
        >>> shim = DeepResearchGroundingShim(topic_hints=["physics", "quantum"])
        >>> resp = await deep_research_provider_seam(req, shim)
        >>> print(f"Found {len(resp.findings)} findings")
    """
    engine = UnifiedResearchEngine(
        config=UnifiedResearchConfig(depth=request.depth)
    )
    try:
        # Extract correlation from grounding shim if provided
        correlation = grounding.correlation if grounding else None
        kwargs = request.to_engine_kwargs()
        kwargs['correlation'] = correlation
        result = await engine.deep_research(**kwargs)
        return DeepResearchResponse.from_unified_result(result)
    finally:
        await engine.cleanup()


# =============================================================================
# TRIAD ADMISSION SEAM (Sprint F11 - Read-Only Descriptor)
# =============================================================================
# Dormant admission metadata for DeepResearch provider candidate.
# This is READ-ONLY admission truth — NOT runtime activation.
#
# WHAT THIS IS:
#   - Provider-owned admission descriptor (identity, owner, blockers)
#   - Explicit statement that DeepResearch is NOT canonical triad authority
#   - Blocker-driven readiness map
#
# WHAT THIS IS NOT:
#   - NO runtime activation of DeepResearch
#   - NO eager initialization
#   - NO new registry world
#   - NO background worker
#   - NO new public provider framework
# =============================================================================

@dataclass(frozen=True)
class TriadAdmissionDescriptor:
    """
    Read-only admission metadata for DeepResearch provider candidate.

    PROVIDER-OWNED DORMANT ADMISSION SEAM — NOT runtime authority.

    This descriptor lives in the provider-owned space (enhanced_research.py)
    and explicitly states:
    1. Who owns this admission (provider candidate identity)
    2. What the triad expects (capability expectations)
    3. What blocks admission (blockers/preconditions)
    4. That this is NOT runtime activation

    The triad remains the canonical authority. This descriptor is a
    declaration of intent and readiness, not execution permission.
    """
    # Identity
    provider_candidate: str = "UnifiedResearchEngine"
    owning_module: str = "enhanced_research"

    # Triad integration state
    triad_authority_exists: bool = True  # AnalyzerResult, CapabilityRouter, ToolRegistry exist
    deepresearch_napojen: bool = False   # DeepResearch NOT wired to triad

    # Capability expectations (what DeepResearch expects from triad)
    expects_analyzer: bool = True   # Expects AnalyzerResult from AutonomousAnalyzer
    expects_router: bool = True     # Expects CapabilityRouter routing
    expects_registry: bool = True  # Expects ToolRegistry.execute_with_limits()

    # Admission blockers (what must be resolved before F11 activation)
    # REASON: Blockers declare existence status, not just TBD.
    # "exists, not wired" = component exists but DeepResearch is not connected.
    blockers: Tuple[str, ...] = (
        "Session seams (BudgetManager, EvidenceLog): exists, not wired to DeepResearch",
        "Security gate (PII gate): exists, not wired to DeepResearch",
        "Minimal grounding seam (ProviderRequest/ProviderResult): exists, not wired to DeepResearch",
        "Transport plane (FetchCoordinator): exists, not wired to DeepResearch runtime",
    )

    # Explicit boundary statement
    is_dormant: bool = True
    is_not_runtime_authority: bool = True
    is_not_activation: bool = True

    @property
    def admission_summary(self) -> str:
        """Human-readable admission status."""
        lines = [
            f"Provider Candidate: {self.provider_candidate}",
            f"Triad Authority Exists: {self.triad_authority_exists}",
            f"DeepResearch Napojen: {self.deepresearch_napojen}",
            f"Dormant: {self.is_dormant}",
            "",
            "Blockers:",
        ]
        for b in self.blockers:
            lines.append(f"  - {b}")
        return "\n".join(lines)


# Canonical singleton for read-only admission queries
DEEP_RESEARCH_ADMISSION = TriadAdmissionDescriptor()


# =============================================================================
# LOCAL CORPUS CONSUMER SEAM — Sprint F8/F11
# =============================================================================
# Read-only consumer declaration: DeepResearch WOULD consume local corpus search
# plane if it existed. This is NOT a new retrieval authority (per F8 invariant).
# This is NOT activation — runtime path remains dormant.
#
# Consumer matrix:
#   DeepResearch provider  → consumer of: local_corpus search plane
#   RAGEngine             → owner of: RAG grounding authority
#   LocalSearchSeam       → owner of: local corpus search plane (dormant)
#
# The seam is PLANNING ONLY — it declares intent and conditions, not execution.
# =============================================================================


@dataclass(frozen=True)
class LocalCorpusConsumerDescriptor:
    """
    Read-only consumer seam for local corpus search plane.

    PROVIDER-OWNED DORMANT CONSUMER SEAM — NOT runtime authority.

    This descriptor lives in the provider-owned space (enhanced_research.py)
    and explicitly declares:
    1. That DeepResearch is a CONSUMER (not owner) of local corpus search
    2. Under what conditions it would consume (depth, query type, state)
    3. What remains dormant (no runtime path, no eager init)
    4. What blocks actual wiring (ingestion plane, corpus readiness)

    RELATIONSHIP TO RAGEngine:
    - RAGEngine = RAG grounding authority (context augmentation, embeddings, HNSW)
    - LocalSearchSeam = local corpus search plane owner (BM25, metadata, read-only)
    - DeepResearch consumer = potential consumer of LocalSearchSeam output
    - These are THREE SEPARATE surfaces — no authority confusion (per F8)

    WHY THIS IS NOT A NEW RETRIEVAL AUTHORITY:
    - LocalSearchSeam already owns the search plane (search_index.py, Sprint 8F6)
    - DeepResearch merely declares it WOULD query that plane if it existed
    - No new provider framework, no new execution path, no eager init
    - Consumer declaration is read-only planning metadata
    """
    # Identity
    consumer_name: str = "UnifiedResearchEngine"
    owning_module: str = "enhanced_research"

    # Consumer role (explicit, not an authority claim)
    is_consumer: bool = True
    is_not_search_plane_owner: bool = True

    # What the consumer wants from local corpus
    would_query_for_depths: Tuple[str, ...] = (
        "BASIC",    # Light corpus touch for factual queries
        "ADVANCED", # Deeper corpus for investigative queries
        "EXHAUSTIVE",  # Full corpus sweep for exhaustive research
    )

    would_query_for_query_types: Tuple[str, ...] = (
        "GENERAL",      # Factual lookup with local grounding
        "ACADEMIC",     # Literature comparison with corpus
        "PERSON",       # Entity-centric with local context
        "ORGANIZATION", # Org-centric with local documents
    )

    # What conditions trigger consumer interest
    conditions_for_consumption: Tuple[str, ...] = (
        "local_corpus_plane_exists=True",       # Search plane is initialized
        "corpus_populated=True",                # At least one document ingested
        "query_has_local_context=True",         # Query benefits from local grounding
        "no_better_external_source_available",  # External source would be overkill
        "budget_allows_local_only=True",        # BudgetManager allows corpus-only path
    )

    # What blocks wiring (pre-F11 activation)
    blockers: Tuple[str, ...] = (
        "LocalSearchSeam ingestion plane: exists, not yet connected to DeepResearch",
        "Corpus population: documents need to be ingested first",
        "BudgetManager seam: exists, no consumer-side budget tracking for corpus queries",
        "DeepResearch runtime path: no activation call site exists",
        "ProviderRequest/ProviderResult handoff: exists, not wired to DeepResearch",
    )

    # Explicit boundary
    is_dormant: bool = True
    is_not_runtime_path: bool = True
    is_not_activation: bool = True

    # RAGEngine separation (per F8 invariant)
    rag_engine_authority: str = "RAGEngine = RAG grounding authority (hybrid_retrieve, context)"
    local_corpus_authority: str = "LocalSearchSeam = search plane owner (BM25, metadata)"
    consumer_declaration: str = "DeepResearch = consumer of LocalSearchSeam output (NOT authority)"

    @property
    def consumer_summary(self) -> str:
        """Human-readable consumer declaration."""
        lines = [
            f"Consumer: {self.consumer_name}",
            f"Module: {self.owning_module}",
            f"Is Consumer (not owner): {self.is_consumer}",
            f"Dormant: {self.is_dormant}",
            "",
            "Would query for depths:",
        ]
        for d in self.would_query_for_depths:
            lines.append(f"  - {d}")
        lines.append("Would query for query types:")
        for qt in self.would_query_for_query_types:
            lines.append(f"  - {qt}")
        lines.append("")
        lines.append("Authority separation:")
        lines.append(f"  RAGEngine: {self.rag_engine_authority}")
        lines.append(f"  LocalSearchSeam: {self.local_corpus_authority}")
        lines.append(f"  DeepResearch consumer: {self.consumer_declaration}")
        lines.append("")
        lines.append("Blockers:")
        for b in self.blockers:
            lines.append(f"  - {b}")
        return "\n".join(lines)


# Canonical singleton for read-only consumer queries
LOCAL_CORPUS_CONSUMER = LocalCorpusConsumerDescriptor()


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # ========================================================================
    # PROVIDER CANDIDATE - UnifiedResearchEngine (DORMANT, F11 target)
    # ========================================================================
    # UnifiedResearchEngine is the dormant canonical provider candidate.
    # It is NOT in the hot path. Admission requires F11 integration:
    #   1. Triáda: PARTIAL — analyzer + router + registry EXISTUJÍ, DeepResearch NE napojen
    #   2. Source plane: EXISTS — SourceFamily + SourcePlan + _build_source_plan()
    #   3. Transport plane (FetchCoordinator): exists, not wired to DeepResearch runtime
    #   4. Session seams (BudgetManager, EvidenceLog): exists, not wired to DeepResearch
    #   5. Security gate (SecurityGate, privacy layer): exists, not wired to DeepResearch
    #   6. Minimal grounding seam (ProviderRequest/ProviderResult): exists, not wired to DeepResearch
    #
    # USAGE: Only via deep_research_provider_seam() after F11 activation.
    'UnifiedResearchEngine',
    'UnifiedResearchResult',
    'deep_research_provider_seam',

    # ========================================================================
    # ORCHESTRATOR RESIDUE - EnhancedResearchOrchestrator (DEPRECATED F187A)
    # ========================================================================
    # DEPRECATED: backward-compat only, NOT canonical orchestrator.
    # Do NOT use for new runtime — use SprintScheduler instead.
    'EnhancedResearchOrchestrator',
    'EnhancedResearchConfig',
    'DEPRECATED_ENHANCED_ORCHESTRATOR_RESIDUE',

    # ========================================================================
    # LOCAL TYPED SEAM (Sprint F11 - Pre-activation bridge)
    # ========================================================================
    # NON-CANONICAL LOCAL SEAM: pre-activation bridge for F11.
    # types.py ProviderRequest/ProviderResult are LLM-centric DTOs that
    # don't semantically fit OSINT search provider output structures.
    'DeepResearchRequest',
    'DeepResearchResponse',
    'DeepResearchGroundingShim',

    # ========================================================================
    # CONVENIENCE FUNCTIONS (NON-CANONICAL HELPERS)
    # ========================================================================
    # DEPRECATED F187A: backward-compat helpers only.
    # For new code, use deep_research_provider_seam() after F11 activation.
    'enhanced_research',
    'deep_research',
    'create_unified_research_engine',

    # ========================================================================
    # ENUMS AND DATA CLASSES
    # ========================================================================
    'ResearchDepth',
    'QueryType',
    # ResearchFinding — REMOVED F187A: COLLISION with legacy/autonomous_orchestrator.py
    # Use ResearchFinding from autonomous_orchestrator facade for compatibility.

    # ========================================================================
    # TRIAD ADMISSION SEAM (Sprint F11 - Read-Only Descriptor)
    # ========================================================================
    'TriadAdmissionDescriptor',
    'DEEP_RESEARCH_ADMISSION',

    # ========================================================================
    # LOCAL CORPUS CONSUMER SEAM (Sprint F8/F11 - Read-Only Descriptor)
    # ========================================================================
    'LocalCorpusConsumerDescriptor',
    'LOCAL_CORPUS_CONSUMER',
]

# DEPRECATED F187A: flag for orchestrator residue detection
DEPRECATED_ENHANCED_ORCHESTRATOR_RESIDUE = True
