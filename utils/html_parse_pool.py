"""
HTML Parse Pool — M1-safe HTML parsing via asyncio.to_thread.
============================================================================
Sprint F265D: Refactor from ProcessPoolExecutor to asyncio.to_thread.

selectolax je GIL-releasing (Rust) + regex je stdlib = žádné GIL bloky.
→ ThreadPoolExecutor místo ProcessPoolExecutor = žádný IPC overhead,
  žádný spawn overhead, žádný 50-100MB proces overhead na M1 8GB.

M1 8GB constraints:
- max_workers=2 = conservative pro 8GB RAM
- Lazy init = žádné import-time costy
- asyncio.to_thread = macOS QoS-aware, automatic backpressure

G1 FIX: beautifulsoup4 REMOVED — selectolax is primary, regex is final fallback.
No external HTML parser dependencies needed.

Použití:
    from hledac.universal.utils.html_parse_pool import parse_html_links, parse_html_text

    # Pro linking extraction (selectolax-based)
    links = await parse_html_links(html_content)

    # Pro text extraction (selectolax-based)
    text = await parse_html_text(html_content)
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from hledac.universal.utils.domain_executors import get_html_executor

if TYPE_CHECKING:
    pass

_POOL: ThreadPoolExecutor | None = None


def _get_pool() -> ThreadPoolExecutor:
    """Get or create the shared thread pool (lazy initialization).

    ISSUE-049: Now delegates to domain_executors.get_html_executor().
    The module-level singleton is kept for backward compatibility with
    call sites that import _get_pool directly.
    """
    global _POOL
    if _POOL is None:
        _POOL = get_html_executor()
    return _POOL


def _parse_links_worker(html: str) -> list[dict[str, str]]:
    """
    CPU-bound: extract links from HTML.
    selectolax-first (Rust, 10-50× faster), regex final fallback.
    Returns list of {url, title} dicts.

    G1 FIX: Removed beautifulsoup4 fallback.
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
    except ImportError:  # noqa: BLE001
        pass

    # Fallback: regex-only (stdlib)
    import re

    pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>', re.IGNORECASE)
    for match in pattern.finditer(html):
        href = match.group(1)
        title = match.group(2).strip()[:200]
        if href.startswith("http"):
            results.append({"url": href, "title": title})
    return results


def _parse_text_worker(html: str) -> str:
    """
    CPU-bound: extract clean text from HTML.
    selectolax-first (Rust), regex final fallback.
    Returns cleaned text string.

    G1 FIX: Removed beautifulsoup4 fallback — selectolax or regex only.
    """
    # Tier 1: selectolax (fast, Rust-based)
    try:
        from selectolax.parser import HTMLParser as _SelectoLaxParser

        tree = _SelectoLaxParser(html)
        for tag in tree.css("script,style"):
            tag.decompose()
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
    except ImportError:  # noqa: BLE001
        pass

    # Tier 2: regex fallback (stdlib only, no external deps)
    import re

    # Strip HTML tags, keep text
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<noscript[^>]*>.*?</noscript>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def parse_html_links(html: str) -> list[dict[str, str]]:
    """
    Async wrapper: extract links from HTML via shared thread pool.
    Uses asyncio.to_thread for M1-safe I/O offloading.

    Returns:
        List of {url, title} dicts for HTTP links.
    """
    return await asyncio.to_thread(_parse_links_worker, html)


async def parse_html_text(html: str) -> str:
    """
    Async wrapper: extract clean text from HTML via shared thread pool.
    Uses asyncio.to_thread for M1-safe I/O offloading.

    Returns:
        Cleaned text string.
    """
    return await asyncio.to_thread(_parse_text_worker, html)


def get_pool_stats() -> dict:
    """Return pool statistics for telemetry."""
    global _POOL
    _DEFAULT_WORKERS = 2  # M1 8GB conservative default
    if _POOL is None:
        return {"pool_type": "ThreadPool", "pool_initialized": False, "max_workers": _DEFAULT_WORKERS}
    return {
        "pool_type": "ThreadPool",
        "pool_initialized": True,
        "max_workers": getattr(_POOL, "_max_workers", _DEFAULT_WORKERS),
    }
