"""
discovery/academic/__init__.py — Academic Intelligence Layer

Sprint F259: Academic Intelligence Layer — canonical adapters.

Adapters:
- arxiv_adapter: arXiv OAI-PMH bulk harvesting
- s2orc_adapter: Semantic Scholar S2ORC full text + citation graph
- openalex_adapter: OpenAlex scholarly graph with concept/institution search
- core_adapter: CORE.ac.uk full-text search (requires API key)
- unpaywall_adapter: DOI → free PDF resolution

Env gates:
- HLEDAC_ENABLE_ACADEMIC=1: Enable academic research lane
- CORE_API_KEY: Required for CORE.ac.uk full-text search

M1 8GB: All adapters async, max 3 concurrent per adapter, fail-soft.
"""
import os
from hledac.universal.utils.asyncx import parallel_ok, safe_wait_for
ACADEMIC_ENABLED = os.environ.get('HLEDAC_ENABLE_ACADEMIC', '1').strip().lower() in ('1', 'true', 'yes', 'on')
__all__ = ['ArxivAdapter', 'ArxivPaper', 'ArxivResult', 'search_arxiv', 'S2ORCAdapter', 'S2Paper', 'S2Result', 'CitationEdge', 'search_s2orc', 'OpenAlexAdapter', 'OpenAlexWork', 'OpenAlexInstitution', 'OpenAlexAuthor', 'OpenAlexResult', 'InstitutionNetwork', 'search_openalex', 'get_institution_network', 'FIELD_CONCEPTS', 'COREAdapter', 'COREWork', 'COREPageResult', 'COREResult', 'search_core_fulltext', 'lookup_core_doi', 'UnpaywallAdapter', 'OAPaper', 'UnpaywallResult', 'resolve_doi', 'resolve_multiple_dois', 'find_free_pdf', 'ACADEMIC_ENABLED', 'get_all_adapters', 'search_all_academic']

def _lazy_import(name: str):
    """Lazy import an adapter."""
    if name == 'arxiv':
        from . import arxiv_adapter
        return arxiv_adapter
    elif name == 's2orc':
        from . import s2orc_adapter
        return s2orc_adapter
    elif name == 'openalex':
        from . import openalex_adapter
        return openalex_adapter
    elif name == 'core':
        from . import core_adapter
        return core_adapter
    elif name == 'unpaywall':
        from . import unpaywall_adapter
        return unpaywall_adapter
    raise ValueError(f'Unknown adapter: {name}')

def __getattr__(name: str):
    """Lazy attribute access for all adapters."""
    if name.startswith('Arxiv'):
        mod = _lazy_import('arxiv')
        return getattr(mod, name)
    elif name.startswith('S2') or name.startswith('CitationEdge'):
        mod = _lazy_import('s2orc')
        return getattr(mod, name)
    elif name.startswith('OpenAlex') or name.startswith('Institution'):
        mod = _lazy_import('openalex')
        return getattr(mod, name)
    elif name.startswith('CORE') or name.startswith('COREResult'):
        mod = _lazy_import('core')
        return getattr(mod, name)
    elif name.startswith('Unpaywall') or name.startswith('OAPaper') or name == 'resolve_doi' or (name == 'resolve_multiple_dois') or (name == 'find_free_pdf'):
        mod = _lazy_import('unpaywall')
        return getattr(mod, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

def get_all_adapters() -> dict[str, object]:
    """
    Get all available academic adapters.

    Returns:
        Dict mapping adapter name to adapter module
    """
    adapters = {}
    for name in ['arxiv', 's2orc', 'openalex', 'core', 'unpaywall']:
        try:
            adapters[name] = _lazy_import(name)
        except ImportError as e:
            import logging
            logging.getLogger(__name__).warning(f'Failed to load {name}: {e}')
    return adapters

async def search_all_academic(query: str, max_results_per_source: int=10, timeout_s: float=10.0) -> dict[str, list]:
    """
    Search all academic sources concurrently.

    Args:
        query: Search query
        max_results_per_source: Max results per adapter
        timeout_s: Total timeout for all adapters (F266-U1: 10s = per-adapter 2.5s × 4 adapters)

    Returns:
        Dict mapping source name to CanonicalFinding list
    """
    if not ACADEMIC_ENABLED:
        return {}
    import asyncio
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    results: dict[str, list[CanonicalFinding]] = {}
    from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
    semaphore = get_semaphore(ConcurrencyCategory.ACADEMIC_SEARCH)

    async def run_adapter(name: str, search_func, **kwargs) -> tuple[str, list[CanonicalFinding]]:
        async with semaphore:
            try:
                findings = await safe_wait_for(search_func(query, **kwargs), timeout=2.5, label='academic_adapter')
                return (name, findings)
            except TimeoutError:
                import logging
                logging.getLogger(__name__).warning(f'{name} timed out after 2.5s')
                return (name, [])
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f'{name} failed: {e}')
                return (name, [])
    tasks = []
    try:
        arxiv_mod = _lazy_import('arxiv')
        tasks.append(run_adapter('arxiv', arxiv_mod.search_arxiv, max_results=max_results_per_source))
    except Exception:  # noqa: BLE001
        pass
    try:
        s2orc_mod = _lazy_import('s2orc')
        tasks.append(run_adapter('s2orc', s2orc_mod.search_s2orc, max_results=max_results_per_source, include_citations=True))
    except Exception:  # noqa: BLE001
        pass
    try:
        openalex_mod = _lazy_import('openalex')
        tasks.append(run_adapter('openalex', openalex_mod.search_openalex, max_results=max_results_per_source))
    except Exception:  # noqa: BLE001
        pass
    try:
        core_mod = _lazy_import('core')
        if core_mod.COREAdapter().has_api_key:
            tasks.append(run_adapter('core', core_mod.search_core_fulltext, max_results=max_results_per_source))
    except Exception:  # noqa: BLE001
        pass
    try:
        completed = await safe_wait_for(parallel_ok(*tasks, label='__init__:209'), timeout=timeout_s, label='academic_search_gather')
    except TimeoutError:
        import logging
        logging.getLogger(__name__).warning(f'search_all_academic timed out after {timeout_s}s')
        return results
    for item in completed:
        if isinstance(item, tuple) and len(item) == 2:
            name, findings = item
            results[name] = findings
    return results

async def traverse_academic_citations(seed_dois: list[str], max_hops: int=2) -> dict[str, list]:
    """
    Traverse academic citation graph from seed DOIs.

    Args:
        seed_dois: List of DOI strings to start from
        max_hops: Max citation hops (2 is typical)

    Returns:
        Dict with papers and citation edges
    """
    if not ACADEMIC_ENABLED:
        return {'papers': [], 'edges': []}
    try:
        from . import s2orc_adapter
        adapter = s2orc_adapter.S2ORCAdapter()
        papers = []
        for doi in seed_dois[:10]:
            results = await adapter.search_papers(doi, max_results=1)
            papers.extend(results)
        cited, edges = await adapter.traverse_citation_graph(papers, max_hops=max_hops)
        return {'papers': cited, 'edges': edges}
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f'Citation traversal failed: {e}')
        return {'papers': [], 'edges': []}

async def enrich_with_free_pdfs(dois: list[str]) -> list[dict]:
    """
    Enrich DOI list with free PDF URLs via Unpaywall.

    Args:
        dois: List of DOI strings

    Returns:
        List of dicts with DOI and free PDF URL
    """
    if not ACADEMIC_ENABLED:
        return []
    try:
        from . import unpaywall_adapter
        papers = await unpaywall_adapter.resolve_multiple_dois(dois[:50])
        return [{'doi': p.doi, 'title': p.title, 'free_pdf': p.best_oa_url, 'oa_status': p.oa_status, 'license': p.best_oa_license} for p in papers if p and p.is_oa]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f'PDF enrichment failed: {e}')
        return []