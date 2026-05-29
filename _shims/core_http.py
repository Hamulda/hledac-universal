"""
Shim for hledac.core.http — bypasses hledac.core.__init__.py chain.
Provides fetch_json and safe_fetch as simple wrappers using httpx.
"""
import logging

import httpx

logger = logging.getLogger(__name__)

async def fetch_json(url: str, timeout: float = 30.0, **kwargs):
    """Simple async JSON fetcher — replaces broken sibling http.py."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

async def safe_fetch(url: str, timeout: float = 30.0, **kwargs):
    """Simple async text fetcher — returns None on failure instead of raising."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, **kwargs)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning(f"safe_fetch failed for {url}: {e}")
        return None

__all__ = ["fetch_json", "safe_fetch"]
