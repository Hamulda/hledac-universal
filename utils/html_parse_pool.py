"""
HTML Parse Pool — M1-safe CPU-bound HTML parsing via ProcessPoolExecutor.
============================================================================
Sprint 8VE D.2: Centralizovaný proces pool pro HTML parsing.

M1 8GB constraints:
- macOS spawn method = jediný bezpečný pro M1 (fork → Metal crash)
- max_workers=2 = conservative pro 8GB RAM
- Lazy init = žádné import-time costy

Použití:
    from utils.html_parse_pool import parse_html_links, parse_html_text

    # Pro linking extraction (selectolax-based)
    links = await parse_html_links(html_content)

    # Pro text extraction (selectolax-based)
    text = await parse_html_text(html_content)
"""

from __future__ import annotations

import asyncio
import multiprocessing as _mp
import resource as _resource
from concurrent.futures import ProcessPoolExecutor as _PPE
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# -----------------------------------------------------------------------
# Module-level state
# -----------------------------------------------------------------------
_POOL: _PPE | None = None
_MAX_WORKERS: int = 2  # M1 8GB conservative


def _get_spawn_context():
    """Get spawn context for M1-safe process creation."""
    return _mp.get_context("spawn")


def _init_worker() -> None:
    """Per-worker initialization — minimize memory footprint."""
    # M1: limit memory per worker to prevent OOM
    try:
        _, hard = _resource.getrlimit(_resource.RLIMIT_RSS)
        # 512 MB per worker — keeps 2 workers under 1GB total
        _resource.setrlimit(_resource.RLIMIT_RSS, (512 * 1024 * 1024, hard))
    except (OSError, ValueError):
        pass  # Not all platforms support this


# -----------------------------------------------------------------------
# CPU-bound parse functions (run in worker processes)
# -----------------------------------------------------------------------

def _parse_links_worker(html: str) -> list[dict[str, str]]:
    """
    CPU-bound: extract links from HTML.
    selectolax-first (Rust, 10-50× faster than bs4), bs4 fallback.
    Returns list of {url, title} dicts.
    """
    results: list[dict[str, str]] = []
    try:
        from selectolax.parser import HTMLParser as _SelectoLaxParser

        tree = _SelectoLaxParser(html)
        for node in tree.css("a[href]"):
            href_raw = node.attributes.get("href", "")
            if not isinstance(href_raw, str):
                continue
            href = href_raw
            text = node.text(strip=True)
            if href.startswith("http"):
                results.append({"url": href, "title": text[:200]})
        return results
    except ImportError:
        pass

    # Fallback: BeautifulSoup (GIL-bound, slower)
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if isinstance(href, str) and href.startswith("http"):
            results.append({"url": href, "title": a.get_text(strip=True)[:200]})
    return results


def _parse_text_worker(html: str) -> str:
    """
    CPU-bound: extract clean text from HTML.
    selectolax-first (Rust), bs4 fallback, regex final fallback.
    Returns cleaned text string.
    """
    # Tier 1: selectolax (fast, Rust-based)
    try:
        from selectolax.parser import HTMLParser as _SelectoLaxParser

        tree = _SelectoLaxParser(html)
        # Remove script/style tags first
        for tag in tree.css("script,style"):
            tag.decompose()
        # Extract text from body or whole tree
        body = tree.css_first("body")
        if body is not None:
            text = body.text(separator=" ", strip=True)
        else:
            text = tree.text(separator=" ", strip=True)
        # Collapse whitespace
        import re

        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text
    except ImportError:
        pass

    # Tier 2: BeautifulSoup (slower, GIL-bound)
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    body = soup.body
    if body:
        text = body.get_text(separator=" ", strip=True)
    else:
        text = soup.get_text(separator=" ", strip=True)
    import re

    text = re.sub(r"\s+", " ", text).strip()
    if text:
        return text

    # Tier 3: regex fallback (stdlib, no deps)
    import re

    # Strip HTML tags, keep text
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# -----------------------------------------------------------------------
# Public async API
# -----------------------------------------------------------------------

async def parse_html_links(html: str) -> list[dict[str, str]]:
    """
    Async wrapper: extract links from HTML via process pool.

    Returns:
        List of {url, title} dicts for HTTP links.
    """
    global _POOL

    if _POOL is None:
        ctx = _get_spawn_context()
        _POOL = _PPE(
            max_workers=_MAX_WORKERS,
            mp_context=ctx,
            initializer=_init_worker,
        )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_POOL, _parse_links_worker, html)


async def parse_html_text(html: str) -> str:
    """
    Async wrapper: extract clean text from HTML via process pool.

    Returns:
        Cleaned text string.
    """
    global _POOL

    if _POOL is None:
        ctx = _get_spawn_context()
        _POOL = _PPE(
            max_workers=_MAX_WORKERS,
            mp_context=ctx,
            initializer=_init_worker,
        )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_POOL, _parse_text_worker, html)


def get_pool_stats() -> dict:
    """Return pool statistics for telemetry."""
    global _POOL
    if _POOL is None:
        return {"pool_initialized": False, "max_workers": _MAX_WORKERS}
    return {
        "pool_initialized": True,
        "max_workers": _MAX_WORKERS,
    }
