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

# -----------------------------------------------------------------------
# Module-level state — migrated to domain_executors (ISSUE-049)
# -----------------------------------------------------------------------
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
    except ImportError:  # noqa: BLE001
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
    except ImportError:  # noqa: BLE001
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
