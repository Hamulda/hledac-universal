"""
Academic Search System - Multi-Source Query Expansion

From MSQES: Multi-Source Query Expansion System
Integrated into Universal Orchestrator for comprehensive academic research.

Features:
- Multi-source academic search (ArXiv, Crossref, Semantic Scholar)
- Query expansion with semantic, syntactic, and domain strategies
- Result deduplication and ranking
- M1-optimized with memory-efficient implementations

Usage:
    engine = AcademicSearchEngine()
    results = await engine.search("quantum computing", max_results=20)
"""
import asyncio
import hashlib
import logging
import time
import urllib.parse
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import msgspec
from datetime import UTC, datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any
import httpx
from hledac.universal.network.session_runtime import async_get_httpx_session
from hledac.universal.transport.session_pool import session_pool
from hledac.universal.utils.deduplication import DeduplicationConfig, DeduplicationEngine
from hledac.universal.utils.deduplication import QueryItem as DedupItem
from hledac.universal.utils.msgspec_json import decode, encode
from hledac.universal.utils.query_expansion import DomainSpecificExpansionStrategy, ExpansionStrategy, MultiStrategyExpander, QueryVariation, SemanticExpansionStrategy, SyntacticExpansionStrategy
from hledac.universal.utils.async_helpers import parallel_ok
from hledac.universal.utils.two_pass_pipeline import TwoPassPipeline, TwoPassPipelineConfig, consumer_fn_to_thread
logger = logging.getLogger(__name__)

class ResultType(Enum):
    """Types of search results."""
    PAPER = auto()
    DATASET = auto()
    WEBPAGE = auto()
    MULTIMEDIA = auto()
    UNKNOWN = auto()

class AcademicSource(Enum):
    """Available academic sources."""
    ARXIV = 'arxiv'
    CROSSREF = 'crossref'
    SEMANTIC_SCHOLAR = 'semantic_scholar'

@dataclass(slots=True)
class SourceConfig:
    """Configuration for a search source."""
    name: str
    enabled: bool = True
    weight: float = 1.0
    timeout_seconds: float = 10.0
    max_results: int = 10
    api_key: str | None = None
    base_url: str | None = None
    rate_limit_per_minute: int = 60

    def __post_init__(self) -> None:
        if self.api_key is None:
            env_key = f'{self.name.upper()}_API_KEY'
            self.api_key = __import__('os').getenv(env_key)

class SearchResult(msgspec.Struct):
    """A single search result."""
    title: str
    url: str
    snippet: str
    source: str
    result_type: ResultType = ResultType.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {'title': self.title, 'url': self.url, 'snippet': self.snippet, 'source': self.source, 'result_type': self.result_type.name, 'metadata': self.metadata, 'relevance_score': self.relevance_score, 'timestamp': self.timestamp.isoformat()}

class SourceResult(msgspec.Struct, frozen=True):
    """Results from a single source."""
    source_name: str
    results: list[SearchResult]
    query_used: str
    execution_time_ms: float
    success: bool
    error_message: str | None = None

class AcademicSearchResult(msgspec.Struct, frozen=True):
    """Complete academic search result."""
    original_query: str
    all_results: list[SearchResult]
    deduplicated_results: list[SearchResult]
    sources_used: list[str]
    total_sources: int
    successful_sources: int
    execution_time_ms: float
    expansions_used: int
    query_variations: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {'original_query': self.original_query, 'all_results_count': len(self.all_results), 'deduplicated_results_count': len(self.deduplicated_results), 'sources_used': self.sources_used, 'total_sources': self.total_sources, 'successful_sources': self.successful_sources, 'execution_time_ms': self.execution_time_ms, 'expansions_used': self.expansions_used, 'query_variations': self.query_variations, 'timestamp': self.timestamp.isoformat(), 'results': [r.to_dict() for r in self.deduplicated_results[:10]]}

@dataclass(frozen=True, slots=True)
class QueryAnalysis:
    """Analysis of a query."""
    original_query: str
    key_terms: list[str] = field(default_factory=list)
    domain_hint: str | None = None
    complexity_score: float = 0.5
    detected_language: str = 'en'

    def __post_init__(self) -> None:
        if not self.key_terms:
            object.__setattr__(self, 'key_terms', self._extract_key_terms())

    def _extract_key_terms(self) -> list[str]:
        """Extract key terms from query."""
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should'}
        words = self.original_query.lower().split()
        return [w for w in words if w not in stop_words and len(w) > 2]

class SourcePerformance(msgspec.Struct):
    """Performance metrics for a source."""
    source_name: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time_ms: float = 0.0
    last_used: datetime | None = None

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 1.0
        return self.successful_requests / self.total_requests

    @property
    def score(self) -> float:
        """Calculate overall source score."""
        success_weight = 0.7
        speed_weight = 0.3
        speed_score = max(0, 1 - self.avg_response_time_ms / 10000)
        return self.success_rate * success_weight + speed_score * speed_weight

    def update(self, success: bool, response_time_ms: float):
        """Update performance metrics."""
        self.total_requests += 1
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
        if self.avg_response_time_ms == 0:
            self.avg_response_time_ms = response_time_ms
        else:
            self.avg_response_time_ms = self.avg_response_time_ms * 0.8 + response_time_ms * 0.2
        self.last_used = datetime.now(UTC)

class BaseSourceAdapter(ABC):
    """Abstract base class for search source adapters."""

    def __init__(self, config: SourceConfig):
        self.config = config
        self.performance = SourcePerformance(source_name=config.name)
        self.logger = logging.getLogger(f'academic.source.{config.name}')

    @abstractmethod
    async def search(self, query: str, max_results: int=10, analysis: QueryAnalysis | None=None) -> list[SearchResult]:
        """Search the source with the given query."""
        pass

    async def execute_search(self, query: str, max_results: int=10, analysis: QueryAnalysis | None=None, **kwargs) -> tuple[list[SearchResult], float, bool]:
        """Execute search with performance tracking."""
        start_time = time.time()
        adapter_name = getattr(self.config, 'name', self.__class__.__name__)
        skipped_kwargs = {k: v for k, v in kwargs.items() if k != 'async_session'}
        async_session_supported = 'async_session' in kwargs
        try:
            async_session = kwargs.get('async_session')
            results = await self.search(query, max_results, analysis, async_session=async_session)
            execution_time = (time.time() - start_time) * 1000
            self.performance.update(success=True, response_time_ms=execution_time)
            return (results, execution_time, True)
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            self.logger.warning(f'[ADAPTER_COMPAT] Search failed for {adapter_name}: error_type={type(e).__name__}, async_session_supported={async_session_supported}, skipped_kwargs={list(skipped_kwargs.keys())}')
            self.performance.update(success=False, response_time_ms=execution_time)
            return ([], execution_time, False)

    def get_performance(self) -> SourcePerformance:
        """Get performance metrics for this source."""
        return self.performance

class ArxivAdapter(BaseSourceAdapter):
    """Adapter for searching ArXiv."""
    __slots__ = tuple(('base_url',))

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        self.base_url = config.base_url or 'http://export.arxiv.org/api/query'

    async def search(self, query: str, max_results: int=10, analysis: QueryAnalysis | None=None, async_session: httpx.AsyncClient | None=None) -> list[SearchResult]:
        """Search ArXiv for papers.

        Args:
            query: Search query
            max_results: Maximum results to return
            analysis: Optional query analysis
            async_session: Optional shared aiohttp session for connection pooling.
                         If not provided, creates a per-call session (legacy behavior).
        """
        try:
            search_query = urllib.parse.quote(query)
            url = f'{self.base_url}?search_query=all:{search_query}&start=0&max_results={max_results}&sortBy=relevance&sortOrder=descending'
            headers = {'User-Agent': 'Hledac-Research/1.0'}

            async def _do_search(session: httpx.AsyncClient) -> list[SearchResult]:
                nonlocal url, headers
                resp = await session.get(url, headers=headers, timeout=httpx.Timeout(self.config.timeout_seconds))
                try:
                    if resp.status_code != 200:
                        self.logger.warning(f'ArXiv API returned status {resp.status_code}')
                        return []
                    xml_content = await resp.text()
                    return self._parse_results(xml_content)
                finally:
                    await resp.aclose()
            if async_session is not None:
                return await _do_search(async_session)
            else:
                shared = httpx.AsyncClient()
                return await _do_search(shared)
        except TimeoutError:
            self.logger.warning('ArXiv search timed out')
            return []
        except Exception as e:
            self.logger.error(f'ArXiv search error: {e}')
            return []

    def _parse_results(self, xml_content: str) -> list[SearchResult]:
        """Parse ArXiv API XML response."""
        results = []
        try:
            ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
            root = ET.fromstring(xml_content.encode('utf-8'))
            for entry in root.findall('atom:entry', ns):
                if entry.find('atom:title', ns) is None:
                    continue
                title_elem = entry.find('atom:title', ns)
                title = title_elem.text if title_elem is not None else 'No Title'
                summary_elem = entry.find('atom:summary', ns)
                summary = summary_elem.text if summary_elem is not None else ''
                id_elem = entry.find('atom:id', ns)
                arxiv_id = id_elem.text if id_elem is not None else ''
                url = arxiv_id if arxiv_id else ''
                authors = []
                for author in entry.findall('atom:author', ns):
                    name_elem = author.find('atom:name', ns)
                    if name_elem is not None:
                        authors.append(name_elem.text)
                published_elem = entry.find('atom:published', ns)
                published = published_elem.text if published_elem is not None else ''
                categories = []
                for category in entry.findall('atom:category', ns):
                    term = category.get('term')
                    if term:
                        categories.append(term)
                pdf_url = ''
                for link in entry.findall('atom:link', ns):
                    if link.get('title') == 'pdf':
                        pdf_url = link.get('href', '')
                        break
                snippet = (summary or '')[:300] + '...' if summary and len(summary) > 300 else summary or ''
                result = SearchResult(title=title.strip(), url=url, snippet=snippet.strip(), source='arxiv', result_type=ResultType.PAPER, metadata={'authors': authors, 'published': published, 'categories': categories, 'pdf_url': pdf_url, 'arxiv_id': arxiv_id.split('/')[-1] if arxiv_id else ''})
                results.append(result)
        except ET.ParseError as e:
            self.logger.error(f'XML parse error: {e}')
        except Exception as e:
            self.logger.error(f'Error parsing ArXiv results: {e}')
        return results

    async def get_paper_details(self, arxiv_id: str) -> dict[str, Any]:
        """Get detailed information about a specific paper."""
        try:
            url = f'{self.base_url}?id_list={arxiv_id}'
            _sess = httpx.AsyncClient()
            resp = await _sess.get(url, timeout=httpx.Timeout(10.0))
            try:
                if resp.status_code == 200:
                    xml_content = await resp.text()
                    results = self._parse_results(xml_content)
                    if results:
                        return results[0].metadata
                return {}
            finally:
                await resp.aclose()
                await _sess.aclose()
        except Exception as e:
            self.logger.error(f'Error fetching paper details: {e}')
            return {}

class CrossrefAdapter(BaseSourceAdapter):
    """Adapter for searching Crossref."""
    __slots__ = tuple(('base_url',))

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        self.base_url = config.base_url or 'https://api.crossref.org/works'

    async def search(self, query: str, max_results: int=10, analysis: QueryAnalysis | None=None, async_session: httpx.AsyncClient | None=None) -> list[SearchResult]:
        """Search Crossref for academic papers.

        Args:
            query: Search query
            max_results: Maximum results to return
            analysis: Optional query analysis
            async_session: Optional shared aiohttp session for connection pooling.
                         If not provided, creates a per-call session (legacy behavior).
        """
        try:
            params = {'query': query, 'rows': min(max_results, 20), 'sort': 'relevance', 'order': 'desc'}
            headers = {'User-Agent': 'Hledac-Research/1.0 (mailto:research@hledac.local)'}
            if self.config.api_key:
                headers['Crossref-Plus-API-Token'] = f'Bearer {self.config.api_key}'

            async def _do_search(session: httpx.AsyncClient) -> list[SearchResult]:
                nonlocal params, headers
                resp = await session.get(self.base_url, params=params, headers=headers, timeout=httpx.Timeout(self.config.timeout_seconds))
                try:
                    if resp.status_code != 200:
                        self.logger.warning(f'Crossref API returned status {resp.status_code}')
                        return []
                    data = await resp.json()
                    return self._parse_results(data)
                finally:
                    await resp.aclose()
            if async_session is not None:
                return await _do_search(async_session)
            else:
                shared = httpx.AsyncClient()
                return await _do_search(shared)
        except TimeoutError:
            self.logger.warning('Crossref search timed out')
            return []
        except Exception as e:
            self.logger.error(f'Crossref search error: {e}')
            return []

    def _parse_results(self, data: dict) -> list[SearchResult]:
        """Parse Crossref API JSON response."""
        results = []
        try:
            items = data.get('message', {}).get('items', [])
            for item in items:
                titles = item.get('title', [])
                title = titles[0] if titles else 'No Title'
                doi = item.get('DOI', '')
                url = item.get('URL', f'https://doi.org/{doi}' if doi else '')
                authors = []
                for author in item.get('author', []):
                    given = author.get('given', '')
                    family = author.get('family', '')
                    if given and family:
                        authors.append(f'{given} {family}')
                    elif family:
                        authors.append(family)
                abstract = item.get('abstract', '')
                if not abstract:
                    container = item.get('container-title', [])
                    container_title = container[0] if container else ''
                    pub_type = item.get('type', 'unknown')
                    abstract = f'{pub_type}: {container_title}' if container_title else pub_type
                snippet = abstract[:300] + '...' if len(abstract) > 300 else abstract
                published = item.get('published-print', {}) or item.get('published-online', {})
                date_parts = published.get('date-parts', [[]])
                pub_date = '-'.join((str(p) for p in date_parts[0])) if date_parts and date_parts[0] else ''
                citations = item.get('is-referenced-by-count', 0)
                result = SearchResult(title=title.strip(), url=url, snippet=snippet.strip(), source='crossref', result_type=ResultType.PAPER, metadata={'authors': authors, 'doi': doi, 'published': pub_date, 'publisher': item.get('publisher', ''), 'citations': citations, 'type': item.get('type', ''), 'container_title': item.get('container-title', [])}, relevance_score=min(citations / 100, 1.0) * 0.3)
                results.append(result)
        except Exception as e:
            self.logger.error(f'Error parsing Crossref results: {e}')
        return results

    async def get_work_by_doi(self, doi: str) -> dict[str, Any]:
        """Get detailed information about a work by DOI."""
        try:
            url = f'{self.base_url}/{doi}'
            headers = {'User-Agent': 'Hledac-Research/1.0 (mailto:research@hledac.local)'}
            _sess = httpx.AsyncClient()
            resp = await _sess.get(url, headers=headers, timeout=httpx.Timeout(10.0))
            try:
                if resp.status_code == 200:
                    data = await resp.json()
                    message = data.get('message', {})
                    return {'title': message.get('title', [''])[0], 'doi': message.get('DOI', ''), 'authors': message.get('author', []), 'published': message.get('published-print', {}), 'publisher': message.get('publisher', ''), 'citations': message.get('is-referenced-by-count', 0)}
                return {}
            finally:
                await resp.aclose()
                await _sess.aclose()
        except Exception as e:
            self.logger.error(f'Error fetching work by DOI: {e}')
            return {}

class SemanticScholarAdapter(BaseSourceAdapter):
    """Adapter for searching Semantic Scholar."""
    __slots__ = tuple(('base_url',))

    def __init__(self, config: SourceConfig):
        super().__init__(config)
        self.base_url = config.base_url or 'https://api.semanticscholar.org/graph/v1'

    async def search(self, query: str, max_results: int=10, analysis: QueryAnalysis | None=None, async_session: httpx.AsyncClient | None=None) -> list[SearchResult]:
        """Search Semantic Scholar for papers.

        Args:
            query: Search query
            max_results: Maximum results to return
            analysis: Optional query analysis
            async_session: Optional shared aiohttp session for connection pooling.
                         If not provided, creates a per-call session (legacy behavior).
        """
        try:
            url = f'{self.base_url}/paper/search'
            params = {'query': query, 'fields': 'title,authors,year,abstract,citationCount,referenceCount,externalIds,url,openAccessPdf', 'limit': min(max_results, 100)}
            headers = {'User-Agent': 'Hledac-Research/1.0'}
            if self.config.api_key:
                headers['x-api-key'] = self.config.api_key

            async def _do_search(session: httpx.AsyncClient) -> list[SearchResult]:
                nonlocal url, params, headers
                resp = await session.get(url, params=params, headers=headers, timeout=httpx.Timeout(self.config.timeout_seconds))
                try:
                    if resp.status_code == 429:
                        self.logger.warning('Semantic Scholar rate limit hit')
                        return []
                    if resp.status_code != 200:
                        self.logger.warning(f'Semantic Scholar API returned status {resp.status_code}')
                        return []
                    data = await resp.json()
                    return self._parse_results(data)
                finally:
                    await resp.aclose()
            if async_session is not None:
                return await _do_search(async_session)
            else:
                shared = httpx.AsyncClient()
                return await _do_search(shared)
        except TimeoutError:
            self.logger.warning('Semantic Scholar search timed out')
            return []
        except Exception as e:
            self.logger.error(f'Semantic Scholar search error: {e}')
            return []

    def _parse_results(self, data: dict) -> list[SearchResult]:
        """Parse Semantic Scholar API JSON response."""
        results = []
        try:
            papers = data.get('data', [])
            for paper in papers:
                title = paper.get('title', 'No Title')
                paper_id = paper.get('paperId', '')
                external_ids = paper.get('externalIds', {})
                doi = external_ids.get('DOI', '')
                url = paper.get('url', '')
                if not url and doi:
                    url = f'https://doi.org/{doi}'
                abstract = paper.get('abstract', '')
                if not abstract:
                    abstract = 'No abstract available'
                snippet = abstract[:300] + '...' if len(abstract) > 300 else abstract
                authors = []
                for author in paper.get('authors', []):
                    name = author.get('name', '')
                    if name:
                        authors.append(name)
                year = paper.get('year', '')
                citation_count = paper.get('citationCount', 0)
                reference_count = paper.get('referenceCount', 0)
                open_access = paper.get('openAccessPdf', {})
                pdf_url = open_access.get('url', '') if open_access else ''
                result = SearchResult(title=title.strip(), url=url, snippet=snippet.strip(), source='semantic_scholar', result_type=ResultType.PAPER, metadata={'authors': authors, 'year': year, 'doi': doi, 'paper_id': paper_id, 'citation_count': citation_count, 'reference_count': reference_count, 'pdf_url': pdf_url}, relevance_score=min(citation_count / 100, 1.0) * 0.5)
                results.append(result)
        except Exception as e:
            self.logger.error(f'Error parsing Semantic Scholar results: {e}')
        return results

    async def get_paper_details(self, paper_id: str) -> dict[str, Any]:
        """Get detailed information about a specific paper."""
        try:
            url = f'{self.base_url}/paper/{paper_id}'
            params = {'fields': 'title,authors,year,abstract,citationCount,referenceCount,externalIds,url,openAccessPdf,fieldsOfStudy,publicationDate,tldr'}
            headers = {'User-Agent': 'Hledac-Research/1.0'}
            if self.config.api_key:
                headers['x-api-key'] = self.config.api_key
            _sess = httpx.AsyncClient()
            resp = await _sess.get(url, params=params, headers=headers, timeout=httpx.Timeout(10.0))
            try:
                if resp.status_code == 200:
                    return await resp.json()
                return {}
            finally:
                await resp.aclose()
                await _sess.aclose()
        except Exception as e:
            self.logger.error(f'Error fetching paper details: {e}')
            return {}

    async def get_citations(self, paper_id: str, limit: int=10) -> list[dict[str, Any]]:
        """Get papers that cite this paper."""
        try:
            url = f'{self.base_url}/paper/{paper_id}/citations'
            params = {'fields': 'title,authors,year,abstract,citationCount', 'limit': limit}
            headers = {'User-Agent': 'Hledac-Research/1.0'}
            if self.config.api_key:
                headers['x-api-key'] = self.config.api_key
            _sess = httpx.AsyncClient()
            resp = await _sess.get(url, params=params, headers=headers, timeout=httpx.Timeout(10.0))
            try:
                if resp.status_code == 200:
                    data = await resp.json()
                    return data.get('data', [])
                return []
            finally:
                await resp.aclose()
                await _sess.aclose()
        except Exception as e:
            self.logger.error(f'Error fetching citations: {e}')
            return []

class AcademicSearchEngine:
    """
    Main engine for Multi-Source Academic Search.

    Coordinates query expansion, source selection, parallel execution,
    and result deduplication.
    """
    __slots__ = tuple(('config', 'dedup_engine', 'enable_deduplication', 'enable_expansion', 'expansion_strategies', 'logger', 'multi_expander', 'source_adapters', 'source_performance'))

    def __init__(self, config: dict[str, Any] | None=None, enable_expansion: bool=True, enable_deduplication: bool=True):
        self.config = config or {}
        self.enable_expansion = enable_expansion
        self.enable_deduplication = enable_deduplication
        self.expansion_strategies: list[ExpansionStrategy] = []
        if enable_expansion:
            self.expansion_strategies = [SemanticExpansionStrategy(max_expansions=3), SyntacticExpansionStrategy(max_expansions=3), DomainSpecificExpansionStrategy(max_expansions=3)]
        self.multi_expander = MultiStrategyExpander(strategies=self.expansion_strategies, max_total_variations=15)
        self.logger = logging.getLogger('academic.engine')
        self.source_adapters: dict[str, BaseSourceAdapter] = {}
        self.source_performance: dict[str, SourcePerformance] = {}
        self._init_sources()
        self.dedup_engine: DeduplicationEngine | None = None
        if enable_deduplication:
            dedup_config = DeduplicationConfig(semantic_threshold=0.85, content_threshold=0.9, metadata_threshold=0.95)
            self.dedup_engine = DeduplicationEngine(dedup_config)

    def _init_sources(self):
        """Initialize source adapters."""
        source_configs = {'arxiv': SourceConfig(name='arxiv', base_url='http://export.arxiv.org/api/query', max_results=10, rate_limit_per_minute=30), 'crossref': SourceConfig(name='crossref', base_url='https://api.crossref.org/works', max_results=10, rate_limit_per_minute=50), 'semantic_scholar': SourceConfig(name='semantic_scholar', base_url='https://api.semanticscholar.org/graph/v1', max_results=10, rate_limit_per_minute=100)}
        source_mapping = {'arxiv': ArxivAdapter, 'crossref': CrossrefAdapter, 'semantic_scholar': SemanticScholarAdapter}
        for name, source_config in source_configs.items():
            if name in source_mapping:
                adapter_class = source_mapping[name]
                self.source_adapters[name] = adapter_class(source_config)
                self.source_performance[name] = SourcePerformance(source_name=name)
        self.logger.info(f'Initialized {len(self.source_adapters)} source adapters')

    async def search(self, query: str, max_results: int=20, enable_expansion: bool | None=None, sources: list[str] | None=None, async_session: httpx.AsyncClient | None=None) -> AcademicSearchResult:
        """
        Execute multi-source academic search.

        Args:
            query: Original search query
            max_results: Maximum total results to return
            enable_expansion: Whether to expand the query (overrides default)
            sources: List of source names to use (default: all)
            async_session: Optional shared aiohttp session for connection pooling.
                         If provided, adapters reuse this session instead of
                         creating per-call sessions (reduces connection overhead).

        Returns:
            Academic search result
        """
        max_results = max_results or 20
        do_expansion = enable_expansion if enable_expansion is not None else self.enable_expansion
        start_time = time.time()
        try:
            analysis = self._analyze_query(query)
            queries_to_search = [query]
            expanded_queries: list[QueryVariation] = []
            query_variations = [query]
            if do_expansion and self.expansion_strategies:
                expanded_queries = await self.multi_expander.expand(query, context={'domain': analysis.domain_hint})
                queries_to_search.extend([exp.query for exp in expanded_queries])
                query_variations = list(dict.fromkeys(queries_to_search))
            self.logger.info(f'Searching with {len(query_variations)} query variants')
            all_source_results = await self._execute_searches(query_variations, analysis, sources, async_session=async_session)
            all_results = []
            for source_result in all_source_results.values():
                all_results.extend(source_result.results)
            ranked_results = (await self._deduplicate_and_rank(all_results, query))[:max_results]
            execution_time = (time.time() - start_time) * 1000
            successful_sources = sum((1 for sr in all_source_results.values() if sr.success))
            result = AcademicSearchResult(original_query=query, all_results=all_results, deduplicated_results=ranked_results, sources_used=list(all_source_results.keys()), total_sources=len(self.source_adapters), successful_sources=successful_sources, execution_time_ms=execution_time, expansions_used=len(expanded_queries), query_variations=query_variations)
            self.logger.info(f'Search completed: {len(all_results)} total, {len(ranked_results)} unique from {successful_sources} sources in {execution_time:.0f}ms')
            return result
        except Exception as e:
            self.logger.error(f'Academic search error: {e}')
            execution_time = (time.time() - start_time) * 1000
            return AcademicSearchResult(original_query=query, all_results=[], deduplicated_results=[], sources_used=[], total_sources=len(self.source_adapters), successful_sources=0, execution_time_ms=execution_time, expansions_used=0)

    def _analyze_query(self, query: str) -> QueryAnalysis:
        """Analyze the query for optimization."""
        return QueryAnalysis(original_query=query)

    async def _execute_searches(self, queries: list[str], analysis: QueryAnalysis, sources: list[str] | None=None, async_session: httpx.AsyncClient | None=None) -> dict[str, SourceResult]:
        """Execute searches across all sources."""
        source_results = {}
        adapters_to_use = self.source_adapters
        if sources:
            adapters_to_use = {name: adapter for name, adapter in self.source_adapters.items() if name in sources}
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, ConcurrencyBudgetRegistry
        registry = await ConcurrencyBudgetRegistry.get_instance_async()
        semaphore = registry.get(ConcurrencyCategory.ACADEMIC_SEARCH)

        async def search_with_limit(source_name: str, adapter: BaseSourceAdapter, query: str):
            async with semaphore:
                return await adapter.execute_search(query, max_results=adapter.config.max_results, analysis=analysis, async_session=async_session)
        tasks = []
        task_info = []
        for source_name, adapter in adapters_to_use.items():
            for query in queries:
                task = search_with_limit(source_name, adapter, query)
                tasks.append(task)
                task_info.append((source_name, query))
        search_results = await parallel_ok(*tasks, label='academic_search:1033')
        source_results_map: dict[str, list[SearchResult]] = {}
        source_times: dict[str, list[float]] = {}
        source_success: dict[str, bool] = {}
        for (source_name, query), result in zip(task_info, search_results, strict=False):
            if source_name not in source_results_map:
                source_results_map[source_name] = []
                source_times[source_name] = []
                source_success[source_name] = True
            if isinstance(result, Exception):
                self.logger.warning(f'Search failed for {source_name}: {result}')
                source_success[source_name] = False
            else:
                results, exec_time, success = result
                source_results_map[source_name].extend(results)
                source_times[source_name].append(exec_time)
                if not success:
                    source_success[source_name] = False
        for source_name in source_results_map:
            results = source_results_map[source_name]
            total_time = sum(source_times[source_name]) if source_times[source_name] else 0
            source_results[source_name] = SourceResult(source_name=source_name, results=results, query_used=queries[0] if queries else '', execution_time_ms=total_time, success=source_success[source_name])
        return source_results

    async def _deduplicate_results(self, results: list[SearchResult]) -> list[SearchResult]:
        """Deduplicate results using deduplication engine."""
        if not results or not self.dedup_engine:
            return results

        async def make_dedup_item(result: SearchResult) -> DedupItem:
            """Build DedupItem from SearchResult (CPU-bound hashlib)."""
            try:
                item_id = await asyncio.to_thread(hashlib.md5, f'{result.title}{result.url}'.encode())
                return DedupItem(id=item_id.hexdigest()[:12], title=result.title, content=result.snippet, url=result.url, source=result.source, metadata=result.metadata)
            except Exception:
                return DedupItem(id=result.url[:12] if result.url else '', title=result.title, content=result.snippet, url=result.url, source=result.source, metadata=result.metadata)
        batch_size = 50
        items: list[DedupItem] = []
        for i in range(0, len(results), batch_size):
            batch = results[i:i + batch_size]
            try:
                batch_items = await parallel_ok(*[make_dedup_item(r) for r in batch], label=f'academic_dedup:{i}')
                items.extend((item for item in batch_items if isinstance(item, DedupItem)))
            except Exception:
                for r in batch:
                    items.append(await make_dedup_item(r))
        dedup_result = await self.dedup_engine.deduplicate(items)
        unique_urls = {item.url for item in dedup_result.unique_items}
        unique_results = [r for r in results if r.url in unique_urls]
        return unique_results

    def _simple_deduplicate(self, results: list[SearchResult]) -> list[SearchResult]:
        """Simple deduplication based on URL and title."""
        if not results:
            return []
        seen_urls = set()
        seen_titles = set()
        unique_results = []
        for result in results:
            normalized_url = self._normalize_url(result.url)
            normalized_title = result.title.lower().strip()
            if normalized_url and normalized_url in seen_urls:
                continue
            if normalized_title in seen_titles:
                continue
            seen_urls.add(normalized_url)
            seen_titles.add(normalized_title)
            unique_results.append(result)
        return unique_results

    async def _rank_results(self, results: list[SearchResult], query: str) -> list[SearchResult]:
        """Rank results by relevance (P1-3: parallel scoring with asyncio.to_thread)."""
        query_terms = set(query.lower().split())
        batch_size = 50

        def score_one(result: SearchResult) -> tuple[SearchResult, float]:
            """Compute relevance_score for one result (CPU-bound)."""
            title_terms = set(result.title.lower().split())
            snippet_terms = set(result.snippet.lower().split())
            title_matches = len(query_terms & title_terms)
            snippet_matches = len(query_terms & snippet_terms)
            title_weight = 0.4
            snippet_weight = 0.2
            source_weight = 0.2
            citation_weight = 0.2
            match_score = title_matches * title_weight + snippet_matches * snippet_weight
            source_scores = {'arxiv': 1.0, 'crossref': 1.0, 'semantic_scholar': 0.9}
            source_score = source_scores.get(result.source, 0.5) * source_weight
            citation_count = result.metadata.get('citation_count', 0)
            citation_score = min(citation_count / 100, 1.0) * citation_weight
            score = match_score + source_score + citation_score
            return (result, score)
        scored: list[tuple[SearchResult, float]] = []
        for i in range(0, len(results), batch_size):
            batch = results[i:i + batch_size]
            try:
                batch_scored = await parallel_ok(*[asyncio.to_thread(score_one, r) for r in batch], label=f'academic_rank:{i}')
                scored.extend((item for item in batch_scored if isinstance(item, tuple) and len(item) == 2))
            except Exception:
                for r in batch:
                    scored.append(score_one(r))
        for result, score in scored:
            result.relevance_score = score
        scored_results = [r for r, _ in scored]
        return sorted(scored_results, key=lambda r: r.relevance_score, reverse=True)

    async def _deduplicate_and_rank(self, results: list[SearchResult], query: str) -> list[SearchResult]:
        """
        Unified deduplication + ranking via single TaskGroup + Queue pipeline.

        Pass 1 (producer): builds DedupItems from SearchResults (CPU-bound hash).
        Queue (maxsize=512): backpressure when consumer is slower than producer.
        Pass 2 (consumer): deduplicates then ranks items pulled from queue.

        Both passes run concurrently within a single TaskGroup — no GIL
        serialization between them. CPU-bound work runs on asyncio.to_thread
        which releases the GIL during the hash/scoring computation.
        """
        if not results:
            return []
        dedup_engine = self.dedup_engine
        query_terms = set(query.lower().split())
        source_scores_map = {'arxiv': 1.0, 'crossref': 1.0, 'semantic_scholar': 0.9}
        dedup_url_set: set[str] = set()

        async def score_and_maybe_keep(item: DedupItem) -> SearchResult | None:
            """Consumer function: score item, check dedup, return if unique."""
            title_terms = set(item.title.lower().split())
            snippet_terms = set((item.content or '').lower().split())
            title_matches = len(query_terms & title_terms)
            snippet_matches = len(query_terms & snippet_terms)
            match_score = title_matches * 0.4 + snippet_matches * 0.2
            source_score = source_scores_map.get(item.source, 0.5) * 0.2
            citation_count = (item.metadata or {}).get('citation_count', 0)
            citation_score = min(citation_count / 100, 1.0) * 0.2
            score = match_score + source_score + citation_score
            match item.metadata:
                case {'url': url} if url:
                    pass
                case _ if item.url:
                    pass
            normalized_url = item.url.lower().replace('https://', '').replace('http://', '')
            if normalized_url in dedup_url_set:
                return None
            dedup_url_set.add(normalized_url)
            return SearchResult(title=item.title, url=item.url, snippet=item.content or '', source=item.source, relevance_score=score, metadata=item.metadata or {})

        async def build_dedup_items() -> list[DedupItem]:
            """Build all DedupItems from search results (Pass 1)."""

            async def make_dedup_item(result: SearchResult) -> DedupItem:
                try:
                    item_id = await asyncio.to_thread(hashlib.md5, f'{result.title}{result.url}'.encode())
                    return DedupItem(id=item_id.hexdigest()[:12], title=result.title, content=result.snippet, url=result.url, source=result.source, metadata=result.metadata)
                except Exception:
                    return DedupItem(id=result.url[:12] if result.url else '', title=result.title, content=result.snippet, url=result.url, source=result.source, metadata=result.metadata)
            if len(results) <= 50:
                items = await parallel_ok(*[make_dedup_item(r) for r in results], label='academic_dedup_rank:build')
                return [it for it in items if isinstance(it, DedupItem)]
            all_items: list[DedupItem] = []
            for i in range(0, len(results), 50):
                batch = results[i:i + 50]
                try:
                    batch_items = await parallel_ok(*[make_dedup_item(r) for r in batch], label=f'academic_dedup_rank:build:{i}')
                    all_items.extend((it for it in batch_items if isinstance(it, DedupItem)))
                except Exception:
                    for r in batch:
                        all_items.append(await make_dedup_item(r))
            return all_items
        if not dedup_engine:
            simple_results = self._simple_deduplicate(results)
            return await self._rank_results(simple_results, query)
        items = await build_dedup_items()
        if not items:
            return []
        try:
            dedup_result = await dedup_engine.deduplicate(items)
            unique_urls = {item.url for item in dedup_result.unique_items}
        except Exception:
            unique_urls = {item.url for item in items}
        unique_items = [it for it in items if it.url in unique_urls]
        scored = await consumer_fn_to_thread(score_and_maybe_keep, unique_items, batch_size=64)
        scored_with_scores = [r for r in scored if r is not None]
        return sorted(scored_with_scores, key=lambda r: r.relevance_score, reverse=True)

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication."""
        if not url:
            return ''
        normalized = url.lower()
        normalized = normalized.replace('https://', '').replace('http://', '')
        normalized = normalized.replace('www.', '')
        normalized = normalized.rstrip('/')
        return normalized

    def get_source_performance(self) -> dict[str, SourcePerformance]:
        """Get performance metrics for all sources."""
        return self.source_performance

    async def cleanup(self):
        """Cleanup resources."""
        if self.dedup_engine:
            await self.dedup_engine.cleanup()
        self.logger.info('AcademicSearchEngine cleanup complete')

async def search_academic(query: str, max_results: int=20, enable_expansion: bool=True) -> AcademicSearchResult:
    """
    Convenience function for academic search.

    Args:
        query: Search query
        max_results: Maximum results to return
        enable_expansion: Whether to expand the query

    Returns:
        Search results
    """
    engine = AcademicSearchEngine(enable_expansion=enable_expansion)
    try:
        result = await engine.search(query, max_results=max_results)
        return result
    finally:
        await engine.cleanup()
__all__ = ['ResultType', 'AcademicSource', 'SourceConfig', 'SearchResult', 'SourceResult', 'AcademicSearchResult', 'QueryAnalysis', 'SourcePerformance', 'BaseSourceAdapter', 'ArxivAdapter', 'CrossrefAdapter', 'SemanticScholarAdapter', 'AcademicSearchEngine', 'search_academic']

class SemanticScholarClient:
    """Semantic Scholar Graph API + ArXiv API — výzkumné papery.
    Zadarmo bez klíče (1000 req/5min). Neindexováno běžnými OSINT nástroji.
    Technical details z research paperů = primární CVE/malware zdroj."""
    _SS_URL = 'https://api.semanticscholar.org/graph/v1/paper/search'
    _ARXIV_URL = 'https://export.arxiv.org/api/query'
    _RATE_S = 0.5
    _CACHE_TTL = 3600 * 6
    __slots__ = tuple(('_cache_dir', '_last_req'))

    def __init__(self, cache_dir: str | Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._last_req = 0.0

    async def __aenter__(self) -> SemanticScholarClient:
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.cleanup()

    async def cleanup(self) -> None:
        """Cleanup resources (placeholder for future connection/state cleanup)."""
        self._last_req = 0.0

    async def search_ss(self, query: str, session: httpx.AsyncClient, limit: int=10) -> list[dict]:
        """Semantic Scholar: [{title, abstract, year, doi, authors}]"""
        import xxhash
        key = xxhash.xxh3_64(f'ss_{query[:80]}'.encode()).hexdigest()
        zst_path = self._cache_dir / f'{key}.json.zst'
        json_path = self._cache_dir / f'{key}.json'
        if zst_path.exists() and time.time() - zst_path.stat().st_mtime < self._CACHE_TTL:
            try:
                import compression.zstd as _zstd
                return decode(_zstd.decompress(zst_path.read_bytes()))
            except (ImportError, Exception):
                pass
        if json_path.exists() and time.time() - json_path.stat().st_mtime < self._CACHE_TTL:
            return decode(json_path.read_bytes())
        await self._throttle()
        params = {'query': query, 'fields': 'title,abstract,year,authors,externalIds', 'limit': limit}
        try:
            resp = await session.get(self._SS_URL, params=params, timeout=httpx.Timeout(12.0))
            try:
                if resp.status_code == 429:
                    await asyncio.sleep(60)
                    return []
                resp.raise_for_status()
                data = await resp.json(content_type=None)
            finally:
                await resp.aclose()
        except Exception as e:
            logger.warning(f"SemanticScholar '{query[:40]}': {e}")
            return []
        items = [{'title': p.get('title', ''), 'abstract': p.get('abstract', '') or '', 'year': p.get('year'), 'doi': (p.get('externalIds') or {}).get('DOI')} for p in data.get('data', [])]
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        zst_path.write_bytes(_zstd.compress(encode(items)))
        return items

    async def search_arxiv(self, query: str, session: httpx.AsyncClient, max_results: int=5) -> list[dict]:
        """ArXiv API — security preprints. [{title, summary, published, link}]"""
        import xxhash
        key = xxhash.xxh3_64(f'ax_{query[:80]}'.encode()).hexdigest()
        zst_path = self._cache_dir / f'{key}_ax.json.zst'
        json_path = self._cache_dir / f'{key}_ax.json'
        if zst_path.exists() and time.time() - zst_path.stat().st_mtime < self._CACHE_TTL:
            try:
                import compression.zstd as _zstd
                return decode(_zstd.decompress(zst_path.read_bytes()))
            except (ImportError, Exception):
                pass
        if json_path.exists() and time.time() - json_path.stat().st_mtime < self._CACHE_TTL:
            return decode(json_path.read_bytes())
        await self._throttle()
        params = {'search_query': f'all:{query}', 'max_results': max_results, 'sortBy': 'submittedDate', 'sortOrder': 'descending'}
        try:
            resp = await session.get(self._ARXIV_URL, params=params, timeout=httpx.Timeout(12.0))
            try:
                resp.raise_for_status()
                text = await resp.text()
            finally:
                await resp.aclose()
        except Exception as e:
            logger.warning(f"ArXiv '{query[:40]}': {e}")
            return []
        try:
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            root_el = ET.fromstring(text)
            items = []
            for entry in root_el.findall('atom:entry', ns):
                items.append({'title': (entry.findtext('atom:title', namespaces=ns) or '').strip(), 'summary': (entry.findtext('atom:summary', namespaces=ns) or '').strip()[:500], 'published': entry.findtext('atom:published', namespaces=ns), 'link': next((l.get('href', '') for l in entry.findall('atom:link', ns) if l.get('type') == 'text/html'), '')})
        except Exception as e:
            logger.warning(f"ArXiv XML parse '{query[:40]}': {e}")
            items = []
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        zst_path.write_bytes(_zstd.compress(encode(items)))
        return items

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_S:
            await asyncio.sleep(self._RATE_S - elapsed)
        self._last_req = time.time()