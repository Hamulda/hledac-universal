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

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Env gate
# ---------------------------------------------------------------------------

ACADEMIC_ENABLED = os.environ.get("HLEDAC_ENABLE_ACADEMIC", "0").strip().lower() in (
    "1", "true", "yes", "on"
)

# ---------------------------------------------------------------------------
# Adapter exports (lazy-loaded)
# ---------------------------------------------------------------------------

__all__ = [
    # arXiv OAI-PMH bulk access
    "ArxivAdapter",
    "ArxivPaper",
    "ArxivResult",
    "search_arxiv",
    # S2ORC / Semantic Scholar
    "S2ORCAdapter",
    "S2Paper",
    "S2Result",
    "CitationEdge",
    "search_s2orc",
    # OpenAlex scholarly graph
    "OpenAlexAdapter",
    "OpenAlexWork",
    "OpenAlexInstitution",
    "OpenAlexAuthor",
    "OpenAlexResult",
    "InstitutionNetwork",
    "search_openalex",
    "get_institution_network",
    "FIELD_CONCEPTS",
    # CORE.ac.uk full-text
    "COREAdapter",
    "COREWork",
    "COREPageResult",
    "COREResult",
    "search_core_fulltext",
    "lookup_core_doi",
    # Unpaywall / OA
    "UnpaywallAdapter",
    "OAPaper",
    "UnpaywallResult",
    "resolve_doi",
    "resolve_multiple_dois",
    "find_free_pdf",
    # Module-level exports
    "ACADEMIC_ENABLED",
    "get_all_adapters",
    "search_all_academic",
]

# ---------------------------------------------------------------------------
# Lazy imports (avoid circular dependencies)
# ---------------------------------------------------------------------------

def _lazy_import(name: str):
    """Lazy import an adapter."""
    if name == "arxiv":
        from . import arxiv_adapter
        return arxiv_adapter
    elif name == "s2orc":
        from . import s2orc_adapter
        return s2orc_adapter
    elif name == "openalex":
        from . import openalex_adapter
        return openalex_adapter
    elif name == "core":
        from . import core_adapter
        return core_adapter
    elif name == "unpaywall":
        from . import unpaywall_adapter
        return unpaywall_adapter
    raise ValueError(f"Unknown adapter: {name}")


def __getattr__(name: str):
    """Lazy attribute access for all adapters."""
    if name.startswith("Arxiv"):
        mod = _lazy_import("arxiv")
        return getattr(mod, name)
    elif name.startswith("S2") or name.startswith("CitationEdge"):
        mod = _lazy_import("s2orc")
        return getattr(mod, name)
    elif name.startswith("OpenAlex") or name.startswith("Institution"):
        mod = _lazy_import("openalex")
        return getattr(mod, name)
    elif name.startswith("CORE") or name.startswith("COREResult"):
        mod = _lazy_import("core")
        return getattr(mod, name)
    elif name.startswith("Unpaywall") or name.startswith("OAPaper") or name == "resolve_doi" or name == "resolve_multiple_dois" or name == "find_free_pdf":
        mod = _lazy_import("unpaywall")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_all_adapters() -> dict[str, object]:
    """
    Get all available academic adapters.

    Returns:
        Dict mapping adapter name to adapter module
    """
    adapters = {}
    for name in ["arxiv", "s2orc", "openalex", "core", "unpaywall"]:
        try:
            adapters[name] = _lazy_import(name)
        except ImportError as e:
            import logging
            logging.getLogger(__name__).warning(f"Failed to load {name}: {e}")
    return adapters


# ---------------------------------------------------------------------------
# Orchestrator: search all academic sources
# ---------------------------------------------------------------------------

async def search_all_academic(
    query: str,
    max_results_per_source: int = 10,
) -> dict[str, list]:
    """
    Search all academic sources concurrently.

    Args:
        query: Search query
        max_results_per_source: Max results per adapter

    Returns:
        Dict mapping source name to CanonicalFinding list
    """
    if not ACADEMIC_ENABLED:
        return {}

    import asyncio
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    results: dict[str, list[CanonicalFinding]] = {}
    semaphore = asyncio.Semaphore(5)  # Max 5 concurrent adapter calls

    async def run_adapter(name: str, search_func, **kwargs) -> tuple[str, list[CanonicalFinding]]:
        async with semaphore:
            try:
                findings = await search_func(query, **kwargs)
                return name, findings
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"{name} failed: {e}")
                return name, []

    # Import and run each adapter
    tasks = []

    # arXiv
    try:
        arxiv_mod = _lazy_import("arxiv")
        tasks.append(run_adapter("arxiv", arxiv_mod.search_arxiv, max_results=max_results_per_source))
    except Exception:
        pass

    # S2ORC
    try:
        s2orc_mod = _lazy_import("s2orc")
        tasks.append(run_adapter("s2orc", s2orc_mod.search_s2orc, max_results=max_results_per_source, include_citations=True))
    except Exception:
        pass

    # OpenAlex
    try:
        openalex_mod = _lazy_import("openalex")
        tasks.append(run_adapter("openalex", openalex_mod.search_openalex, max_results=max_results_per_source))
    except Exception:
        pass

    # CORE (requires API key)
    try:
        core_mod = _lazy_import("core")
        if core_mod.COREAdapter().has_api_key:
            tasks.append(run_adapter("core", core_mod.search_core_fulltext, max_results=max_results_per_source))
    except Exception:
        pass

    # Run all
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for item in completed:
        if isinstance(item, tuple) and len(item) == 2:
            name, findings = item
            results[name] = findings

    return results


# ---------------------------------------------------------------------------
# Citation graph traversal helper
# ---------------------------------------------------------------------------

async def traverse_academic_citations(
    seed_dois: list[str],
    max_hops: int = 2,
) -> dict[str, list]:
    """
    Traverse academic citation graph from seed DOIs.

    Args:
        seed_dois: List of DOI strings to start from
        max_hops: Max citation hops (2 is typical)

    Returns:
        Dict with papers and citation edges
    """
    if not ACADEMIC_ENABLED:
        return {"papers": [], "edges": []}

    try:
        from . import s2orc_adapter
        adapter = s2orc_adapter.S2ORCAdapter()

        # Convert DOIs to S2Paper
        papers = []
        for doi in seed_dois[:10]:
            results = await adapter.search_papers(doi, max_results=1)
            papers.extend(results)

        # Traverse
        cited, edges = await adapter.traverse_citation_graph(papers, max_hops=max_hops)

        return {
            "papers": cited,
            "edges": edges,
        }

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Citation traversal failed: {e}")
        return {"papers": [], "edges": []}


# ---------------------------------------------------------------------------
# DOI -> free PDF helper
# ---------------------------------------------------------------------------

async def enrich_with_free_pdfs(
    dois: list[str],
) -> list[dict]:
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

        return [
            {
                "doi": p.doi,
                "title": p.title,
                "free_pdf": p.best_oa_url,
                "oa_status": p.oa_status,
                "license": p.best_oa_license,
            }
            for p in papers
            if p and p.is_oa
        ]

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"PDF enrichment failed: {e}")
        return []