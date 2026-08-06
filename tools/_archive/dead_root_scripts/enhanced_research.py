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
import asyncio
import gc
import hashlib
import logging
import re
import secrets
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
import msgspec
from datetime import UTC, datetime
from enum import Enum, auto
from typing import Any
from hledac.universal.utils.async_helpers import chunked_taskgroup, parallel
from .knowledge.rag_engine import Document
from .layers.stealth_layer import BehaviorPattern, BehaviorSimulator, SimulationConfig
from .project_types import CanonicalGroundingHints, ResearchConfig, ResearchMode, ResearchResult, RunCorrelation, UniversalResearchOrchestrator
from .utils import PerformanceMonitor, PredictivePlanner, Task, Workflow, WorkflowEngine
from .utils.query_expansion import ExpansionConfig as WordlistConfig
from .utils.query_expansion import QueryExpander as IntelligentWordlistGenerator
from .utils.ranking import RankedResult as SearchResult
from .utils.ranking import ReciprocalRankFusion, RRFConfig
try:
    from .intelligence import AcademicSearchEngine, ArchiveDiscovery, ArchiveResurrector, DataLeakHunter, StealthCrawler, StealthWebScraper, TemporalAnalyzer, UnifiedWebIntelligence, quick_scrape, search_academic, search_archives
    INTELLIGENCE_AVAILABLE = True
except ImportError:
    INTELLIGENCE_AVAILABLE = False
logger = logging.getLogger(__name__)
_EMAIL_PATTERN = re.compile('[\\w\\.-]+@[\\w\\.-]+\\.\\w+')
_DOMAIN_PATTERN = re.compile('(?:https?://)?([\\w\\.-]+\\.\\w{2,})')
_ADVANCED_RAG_ENV = 'HLEDAC_ENABLE_ADVANCED_RAG'
_ADVANCED_STEALTH_ENV = 'HLEDAC_ENABLE_ADVANCED_STEALTH'
_EVIDENCE_ANALYZER_ENV = 'HLEDAC_ENABLE_EVIDENCE_ANALYZER'
_STRUCTURED_ENV = 'HLEDAC_ENABLE_STRUCTURED'

def _env_flag(name: str, default: bool=False) -> bool:
    """Read boolean env var with explicit values (1, true, yes)."""
    import os
    raw = os.environ.get(name, '').strip().lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'off'):
        return False
    return default
_MAX_ADVANCED_RAG_FINDINGS = 20
_MAX_STRUCTURED_ENTITIES = 30
_MAX_STEALTH_FETCHES = 5
_MAX_STEALTH_DEPTH = 1

class ResearchDepth(Enum):
    """Research depth levels - each adds more tools and thoroughness."""
    BASIC = auto()
    ADVANCED = auto()
    EXHAUSTIVE = auto()

class QueryType(Enum):
    """Types of queries for intelligent routing."""
    ACADEMIC = 'academic'
    TECHNICAL = 'technical'
    NEWS = 'news'
    HISTORICAL = 'historical'
    PERSON = 'person'
    ORGANIZATION = 'organization'
    SECURITY = 'security'
    GENERAL = 'general'

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()

class SourceFamily(Enum):
    """Source families for research — defines which engines/tools are used.

    PROVIDER-OWNED INTERNAL SEAM: This enum is an internal planning artifact,
    NOT a public authority surface. It is used by _build_source_plan() to
    determine which lazy-loaded engines to route to.

    LOCAL_CORPUS is a CONSUMER SEAM — DeepResearch does NOT own the search plane.
    It merely declares that it WOULD consume local corpus results if the plane
    existed. This is NOT a new retrieval authority (per F8 invariant).
    """
    WEB = 'web'
    ACADEMIC = 'academic'
    ARCHIVE = 'archive'
    SECURITY = 'security'
    TEMPORAL = 'temporal'
    OSINT = 'osint'
    LOCAL_CORPUS = 'local_corpus'

class UnifiedResearchConfig(msgspec.Struct, gc=False):
    """Configuration for unified research engine.

    M1 Adaptive: All memory/concurrency settings tuned based on detected RAM tier.

    Attributes:
        depth: Research depth level (BASIC/ADVANCED/EXHAUSTIVE)
        max_memory_mb: Maximum memory usage in MB (adaptive: 8GB->4096, 16GB->8192, etc.)
        enable_parallel: Enable parallel tool execution
        max_concurrent_tools: Maximum concurrent tools (adaptive: 8GB->2, 16GB->4, etc.)
        chunk_size: Results processing chunk size
        enable_rrf: Enable Reciprocal Rank Fusion
        rrf_k: RRF fusion parameter
        enable_deduplication: Enable result deduplication
        enable_temporal_analysis: Enable time-series analysis
        enable_data_leak_check: Enable breach monitoring
        cache_results: Cache intermediate results
        cache_ttl_seconds: Cache time-to-live
    """

    @staticmethod
    def _get_adaptive_limits() -> tuple[int, int]:
        """Get adaptive memory and concurrency limits from SystemDetector."""
        try:
            from core.system_detector import get_system_detector
            detector = get_system_detector()
            return (detector.max_memory_mb, detector.max_concurrent_tools)
        except Exception:
            return (4096, 2)
    depth: ResearchDepth = ResearchDepth.ADVANCED
    max_memory_mb: int = field(default_factory=lambda: UnifiedResearchConfig._get_adaptive_limits()[0])
    enable_parallel: bool = True
    max_concurrent_tools: int = field(default_factory=lambda: UnifiedResearchConfig._get_adaptive_limits()[1])
    chunk_size: int = 50
    enable_rrf: bool = True
    rrf_k: int = 60
    enable_deduplication: bool = True
    dedup_threshold: float = 0.7
    enable_temporal_analysis: bool = True
    enable_data_leak_check: bool = True
    enable_archive_search: bool = True
    enable_stealth_crawling: bool = True
    cache_results: bool = True
    cache_ttl_seconds: int = 3600
    academic_sources: list[str] = field(default_factory=lambda: ['arxiv', 'crossref', 'semantic_scholar'])
    archive_sources: list[str] = field(default_factory=lambda: ['wayback', 'archive_today'])
    enable_advanced_rag: bool = False
    enable_stealth_browser: bool = False
    enable_evidence_analyzer: bool = False
    enable_structured_extraction: bool = False
    max_advanced_findings: int = _MAX_ADVANCED_RAG_FINDINGS

    def should_use_tool(self, tool_name: str) -> bool:
        """Check if a tool should be used based on depth config."""
        tool_depth_map = {'academic': ResearchDepth.BASIC, 'web': ResearchDepth.BASIC, 'stealth_crawler': ResearchDepth.ADVANCED, 'archives': ResearchDepth.ADVANCED, 'temporal': ResearchDepth.EXHAUSTIVE, 'data_leak': ResearchDepth.EXHAUSTIVE, 'osint': ResearchDepth.EXHAUSTIVE}
        required_depth = tool_depth_map.get(tool_name, ResearchDepth.BASIC)
        return self.depth.value >= required_depth.value

class ResearchFinding(msgspec.Struct, gc=False):
    """A single research finding with rich metadata."""
    id: str
    title: str
    content: str
    url: str | None
    source: str
    source_type: str
    timestamp: datetime
    relevance_score: float = 0.0
    credibility_score: float = 0.5
    temporal_relevance: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {'id': self.id, 'title': self.title, 'content': self.content[:500] if self.content else '', 'url': self.url, 'source': self.source, 'source_type': self.source_type, 'timestamp': self.timestamp.isoformat(), 'relevance_score': self.relevance_score, 'credibility_score': self.credibility_score, 'metadata': self.metadata}

class UnifiedResearchResult(msgspec.Struct, gc=False):
    """Complete result from unified research."""
    query: str
    depth: ResearchDepth
    query_type: QueryType
    findings: list[ResearchFinding] = field(default_factory=list)
    fused_results: list[dict[str, Any]] = field(default_factory=list)
    correlation: RunCorrelation | None = None
    temporal_analysis: dict[str, Any] | None = None
    cross_references: list[dict[str, Any]] = field(default_factory=list)
    validation_report: dict[str, Any] | None = None
    sources_used: list[str] = field(default_factory=list)
    total_sources_found: int = 0
    unique_sources: int = 0
    execution_time_seconds: float = 0.0
    memory_peak_mb: float = 0.0
    tools_executed: list[str] = field(default_factory=list)
    confidence_score: float = 0.0
    coverage_score: float = 0.0
    completed_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {'query': self.query, 'depth': self.depth.name, 'query_type': self.query_type.value, 'findings_count': len(self.findings), 'sources_used': self.sources_used, 'total_sources': self.total_sources_found, 'unique_sources': self.unique_sources, 'execution_time': self.execution_time_seconds, 'confidence': self.confidence_score, 'coverage': self.coverage_score, 'completed_at': self.completed_at.isoformat()}

class EnhancedResearchConfig(msgspec.Struct, gc=False):
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
    enable_fusion: bool = True
    rrf_k: int = 60
    enable_rag: bool = True
    rag_top_k: int = 5
    enable_expansion: bool = True
    max_query_variations: int = 10
    enable_stealth: bool = True
    behavior_pattern: BehaviorPattern = BehaviorPattern.RESEARCHER
    sources: list[str] = field(default_factory=lambda: ['web', 'scholar', 'arxiv', 'semantic_scholar', 'news'])

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
    __slots__ = tuple(('_academic_engine', '_advanced_rag', '_archive_discovery', '_archive_resurrector', '_cache', '_data_leak_hunter', '_evidence_analyzer', '_performance_monitor', '_rrf', '_semaphore', '_start_time', '_stats', '_stealth_browser', '_stealth_crawler', '_stealth_fetch_count', '_stealth_scraper', '_temporal_analyzer', '_web_intelligence', 'config', 'research_config'))

    def __init__(self, config: UnifiedResearchConfig | None=None, research_config: ResearchConfig | None=None):
        """
        Initialize Unified Research Engine.

        Args:
            config: Unified research configuration
            research_config: Base research configuration
        """
        _SENTINEL = object()
        cfg_from_caller = config
        self.config = config or UnifiedResearchConfig()
        if cfg_from_caller is _SENTINEL or cfg_from_caller is None:
            self.config.enable_advanced_rag = _env_flag(_ADVANCED_RAG_ENV, default=False)
            self.config.enable_stealth_browser = _env_flag(_ADVANCED_STEALTH_ENV, default=False)
            self.config.enable_evidence_analyzer = _env_flag(_EVIDENCE_ANALYZER_ENV, default=False)
            self.config.enable_structured_extraction = _env_flag(_STRUCTURED_ENV, default=False)
        self.research_config = research_config
        self._performance_monitor = PerformanceMonitor()
        self._start_time: float | None = None
        self._academic_engine: Any | None = None
        self._archive_discovery: Any | None = None
        self._archive_resurrector: Any | None = None
        self._stealth_crawler: Any | None = None
        self._stealth_scraper: Any | None = None
        self._web_intelligence: Any | None = None
        self._data_leak_hunter: Any | None = None
        self._temporal_analyzer: Any | None = None
        self._advanced_rag: Any | None = None
        self._stealth_browser: Any | None = None
        self._evidence_analyzer: Any | None = None
        self._stealth_fetch_count: int = 0
        self._rrf = ReciprocalRankFusion(RRFConfig(k=self.config.rrf_k))
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_tools)
        self._cache: dict[str, tuple[Any, float]] = {}
        self._stats = {'queries_processed': 0, 'tools_initialized': 0, 'total_findings': 0, 'cache_hits': 0, 'advanced_rag_queries': 0, 'stealth_fetches': 0, 'evidence_analyses': 0, 'structured_entities': 0}
        logger.info(f'UnifiedResearchEngine initialized (depth: {self.config.depth.name})')
        logger.info(f'M1 Optimized: max_concurrent={self.config.max_concurrent_tools}, chunk_size={self.config.chunk_size}')

    async def _get_academic_engine(self) -> Any:
        """Lazy load academic search engine."""
        if self._academic_engine is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError('Intelligence tools not available')
            self._academic_engine = AcademicSearchEngine(enable_expansion=True, enable_deduplication=True)
            self._stats['tools_initialized'] += 1
            logger.debug('AcademicSearchEngine initialized')
        return self._academic_engine

    async def _get_archive_discovery(self) -> Any:
        """Lazy load archive discovery."""
        if self._archive_discovery is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError('Intelligence tools not available')
            self._archive_discovery = ArchiveDiscovery()
            self._stats['tools_initialized'] += 1
            logger.debug('ArchiveDiscovery initialized')
        return self._archive_discovery

    async def _get_archive_resurrector(self) -> Any:
        """Lazy load archive resurrector."""
        if self._archive_resurrector is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError('Intelligence tools not available')
            self._archive_resurrector = ArchiveResurrector()
            await self._archive_resurrector.initialize()
            self._stats['tools_initialized'] += 1
            logger.debug('ArchiveResurrector initialized')
        return self._archive_resurrector

    async def _get_stealth_crawler(self) -> Any:
        """Lazy load stealth crawler."""
        if self._stealth_crawler is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError('Intelligence tools not available')
            self._stealth_crawler = StealthCrawler()
            self._stats['tools_initialized'] += 1
            logger.debug('StealthCrawler initialized')
        return self._stealth_crawler

    async def _get_stealth_scraper(self) -> Any:
        """Lazy load stealth web scraper."""
        if self._stealth_scraper is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError('Intelligence tools not available')
            self._stealth_scraper = StealthWebScraper()
            await self._stealth_scraper.initialize()
            self._stats['tools_initialized'] += 1
            logger.debug('StealthWebScraper initialized')
        return self._stealth_scraper

    async def _get_web_intelligence(self) -> Any:
        """Lazy load web intelligence."""
        if self._web_intelligence is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError('Intelligence tools not available')
            self._web_intelligence = UnifiedWebIntelligence()
            self._stats['tools_initialized'] += 1
            logger.debug('UnifiedWebIntelligence initialized')
        return self._web_intelligence

    async def _get_data_leak_hunter(self) -> Any:
        """Lazy load data leak hunter."""
        if self._data_leak_hunter is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError('Intelligence tools not available')
            self._data_leak_hunter = DataLeakHunter()
            await self._data_leak_hunter.initialize()
            self._stats['tools_initialized'] += 1
            logger.debug('DataLeakHunter initialized')
        return self._data_leak_hunter

    async def _get_temporal_analyzer(self) -> Any:
        """Lazy load temporal analyzer."""
        if self._temporal_analyzer is None:
            if not INTELLIGENCE_AVAILABLE:
                raise RuntimeError('Intelligence tools not available')
            self._temporal_analyzer = TemporalAnalyzer()
            self._stats['tools_initialized'] += 1
            logger.debug('TemporalAnalyzer initialized')
        return self._temporal_analyzer

    async def _get_advanced_rag(self) -> Any:
        """Lazy load advanced RAG orchestrator (advanced_rag.rag_orchestrator).

        Returns None when capability flag HLEDAC_ENABLE_ADVANCED_RAG=0.
        """
        if not self.config.enable_advanced_rag:
            return None
        if self._advanced_rag is None:
            try:
                from .advanced_rag.rag_orchestrator import RAGOrchestrator
                self._advanced_rag = RAGOrchestrator()
                await self._advanced_rag.initialize()
                self._stats['tools_initialized'] += 1
                logger.info('Advanced RAG orchestrator initialized (LanceDB-backed)')
            except Exception as e:
                logger.warning(f'Advanced RAG init failed: {e}')
                self._advanced_rag = None
        return self._advanced_rag

    async def _get_stealth_browser(self) -> Any:
        """Lazy load stealth browser (advanced_web.stealth_browser).

        Returns None when capability flag HLEDAC_ENABLE_ADVANCED_STEALTH=0.
        Honors M1 constraint: max 2 concurrent browser tabs (already enforced
        in stealth_browser._MAX_CONCURRENT_TABS).
        """
        if not self.config.enable_stealth_browser:
            return None
        if self._stealth_browser is None:
            try:
                from .advanced_web.stealth_browser import StealthBrowser
                self._stealth_browser = StealthBrowser()
                self._stats['tools_initialized'] += 1
                logger.info('Stealth browser initialized (max 2 concurrent tabs)')
            except Exception as e:
                logger.warning(f'Stealth browser init failed: {e}')
                self._stealth_browser = None
        return self._stealth_browser

    async def _get_evidence_analyzer(self) -> Any:
        """Lazy load evidence network analyzer (advanced_web.evidence_network_analyzer).

        Returns None when capability flag HLEDAC_ENABLE_EVIDENCE_ANALYZER=0.
        The instance is currently a NOT_IMPLEMENTED graceful stub.
        """
        if not self.config.enable_evidence_analyzer:
            return None
        if self._evidence_analyzer is None:
            try:
                from .advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
                self._evidence_analyzer = EvidenceNetworkAnalyzer()
                self._stats['tools_initialized'] += 1
                logger.debug('Evidence network analyzer initialized (NOT_IMPLEMENTED stub)')
            except Exception as e:
                logger.warning(f'Evidence analyzer init failed: {e}')
                self._evidence_analyzer = None
        return self._evidence_analyzer

    def _classify_query(self, query: str) -> QueryType:
        """
        Classify query type for intelligent tool selection.

        Uses keyword matching and heuristics to determine the best
        query type for routing to appropriate tools.
        """
        query_lower = query.lower()
        academic_keywords = ['paper', 'research', 'study', 'journal', 'arxiv', 'doi', 'citation', 'publication', 'conference', 'thesis', 'dissertation', 'peer-reviewed', 'methodology', 'hypothesis', 'experiment']
        if any((kw in query_lower for kw in academic_keywords)):
            return QueryType.ACADEMIC
        technical_keywords = ['api', 'code', 'github', 'documentation', 'sdk', 'library', 'framework', 'tutorial', 'how to', 'implementation', 'algorithm']
        if any((kw in query_lower for kw in technical_keywords)):
            return QueryType.TECHNICAL
        news_keywords = ['news', 'latest', 'recent', 'today', 'yesterday', 'this week', 'breaking', 'update', 'announcement', 'launch', 'release']
        if any((kw in query_lower for kw in news_keywords)):
            return QueryType.NEWS
        historical_keywords = ['history', 'archived', 'past', 'wayback', 'old', 'former', 'vintage', 'retro', 'legacy', 'deprecated', 'original']
        if any((kw in query_lower for kw in historical_keywords)):
            return QueryType.HISTORICAL
        person_keywords = ['person', 'people', 'biography', 'profile', 'who is', 'founder', 'ceo', 'author', 'researcher', 'developer', 'contact']
        if any((kw in query_lower for kw in person_keywords)):
            return QueryType.PERSON
        org_keywords = ['company', 'organization', 'corp', 'inc', 'ltd', 'startup', 'enterprise', 'business', 'firm', 'agency', 'institute']
        if any((kw in query_lower for kw in org_keywords)):
            return QueryType.ORGANIZATION
        security_keywords = ['vulnerability', 'exploit', 'breach', 'hack', 'security', 'cve', 'malware', 'ransomware', 'phishing', 'leak']
        if any((kw in query_lower for kw in security_keywords)):
            return QueryType.SECURITY
        return QueryType.GENERAL

    def _select_tools_for_query(self, query_type: QueryType) -> list[str]:
        """
        Select appropriate tools based on query type and depth.

        Returns list of tool names to execute.
        """
        tools = []
        if self.config.should_use_tool('academic'):
            tools.append('academic')
        if self.config.should_use_tool('web'):
            tools.append('stealth_crawler')
        if self.config.depth.value >= ResearchDepth.ADVANCED.value:
            if self.config.should_use_tool('archives'):
                tools.append('archives')
        if self.config.depth.value >= ResearchDepth.EXHAUSTIVE.value:
            if self.config.should_use_tool('temporal'):
                tools.append('temporal')
            if self.config.should_use_tool('data_leak'):
                tools.append('data_leak')
            if self.config.should_use_tool('osint'):
                tools.append('osint')
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

    async def deep_research(self, query: str, depth: ResearchDepth | None=None, query_type: QueryType | None=None, max_results: int=50, correlation: RunCorrelation | None=None, grounding_hints: CanonicalGroundingHints | None=None) -> UnifiedResearchResult:
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
        self._stealth_fetch_count = 0
        research_depth = depth or self.config.depth
        detected_type = query_type or self._classify_query(query)
        logger.info(f"Starting deep research: '{query}'")
        logger.info(f'Depth: {research_depth.name}, Type: {detected_type.value}')
        result = UnifiedResearchResult(query=query, depth=research_depth, query_type=detected_type, correlation=correlation)
        tools_to_use = self._select_tools_for_query(detected_type)
        logger.info(f'Selected tools: {tools_to_use}')
        all_findings: list[ResearchFinding] = []
        try:
            search_tasks = []
            if 'academic' in tools_to_use:
                search_tasks.append(self._task_search(query, 'academic'))
            if 'stealth_crawler' in tools_to_use:
                search_tasks.append(self._task_search(query, 'web'))
            async with self._semaphore:
                search_results = await parallel(search_tasks, policy="log", ctx="enhanced_research:857")
            for findings in search_results.ok:
                if isinstance(findings, list):
                    all_findings.extend(findings)
            self._context_swap()
            if self.config.enable_advanced_rag:
                rag_findings = await self._task_advanced_rag(query)
                if rag_findings:
                    all_findings.extend(rag_findings)
                    self._context_swap()
            if research_depth == ResearchDepth.EXHAUSTIVE:
                cross_ref_tasks = []
                if 'archives' in tools_to_use:
                    cross_ref_tasks.append(self._task_cross_reference(query, all_findings))
                if 'data_leak' in tools_to_use:
                    cross_ref_tasks.append(self._task_data_leak_check(query))
                if cross_ref_tasks:
                    async with self._semaphore:
                        cross_results = await parallel(cross_ref_tasks, policy="log", ctx="enhanced_research:888")
                    for findings in cross_results.ok:
                        if isinstance(findings, list):
                            all_findings.extend(findings)
                self._context_swap()
            if self.config.enable_stealth_browser:
                stealth_findings = await self._task_stealth_browser(query, all_findings)
                if stealth_findings:
                    all_findings.extend(stealth_findings)
                    self._context_swap()
            if self.config.enable_stealth_browser or self.config.enable_structured_extraction:
                structured_findings = await self._task_structured_extraction(all_findings)
                if structured_findings:
                    all_findings.extend(structured_findings)
                    self._context_swap()
            if 'temporal' in tools_to_use and len(all_findings) > 5:
                temporal_result = await self._task_analyze(query, all_findings)
                result.temporal_analysis = temporal_result
                self._context_swap()
            validation = await self._task_validate(query, all_findings)
            result.validation_report = validation
            if self.config.enable_evidence_analyzer:
                evidence_result = await self._task_evidence_analysis(all_findings)
                if evidence_result:
                    result.cross_references = evidence_result.get('edges', result.cross_references)
            fused = await self._task_synthesize(query, all_findings)
            result.fused_results = fused
            if self.config.enable_deduplication:
                all_findings = self._deduplicate_findings(all_findings)
            all_findings = self._rank_findings(all_findings, query)
            result.findings = all_findings[:max_results]
            result.total_sources_found = len(all_findings)
            result.unique_sources = len({f.url for f in result.findings if f.url})
            result.sources_used = list({f.source for f in result.findings})
            result.tools_executed = tools_to_use
            result.confidence_score = self._calculate_confidence(result)
            result.coverage_score = min(1.0, len(result.findings) / max_results)
        except Exception as e:
            logger.error(f'Deep research error: {e}')
            result.findings = all_findings
        finally:
            if self._start_time:
                result.execution_time_seconds = time.time() - self._start_time
            self._stats['queries_processed'] += 1
            self._stats['total_findings'] += len(result.findings)
            logger.info(f'Deep research completed in {result.execution_time_seconds:.2f}s')
            logger.info(f'Found {len(result.findings)} findings from {len(result.sources_used)} sources')
        return result

    async def _task_search(self, query: str, source_type: str) -> list[ResearchFinding]:
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
                engine = await self._get_academic_engine()
                result = await engine.search(query, max_results=20)
                for r in result.deduplicated_results:
                    finding = ResearchFinding(id=hashlib.blake2b(f'{r.title}{r.url}'.encode(), digest_size=8).hexdigest(), title=r.title, content=r.snippet, url=r.url, source='academic_search', source_type='academic', timestamp=datetime.now(UTC), relevance_score=r.relevance_score, credibility_score=0.8 if r.source in ['arxiv', 'crossref'] else 0.6, metadata={'authors': r.metadata.get('authors', []), 'published': r.metadata.get('published', ''), 'citations': r.metadata.get('citation_count', 0), 'source_name': r.source})
                    findings.append(finding)
                logger.info(f'Academic search: {len(findings)} results')
            elif source_type == 'web':
                crawler = await self._get_stealth_crawler()
                results = crawler.search(query, num_results=15)
                for r in results:
                    finding = ResearchFinding(id=hashlib.blake2b(f'{r.title}{r.url}'.encode(), digest_size=8).hexdigest(), title=r.title, content=r.snippet, url=r.url, source='stealth_crawler', source_type='web', timestamp=datetime.now(UTC), relevance_score=0.5, credibility_score=0.5, metadata={'rank': r.rank})
                    findings.append(finding)
                logger.info(f'Web search: {len(findings)} results')
        except Exception as e:
            logger.warning(f'Search task failed ({source_type}): {e}')
        return findings

    async def _task_analyze(self, query: str, findings: list[ResearchFinding]) -> dict[str, Any]:
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
            timestamps = []
            values = []
            for _i, f in enumerate(findings):
                ts = f.temporal_relevance or f.timestamp
                timestamps.append(ts)
                values.append(f.relevance_score)
            if len(timestamps) < 5:
                return {'error': 'Insufficient data for temporal analysis'}
            analysis = analyzer.analyze(query=query, timestamps=timestamps, values=values, analysis_types=['trend', 'patterns', 'scenarios'])
            return {'trend_direction': analysis.trend.direction.value if analysis.trend else None, 'trend_confidence': analysis.trend.confidence if analysis.trend else 0, 'patterns_detected': len(analysis.patterns), 'scenarios_generated': len(analysis.scenarios), 'overall_confidence': analysis.overall_confidence, 'insights': analysis.insights, 'recommendations': analysis.recommendations}
        except Exception as e:
            logger.warning(f'Analysis task failed: {e}')
            return {'error': str(e)}

    async def _task_synthesize(self, query: str, findings: list[ResearchFinding]) -> list[dict[str, Any]]:
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
            source_results: dict[str, list[SearchResult]] = {}
            for i, f in enumerate(findings):
                source = f.source_type
                if source not in source_results:
                    source_results[source] = []
                source_results[source].append(SearchResult(id=f.id, title=f.title, content=f.content, url=f.url, source=source, score=f.relevance_score, rank=i + 1, metadata=f.metadata))
            if self.config.enable_rrf and len(source_results) > 1:
                fused = await self._rrf.fuse(source_results)
            else:
                fused = []
                for source, results in source_results.items():
                    fused.extend(results)
                fused.sort(key=lambda x: x.score, reverse=True)
            return [{'id': r.id, 'title': r.title, 'content': r.content[:500] if r.content else '', 'url': r.url, 'source': r.source, 'score': r.score, 'rank': r.rank, 'metadata': r.metadata} for r in fused[:50]]
        except Exception as e:
            logger.warning(f'Synthesis task failed: {e}')
            return [{'id': f.id, 'title': f.title, 'content': f.content[:500] if f.content else '', 'url': f.url, 'source': f.source_type, 'score': f.relevance_score} for f in sorted(findings, key=lambda x: x.relevance_score, reverse=True)[:50]]

    async def _task_cross_reference(self, query: str, existing_findings: list[ResearchFinding]) -> list[ResearchFinding]:
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
            urls_to_check = [f.url for f in existing_findings[:10] if f.url and f.source_type == 'web']
            if not urls_to_check:
                return []
            resurrector = await self._get_archive_resurrector()

            async def _resurrect_one(url: str) -> ResearchFinding | None:
                """ISSUE-006 parallel fetch — was sequential resurrect()."""
                try:
                    res_result = await resurrector.resurrect(url)
                    if res_result.success and res_result.best_snapshot:
                        return ResearchFinding(id=f'arch_{res_result.request_id}', title=res_result.title or f'Archive: {url}', content=res_result.content[:1000] if res_result.content else '', url=res_result.best_snapshot.archived_url, source='archive_resurrector', source_type='archive', timestamp=res_result.best_snapshot.timestamp, relevance_score=0.6, credibility_score=0.7, metadata={'original_url': url, 'snapshot_timestamp': res_result.best_snapshot.timestamp.isoformat(), 'content_type': res_result.best_snapshot.content_type.value, 'quality_score': res_result.best_snapshot.quality_score})
                except Exception as e:
                    logger.debug(f'Cross-reference failed for {url}: {e}')
                return None
            results = await chunked_taskgroup(urls_to_check, _resurrect_one, batch_size=10, concurrency=5, ctx='archive_resurrection')
            cross_ref_findings = [r for r in results if r is not None]
            logger.info(f'Cross-reference: {len(cross_ref_findings)} archive findings')
        except Exception as e:
            logger.warning(f'Cross-reference task failed: {e}')
        return cross_ref_findings

    async def _task_data_leak_check(self, query: str) -> list[ResearchFinding]:
        """
        Check for data leaks related to query.

        Args:
            query: Research query (may contain emails, domains, etc.)

        Returns:
            Leak findings if any
        """
        leak_findings = []
        try:
            emails = _EMAIL_PATTERN.findall(query)
            domains = _DOMAIN_PATTERN.findall(query)
            if not emails and (not domains):
                return []
            hunter = await self._get_data_leak_hunter()
            # F1 FIX: use lambda pattern for dynamic UMA-aware concurrency
            from hledac.universal.core.concurrency_registry import concurrency_budget, ConcurrencyCategory
            raw_alerts = await bounded_parallel_map(emails[:3], lambda e: hunter.check_target(e, 'email'), concurrency=lambda: concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL), ctx='data_leak_email')
            for alerts in raw_alerts:
                if alerts is None:
                    continue
                for alert in alerts:
                    finding = ResearchFinding(id=f'leak_{alert.alert_id}', title=f'Data Leak: {alert.breach_name}', content=f'Target found in breach: {alert.breach_name}', url=alert.url, source='data_leak_hunter', source_type='security', timestamp=alert.timestamp, relevance_score=0.9 if alert.severity.value in ['high', 'critical'] else 0.7, credibility_score=0.8, metadata={'target': alert.target, 'breach_name': alert.breach_name, 'severity': alert.severity.value, 'leaked_data_types': alert.leaked_data.get('compromised_data', [])})
                    leak_findings.append(finding)
            logger.info(f'Data leak check: {len(leak_findings)} alerts')
        except Exception as e:
            logger.warning(f'Data leak check failed: {e}')
        return leak_findings

    async def _task_advanced_rag(self, query: str) -> list[ResearchFinding]:
        """
        Phase 1.5: Advanced RAG grounding via advanced_rag.RAGOrchestrator.

        Bounded contract:
            - Returns at most config.max_advanced_findings ResearchFinding objects.
            - Never raises: any exception → empty list + warning log.
            - Backs onto canonical LanceDBIdentityStore (single connection).
        """
        findings: list[ResearchFinding] = []
        try:
            rag = await self._get_advanced_rag()
            if rag is None:
                return []
            result = await rag.research_and_answer(query=query, confidence_threshold=0.6, priority=5)
            self._stats['advanced_rag_queries'] += 1
            cap = min(self.config.max_advanced_findings, _MAX_ADVANCED_RAG_FINDINGS)
            sources = result.get('sources', []) or []
            for src in sources[:cap]:
                text = (src.get('text') or '').strip()
                if not text:
                    continue
                sim = float(src.get('similarity', 0.0))
                finding = ResearchFinding(id=hashlib.blake2b(f'rag:{text[:80]}'.encode(), digest_size=8).hexdigest(), title=f'RAG source (sim={sim:.2f})', content=text[:500], url=None, source='advanced_rag', source_type='rag', timestamp=datetime.now(UTC), relevance_score=sim, credibility_score=0.7, metadata={'stages_completed': result.get('stages_completed', []), 'confidence': result.get('confidence', 0.0), 'processing_time': (result.get('metadata') or {}).get('processing_time', 0.0)})
                findings.append(finding)
        except Exception as e:
            logger.warning(f'_task_advanced_rag failed: {e}')
        return findings

    async def _task_stealth_browser(self, query: str, existing_findings: list[ResearchFinding]) -> list[ResearchFinding]:
        """
        Phase 2.5: Stealth browser enrichment via advanced_web.StealthBrowser.

        Fetches the top _MAX_STEALTH_FETCHES web URLs from existing findings
        with JS-rendered content. Honors M1 constraint: stealth_browser caps
        concurrent tabs at 2 (see advanced_web/stealth_browser._MAX_CONCURRENT_TABS).

        Bounded contract:
            - At most _MAX_STEALTH_FETCHES URLs per sprint.
            - _stealth_fetch_count resets each sprint.
            - Only fetches URLs whose source_type is 'web' (avoid double-fetch).
            - Never raises: any exception → empty list + warning log.
        """
        findings: list[ResearchFinding] = []
        if self._stealth_fetch_count >= _MAX_STEALTH_FETCHES:
            logger.debug('_task_stealth_browser: per-sprint cap reached (%d)', _MAX_STEALTH_FETCHES)
            return findings
        try:
            browser = await self._get_stealth_browser()
            if browser is None:
                return []
            urls: list[str] = []
            seen: set[str] = set()
            for f in existing_findings:
                if f.url and f.source_type == 'web' and (f.url not in seen):
                    seen.add(f.url)
                    urls.append(f.url)
                if len(urls) >= _MAX_STEALTH_FETCHES:
                    break
            if not urls:
                return findings
            budget = _MAX_STEALTH_FETCHES - self._stealth_fetch_count
            urls = urls[:budget]

            async def _fetch_one(url: str) -> ResearchFinding | None:
                """ISSUE-006 parallel fetch — was sequential browser.fetch()."""
                self._stealth_fetch_count += 1
                self._stats['stealth_fetches'] += 1
                try:
                    result = await browser.fetch(url, depth=_MAX_STEALTH_DEPTH)
                except Exception as e:
                    logger.debug(f'Stealth fetch failed for {url}: {e}')
                    return None
                if not isinstance(result, dict) or result.get('status') != 200:
                    return None
                content = (result.get('content') or '').strip()[:1000]
                if not content:
                    return None
                title = (result.get('title') or '').strip() or f'Stealth: {url}'
                return ResearchFinding(id=hashlib.blake2b(f'stealth:{url}'.encode(), digest_size=8).hexdigest(), title=title[:200], content=content, url=url, source='stealth_browser', source_type='web_stealth', timestamp=datetime.now(UTC), relevance_score=0.6, credibility_score=0.7, metadata={'js_rendered': bool(result.get('js_rendered', False)), 'links': (result.get('links') or [])[:10], 'budget_remaining': _MAX_STEALTH_FETCHES - self._stealth_fetch_count})
            results = await chunked_taskgroup(urls, _fetch_one, batch_size=10, concurrency=2, ctx='stealth_browser')
            findings = [r for r in results if r is not None]
        except Exception as e:
            logger.warning(f'_task_stealth_browser failed: {e}')
        return findings

    async def _task_evidence_analysis(self, findings: list[ResearchFinding]) -> dict[str, Any] | None:
        """
        Phase 4.5: Evidence network analysis via
        advanced_web.EvidenceNetworkAnalyzer.

        Currently the analyzer is a NOT_IMPLEMENTED stub. When the real
        implementation lands (IMPLEMENTATION_ROADMAP T1), this seam stays
        the same — the stub will be replaced transparently.

        Returns:
            dict with at minimum 'edges' key (list of relationships), or None
            on failure.
        """
        try:
            analyzer = await self._get_evidence_analyzer()
            if analyzer is None:
                return None
            self._stats['evidence_analyses'] += 1
            entities: list[dict[str, Any]] = []
            for f in findings[:50]:
                if f.url:
                    entities.append({'type': 'url', 'value': f.url, 'sources': [f.source]})
            return await analyzer.analyze_network(entities)
        except Exception as e:
            logger.warning(f'_task_evidence_analysis failed: {e}')
            return None

    async def _task_structured_extraction(self, existing_findings: list[ResearchFinding]) -> list[ResearchFinding]:
        """
        Phase 2.6: Structured data extraction (W3C JSON-LD + microdata + RDFa).

        Consumes the HTML content already produced by StealthBrowser or fetches
        top web URLs directly. Each entity is converted to a ResearchFinding
        with ioc_kind as source_type. Bounded: MAX_STRUCTURED_ENTITIES per sprint.

        Fail-soft: any exception → empty list + warning log.
        """
        findings: list[ResearchFinding] = []
        if not existing_findings:
            return findings
        try:
            from .advanced_web.structured_extractor import StructuredExtractor
            extractor = StructuredExtractor()
        except Exception as e:
            logger.warning(f'_task_structured_extraction: import failed: {e}')
            return findings
        seen: set[str] = set()
        urls: list[str] = []
        for f in existing_findings:
            if f.url and f.source_type in ('web', 'web_stealth') and (f.url not in seen):
                seen.add(f.url)
                urls.append(f.url)
            if len(urls) >= _MAX_STRUCTURED_ENTITIES:
                break
        budget = _MAX_STRUCTURED_ENTITIES - len(findings)
        for url in urls[:budget]:
            try:
                html = f.metadata.get('html') if hasattr(f, 'metadata') else None
                if not html:
                    continue
                extraction = extractor.extract(html, source_url=url)
                self._stats['structured_entities'] = self._stats.get('structured_entities', 0) + len(extraction.entities)
                for ent in extraction.entities:
                    if len(findings) >= _MAX_STRUCTURED_ENTITIES:
                        break
                    findings.append(ResearchFinding(id=hashlib.blake2b(f'structured:{ent.entity_id}'.encode(), digest_size=8).hexdigest(), title=f'[{ent.ioc_kind}] {ent.entity_type}: {ent.value}', content=ent.value[:500], url=ent.url or url, source='structured_extractor', source_type=ent.ioc_kind, timestamp=datetime.now(UTC), relevance_score=0.6, credibility_score=0.7, metadata={'entity_type': ent.entity_type, 'entity_id': ent.entity_id, 'properties': dict(ent.properties)}))
            except Exception as e:
                logger.debug(f'_task_structured_extraction: {url} failed: {e}')
        return findings

    async def _task_validate(self, query: str, findings: list[ResearchFinding]) -> dict[str, Any]:
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
            url_groups: dict[str, list[ResearchFinding]] = {}
            for f in findings:
                if f.url:
                    url_groups.setdefault(f.url, []).append(f)
            validated_count = 0
            cross_validated = []
            for url, group in url_groups.items():
                if len(group) > 1:
                    validated_count += 1
                    cross_validated.append({'url': url, 'sources': [f.source for f in group], 'agreement_score': len(group) / len({f.source for f in findings})})
            total_with_url = len([f for f in findings if f.url])
            validation_rate = validated_count / total_with_url if total_with_url > 0 else 0
            return {'valid': validation_rate > 0.1, 'validation_rate': validation_rate, 'total_findings': len(findings), 'cross_validated_count': validated_count, 'cross_validated_urls': cross_validated[:10], 'source_diversity': len({f.source for f in findings}), 'high_credibility_count': len([f for f in findings if f.credibility_score > 0.7])}
        except Exception as e:
            logger.warning(f'Validation task failed: {e}')
            return {'valid': False, 'error': str(e)}

    async def _task_enhance(self, query: str, context: dict[str, Any]) -> str:
        """
        Generate enhanced/reformulated query based on context.

        Args:
            query: Original query
            context: Research context

        Returns:
            Enhanced query string
        """
        enhancements = []
        if 'key_terms' in context:
            enhancements.extend(context['key_terms'][:2])
        if 'temporal_analysis' in context:
            current_year = datetime.now(UTC).year
            enhancements.append(str(current_year))
        if enhancements:
            return f"{query} {' '.join(enhancements)}"
        return query

    async def _task_source_discovery(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """Task: Source Discovery via DeepSourceRegistry (Sprint F270, Phase 2.7).

        Discovers curated beyond-surface OSINT sources (dark web, archives,
        paste sites, code intelligence, leak DBs, P2P gateways) that are
        relevant to the current query. Filters by current transport
        capabilities so unreachable sources are pruned eagerly.

        Returns a dict compatible with the rest of the UnifiedResearchEngine
        task pipeline:
            {
                "query": str,
                "findings": list[CanonicalFinding],   # source_type="source_discovery"
                "count": int,
                "tier": str | None,                    # tier filter used
            }
        """
        logger.info(f"Task: Source discovery for '{query}'")
        tier = context.get('tier') if isinstance(context, dict) else None
        caps_override = context.get('transport_capabilities') if isinstance(context, dict) else None
        transport_caps = caps_override if isinstance(caps_override, set) else None
        max_results = 20
        if isinstance(context, dict) and isinstance(context.get('max_results'), int):
            max_results = max(1, min(100, context['max_results']))
        try:
            findings = await asyncio.to_thread(discover_deep_sources, query, transport_caps, max_results, tier)
        except Exception as exc:
            logger.warning(f'Source discovery failed: {exc}')
            return {'query': query, 'findings': [], 'count': 0, 'tier': tier}
        return {'query': query, 'findings': findings, 'count': len(findings), 'tier': tier}

    def _deduplicate_findings(self, findings: list[ResearchFinding]) -> list[ResearchFinding]:
        """Deduplicate findings based on URL and content similarity."""
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        unique: list[ResearchFinding] = []
        for f in findings:
            if f.url:
                normalized_url = f.url.lower().rstrip('/')
                if normalized_url in seen_urls:
                    continue
                seen_urls.add(normalized_url)
            content_hash = hashlib.blake2b(f.content[:200].lower().encode(), digest_size=8).hexdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            unique.append(f)
        return unique

    def _rank_findings(self, findings: list[ResearchFinding], query: str) -> list[ResearchFinding]:
        """Rank findings by relevance to query."""
        query_terms = set(query.lower().split())
        for f in findings:
            title_terms = set(f.title.lower().split())
            content_terms = set(f.content.lower().split())
            title_matches = len(query_terms & title_terms)
            content_matches = len(query_terms & content_terms)
            f.relevance_score = f.relevance_score * 0.3 + title_matches / len(query_terms) * 0.4 + content_matches / len(query_terms) * 0.2 + f.credibility_score * 0.1
        return sorted(findings, key=lambda x: x.relevance_score, reverse=True)

    def _calculate_confidence(self, result: UnifiedResearchResult) -> float:
        """Calculate overall confidence score."""
        if not result.findings:
            return 0.0
        source_factor = min(1.0, len(result.sources_used) / 3)
        avg_credibility = sum((f.credibility_score for f in result.findings)) / len(result.findings)
        validation_factor = result.validation_report.get('validation_rate', 0) if result.validation_report else 0
        confidence = source_factor * 0.3 + avg_credibility * 0.4 + validation_factor * 0.3
        return min(1.0, confidence)

    def _context_swap(self) -> None:
        """M1 Optimization: Aggressive cleanup between phases."""
        gc.collect()
        logger.debug('Context swap: garbage collection completed')

    async def cleanup(self) -> None:
        """Cleanup all resources."""
        logger.info('Cleaning up UnifiedResearchEngine...')
        tools = [self._academic_engine, self._archive_discovery, self._archive_resurrector, self._stealth_crawler, self._stealth_scraper, self._web_intelligence, self._data_leak_hunter]
        for tool in tools:
            if tool and hasattr(tool, 'cleanup'):
                try:
                    await tool.cleanup()
                except Exception as e:
                    logger.debug(f'Cleanup error: {e}')
        for provider in (self._advanced_rag, self._stealth_browser, self._evidence_analyzer):
            if provider and hasattr(provider, 'cleanup'):
                try:
                    await provider.cleanup()
                except Exception as e:
                    logger.debug(f'Advanced provider cleanup error: {e}')
        self._advanced_rag = None
        self._stealth_browser = None
        self._evidence_analyzer = None
        self._stealth_fetch_count = 0
        self._cache.clear()
        self._context_swap()
        logger.info('UnifiedResearchEngine cleanup completed')

    def get_statistics(self) -> dict[str, Any]:
        """Get engine statistics."""
        return {**self._stats, 'config': {'depth': self.config.depth.name, 'max_concurrent': self.config.max_concurrent_tools, 'parallel_enabled': self.config.enable_parallel, 'advanced_rag_enabled': self.config.enable_advanced_rag, 'stealth_browser_enabled': self.config.enable_stealth_browser, 'evidence_analyzer_enabled': self.config.enable_evidence_analyzer}, 'tools_initialized': self._stats['tools_initialized']}

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
    __slots__ = tuple(('_stats', 'enhanced_config', 'performance_monitor', 'predictive_planner', 'workflow_engine'))

    def __init__(self, config: ResearchConfig | None=None, enhanced_config: EnhancedResearchConfig | None=None):
        super().__init__(config)
        self.enhanced_config = enhanced_config or EnhancedResearchConfig()
        self.workflow_engine = WorkflowEngine(max_concurrency=3)
        self.predictive_planner = PredictivePlanner(min_confidence=0.7)
        self.performance_monitor = PerformanceMonitor()
        self._init_enhancement_components()
        self._stats: dict[str, Any] = {'queries_expanded': 0, 'sources_fused': 0, 'documents_retrieved': 0, 'stealth_operations': 0}
        logger.info('EnhancedResearchOrchestrator initialized')
        logger.info('Features: Workflow Engine, Predictive Planning, Performance Monitoring')
        logger.info('Extended Features: Query Expansion, Result Fusion, Hybrid RAG, Stealth Mode')

    def _init_enhancement_components(self) -> None:
        """Initialize research enhancement components based on configuration."""
        cfg = self.enhanced_config
        if cfg.enable_fusion:
            self.rrf = ReciprocalRankFusion(RRFConfig(k=cfg.rrf_k))
        else:
            self.rrf = None
        if cfg.enable_rag:
            from .knowledge.rag_engine import RAGConfig, RAGEngine
            self.rag = RAGEngine(RAGConfig())
        else:
            self.rag = None
        if cfg.enable_expansion:
            self.wordlist = IntelligentWordlistGenerator(WordlistConfig(max_variations=cfg.max_query_variations, domain_context='academic'))
        else:
            self.wordlist = None
        if cfg.enable_stealth:
            self.behavior = BehaviorSimulator(SimulationConfig(pattern=cfg.behavior_pattern))
        else:
            self.behavior = None

    async def expand_research_query(self, query: str, domain: str | None=None) -> list[str]:
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
        if domain:
            domain_variations = await bounded_parallel_map(variations[:5], lambda v: self.wordlist.generate_for_discovery([v], modifiers=['paper', 'research', 'study', 'review']), concurrency=5, ctx='domain_variations')
            for ext_result in domain_variations:
                if ext_result is not None:
                    variations.extend(ext_result)
        unique = list(dict.fromkeys(variations))
        self._stats['queries_expanded'] += len(unique)
        logger.info(f"Expanded query '{query}' into {len(unique)} variations")
        return unique[:self.enhanced_config.max_query_variations]

    async def fuse_research_results(self, source_results: dict[str, list[dict[str, Any]]]) -> list[SearchResult]:
        """
        Fuse results from multiple research sources using Reciprocal Rank Fusion.

        RRF combines ranked results from different sources without requiring
        score normalization, producing a single unified ranking.

        Args:
            source_results: dict mapping source name to list of results.
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
            all_results = []
            for source, results in source_results.items():
                for r in results:
                    all_results.append(SearchResult(id=r.get('url', '') or r.get('title', ''), title=r.get('title', ''), content=r.get('content', ''), url=r.get('url'), source=source, score=r.get('score', 0.0)))
            return all_results
        search_results: dict[str, list[SearchResult]] = {}
        for source, results in source_results.items():
            search_results[source] = []
            for i, r in enumerate(results):
                result = SearchResult(id=r.get('url', f'{source}_{i}'), title=r.get('title', ''), content=r.get('content', ''), url=r.get('url'), source=source, score=r.get('score', 0.0), rank=i + 1, metadata=r.get('metadata', {}))
                search_results[source].append(result)
        fused = await self.rrf.fuse(search_results)
        self._stats['sources_fused'] += len(source_results)
        logger.info(f'Fused {len(source_results)} sources into {len(fused)} unique results')
        return fused

    async def retrieve_research_context(self, query: str, documents: list[dict[str, Any]]) -> list[str]:
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
            return [d.get('content', '') for d in documents[:cfg.rag_top_k]]
        docs = []
        for i, d in enumerate(documents):
            doc = Document(id=d.get('id', f'doc_{i}'), content=d.get('content', ''), metadata=d.get('metadata', {}))
            docs.append(doc)
        results = await self.rag.hybrid_retrieve(query, docs, top_k=cfg.rag_top_k)
        self._stats['documents_retrieved'] += len(results)
        return [r.chunk_text for r in results]

    async def stealth_research(self, query: str, url: str, scrape_func: Callable | None=None) -> dict[str, Any]:
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
            logger.warning('Stealth mode disabled, falling back to normal research')
            return await self.research(query, domain='academic')
        logger.info(f'Starting stealth research: {url}')
        behavior_stats = await self.behavior.simulate_page_visit(num_scrolls=_RNG.randint(2, 5), read_time=_RNG.uniform(10, 20))
        content = None
        if scrape_func:
            try:
                content = await scrape_func(url, self.behavior)
            except Exception as e:
                logger.error(f'Stealth scrape failed: {e}')
        self._stats['stealth_operations'] += 1
        return {'query': query, 'url': url, 'content': content, 'behavior_simulation': behavior_stats, 'success': content is not None}

    async def research(self, query: str, search_func: Callable | None=None, domain: str | None=None) -> dict[str, Any]:
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
        logger.info(f'Starting enhanced research for: {query}')
        start_time = self.performance_monitor.start_timer()
        queries = await self.expand_research_query(query, domain)
        all_results: dict[str, list[dict[str, Any]]] = {}
        if search_func:
            search_results = await bounded_parallel_map(queries[:3], search_func, concurrency=lambda: concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL), ctx='search_queries')
            for results in search_results:
                if results is None:
                    continue
                try:
                    source = results.get('source', 'unknown')
                    if source not in all_results:
                        all_results[source] = []
                    all_results[source].extend(results.get('results', []))
                except Exception as e:
                    logger.warning(f'Search failed for query: {e}')
        fused_results = []
        if all_results:
            fused_results = await self.fuse_research_results(all_results)
        context = []
        if fused_results:
            documents = [{'id': r.id, 'content': r.content, 'metadata': {'title': r.title, 'url': r.url, 'source': r.source}} for r in fused_results[:20]]
            context = await self.retrieve_research_context(query, documents)
        perf_stats = self.performance_monitor.record(tokens=sum((len(r.content.split()) for r in fused_results[:10])), start_time=start_time)
        logger.info(f"Research completed in {perf_stats['duration']:.2f}s")
        return {'query': query, 'expanded_queries': queries, 'fused_results': [{'title': r.title, 'content': r.content[:500], 'url': r.url, 'source': r.source, 'score': r.score, 'rank': r.rank} for r in fused_results[:10]], 'context': context, 'statistics': {'queries_expanded': len(queries), 'sources_searched': len(all_results), 'results_fused': len(fused_results), 'context_chunks': len(context), 'duration_seconds': perf_stats.get('duration', 0), 'tokens_processed': perf_stats.get('tokens', 0)}}

    def create_research_workflow(self, query: str, mode: ResearchMode=None) -> Workflow:
        """
        Vytvořit výzkumný workflow.

        Args:
            query: Výzkumný dotaz
            mode: Režim výzkumu

        Returns:
            Workflow definice
        """
        mode = mode or self.config.mode
        workflow = Workflow(id=f'research_{hash(query) % 10000}', name=f'Research: {query[:50]}', context={'query': query, 'mode': mode.value})
        task_search = Task(id='search', name='Initial Search', func=self._task_search, params={'query': query})
        workflow.add_task(task_search)
        task_osint = Task(id='osint', name='OSINT Discovery', func=self._task_osint, params={'query': query})
        workflow.add_task(task_osint)
        task_academic = Task(id='academic', name='Academic Search', func=self._task_academic, params={'query': query}, dependencies=['search'])
        workflow.add_task(task_academic)
        task_deep_read = Task(id='deep_read', name='Deep Read', func=self._task_deep_read, params={'urls': '${osint_result.urls}'}, dependencies=['osint'], max_retries=2)
        workflow.add_task(task_deep_read)
        task_fact_check = Task(id='fact_check', name='Fact Check', func=self._task_fact_check, params={}, dependencies=['academic', 'deep_read'])
        workflow.add_task(task_fact_check)
        task_synthesis = Task(id='synthesis', name='Synthesis', func=self._task_synthesis, params={'query': query}, dependencies=['fact_check'])
        workflow.add_task(task_synthesis)
        return workflow

    async def execute_workflow(self, workflow: Workflow, use_predictions: bool=True) -> ResearchResult:
        """
        Vykonat workflow s prediktivním plánováním.

        Args:
            workflow: Workflow k vykonání
            use_predictions: Použít prediktivní plánování

        Returns:
            Výsledek výzkumu
        """
        logger.info(f'Executing workflow: {workflow.name}')
        start_time = self.performance_monitor.start_timer()
        if use_predictions:
            result = await self._execute_with_prediction(workflow)
        else:
            results = await self.workflow_engine.execute(workflow)
            result = self._compile_results(workflow, results)
        perf_stats = self.performance_monitor.record(tokens=len(result.final_answer.split()), start_time=start_time)
        logger.info(f"Workflow completed in {perf_stats['duration']:.2f}s")
        logger.info(f"Speedup: {perf_stats.get('speedup', 0):.1f}×")
        return result

    async def _execute_with_prediction(self, workflow: Workflow) -> ResearchResult:
        """Vykonat s prediktivním plánováním"""

        async def planner(ctx):
            return [{'action': task.id, 'params': task.params} for task in workflow.tasks.values()]

        async def executor(action, params, ctx):
            if action in workflow.tasks:
                task = workflow.tasks[action]
                return await task.execute(ctx)
            return None
        predictive_result = await self.predictive_planner.plan_with_prediction(planner_func=planner, executor_func=executor, context=workflow.context)
        return self._compile_results(workflow, predictive_result.get('results', {}))

    def _compile_results(self, workflow: Workflow, results: dict[str, Any]) -> ResearchResult:
        """Zkompilovat výsledky do ResearchResult"""
        synthesis = results.get('synthesis', '')
        sources = []
        for task_id, result in results.items():
            if isinstance(result, dict) and 'url' in result:
                sources.append({'url': result['url'], 'title': result.get('title', ''), 'type': task_id})
        return ResearchResult(success=True, query=workflow.context.get('query', ''), mode=self.config.mode, final_answer=synthesis if synthesis else 'Research completed', sources=sources, statistics={'workflow_duration': sum((t.duration() or 0 for t in workflow.tasks.values())), 'tasks_completed': sum((1 for t in workflow.tasks.values() if t.status.value == 'completed'))})

    async def _task_search(self, query: str, context: dict) -> dict:
        """Task: Initial Search using query expansion and RAG"""
        logger.info(f"Task: Search for '{query}'")
        results = []
        if self.wordlist is not None:
            try:
                variations = self.wordlist.expand(query)
                logger.info(f'Query expanded into {len(variations)} variations')
            except Exception as e:
                logger.warning(f'Query expansion failed: {e}')
                variations = [query]
        else:
            variations = [query]
        if self.rag is not None:
            try:
                rag_results_list = await bounded_parallel_map(variations[:3], lambda v: self.rag.retrieve(v, top_k=5), concurrency=lambda: concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL), ctx='rag_retrieval')
                for rag_results in rag_results_list:
                    if rag_results is not None:
                        results.extend(rag_results)
            except Exception as e:
                logger.warning(f'RAG retrieval failed: {e}')
        if self.behavior is not None and self.enhanced_config.enable_stealth:
            try:
                await self.behavior.simulate_access_pattern()
            except Exception as e:
                logger.debug(f'Behavior simulation skipped: {e}')
        return {'query': query, 'variations': variations, 'results_count': len(results), 'results': results[:10]}

    async def _task_osint(self, query: str, context: dict) -> dict:
        """Task: OSINT Discovery using web intelligence"""
        logger.info(f"Task: OSINT for '{query}'")
        urls = []
        sources = {}
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
                logger.warning(f'Web search failed: {e}')
        if hasattr(self, '_search_archives'):
            try:
                archive_results = await self._search_archives(query)
                for result in archive_results.get('results', []):
                    if 'url' in result:
                        urls.append(result['url'])
                sources['archives'] = len(archive_results.get('results', []))
            except Exception as e:
                logger.debug(f'Archive search skipped: {e}')
        unique_urls = list(dict.fromkeys(urls))[:20]
        return {'query': query, 'urls': unique_urls, 'count': len(unique_urls), 'sources': sources}

    async def _task_academic(self, query: str, context: dict) -> dict:
        """Task: Academic Search using academic search engine"""
        logger.info(f"Task: Academic search for '{query}'")
        papers = []
        try:
            from .intelligence.academic_search import AcademicSearchEngine
            engine = AcademicSearchEngine()
            search_results = await engine.search(query, max_results=10)
            for result in search_results:
                papers.append({'title': getattr(result, 'title', 'Unknown'), 'authors': getattr(result, 'authors', []), 'year': getattr(result, 'year', None), 'url': getattr(result, 'url', None), 'pdf_url': getattr(result, 'pdf_url', None), 'source': getattr(result, 'source', 'unknown'), 'score': getattr(result, 'score', 0.0)})
        except ImportError:
            logger.debug('Academic search engine not available')
        except Exception as e:
            logger.warning(f'Academic search failed: {e}')
        if not papers and self.rag is not None:
            try:
                rag_results = await self.rag.retrieve(query, top_k=5)
                for doc in rag_results:
                    papers.append({'title': getattr(doc, 'title', 'Document'), 'content': getattr(doc, 'content', '')[:500], 'source': 'rag'})
            except Exception as e:
                logger.debug(f'RAG fallback failed: {e}')
        return {'query': query, 'papers': papers, 'count': len(papers)}

    async def _task_deep_read(self, urls: list[str], context: dict) -> dict:
        """Task: Deep Read using RAG and content extraction"""
        logger.info(f'Task: Deep read {len(urls)} URLs')
        urls = urls[:5]

        async def _read_one(url: str) -> tuple[str, list[dict]]:
            """ISSUE-006 parallel fetch — was sequential rag.retrieve()."""
            try:
                if self.rag is not None:
                    docs = await self.rag.retrieve(f'site:{url}', top_k=3)
                    contents = []
                    for doc in docs:
                        contents.append({'url': url, 'title': getattr(doc, 'title', ''), 'content': getattr(doc, 'content', '')[:2000], 'source': getattr(doc, 'source', 'unknown')})
                    if self.behavior is not None and self.enhanced_config.enable_stealth:
                        import asyncio
                        await asyncio.sleep(0.5)
                    return (url, contents)
            except Exception as e:
                logger.warning(f'Failed to read {url}: {e}')
            return (url, [])
        results = await chunked_taskgroup(urls, _read_one, batch_size=5, concurrency=lambda: concurrency_budget(ConcurrencyCategory.SCRAPE_GENERAL), ctx='deep_read')
        contents = []
        urls_read = []
        for url, result_contents in results:
            if result_contents:
                urls_read.append(url)
                contents.extend(result_contents)
        return {'urls_read': urls_read, 'content': contents, 'count': len(contents)}

    async def _task_fact_check(self, context: dict) -> dict:
        """Task: Fact Check using cross-referencing"""
        logger.info('Task: Fact check')
        claims_checked = 0
        verified = []
        claims = context.get('claims', [])
        sources = context.get('sources', [])
        if not claims:
            return {'claims_checked': 0, 'verified': [], 'status': 'no_claims'}
        for claim in claims[:5]:
            claims_checked += 1
            verification = {'claim': claim, 'status': 'unverified', 'confidence': 0.0, 'sources': []}
            if sources and self.rag is not None:
                try:
                    results = await self.rag.retrieve(claim, top_k=3)
                    if results:
                        scores = [getattr(r, 'score', 0) for r in results]
                        avg_score = sum(scores) / len(scores) if scores else 0
                        if avg_score > 0.8:
                            verification['status'] = 'verified'
                            verification['confidence'] = avg_score
                        elif avg_score > 0.5:
                            verification['status'] = 'partial'
                            verification['confidence'] = avg_score
                        verification['sources'] = [getattr(r, 'source', 'unknown') for r in results[:3]]
                except Exception as e:
                    logger.debug(f'Fact check verification failed: {e}')
            verified.append(verification)
        return {'claims_checked': claims_checked, 'verified': verified, 'status': 'completed'}

    async def _task_synthesis(self, query: str, context: dict) -> str:
        """Task: Synthesis using RAG and result fusion"""
        logger.info(f"Task: Synthesis for '{query}'")
        all_results = {}
        if 'search_results' in context:
            all_results['search'] = context['search_results']
        if 'papers' in context:
            all_results['academic'] = [{'title': p.get('title', ''), 'content': p.get('abstract', '')} for p in context['papers']]
        if 'deep_read_content' in context:
            all_results['deep_read'] = context['deep_read_content']
        if self.rrf is not None and self.enhanced_config.enable_fusion:
            try:
                fused = await self.fuse_research_results(all_results)
                top_results = fused[:5]
                synthesis_parts = [f'## Synthesis for: {query}\n']
                for i, result in enumerate(top_results, 1):
                    title = getattr(result, 'title', 'Untitled')
                    content = getattr(result, 'content', '')[:500]
                    source = getattr(result, 'source', 'unknown')
                    score = getattr(result, 'score', 0)
                    synthesis_parts.append(f'\n### Source {i} ({source}, score: {score:.2f})\n**{title}**\n{content}...\n')
                return '\n'.join(synthesis_parts)
            except Exception as e:
                logger.warning(f'Fusion failed, using fallback: {e}')
        parts = [f'## Synthesis for: {query}\n\n']
        for source, results in all_results.items():
            parts.append(f'\n### From {source}:\n')
            for i, result in enumerate(results[:3], 1):
                if isinstance(result, dict):
                    title = result.get('title', result.get('url', 'Untitled'))
                    parts.append(f'{i}. {title}\n')
        return ''.join(parts)

    def get_performance_stats(self) -> dict[str, Any]:
        """Získat statistiky výkonu"""
        return {'performance': self.performance_monitor.get_stats(), 'predictions': self.predictive_planner.get_stats()}

    def get_enhanced_statistics(self) -> dict[str, Any]:
        """
        Get comprehensive statistics for all enhanced research features.

        Returns:
            Dictionary with statistics for query expansion, result fusion,
            RAG retrieval, and stealth operations.
        """
        return {**self._stats, 'config': {'fusion_enabled': self.enhanced_config.enable_fusion, 'rag_enabled': self.enhanced_config.enable_rag, 'expansion_enabled': self.enhanced_config.enable_expansion, 'stealth_enabled': self.enhanced_config.enable_stealth}, 'performance': self.performance_monitor.get_stats(), 'predictions': self.predictive_planner.get_stats()}

async def enhanced_research(query: str, search_func: Callable | None=None, domain: str='academic', config: EnhancedResearchConfig | None=None) -> dict[str, Any]:
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

async def deep_research(query: str, depth: ResearchDepth=ResearchDepth.ADVANCED, max_results: int=50) -> UnifiedResearchResult:
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

def create_unified_research_engine(depth: ResearchDepth=ResearchDepth.ADVANCED, **kwargs) -> UnifiedResearchEngine:
    """
    NON-CANONICAL factory function — backward-compat only.
    Will be removed in v2.0. Use UnifiedResearchEngine(config=UnifiedResearchConfig(...)) directly.

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
    warnings.warn('create_unified_research_engine() is deprecated. Use UnifiedResearchEngine(config=UnifiedResearchConfig(...)) directly.', DeprecationWarning, stacklevel=2)
    config = UnifiedResearchConfig(depth=depth, **kwargs)
    return UnifiedResearchEngine(config=config)

class SourcePlan(msgspec.Struct, frozen=True, gc=False):
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
    families: tuple[SourceFamily, ...]
    engines: tuple[str, ...]
    reasoning: str
    conditions: tuple[str, ...]
    excluded: tuple[SourceFamily, ...] = field(default_factory=())

    def to_display_dict(self) -> dict[str, Any]:
        """Human-readable dict for debugging/logging."""
        return {'families': [f.value for f in self.families], 'engines': list(self.engines), 'reasoning': self.reasoning, 'conditions': list(self.conditions), 'excluded': [f.value for f in self.excluded]}

def _build_source_plan(query_type: QueryType, depth: ResearchDepth, config: UnifiedResearchConfig | None=None) -> SourcePlan:
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
    if depth == ResearchDepth.BASIC:
        base_families = [SourceFamily.WEB, SourceFamily.ACADEMIC]
        base_engines = ('stealth_crawler', 'academic')
    elif depth == ResearchDepth.ADVANCED:
        base_families = [SourceFamily.WEB, SourceFamily.ACADEMIC, SourceFamily.ARCHIVE]
        base_engines = ('stealth_crawler', 'academic', 'archives')
    else:
        base_families = [SourceFamily.WEB, SourceFamily.ACADEMIC, SourceFamily.ARCHIVE, SourceFamily.SECURITY, SourceFamily.TEMPORAL, SourceFamily.OSINT]
        base_engines = ('stealth_crawler', 'academic', 'archives', 'data_leak', 'temporal', 'osint')
    families = list(base_families)
    engines = list(base_engines)
    excluded: list[SourceFamily] = []
    conditions: list[str] = [f'depth={depth.name}']
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
        if depth == ResearchDepth.EXHAUSTIVE and SourceFamily.OSINT not in families:
            families.append(SourceFamily.OSINT)
            engines = list(engines) + ['osint']
        conditions.append('query_type=PERSON')
    elif query_type == QueryType.ORGANIZATION:
        if depth.value >= ResearchDepth.ADVANCED.value:
            if SourceFamily.ARCHIVE not in families:
                families.append(SourceFamily.ARCHIVE)
                engines = list(engines) + ['archives']
        conditions.append('query_type=ORGANIZATION')
    else:
        conditions.append(f'query_type={query_type.value}')
    if config:
        if not cfg.should_use_tool('academic') and SourceFamily.ACADEMIC in families:
            families.remove(SourceFamily.ACADEMIC)
            engines = [e for e in engines if e != 'academic']
            excluded.append(SourceFamily.ACADEMIC)
        if not cfg.should_use_tool('archives') and SourceFamily.ARCHIVE in families:
            families.remove(SourceFamily.ARCHIVE)
            engines = [e for e in engines if e != 'archives']
            excluded.append(SourceFamily.ARCHIVE)
    reasoning = f'depth={depth.name}, query_type={query_type.value}, families={len(families)}, engines={len(engines)}'
    return SourcePlan(families=tuple(families), engines=tuple(engines), reasoning=reasoning, conditions=tuple(conditions), excluded=tuple(excluded))

class DeepResearchRequest(msgspec.Struct, gc=False):
    """
    Request wrapper for deep research provider seam.

    NON-CANONICAL: Toto NENÍ ProviderRequest z types.py.
    Používá se pouze jako interní seam před F11 připojením.

    Canonical ProviderRequest/ProviderResult z types.py bude použito
    AŽ PO napojení na triádu a session seams.

    Migration direction:
        DeepResearchRequest.grounding_hints (raw dict)
            → CanonicalGroundingHints (types.py:1702)
        via CanonicalGroundingHints.from_shim() classmethod.
        Currently discarded in to_engine_kwargs(); activation would
        wire this to engine's grounding parameter once F11 is ready.
    """
    query: str
    depth: ResearchDepth = ResearchDepth.ADVANCED
    query_type: QueryType | None = None
    max_results: int = 50
    grounding_hints: dict[str, list[str]] | None = None

    def to_engine_kwargs(self) -> dict[str, Any]:
        """Convert to UnifiedResearchEngine.deep_research() kwargs."""
        kwargs = {'query': self.query, 'depth': self.depth, 'query_type': self.query_type, 'max_results': self.max_results}
        if self.grounding_hints:
            from .project_types import CanonicalGroundingHints
            _canonical_hints = CanonicalGroundingHints.from_shim(topic_hints=tuple(self.grounding_hints.get('topics', [])), domain_tags=tuple(self.grounding_hints.get('domains', [])))
            if not _canonical_hints.is_empty():
                kwargs['grounding_hints'] = _canonical_hints
        return kwargs

class DeepResearchResponse(msgspec.Struct, gc=False):
    """
    Response wrapper for deep research provider seam.

    NON-CANONICAL: Toto NENÍ ProviderResult z types.py.
    Používá se pouze jako interní seam před F11 připojením.

    Canonical ProviderRequest/ProviderResult z types.py bude použito
    AŽ PO napojení na triádu a session seams.
    """
    findings: list[ResearchFinding]
    fused_results: list[dict[str, Any]]
    confidence_score: float
    execution_time_seconds: float
    sources_used: list[str]
    tools_executed: list[str]

    @classmethod
    def from_unified_result(cls, result: UnifiedResearchResult) -> DeepResearchResponse:
        """Create from UnifiedResearchResult."""
        return cls(findings=result.findings, fused_results=result.fused_results, confidence_score=result.confidence_score, execution_time_seconds=result.execution_time_seconds, sources_used=result.sources_used, tools_executed=result.tools_executed)

class _BudgetHints(msgspec.Struct, frozen=True, gc=False):
    """Internal budget hints for DeepResearch session.

    Not a canonical contract — internal to enhanced_research.py.
    """
    stagnation_tolerance: int = 0
    confidence_boost: float = 0.0

class _EvidenceHints(msgspec.Struct, frozen=True, gc=False):
    """Internal evidence/logging hints for DeepResearch session.

    Not a canonical contract — internal to enhanced_research.py.
    """
    log_level: str = 'INFO'
    detail_depth: str = 'standard'

class _PolicyFlags(msgspec.Struct, frozen=True, gc=False):
    """Internal execution policy flags for DeepResearch session.

    Not a canonical contract — internal to enhanced_research.py.
    """
    skip_stagnation_check: bool = False
    force_exhaustive: bool = False

class DeepResearchGroundingShim(msgspec.Struct, gc=False):
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
    topic_hints: list[str] = field(default_factory=list)
    domain_tags: list[str] = field(default_factory=list)
    correlation: RunCorrelation | None = None
    budget_hints: _BudgetHints | None = None
    evidence_hints: _EvidenceHints | None = None
    policy_flags: _PolicyFlags | None = None

    def is_empty(self) -> bool:
        """Returns True if no grounding metadata is set."""
        return len(self.topic_hints) == 0 and len(self.domain_tags) == 0 and (self.correlation is None) and (self.budget_hints is None) and (self.evidence_hints is None) and (self.policy_flags is None)

    def merge(self, other: DeepResearchGroundingShim) -> DeepResearchGroundingShim:
        """Merge another shim into this one (deduplicates + concatenates)."""
        return DeepResearchGroundingShim(topic_hints=list(set(self.topic_hints + other.topic_hints)), domain_tags=list(set(self.domain_tags + other.domain_tags)), correlation=other.correlation or self.correlation, budget_hints=other.budget_hints or self.budget_hints, evidence_hints=other.evidence_hints or self.evidence_hints, policy_flags=other.policy_flags or self.policy_flags)

async def deep_research_provider_seam(request: DeepResearchRequest, grounding: DeepResearchGroundingShim | None=None) -> DeepResearchResponse:
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
    engine = UnifiedResearchEngine(config=UnifiedResearchConfig(depth=request.depth))
    try:
        correlation = grounding.correlation if grounding else None
        kwargs = request.to_engine_kwargs()
        kwargs['correlation'] = correlation
        result = await engine.deep_research(**kwargs)
        return DeepResearchResponse.from_unified_result(result)
    finally:
        await engine.cleanup()

class TriadAdmissionDescriptor(msgspec.Struct, frozen=True, gc=False):
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
    provider_candidate: str = 'UnifiedResearchEngine'
    owning_module: str = 'enhanced_research'
    triad_authority_exists: bool = True
    deepresearch_napojen: bool = False
    expects_analyzer: bool = True
    expects_router: bool = True
    expects_registry: bool = True
    blockers: tuple[str, ...] = ('Session seams (BudgetManager, EvidenceLog): exists, not wired to DeepResearch', 'Security gate (PII gate): exists, not wired to DeepResearch', 'Minimal grounding seam (ProviderRequest/ProviderResult): exists, not wired to DeepResearch', 'Transport plane (FetchCoordinator): exists, not wired to DeepResearch runtime')
    is_dormant: bool = True
    is_not_runtime_authority: bool = True
    is_not_activation: bool = True

    @property
    def admission_summary(self) -> str:
        """Human-readable admission status."""
        lines = [f'Provider Candidate: {self.provider_candidate}', f'Triad Authority Exists: {self.triad_authority_exists}', f'DeepResearch Napojen: {self.deepresearch_napojen}', f'Dormant: {self.is_dormant}', '', 'Blockers:']
        for b in self.blockers:
            lines.append(f'  - {b}')
        return '\n'.join(lines)
DEEP_RESEARCH_ADMISSION = TriadAdmissionDescriptor()

class LocalCorpusConsumerDescriptor(msgspec.Struct, frozen=True, gc=False):
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
    consumer_name: str = 'UnifiedResearchEngine'
    owning_module: str = 'enhanced_research'
    is_consumer: bool = True
    is_not_search_plane_owner: bool = True
    would_query_for_depths: tuple[str, ...] = ('BASIC', 'ADVANCED', 'EXHAUSTIVE')
    would_query_for_query_types: tuple[str, ...] = ('GENERAL', 'ACADEMIC', 'PERSON', 'ORGANIZATION')
    conditions_for_consumption: tuple[str, ...] = ('local_corpus_plane_exists=True', 'corpus_populated=True', 'query_has_local_context=True', 'no_better_external_source_available', 'budget_allows_local_only=True')
    blockers: tuple[str, ...] = ('LocalSearchSeam ingestion plane: exists, not yet connected to DeepResearch', 'Corpus population: documents need to be ingested first', 'BudgetManager seam: exists, no consumer-side budget tracking for corpus queries', 'DeepResearch runtime path: no activation call site exists', 'ProviderRequest/ProviderResult handoff: exists, not wired to DeepResearch')
    is_dormant: bool = True
    is_not_runtime_path: bool = True
    is_not_activation: bool = True
    rag_engine_authority: str = 'RAGEngine = RAG grounding authority (hybrid_retrieve, context)'
    local_corpus_authority: str = 'LocalSearchSeam = search plane owner (BM25, metadata)'
    consumer_declaration: str = 'DeepResearch = consumer of LocalSearchSeam output (NOT authority)'

    @property
    def consumer_summary(self) -> str:
        """Human-readable consumer declaration."""
        lines = [f'Consumer: {self.consumer_name}', f'Module: {self.owning_module}', f'Is Consumer (not owner): {self.is_consumer}', f'Dormant: {self.is_dormant}', '', 'Would query for depths:']
        for d in self.would_query_for_depths:
            lines.append(f'  - {d}')
        lines.append('Would query for query types:')
        for qt in self.would_query_for_query_types:
            lines.append(f'  - {qt}')
        lines.append('')
        lines.append('Authority separation:')
        lines.append(f'  RAGEngine: {self.rag_engine_authority}')
        lines.append(f'  LocalSearchSeam: {self.local_corpus_authority}')
        lines.append(f'  DeepResearch consumer: {self.consumer_declaration}')
        lines.append('')
        lines.append('Blockers:')
        for b in self.blockers:
            lines.append(f'  - {b}')
        return '\n'.join(lines)
LOCAL_CORPUS_CONSUMER = LocalCorpusConsumerDescriptor()
__all__ = ['UnifiedResearchEngine', 'UnifiedResearchResult', 'deep_research_provider_seam', 'EnhancedResearchOrchestrator', 'EnhancedResearchConfig', 'DEPRECATED_ENHANCED_ORCHESTRATOR_RESIDUE', 'DeepResearchRequest', 'DeepResearchResponse', 'DeepResearchGroundingShim', 'enhanced_research', 'deep_research', 'create_unified_research_engine', 'ResearchDepth', 'QueryType', 'TriadAdmissionDescriptor', 'DEEP_RESEARCH_ADMISSION', 'LocalCorpusConsumerDescriptor', 'LOCAL_CORPUS_CONSUMER']
DEPRECATED_ENHANCED_ORCHESTRATOR_RESIDUE = True

def _detect_transport_capabilities() -> set[str]:
    """Best-effort detection of currently-available transports.

    Lazy: never raises, never imports at module level. Mirrors the canonical
    TransportResolver._check_transports() logic in transport/transport_resolver.py
    but is decoupled (no ImportError surface) for the DeepResearch seam.

    Returns a subset of {"direct", "tor", "i2p", "curl_cffi", "nym"}.
    Direct + curl_cffi are always assumed available on the M1 baseline.
    """
    caps: set[str] = {'direct', 'curl_cffi'}
    try:
        from hledac.universal.transport.transport_resolver import TransportResolver
        resolver = TransportResolver()
        resolver._check_transports()
        if getattr(resolver, '_tor_class', None) is not None:
            caps.add('tor')
        if getattr(resolver, '_nym_class', None) is not None:
            caps.add('nym')
    except Exception:
        pass
    return caps

def discover_deep_sources(query: str, transport_capabilities: set[str] | None=None, max_results: int=20, tier: str | None=None) -> list[Any]:
    """Curated, transport-aware discovery of beyond-surface OSINT sources.

    Phase 2.7 of the UnifiedResearchEngine pipeline. Returns up to
    `max_results` relevant `CanonicalFinding` objects (source_type="source_discovery"),
    filtered by current transport capabilities and (optionally) source tier.

    Args:
        query: Research query — used for relevance scoring (substring match on
            source name + URL, case-insensitive).
        transport_capabilities: Set of available transport identifiers
            (e.g., {"direct", "tor"}). If None, autodetect via
            `_detect_transport_capabilities()`.
        max_results: Hard cap on returned findings (default 20, M1-bounded).
        tier: Optional source tier filter — "surface" | "dark" | "archive" |
            "p2p" | "academic".

    Returns:
        list of CanonicalFinding with source_type="source_discovery".
        Empty list on any error (fail-soft).

    Sprint F270 invariants:
        - M1 fail-safe: no exception ever escapes.
        - Pure in-memory catalog — no eager I/O.
        - LMDB persistence is opt-in (caller-controlled).
        - Relevance score is a simple substring match in [0.0, 1.0].
    """
    if not query or not isinstance(query, str):
        return []
    try:
        from hledac.universal.discovery.deep_source_registry import DeepSourceRegistry
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    except Exception as exc:
        logger.debug('discover_deep_sources: import failed: %s', exc)
        return []
    try:
        registry = DeepSourceRegistry()
    except Exception as exc:
        logger.warning('discover_deep_sources: registry build failed: %s', exc)
        return []
    if transport_capabilities is None:
        transport_capabilities = _detect_transport_capabilities()
    try:
        sources = registry.get_available_sources(transport_capabilities)
    except Exception as exc:
        logger.warning('discover_deep_sources: filter failed: %s', exc)
        return []
    if tier is not None:
        sources = [s for s in sources if s.source_tier == tier]
    q_lower = query.lower().strip()
    scored: list[tuple[float, Any]] = []
    for src in sources:
        name_hit = q_lower in src.name.lower() if q_lower else 0.0
        url_hit = q_lower in src.base_url.lower() if q_lower else 0.0
        score = 0.0
        if q_lower:
            if name_hit:
                score += 0.6
            if url_hit:
                score += 0.3
        score += 0.1 * src.reliability
        scored.append((score, src))
    scored.sort(key=lambda x: (-x[0], -x[1].reliability, x[1].name))
    findings: list[Any] = []
    now_ts = time.time()
    for score, src in scored[:max_results]:
        try:
            finding = CanonicalFinding(finding_id=f'dsr:{src.source_id}', query=query, source_type='source_discovery', confidence=min(1.0, max(0.0, 0.5 * score + 0.5 * src.reliability)), ts=now_ts, provenance=('DeepSourceRegistry', src.name), payload_text=f'name={src.name}\nbase_url={src.base_url}\ntier={src.source_tier}\ntransport={src.transport_required}\ndata_type={src.data_type}\nreliability={src.reliability}\nlast_verified={src.last_verified}\nrelevance_score={score:.3f}\n')
            findings.append(finding)
        except Exception as exc:
            logger.debug('discover_deep_sources: skip %s (%s)', src.source_id, exc)
            continue
    return findings