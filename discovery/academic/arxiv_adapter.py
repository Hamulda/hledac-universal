"""
discovery/academic/arxiv_adapter.py — arXiv Bulk Access via OAI-PMH

Sprint F259: Academic Intelligence Layer — arXiv OAI-PMH bulk harvesting.




Features:
- OAI-PMH endpoint for incremental/delta harvesting (not just search API)
- from date param for incremental updates only
- Full metadata: MSC classifications, journal refs, author ORCID
- Returns CanonicalFinding with source_type="arxiv_bulk"

M1 8GB: asyncio.Semaphore(3), bounded results, fail-soft.
"""
import asyncio
import logging
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, NamedTuple, cast
if TYPE_CHECKING:
    from xml.etree.ElementTree import Element
from hledac.universal.knowledge.duckdb_store import CanonicalFinding
logger = logging.getLogger(__name__)
OAI_PMH_ENDPOINT = 'http://export.arxiv.org/oai2'
MAX_RESULTS = 20
MAX_CONCURRENT_REQUESTS = 3
REQUEST_TIMEOUT_S = 5.0
MSC_PATTERN = '\\b\\d{2}[A-Z]{2}\\d{2,3}\\b'

class ArxivPaper(NamedTuple):
    """Structured arXiv paper."""
    id: str
    title: str
    authors: list[str]
    abstract: str
    categories: list[str]
    msc_class: str | None
    journal_ref: str | None
    orcid: list[str]
    published: str
    updated: str
    doi: str | None
    pdf_url: str

class ArxivResult(NamedTuple):
    """Result of arXiv search."""
    papers: list[ArxivPaper]
    error: str | None
    total_harvested: int = 0

def _parse_oai_response(xml_content: bytes) -> list[dict]:
    """Parse OAI-PMH XML response into record dicts."""
    try:
        root = ET.fromstring(xml_content)
        ns = {'oai': 'http://www.openarchives.org/OAI/2.0/'}
        records = []
        for record in root.findall('.//oai:record', ns):
            header = record.find('oai:header', ns)
            if header is None:
                continue
            identifier = cast('Element', header.find('oai:identifier', ns))
            if identifier is None:
                continue
            metadata = record.find('oai:metadata', ns)
            if metadata is None:
                continue
            arxiv_ns = {'arxiv': 'http://arxiv.org/schemas/atom'}
            data = {}
            title_el = metadata.find('.//arxiv:title', arxiv_ns) or metadata.find('.//{http://arxiv.org/schemas/atom}title')
            if title_el is None:
                title_el = metadata.find('.//title')
            if title_el is not None:
                data['title'] = ' '.join(title_el.text.split()) if title_el.text else ''
            authors = []
            for author_el in metadata.findall('.//author') + metadata.findall('.//{http://arxiv.org/schemas/atom}author'):
                name_el = author_el.find('name') or author_el.find('.//keyname') or author_el
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())
                orcid_el = author_el.find('orcid')
                if orcid_el is None:
                    orcid_el = author_el.find('.//{http://arxiv.org/OAI/ArXiv/}orcid')
                if orcid_el is not None and orcid_el.text:
                    orcid_val = orcid_el.text.strip()
                    if 'orcid.org' in orcid_val or orcid_val.startswith('0000'):
                        orcid_list = cast(list[str], data.setdefault('orcid', []))
                        orcid_list.append(orcid_val)
            data['authors'] = authors
            abstract_el = metadata.find('.//abstract') or metadata.find('.//{http://arxiv.org/schemas/atom}summary')
            if abstract_el is None:
                abstract_el = metadata.find('.//summary')
            if abstract_el is not None:
                data['abstract'] = ' '.join(abstract_el.text.split()) if abstract_el.text else ''
            categories = []
            for cat_el in metadata.findall('.//category') + metadata.findall('.//{http://arxiv.org/schemas/atom}category'):
                term = cat_el.get('term') or cat_el.get('subject')
                if term:
                    categories.append(term)
            data['categories'] = categories
            msc = None
            for comm in metadata.findall('.//comment') + metadata.findall('.//{http://arxiv.org/schemas/atom}comment'):
                if comm.text:
                    import re
                    match = re.search(MSC_PATTERN, comm.text)
                    if match:
                        msc = match.group(0)
                        break
            data['msc_class'] = msc
            journal_el = metadata.find('.//journal-ref') or metadata.find('.//{http://arxiv.org/schemas/atom}journal_ref')
            if journal_el is not None and journal_el.text:
                data['journal_ref'] = journal_el.text.strip()
            doi_el = metadata.find('.//doi') or metadata.find('.//{http://arxiv.org/schemas/atom}doi')
            if doi_el is not None and doi_el.text:
                data['doi'] = doi_el.text.strip()
            date_el = metadata.find('.//published') or metadata.find('.//created')
            if date_el is not None and date_el.text:
                data['published'] = date_el.text[:10]
            updated_el = metadata.find('.//updated')
            if updated_el is not None and updated_el.text:
                data['updated'] = updated_el.text[:10]
            data['id'] = cast('Element', identifier).text or ''
            records.append(data)
        return records
    except ET.ParseError as e:
        logger.error(f'OAI XML parse error: {e}')
        return []

class ArxivAdapter:
    """arXiv OAI-PMH bulk access adapter."""
    __slots__ = tuple(('_cache', '_cache_ttl', '_semaphore'))

    def __init__(self) -> None:
        from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
        self._semaphore = get_semaphore(ConcurrencyCategory.ACADEMIC_SEARCH)
        self._cache: dict[str, tuple[float, list[ArxivPaper]]] = {}
        self._cache_ttl = 900.0

    async def harvest(self, query: str | None=None, from_date: str | None=None, max_results: int=MAX_RESULTS) -> ArxivResult:
        """
        Harvest papers from arXiv OAI-PMH.

        Args:
            query: Optional search query (sets resumption token)
            from_date: ISO date for incremental harvesting (e.g., "2024-01-01")
            max_results: Maximum papers to return

        Returns:
            ArxivResult with papers list and metadata
        """
        async with self._semaphore:
            try:
                papers = []
                params = {'verb': 'ListRecords', 'metadataPrefix': 'oai_arXiv'}
                if from_date:
                    params['from'] = from_date
                elif query:
                    return await self._search_mode(query, max_results)
                url = f'{OAI_PMH_ENDPOINT}?{urllib.parse.urlencode(params)}'
                async with asyncio.timeout(REQUEST_TIMEOUT_S):
                    async with session.get(url) as resp:
                        if resp.status_code != 200:
                            return ArxivResult([], f'HTTP {resp.status_code}')
                        content = await resp.read()
                records = _parse_oai_response(content)
                for rec in records[:max_results]:
                    papers.append(ArxivPaper(id=rec.get('id', ''), title=rec.get('title', ''), authors=rec.get('authors', []), abstract=rec.get('abstract', ''), categories=rec.get('categories', []), msc_class=rec.get('msc_class'), journal_ref=rec.get('journal_ref'), orcid=rec.get('orcid', []), published=rec.get('published', ''), updated=rec.get('updated', ''), doi=rec.get('doi'), pdf_url=f"https://arxiv.org/pdf/{rec.get('id', '')}.pdf"))
                return ArxivResult(papers, None, len(papers))
            except TimeoutError:
                return ArxivResult([], 'timeout')
            except Exception as e:
                logger.error(f'arXiv harvest error: {e}')
                return ArxivResult([], str(e))

    async def _search_mode(self, query: str, max_results: int) -> ArxivResult:
        """Use arXiv API for search queries (not OAI-PMH)."""
        try:
            from hledac.universal.fetching.public_fetcher import async_fetch_public_text
            url = 'http://export.arxiv.org/api/query'
            params = {'search_query': f'all:{query}'.replace(' ', '+'), 'max_results': max_results, 'start': 0}
            full_url = f'{url}?{urllib.parse.urlencode(params)}'
            result = await async_fetch_public_text(full_url, timeout_s=REQUEST_TIMEOUT_S, use_stealth=True)
            if not result or not result.text:
                result = await async_fetch_public_text(full_url, timeout_s=REQUEST_TIMEOUT_S, use_stealth=False)
            if not result or not result.text:
                return ArxivResult([], 'no content')
            root = ET.fromstring(result.text)
            ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
            papers = []
            for entry in root.findall('atom:entry', ns)[:max_results]:
                title = cast('Element', entry.find('atom:title', ns))
                _authors: list[str] = []
                for a in entry.findall('atom:author', ns):
                    name_el = a.find('atom:name', ns)
                    if name_el is not None and name_el.text:
                        _authors.append(name_el.text)
                authors = _authors
                summary = cast('Element', entry.find('atom:summary', ns))
                categories = [c.get('term', '') for c in entry.findall('atom:category', ns)]
                orcid_list = []
                for author in entry.findall('atom:author', ns):
                    orcid = author.find('arxiv:orcid', ns)
                    if orcid is not None and orcid.text:
                        orcid_list.append(orcid.text)
                doi_el = entry.find('arxiv:doi', ns)
                doi = doi_el.text if doi_el is not None else None
                published_el = entry.find('atom:published', ns)
                updated_el = entry.find('atom:updated', ns)
                published = (published_el.text or '')[:10] if published_el is not None else ''
                updated = (updated_el.text or '')[:10] if updated_el is not None else ''
                entry_id = cast('Element', entry.find('atom:id', ns))
                paper_id = (entry_id.text or '').split('/')[-1]
                papers.append(ArxivPaper(id=paper_id, title=' '.join((title.text or '').split()), authors=authors, abstract=' '.join((summary.text or '').split()), categories=categories, msc_class=None, journal_ref=None, orcid=orcid_list, published=published, updated=updated, doi=doi, pdf_url=f'https://arxiv.org/pdf/{paper_id}.pdf'))
            return ArxivResult(papers, None, len(papers))
        except Exception as e:
            logger.error(f'arXiv search error: {e}')
            return ArxivResult([], str(e))

    def to_canonical_findings(self, papers: list[ArxivPaper], query: str) -> list[CanonicalFinding]:
        """Convert arXiv papers to CanonicalFinding."""
        findings = []
        for paper in papers:
            import hashlib
            fid = hashlib.sha256(f'{query}\x00{paper.id}\x00arxiv'.encode()).hexdigest()[:16]
            payload = '\n'.join([f'title: {paper.title}', f"authors: {', '.join(paper.authors)}", f"categories: {', '.join(paper.categories)}", f'abstract: {paper.abstract[:1000]}', f"msc_class: {paper.msc_class or 'N/A'}", f"journal_ref: {paper.journal_ref or 'N/A'}", f"doi: {paper.doi or 'N/A'}"])
            findings.append(CanonicalFinding(finding_id=fid, query=query, source_type='arxiv_bulk', confidence=0.8, ts=time.time(), provenance=('arxiv', paper.id, paper.title[:50]), payload_text=payload))
        return findings
_adapter: ArxivAdapter | None = None

async def search_arxiv(query: str, from_date: str | None=None, max_results: int=MAX_RESULTS) -> list[CanonicalFinding]:
    """Search arXiv and return CanonicalFinding list."""
    global _adapter
    if _adapter is None:
        _adapter = ArxivAdapter()
    result = await _adapter.harvest(query=query, from_date=from_date, max_results=max_results)
    if result.error:
        logger.warning(f'arXiv search failed: {result.error}')
        return []
    return _adapter.to_canonical_findings(result.papers, query)
__all__ = ['ArxivAdapter', 'ArxivPaper', 'ArxivResult', 'search_arxiv']