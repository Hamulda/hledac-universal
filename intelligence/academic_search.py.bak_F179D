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

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import urllib.parse
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp

from hledac.universal.utils.query_expansion import (
    ExpansionStrategy,
    MultiStrategyExpander,
    QueryVariation,
    SemanticExpansionStrategy,
    SyntacticExpansionStrategy,
    DomainSpecificExpansionStrategy
)
from hledac.universal.utils.deduplication import (
    DeduplicationEngine,
    DeduplicationConfig,
    QueryItem as DedupItem
)

logger = logging.getLogger(__name__)


# =============================================================================
# ENUMS AND TYPES
# =============================================================================

class ResultType(Enum):
    """Types of search results."""
    PAPER = auto()
    DATASET = auto()
    WEBPAGE = auto()
    MULTIMEDIA = auto()
    UNKNOWN = auto()


class AcademicSource(Enum):
    """Available academic sources."""
    ARXIV = "arxiv"
    CROSSREF = "crossref"
    SEMANTIC_SCHOLAR = "semantic_scholar"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class SourceConfig:
    """Configuration for a search source."""
    name: str
    enabled: bool = True
    weight: float = 1.0
    timeout_seconds: float = 10.0
    max_results: int = 10
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    rate_limit_per_minute: int = 60
    
    def __post_init__(self):
        # Load API key from environment if not provided
        if self.api_key is None:
            env_key = f"{self.name.upper()}_API_KEY"
            self.api_key = __import__('os').getenv(env_key)


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    url: str
    snippet: str
    source: str
    result_type: ResultType = ResultType.UNKNOWN
    metadata: Dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "result_type": self.result_type.name,
            "metadata": self.metadata,
            "relevance_score": self.relevance_score,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class SourceResult:
    """Results from a single source."""
    source_name: str
    results: List[SearchResult]
    query_used: str
    execution_time_ms: float
    success: bool
    error_message: Optional[str] = None


@dataclass
class AcademicSearchResult:
    """Complete academic search result."""
    original_query: str
    all_results: List[SearchResult]
    deduplicated_results: List[SearchResult]
    sources_used: List[str]
    total_sources: int
    successful_sources: int
    execution_time_ms: float
    expansions_used: int
    query_variations: List[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_query": self.original_query,
            "all_results_count": len(self.all_results),
            "deduplicated_results_count": len(self.deduplicated_results),
            "sources_used": self.sources_used,
            "total_sources": self.total_sources,
            "successful_sources": self.successful_sources,
            "execution_time_ms": self.execution_time_ms,
            "expansions_used": self.expansions_used,
            "query_variations": self.query_variations,
            "timestamp": self.timestamp.isoformat(),
            "results": [r.to_dict() for r in self.deduplicated_results[:10]]
        }


@dataclass
class QueryAnalysis:
    """Analysis of a query."""
    original_query: str
    key_terms: List[str] = field(default_factory=list)
    domain_hint: Optional[str] = None
    complexity_score: float = 0.5
    detected_language: str = "en"
    
    def __post_init__(self):
        if not self.key_terms:
            self.key_terms = self._extract_key_terms()
    
    def _extract_key_terms(self) -> List[str]:
        """Extract key terms from query."""
        stop_words = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
            "for", "of", "with", "by", "is", "are", "was", "were", "be",
            "been", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should"
        }
        words = self.original_query.lower().split()
        return [w for w in words if w not in stop_words and len(w) > 2]


@dataclass
class SourcePerformance:
    """Performance metrics for a source."""
    source_name: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time_ms: float = 0.0
    last_used: Optional[datetime] = None
    
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
        speed_score = max(0, 1 - (self.avg_response_time_ms / 10000))
        return (self.success_rate * success_weight) + (speed_score * speed_weight)
    
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
            self.avg_response_time_ms = (self.avg_response_time_ms * 0.8) + (response_time_ms * 0.2)
        
        self.last_used = datetime.now()


# =============================================================================
# BASE SOURCE ADAPTER
# =============================================================================

class BaseSourceAdapter(ABC):
    """Abstract base class for search source adapters."""
    
    def __init__(self, config: SourceConfig):
        self.config = config
        self.performance = SourcePerformance(source_name=config.name)
        self.logger = logging.getLogger(f"academic.source.{config.name}")
    
    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 10,
        analysis: Optional[QueryAnalysis] = None
    ) -> List[SearchResult]:
        """Search the source with the given query."""
        pass
    
    async def execute_search(
        self,
        query: str,
        max_results: int = 10,
        analysis: Optional[QueryAnalysis] = None
    ) -> Tuple[List[SearchResult], float, bool]:
        """Execute search with performance tracking."""
        start_time = time.time()
        
        try:
            results = await self.search(query, max_results, analysis)
            execution_time = (time.time() - start_time) * 1000
            self.performance.update(success=True, response_time_ms=execution_time)
            return results, execution_time, True
            
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            self.logger.error(f"Search failed: {e}")
            self.performance.update(success=False, response_time_ms=execution_time)
            return [], execution_time, False
    
    def get_performance(self) -> SourcePerformance:
        """Get performance metrics for this source."""
        return self.performance


# =============================================================================
# ARXIV ADAPTER
# =============================================================================

class ArxivAdapter(BaseSourceAdapter):
    """Adapter for searching ArXiv."""
    
    def __init__(self, config: SourceConfig):
        super().__init__(config)
        self.base_url = config.base_url or "http://export.arxiv.org/api/query"
    
    async def search(
        self,
        query: str,
        max_results: int = 10,
        analysis: Optional[QueryAnalysis] = None,
        async_session: Optional[aiohttp.ClientSession] = None
    ) -> List[SearchResult]:
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
            url = (
                f"{self.base_url}?"
                f"search_query=all:{search_query}&"
                f"start=0&"
                f"max_results={max_results}&"
                f"sortBy=relevance&"
                f"sortOrder=descending"
            )

            headers = {"User-Agent": "Hledac-Research/1.0"}

            async def _do_search(session: aiohttp.ClientSession) -> List[SearchResult]:
                nonlocal url, headers
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds)
                ) as response:
                    if response.status != 200:
                        self.logger.warning(f"ArXiv API returned status {response.status}")
                        return []

                    xml_content = await response.text()
                    return self._parse_results(xml_content)

            if async_session is not None:
                return await _do_search(async_session)
            else:
                async with aiohttp.ClientSession() as session:
                    return await _do_search(session)

        except asyncio.TimeoutError:
            self.logger.warning("ArXiv search timed out")
            return []
        except Exception as e:
            self.logger.error(f"ArXiv search error: {e}")
            return []
    
    def _parse_results(self, xml_content: str) -> List[SearchResult]:
        """Parse ArXiv API XML response."""
        results = []
        
        try:
            ns = {
                'atom': 'http://www.w3.org/2005/Atom',
                'arxiv': 'http://arxiv.org/schemas/atom'
            }
            
            root = ET.fromstring(xml_content.encode('utf-8'))
            
            for entry in root.findall('atom:entry', ns):
                if entry.find('atom:title', ns) is None:
                    continue
                
                title_elem = entry.find('atom:title', ns)
                title = title_elem.text if title_elem is not None else "No Title"
                
                summary_elem = entry.find('atom:summary', ns)
                summary = summary_elem.text if summary_elem is not None else ""
                
                id_elem = entry.find('atom:id', ns)
                arxiv_id = id_elem.text if id_elem is not None else ""
                url = arxiv_id if arxiv_id else ""
                
                # Get authors
                authors = []
                for author in entry.findall('atom:author', ns):
                    name_elem = author.find('atom:name', ns)
                    if name_elem is not None:
                        authors.append(name_elem.text)
                
                # Get published date
                published_elem = entry.find('atom:published', ns)
                published = published_elem.text if published_elem is not None else ""
                
                # Get categories
                categories = []
                for category in entry.findall('atom:category', ns):
                    term = category.get('term')
                    if term:
                        categories.append(term)
                
                # Get PDF link
                pdf_url = ""
                for link in entry.findall('atom:link', ns):
                    if link.get('title') == 'pdf':
                        pdf_url = link.get('href', '')
                        break
                
                snippet = summary[:300] + "..." if len(summary) > 300 else summary
                
                result = SearchResult(
                    title=title.strip(),
                    url=url,
                    snippet=snippet.strip(),
                    source="arxiv",
                    result_type=ResultType.PAPER,
                    metadata={
                        "authors": authors,
                        "published": published,
                        "categories": categories,
                        "pdf_url": pdf_url,
                        "arxiv_id": arxiv_id.split('/')[-1] if arxiv_id else ""
                    }
                )
                results.append(result)
                
        except ET.ParseError as e:
            self.logger.error(f"XML parse error: {e}")
        except Exception as e:
            self.logger.error(f"Error parsing ArXiv results: {e}")
        
        return results
    
    async def get_paper_details(self, arxiv_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific paper."""
        try:
            url = f"{self.base_url}?id_list={arxiv_id}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        xml_content = await response.text()
                        results = self._parse_results(xml_content)
                        if results:
                            return results[0].metadata
                    return {}
        except Exception as e:
            self.logger.error(f"Error fetching paper details: {e}")
            return {}


# =============================================================================
# CROSSREF ADAPTER
# =============================================================================

class CrossrefAdapter(BaseSourceAdapter):
    """Adapter for searching Crossref."""
    
    def __init__(self, config: SourceConfig):
        super().__init__(config)
        self.base_url = config.base_url or "https://api.crossref.org/works"

    async def search(
        self,
        query: str,
        max_results: int = 10,
        analysis: Optional[QueryAnalysis] = None,
        async_session: Optional[aiohttp.ClientSession] = None
    ) -> List[SearchResult]:
        """Search Crossref for academic papers.

        Args:
            query: Search query
            max_results: Maximum results to return
            analysis: Optional query analysis
            async_session: Optional shared aiohttp session for connection pooling.
                         If not provided, creates a per-call session (legacy behavior).
        """
        try:
            params = {
                "query": query,
                "rows": min(max_results, 20),
                "sort": "relevance",
                "order": "desc"
            }

            headers = {
                "User-Agent": "Hledac-Research/1.0 (mailto:research@hledac.local)"
            }

            if self.config.api_key:
                headers["Crossref-Plus-API-Token"] = f"Bearer {self.config.api_key}"

            async def _do_search(session: aiohttp.ClientSession) -> List[SearchResult]:
                nonlocal params, headers
                async with session.get(
                    self.base_url,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds)
                ) as response:
                    if response.status != 200:
                        self.logger.warning(f"Crossref API returned status {response.status}")
                        return []

                    data = await response.json()
                    return self._parse_results(data)

            if async_session is not None:
                return await _do_search(async_session)
            else:
                async with aiohttp.ClientSession() as session:
                    return await _do_search(session)

        except asyncio.TimeoutError:
            self.logger.warning("Crossref search timed out")
            return []
        except Exception as e:
            self.logger.error(f"Crossref search error: {e}")
            return []
    
    def _parse_results(self, data: Dict) -> List[SearchResult]:
        """Parse Crossref API JSON response."""
        results = []
        
        try:
            items = data.get("message", {}).get("items", [])
            
            for item in items:
                titles = item.get("title", [])
                title = titles[0] if titles else "No Title"
                
                doi = item.get("DOI", "")
                url = item.get("URL", f"https://doi.org/{doi}" if doi else "")
                
                # Get authors
                authors = []
                for author in item.get("author", []):
                    given = author.get("given", "")
                    family = author.get("family", "")
                    if given and family:
                        authors.append(f"{given} {family}")
                    elif family:
                        authors.append(family)
                
                # Get abstract (rarely available in Crossref)
                abstract = item.get("abstract", "")
                if not abstract:
                    container = item.get("container-title", [])
                    container_title = container[0] if container else ""
                    pub_type = item.get("type", "unknown")
                    abstract = f"{pub_type}: {container_title}" if container_title else pub_type
                
                snippet = abstract[:300] + "..." if len(abstract) > 300 else abstract
                
                # Get publication date
                published = item.get("published-print", {}) or item.get("published-online", {})
                date_parts = published.get("date-parts", [[]])
                pub_date = "-".join(str(p) for p in date_parts[0]) if date_parts and date_parts[0] else ""
                
                # Get citation count
                citations = item.get("is-referenced-by-count", 0)
                
                result = SearchResult(
                    title=title.strip(),
                    url=url,
                    snippet=snippet.strip(),
                    source="crossref",
                    result_type=ResultType.PAPER,
                    metadata={
                        "authors": authors,
                        "doi": doi,
                        "published": pub_date,
                        "publisher": item.get("publisher", ""),
                        "citations": citations,
                        "type": item.get("type", ""),
                        "container_title": item.get("container-title", [])
                    },
                    relevance_score=min(citations / 100, 1.0) * 0.3
                )
                results.append(result)
                
        except Exception as e:
            self.logger.error(f"Error parsing Crossref results: {e}")
        
        return results
    
    async def get_work_by_doi(self, doi: str) -> Dict[str, Any]:
        """Get detailed information about a work by DOI."""
        try:
            url = f"{self.base_url}/{doi}"
            
            headers = {
                "User-Agent": "Hledac-Research/1.0 (mailto:research@hledac.local)"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        message = data.get("message", {})
                        return {
                            "title": message.get("title", [""])[0],
                            "doi": message.get("DOI", ""),
                            "authors": message.get("author", []),
                            "published": message.get("published-print", {}),
                            "publisher": message.get("publisher", ""),
                            "citations": message.get("is-referenced-by-count", 0)
                        }
                    return {}
        except Exception as e:
            self.logger.error(f"Error fetching work by DOI: {e}")
            return {}


# =============================================================================
# SEMANTIC SCHOLAR ADAPTER
# =============================================================================

class SemanticScholarAdapter(BaseSourceAdapter):
    """Adapter for searching Semantic Scholar."""
    
    def __init__(self, config: SourceConfig):
        super().__init__(config)
        self.base_url = config.base_url or "https://api.semanticscholar.org/graph/v1"

    async def search(
        self,
        query: str,
        max_results: int = 10,
        analysis: Optional[QueryAnalysis] = None,
        async_session: Optional[aiohttp.ClientSession] = None
    ) -> List[SearchResult]:
        """Search Semantic Scholar for papers.

        Args:
            query: Search query
            max_results: Maximum results to return
            analysis: Optional query analysis
            async_session: Optional shared aiohttp session for connection pooling.
                         If not provided, creates a per-call session (legacy behavior).
        """
        try:
            url = f"{self.base_url}/paper/search"

            params = {
                "query": query,
                "fields": "title,authors,year,abstract,citationCount,referenceCount,externalIds,url,openAccessPdf",
                "limit": min(max_results, 100)
            }

            headers = {"User-Agent": "Hledac-Research/1.0"}

            if self.config.api_key:
                headers["x-api-key"] = self.config.api_key

            async def _do_search(session: aiohttp.ClientSession) -> List[SearchResult]:
                nonlocal url, params, headers
                async with session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds)
                ) as response:
                    if response.status == 429:
                        self.logger.warning("Semantic Scholar rate limit hit")
                        return []

                    if response.status != 200:
                        self.logger.warning(f"Semantic Scholar API returned status {response.status}")
                        return []

                    data = await response.json()
                    return self._parse_results(data)

            if async_session is not None:
                return await _do_search(async_session)
            else:
                async with aiohttp.ClientSession() as session:
                    return await _do_search(session)

        except asyncio.TimeoutError:
            self.logger.warning("Semantic Scholar search timed out")
            return []
        except Exception as e:
            self.logger.error(f"Semantic Scholar search error: {e}")
            return []
    
    def _parse_results(self, data: Dict) -> List[SearchResult]:
        """Parse Semantic Scholar API JSON response."""
        results = []
        
        try:
            papers = data.get("data", [])
            
            for paper in papers:
                title = paper.get("title", "No Title")
                paper_id = paper.get("paperId", "")
                
                external_ids = paper.get("externalIds", {})
                doi = external_ids.get("DOI", "")
                
                url = paper.get("url", "")
                if not url and doi:
                    url = f"https://doi.org/{doi}"
                
                abstract = paper.get("abstract", "")
                if not abstract:
                    abstract = "No abstract available"
                
                snippet = abstract[:300] + "..." if len(abstract) > 300 else abstract
                
                # Get authors
                authors = []
                for author in paper.get("authors", []):
                    name = author.get("name", "")
                    if name:
                        authors.append(name)
                
                year = paper.get("year", "")
                citation_count = paper.get("citationCount", 0)
                reference_count = paper.get("referenceCount", 0)
                
                open_access = paper.get("openAccessPdf", {})
                pdf_url = open_access.get("url", "") if open_access else ""
                
                result = SearchResult(
                    title=title.strip(),
                    url=url,
                    snippet=snippet.strip(),
                    source="semantic_scholar",
                    result_type=ResultType.PAPER,
                    metadata={
                        "authors": authors,
                        "year": year,
                        "doi": doi,
                        "paper_id": paper_id,
                        "citation_count": citation_count,
                        "reference_count": reference_count,
                        "pdf_url": pdf_url
                    },
                    relevance_score=min(citation_count / 100, 1.0) * 0.5
                )
                results.append(result)
                
        except Exception as e:
            self.logger.error(f"Error parsing Semantic Scholar results: {e}")
        
        return results
    
    async def get_paper_details(self, paper_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific paper."""
        try:
            url = f"{self.base_url}/paper/{paper_id}"
            
            params = {
                "fields": "title,authors,year,abstract,citationCount,referenceCount,externalIds,url,openAccessPdf,fieldsOfStudy,publicationDate,tldr"
            }
            
            headers = {"User-Agent": "Hledac-Research/1.0"}
            
            if self.config.api_key:
                headers["x-api-key"] = self.config.api_key
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        return await response.json()
                    return {}
        except Exception as e:
            self.logger.error(f"Error fetching paper details: {e}")
            return {}
    
    async def get_citations(self, paper_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get papers that cite this paper."""
        try:
            url = f"{self.base_url}/paper/{paper_id}/citations"
            params = {
                "fields": "title,authors,year,abstract,citationCount",
                "limit": limit
            }
            
            headers = {"User-Agent": "Hledac-Research/1.0"}
            
            if self.config.api_key:
                headers["x-api-key"] = self.config.api_key
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("data", [])
                    return []
        except Exception as e:
            self.logger.error(f"Error fetching citations: {e}")
            return []


# =============================================================================
# MAIN ACADEMIC SEARCH ENGINE
# =============================================================================

class AcademicSearchEngine:
    """
    Main engine for Multi-Source Academic Search.
    
    Coordinates query expansion, source selection, parallel execution,
    and result deduplication.
    """
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        enable_expansion: bool = True,
        enable_deduplication: bool = True
    ):
        self.config = config or {}
        self.enable_expansion = enable_expansion
        self.enable_deduplication = enable_deduplication
        
        # Initialize expansion strategies
        self.expansion_strategies: List[ExpansionStrategy] = []
        if enable_expansion:
            self.expansion_strategies = [
                SemanticExpansionStrategy(max_expansions=3),
                SyntacticExpansionStrategy(max_expansions=3),
                DomainSpecificExpansionStrategy(max_expansions=3)
            ]
        
        self.multi_expander = MultiStrategyExpander(
            strategies=self.expansion_strategies,
            max_total_variations=15
        )

        self.logger = logging.getLogger("academic.engine")

        # Initialize source adapters
        self.source_adapters: Dict[str, BaseSourceAdapter] = {}
        self.source_performance: Dict[str, SourcePerformance] = {}
        self._init_sources()

        # Initialize deduplication engine
        self.dedup_engine: Optional[DeduplicationEngine] = None
        if enable_deduplication:
            dedup_config = DeduplicationConfig(
                semantic_threshold=0.85,
                content_threshold=0.90,
                metadata_threshold=0.95
            )
            self.dedup_engine = DeduplicationEngine(dedup_config)

    def _init_sources(self):
        """Initialize source adapters."""
        source_configs = {
            "arxiv": SourceConfig(
                name="arxiv",
                base_url="http://export.arxiv.org/api/query",
                max_results=10,
                rate_limit_per_minute=30
            ),
            "crossref": SourceConfig(
                name="crossref",
                base_url="https://api.crossref.org/works",
                max_results=10,
                rate_limit_per_minute=50
            ),
            "semantic_scholar": SourceConfig(
                name="semantic_scholar",
                base_url="https://api.semanticscholar.org/graph/v1",
                max_results=10,
                rate_limit_per_minute=100
            ),
        }

        source_mapping = {
            "arxiv": ArxivAdapter,
            "crossref": CrossrefAdapter,
            "semantic_scholar": SemanticScholarAdapter,
        }

        for name, source_config in source_configs.items():
            if name in source_mapping:
                adapter_class = source_mapping[name]
                self.source_adapters[name] = adapter_class(source_config)
                self.source_performance[name] = SourcePerformance(source_name=name)

        self.logger.info(f"Initialized {len(self.source_adapters)} source adapters")

    async def search(
        self,
        query: str,
        max_results: int = 20,
        enable_expansion: Optional[bool] = None,
        sources: Optional[List[str]] = None
    ) -> AcademicSearchResult:
        """
        Execute multi-source academic search.
        
        Args:
            query: Original search query
            max_results: Maximum total results to return
            enable_expansion: Whether to expand the query (overrides default)
            sources: List of source names to use (default: all)
            
        Returns:
            Academic search result
        """
        max_results = max_results or 20
        do_expansion = enable_expansion if enable_expansion is not None else self.enable_expansion
        start_time = time.time()
        
        try:
            # Phase 1: Query Analysis
            analysis = self._analyze_query(query)
            
            # Phase 2: Query Expansion
            queries_to_search = [query]
            expanded_queries: List[QueryVariation] = []
            query_variations = [query]
            
            if do_expansion and self.expansion_strategies:
                expanded_queries = await self.multi_expander.expand(
                    query, context={"domain": analysis.domain_hint}
                )
                queries_to_search.extend([exp.query for exp in expanded_queries])
                query_variations = list(dict.fromkeys(queries_to_search))  # Remove duplicates
            
            self.logger.info(f"Searching with {len(query_variations)} query variants")
            
            # Phase 3: Execute searches across sources
            all_source_results = await self._execute_searches(
                query_variations, analysis, sources
            )
            
            # Collect all results
            all_results = []
            for source_result in all_source_results.values():
                all_results.extend(source_result.results)
            
            # Phase 4: Deduplication
            if self.enable_deduplication and self.dedup_engine:
                deduplicated = await self._deduplicate_results(all_results)
            else:
                deduplicated = self._simple_deduplicate(all_results)
            
            # Phase 5: Ranking
            ranked_results = self._rank_results(deduplicated, query)[:max_results]
            
            execution_time = (time.time() - start_time) * 1000
            
            # Calculate success stats
            successful_sources = sum(
                1 for sr in all_source_results.values() if sr.success
            )
            
            result = AcademicSearchResult(
                original_query=query,
                all_results=all_results,
                deduplicated_results=ranked_results,
                sources_used=list(all_source_results.keys()),
                total_sources=len(self.source_adapters),
                successful_sources=successful_sources,
                execution_time_ms=execution_time,
                expansions_used=len(expanded_queries),
                query_variations=query_variations
            )
            
            self.logger.info(
                f"Search completed: {len(all_results)} total, "
                f"{len(ranked_results)} unique from {successful_sources} sources "
                f"in {execution_time:.0f}ms"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"Academic search error: {e}")
            execution_time = (time.time() - start_time) * 1000
            
            return AcademicSearchResult(
                original_query=query,
                all_results=[],
                deduplicated_results=[],
                sources_used=[],
                total_sources=len(self.source_adapters),
                successful_sources=0,
                execution_time_ms=execution_time,
                expansions_used=0
            )
    
    def _analyze_query(self, query: str) -> QueryAnalysis:
        """Analyze the query for optimization."""
        return QueryAnalysis(original_query=query)
    
    async def _execute_searches(
        self,
        queries: List[str],
        analysis: QueryAnalysis,
        sources: Optional[List[str]] = None
    ) -> Dict[str, SourceResult]:
        """Execute searches across all sources."""
        source_results = {}
        
        # Filter sources if specified
        adapters_to_use = self.source_adapters
        if sources:
            adapters_to_use = {
                name: adapter for name, adapter in self.source_adapters.items()
                if name in sources
            }
        
        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(5)
        
        async def search_with_limit(
            source_name: str,
            adapter: BaseSourceAdapter,
            query: str
        ):
            async with semaphore:
                return await adapter.execute_search(
                    query,
                    max_results=adapter.config.max_results,
                    analysis=analysis
                )
        
        # Execute all searches
        tasks = []
        task_info = []
        
        for source_name, adapter in adapters_to_use.items():
            for query in queries:
                task = search_with_limit(source_name, adapter, query)
                tasks.append(task)
                task_info.append((source_name, query))
        
        search_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        source_results_map: Dict[str, List[SearchResult]] = {}
        source_times: Dict[str, List[float]] = {}
        source_success: Dict[str, bool] = {}
        
        for (source_name, query), result in zip(task_info, search_results):
            if source_name not in source_results_map:
                source_results_map[source_name] = []
                source_times[source_name] = []
                source_success[source_name] = True
            
            if isinstance(result, Exception):
                self.logger.warning(f"Search failed for {source_name}: {result}")
                source_success[source_name] = False
            else:
                results, exec_time, success = result
                source_results_map[source_name].extend(results)
                source_times[source_name].append(exec_time)
                if not success:
                    source_success[source_name] = False
        
        # Create SourceResult objects
        for source_name in source_results_map:
            results = source_results_map[source_name]
            total_time = sum(source_times[source_name]) if source_times[source_name] else 0
            
            source_results[source_name] = SourceResult(
                source_name=source_name,
                results=results,
                query_used=queries[0] if queries else "",
                execution_time_ms=total_time,
                success=source_success[source_name]
            )
        
        return source_results
    
    async def _deduplicate_results(
        self, results: List[SearchResult]
    ) -> List[SearchResult]:
        """Deduplicate results using deduplication engine."""
        if not results or not self.dedup_engine:
            return results
        
        # Convert to QueryItems
        items = []
        for result in results:
            item = DedupItem(
                id=hashlib.md5(f"{result.title}{result.url}".encode()).hexdigest()[:12],
                title=result.title,
                content=result.snippet,
                url=result.url,
                source=result.source,
                metadata=result.metadata
            )
            items.append(item)
        
        # Run deduplication
        dedup_result = await self.dedup_engine.deduplicate(items)
        
        # Map back to SearchResults
        unique_urls = {item.url for item in dedup_result.unique_items}
        unique_results = [r for r in results if r.url in unique_urls]
        
        return unique_results
    
    def _simple_deduplicate(self, results: List[SearchResult]) -> List[SearchResult]:
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
    
    def _rank_results(
        self,
        results: List[SearchResult],
        query: str
    ) -> List[SearchResult]:
        """Rank results by relevance."""
        query_terms = set(query.lower().split())
        
        for result in results:
            title_terms = set(result.title.lower().split())
            snippet_terms = set(result.snippet.lower().split())
            
            title_matches = len(query_terms & title_terms)
            snippet_matches = len(query_terms & snippet_terms)
            
            title_weight = 0.4
            snippet_weight = 0.2
            source_weight = 0.2
            citation_weight = 0.2
            
            match_score = (title_matches * title_weight + snippet_matches * snippet_weight)
            
            source_scores = {
                "arxiv": 1.0,
                "crossref": 1.0,
                "semantic_scholar": 0.9
            }
            source_score = source_scores.get(result.source, 0.5) * source_weight
            
            citation_count = result.metadata.get("citation_count", 0)
            citation_score = min(citation_count / 100, 1.0) * citation_weight
            
            result.relevance_score = match_score + source_score + citation_score
        
        return sorted(results, key=lambda r: r.relevance_score, reverse=True)
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL for deduplication."""
        if not url:
            return ""
        
        normalized = url.lower()
        normalized = normalized.replace("https://", "").replace("http://", "")
        normalized = normalized.replace("www.", "")
        normalized = normalized.rstrip("/")
        
        return normalized
    
    def get_source_performance(self) -> Dict[str, SourcePerformance]:
        """Get performance metrics for all sources."""
        return self.source_performance
    
    async def cleanup(self):
        """Cleanup resources."""
        if self.dedup_engine:
            await self.dedup_engine.cleanup()
        self.logger.info("AcademicSearchEngine cleanup complete")


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def search_academic(
    query: str,
    max_results: int = 20,
    enable_expansion: bool = True
) -> AcademicSearchResult:
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


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Enums
    'ResultType',
    'AcademicSource',
    
    # Data classes
    'SourceConfig',
    'SearchResult',
    'SourceResult',
    'AcademicSearchResult',
    'QueryAnalysis',
    'SourcePerformance',
    
    # Adapters
    'BaseSourceAdapter',
    'ArxivAdapter',
    'CrossrefAdapter',
    'SemanticScholarAdapter',
    
    # Main engine
    'AcademicSearchEngine',
    'search_academic',
]


# =============================================================================
# SemanticScholarClient — Sprint 8UB: CVE → Academic papers
# =============================================================================

class SemanticScholarClient:
    """Semantic Scholar Graph API + ArXiv API — výzkumné papery.
    Zadarmo bez klíče (1000 req/5min). Neindexováno běžnými OSINT nástroji.
    Technical details z research paperů = primární CVE/malware zdroj."""

    _SS_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    _ARXIV_URL = "https://export.arxiv.org/api/query"
    _RATE_S = 0.5
    _CACHE_TTL = 3600 * 6  # 6h

    def __init__(self, cache_dir: str | Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._last_req = 0.0

    async def search_ss(
        self,
        query: str,
        session: aiohttp.ClientSession,
        limit: int = 10,
    ) -> list[dict]:
        """Semantic Scholar: [{title, abstract, year, doi, authors}]"""
        import xxhash, orjson

        key = xxhash.xxh64(f"ss_{query[:80]}".encode()).hexdigest()
        cp = self._cache_dir / f"{key}.json"
        if cp.exists() and (time.time() - cp.stat().st_mtime < self._CACHE_TTL):
            return orjson.loads(cp.read_bytes())

        await self._throttle()
        params = {
            "query": query,
            "fields": "title,abstract,year,authors,externalIds",
            "limit": limit,
        }
        try:
            async with session.get(
                self._SS_URL, params=params,
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                if r.status == 429:
                    await asyncio.sleep(60)
                    return []
                r.raise_for_status()
                data = await r.json(content_type=None)
        except Exception as e:
            logger.warning(f"SemanticScholar '{query[:40]}': {e}")
            return []

        items = [
            {"title": p.get("title", ""),
             "abstract": p.get("abstract", "") or "",
             "year": p.get("year"),
             "doi": (p.get("externalIds") or {}).get("DOI")}
            for p in data.get("data", [])
        ]
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(orjson.dumps(items))
        return items

    async def search_arxiv(
        self,
        query: str,
        session: aiohttp.ClientSession,
        max_results: int = 5,
    ) -> list[dict]:
        """ArXiv API — security preprints. [{title, summary, published, link}]"""
        import xxhash, orjson

        key = xxhash.xxh64(f"ax_{query[:80]}".encode()).hexdigest()
        cp = self._cache_dir / f"{key}_ax.json"
        if cp.exists() and (time.time() - cp.stat().st_mtime < self._CACHE_TTL):
            return orjson.loads(cp.read_bytes())

        await self._throttle()
        params = {
            "search_query": f"all:{query}",
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        try:
            async with session.get(
                self._ARXIV_URL, params=params,
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                r.raise_for_status()
                text = await r.text()
        except Exception as e:
            logger.warning(f"ArXiv '{query[:40]}': {e}")
            return []

        try:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            root_el = ET.fromstring(text)
            items = []
            for entry in root_el.findall("atom:entry", ns):
                items.append({
                    "title": (entry.findtext("atom:title", namespaces=ns) or "").strip(),
                    "summary": (entry.findtext("atom:summary", namespaces=ns) or "").strip()[:500],
                    "published": entry.findtext("atom:published", namespaces=ns),
                    "link": next(
                        (l.get("href", "") for l in entry.findall("atom:link", ns)
                         if l.get("type") == "text/html"),
                        ""
                    ),
                })
        except Exception as e:
            logger.warning(f"ArXiv XML parse '{query[:40]}': {e}")
            items = []

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(orjson.dumps(items))
        return items

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_S:
            await asyncio.sleep(self._RATE_S - elapsed)
        self._last_req = time.time()

