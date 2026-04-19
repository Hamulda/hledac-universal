"""Certificate Transparency log scanner (crt.sh) with local cache."""
import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

# Canonical timeout constants for CT scan — use with asyncio.timeout()
_CT_CONNECT_TIMEOUT_S: float = 10.0
_CT_READ_TIMEOUT_S: float = 15.0

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    logger.warning("[CT] aiohttp not installed, external CT scanning disabled")


class _CTLogScanner:
    """Scan crt.sh for subdomains and certificates, with local SQLite cache.

    NON-HOT-PATH surface — owns its session lifecycle when used standalone.
    Supports shared-session injection for connection pooling when called from
    a coordinator that manages session lifetime externally."""

    CACHE_DIR = Path.home() / ".hledac" / "ct_cache"
    CACHE_DB = CACHE_DIR / "ct_logs.db"

    def __init__(self, allow_external: bool = False, cache_ttl_days: int = 30):
        self.allow_external = allow_external
        self.cache_ttl_days = cache_ttl_days
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize SQLite cache table."""
        with sqlite3.connect(self.CACHE_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ct_cache (
                    domain TEXT PRIMARY KEY,
                    subdomains TEXT,
                    fetched_at REAL
                )
            """)
            conn.commit()

    async def get_subdomains(
        self,
        domain: str,
        *,
        async_session: Optional["aiohttp.ClientSession"] = None
    ) -> List[str]:
        """Get subdomains for a domain, using cache first.

        Args:
            domain: Domain to scan
            async_session: Optional shared aiohttp session for connection pooling.
                          If not provided, creates a per-call session (legacy behavior).
        """
        # 1. Check cache
        cached = self._get_cached(domain)
        if cached is not None:
            logger.debug(f"[CT] Cache hit for {domain}: {len(cached)} subdomains")
            return cached

        # 2. If external not allowed, return empty
        if not self.allow_external:
            return []

        # 3. Fetch from crt.sh
        if not AIOHTTP_AVAILABLE:
            logger.warning("[CT] aiohttp not available, cannot fetch from crt.sh")
            return []

        # Sprint 8I: Support shared session for connection pooling
        # aiohttp is guaranteed to be available here (AIOHTTP_AVAILABLE=True)
        import aiohttp as _aiohttp

        async def _fetch_with_session(session: _aiohttp.ClientSession) -> List[str]:
            url = f"https://crt.sh/?q=%.{domain}&output=json"
            async with session.get(
                url,
                timeout=_aiohttp.ClientTimeout(
                    connect=_CT_CONNECT_TIMEOUT_S,
                    sock_read=_CT_READ_TIMEOUT_S,
                ),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
            return data

        try:
            if async_session is not None:
                data = await _fetch_with_session(async_session)
            else:
                async with _aiohttp.ClientSession() as session:
                    data = await _fetch_with_session(session)

            # Parse subdomains
            subdomains: Set[str] = set()
            for entry in data[:100]:  # bounded
                # crt.sh returns JSON objects with name_value field
                entry_dict: dict = dict(entry) if isinstance(entry, dict) else {}
                name = entry_dict.get('name_value', '')
                if name.endswith(f".{domain}"):
                    subdomains.add(name)
                # Also handle multi-line entries
                if '\n' in name:
                    for n in name.split('\n'):
                        if n.endswith(f".{domain}"):
                            subdomains.add(n)

            result = list(subdomains)[:200]  # bounded
            # Save to cache
            self._save_to_cache(domain, result)
            return result

        except asyncio.TimeoutError:
            logger.warning(f"[CT] Timeout for {domain}")
            return []
        except Exception as e:
            logger.warning(f"[CT] Error for {domain}: {e}")
            return []

    def _get_cached(self, domain: str) -> Optional[List[str]]:
        """Return cached subdomains if fresh enough."""
        import time
        now = time.time()
        ttl_seconds = self.cache_ttl_days * 86400

        with sqlite3.connect(self.CACHE_DB) as conn:
            row = conn.execute(
                "SELECT subdomains, fetched_at FROM ct_cache WHERE domain = ?",
                (domain,)
            ).fetchone()
            if row and (now - row[1]) < ttl_seconds:
                return json.loads(row[0])
        return None

    def _save_to_cache(self, domain: str, subdomains: List[str]):
        """Store subdomains in cache."""
        import time
        with sqlite3.connect(self.CACHE_DB) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ct_cache (domain, subdomains, fetched_at) VALUES (?, ?, ?)",
                (domain, json.dumps(subdomains), time.time())
            )
            conn.commit()
