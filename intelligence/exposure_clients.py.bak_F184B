"""
Sprint F300E: Mixed Exposure Intelligence Clients

Dva transport modely v jednom souboru — toto je záměrný mixed model:

OWN-SESSION (LMDB cache):
  - ShodanClient: vlastní aiohttp session, LMDB ExposureCache, 7 dní TTL
  - CensysClient: vlastní aiohttp session, LMDB ExposureCache, 7 dní TTL
  Bez API key → LMDB-only mode, žádná HTTP volání.
  LMDB single-writer: _DB_EXECUTOR = ThreadPoolExecutor(max_workers=1)

INJECTED-SESSION (file xxhash cache):
  - GitHubCodeSearchClient: session předána zvenku, file cache 1h TTL
  - MalwareBazaarClient: session předána zvenku, file cache 1h TTL
  - GreyNoiseClient: session předána zvenku, file cache 4h TTL
  Throttle: rate-limit per klient, ne per session.

Mixed model NENÍ design flaw — je to správné rozdělení:
  - Own-session klienti: dlouhodobá LMDB cache, API key management internal
  - Injected-session klienti: lightweight, sdílená session z pivot dispatch
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.paths import open_lmdb

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

EXPOSURE_CACHE_ROOT = Path.home() / ".hledac" / "lmdb" / "exposure_cache.lmdb"
_EXPOSURE_CACHE_TTL = 7 * 24 * 60 * 60  # 7 days in seconds

# DB executor pro LMDB write (single-writer, B.3 invariant)
_DB_EXECUTOR = ThreadPoolExecutor(max_workers=1)

# =============================================================================
# LMDB Cache Helpers
# =============================================================================

def _default_serializer(obj: Any) -> bytes:
    """Default JSON serializer pro LMDB cache."""
    return json.dumps(obj).encode("utf-8")


def _default_deserializer(data: bytes) -> Any:
    """Default JSON deserializer pro LMDB cache."""
    return json.loads(data.decode("utf-8"))


class ExposureCache:
    """
    LMDB-backed cache pro exposure klienty.
    Single-writer přes DB_EXECUTOR.
    TTL: 7 dní.
    """

    def __init__(
        self,
        cache_path: Path = EXPOSURE_CACHE_ROOT,
        prefix: str = "exp",
    ) -> None:
        self._cache_path = cache_path
        self._prefix = prefix
        self._env = None
        self._lock = asyncio.Lock()

    def _open_env(self) -> Any:
        """Otevře LMDB env lazy."""
        if self._env is None:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._env = open_lmdb(self._cache_path, map_size=256 * 1024 * 1024)  # 256MB
        return self._env

    def _make_key(self, key: str) -> bytes:
        return f"{self._prefix}:{key}".encode("utf-8")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Synchroní LMDB get. Vrací cached data nebo None.
        Kontroluje TTL.
        """
        try:
            env = self._open_env()
            db_key = self._make_key(key)
            with env.begin() as txn:
                raw = txn.get(db_key)
                if raw is None:
                    return None

            # Parse cached value
            try:
                cached = _default_deserializer(raw)
            except Exception:
                return None

            # Check TTL
            ts = cached.get("_cached_at", 0)
            if time.monotonic() - ts > _EXPOSURE_CACHE_TTL:
                # Expired
                return None

            # Return data without _cached_at
            result = {k: v for k, v in cached.items() if k != "_cached_at"}
            return result

        except Exception as e:
            logger.debug(f"ExposureCache get error for {key}: {e}")
            return None

    def set(self, key: str, data: Dict[str, Any]) -> bool:
        """
        Synchroní LMDB set. Vrací True při úspěchu.
        Single-writer přes DB_EXECUTOR.
        """
        try:
            env = self._open_env()
            db_key = self._make_key(key)

            # Add timestamp
            to_store = dict(data)
            to_store["_cached_at"] = time.monotonic()

            raw = _default_serializer(to_store)

            def _write() -> None:
                with env.begin(write=True) as txn:
                    txn.put(db_key, raw)

            # Submit to single-writer executor
            future = _DB_EXECUTOR.submit(_write)
            future.result(timeout=5.0)
            return True

        except Exception as e:
            logger.debug(f"ExposureCache set error for {key}: {e}")
            return False

    def close(self) -> None:
        if self._env is not None:
            try:
                self._env.close()
            except Exception:
                pass
            self._env = None


# =============================================================================
# ShodanClient
# =============================================================================

class ShodanClient:
    """
    Shodan API client s LMDB cache.

    Cache key: shodan:{ip}
    TTL: 7 dní

    Bez SHODAN_API_KEY: LMDB-only mode, žádné HTTP volání.
    """

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self._api_key = os.environ.get("SHODAN_API_KEY", "")
        self._cache = ExposureCache(prefix="shodan")
        # Optional injected session; None triggers lazy own-session fallback
        self._injected_session: Optional[aiohttp.ClientSession] = session

    async def _get_session(self) -> aiohttp.ClientSession:
        # Use injected session if provided; otherwise fall back to shared plain-TCP surface
        if self._injected_session is not None and not self._injected_session.closed:
            return self._injected_session
        return await async_get_aiohttp_session()

    async def query_host(self, ip: str) -> Optional[Dict[str, Any]]:
        """
        Query Shodan data pro danou IP.

        1. LMDB lookup (b"shodan:" + ip)
        2. Cache hit → return cached data
        3. Cache miss + SHODAN_API_KEY → HTTP GET api.shodan.io
        4. Cache miss + no key → log INFO + return None

        Returns:
            dict s Shodan daty nebo None.
        """
        cache_key = ip

        # Step 1: LMDB lookup
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Shodan cache hit for {ip}")
            return cached

        # Step 2: Bez API key → offline only
        if not self._api_key:
            logger.info(f"Shodan cache miss for {ip}, no API key configured")
            return None

        # Step 3: HTTP fetch
        logger.debug(f"Shodan API call for {ip}")
        try:
            session = await self._get_session()
            url = f"https://api.shodan.io/shodan/host/{ip}"
            params = {"key": self._api_key}

            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Write to cache async přes executor
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        _DB_EXECUTOR,
                        lambda: self._cache.set(cache_key, data),
                    )
                    return data
                elif resp.status == 404:
                    # Host not found in Shodan - cache negative result
                    none_data = {"_not_found": True, "ip": ip}
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        _DB_EXECUTOR,
                        lambda: self._cache.set(cache_key, none_data),
                    )
                    return None
                else:
                    logger.warning(f"Shodan API error: {resp.status}")
                    return None

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Shodan query_host error for {ip}: {e}")
            return None

    async def close(self) -> None:
        # Session lifecycle is caller-owned; only close our cache
        self._cache.close()


# =============================================================================
# CensysClient
# =============================================================================

class CensysClient:
    """
    Censys API client s LMDB cache.

    Cache key: censys:{query_hash}
    TTL: 7 dní

    Bez CENSYS_API_ID/CENSYS_API_SECRET: LMDB-only mode.
    """

    def __init__(
        self,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> None:
        self._api_id = os.environ.get("CENSYS_API_ID", "")
        self._api_secret = os.environ.get("CENSYS_API_SECRET", "")
        self._cache = ExposureCache(prefix="censys")
        # Optional injected session; None triggers lazy own-session fallback
        self._injected_session: Optional[aiohttp.ClientSession] = session

    async def _get_session(self) -> aiohttp.ClientSession:
        # Use injected session if provided; otherwise fall back to shared plain-TCP surface
        if self._injected_session is not None and not self._injected_session.closed:
            return self._injected_session
        return await async_get_aiohttp_session()

    async def search_hosts(self, query: str) -> Optional[List[Dict[str, Any]]]:
        """
        Search Censys hosts.

        1. LMDB lookup (b"censys:" + query)
        2. Cache hit → return cached data
        3. Cache miss + API credentials → HTTP POST to Censys API v2
        4. Cache miss + no credentials → log INFO + return None

        Returns:
            list of host results nebo None.
        """
        import hashlib
        cache_key = hashlib.md5(query.encode()).hexdigest()

        # Step 1: LMDB lookup
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Censys cache hit for query: {query[:50]}")
            return cached.get("results")

        # Step 2: Bez API credentials → offline only
        if not self._api_id or not self._api_secret:
            logger.info(f"Censys cache miss for query, no API credentials configured")
            return None

        # Step 3: HTTP fetch
        logger.debug(f"Censys API call for query: {query[:50]}")
        try:
            session = await self._get_session()
            url = "https://search.censys.io/api/v1/search/ipv4"
            auth = aiohttp.BasicAuth(self._api_id, self._api_secret)
            params = {"q": query}

            async with session.get(url, auth=auth, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    # Write to cache async přes executor
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        _DB_EXECUTOR,
                        lambda: self._cache.set(cache_key, {"results": results}),
                    )
                    return results
                else:
                    logger.warning(f"Censys API error: {resp.status}")
                    return None

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Censys search_hosts error: {e}")
            return None

    async def view_host(self, ip: str) -> Optional[Dict[str, Any]]:
        """
        View Censys host details.

        1. LMDB lookup (censys:view:{ip})
        2. Cache hit → return
        3. Cache miss + API credentials → HTTP GET
        4. Cache miss + no credentials → None
        """
        cache_key = f"view:{ip}"

        # Step 1: LMDB lookup
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Censys cache hit for view: {ip}")
            return cached

        # Step 2: Bez API credentials
        if not self._api_id or not self._api_secret:
            logger.info(f"Censys cache miss for view {ip}, no API credentials configured")
            return None

        # Step 3: HTTP fetch
        logger.debug(f"Censys API view call for {ip}")
        try:
            session = await self._get_session()
            url = f"https://search.censys.io/api/v1/view/ipv4/{ip}"
            auth = aiohttp.BasicAuth(self._api_id, self._api_secret)

            async with session.get(url, auth=auth) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        _DB_EXECUTOR,
                        lambda: self._cache.set(cache_key, data),
                    )
                    return data
                elif resp.status == 404:
                    return None
                else:
                    logger.warning(f"Censys view API error: {resp.status}")
                    return None

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Censys view_host error for {ip}: {e}")
            return None

    async def close(self) -> None:
        # Session lifecycle is caller-owned; only close our cache
        self._cache.close()


# =============================================================================
# Sprint 8TB: GitHub Code Search Client
# =============================================================================


class GitHubCodeSearchClient:
    """
    GitHub Code Search API — CVE PoC + malware samples.

    M1: aiohttp async, 1h xxhash cache, orjson serialization.
    Without GITHUB_TOKEN: 60 req/h unauthenticated limit.
    """

    _RATE_UNAUTH = 60.0  # seconds between requests without token (60/h)
    _RATE_AUTH = 6.0     # seconds between requests with token (10/min)
    _CACHE_TTL = 3600    # 1 hour

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._token = os.environ.get("GITHUB_TOKEN", "")
        self._rate_s = self._RATE_AUTH if self._token else self._RATE_UNAUTH
        self._last_req = 0.0

    async def search_cve(
        self, cve_id: str, session: aiohttp.ClientSession
    ) -> list[dict]:
        """
        Search GitHub code for CVE PoC samples.

        Returns [{repo, url, path, stars}] — max 10 results.
        """
        import xxhash
        import orjson

        key = xxhash.xxh64(f"ghcs_{cve_id}".encode()).hexdigest()
        cp = self._cache_dir / f"{key}.json"
        if cp.exists() and (time.time() - cp.stat().st_mtime < self._CACHE_TTL):
            return orjson.loads(cp.read_bytes())

        await self._throttle()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        params = {
            "q": f"{cve_id} language:Python OR language:C exploit OR poc",
            "per_page": 10,
            "sort": "indexed",
        }
        try:
            async with session.get(
                "https://api.github.com/search/code",
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                if r.status == 403:
                    logger.warning(f"GitHub rate limit hit for {cve_id}")
                    return []
                r.raise_for_status()
                data = await r.json(content_type=None)
        except Exception as e:
            logger.warning(f"GitHubCodeSearch {cve_id}: {e}")
            return []

        items = [
            {
                "repo": i["repository"]["full_name"],
                "url": i["html_url"],
                "path": i["path"],
                "stars": i["repository"].get("stargazers_count", 0),
            }
            for i in data.get("items", [])
        ]
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(orjson.dumps(items))
        return items

    async def close(self) -> None:
        """No-op — kept for API consistency with other clients."""
        pass

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._rate_s:
            await asyncio.sleep(self._rate_s - elapsed)
        self._last_req = time.time()


# =============================================================================
# Sprint 8TB: MalwareBazaar Client
# =============================================================================


class MalwareBazaarClient:
    """
    Abuse.ch MalwareBazaar — hash intel + malware family tags.

    M1: pure aiohttp, 1h cache, orjson.
    """

    _API_URL = "https://mb-api.abuse.ch/api/v1/"
    _RATE_S = 2.0   # 1 request per 2 seconds
    _CACHE_TTL = 3600

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._last_req = 0.0

    async def query_hash(
        self, file_hash: str, session: aiohttp.ClientSession
    ) -> dict:
        """
        Query MalwareBazaar for file hash intelligence.

        Returns raw MB response dict with query_status and data.
        """
        import xxhash
        import orjson

        key = xxhash.xxh64(f"mb_{file_hash}".encode()).hexdigest()
        cp = self._cache_dir / f"{key}.json"
        if cp.exists() and (time.time() - cp.stat().st_mtime < self._CACHE_TTL):
            return orjson.loads(cp.read_bytes())

        await self._throttle()
        try:
            async with session.post(
                self._API_URL,
                json={"query": "get_info", "hash": file_hash},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                r.raise_for_status()
                data = await r.json(content_type=None)
        except Exception as e:
            logger.warning(f"MalwareBazaar {file_hash}: {e}")
            return {"query_status": "error", "data": []}

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(orjson.dumps(data))
        return data

    def extract_iocs(self, mb_resp: dict) -> list[tuple[str, str]]:
        """
        Extract IOCs from MalwareBazaar response.

        Returns [(value, ioc_type)] tuples including:
        - sha256, md5, sha1 hashes
        - imphash
        - malware family tags
        - C2 IPs from vendor_intel
        """
        out: list[tuple[str, str]] = []
        for entry in mb_resp.get("data") or []:
            for h_field, h_type in [
                ("sha256_hash", "sha256"),
                ("md5_hash", "md5"),
                ("sha1_hash", "sha1"),
                ("imphash", "md5"),
            ]:
                if v := entry.get(h_field):
                    out.append((v, h_type))
            # Malware family tags
            for tag in entry.get("tags") or []:
                out.append((str(tag), "malware_family"))
            # C2 IPs from vendor_intel
            for vendor_data in (entry.get("vendor_intel") or {}).values():
                if isinstance(vendor_data, dict) and (ip := vendor_data.get("ip")):
                    out.append((ip, "ipv4"))
        return out

    async def close(self) -> None:
        """No-op — kept for API consistency."""
        pass

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_S:
            await asyncio.sleep(self._RATE_S - elapsed)
        self._last_req = time.time()


# =============================================================================
# GreyNoiseClient — Sprint 8UB: IP classification bez API klíče
# =============================================================================

class GreyNoiseClient:
    """GreyNoise Community API — IP classification bez API klíče.
    https://api.greynoise.io/v3/community/{ip}
    Klasifikuje IP jako: malicious / benign / unknown.
    Enrichment dat: scanner_type, tags, organization."""

    _API_URL = "https://api.greynoise.io/v3/community/{ip}"
    _RATE_S = 1.5
    _CACHE_TTL = 3600 * 4  # 4h

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._last_req = 0.0

    async def classify_ip(
        self,
        ip: str,
        session: aiohttp.ClientSession,
    ) -> dict:
        """Vrátí {"ip", "classification", "name", "link", "noise", "riot"}"""
        import xxhash, orjson

        key = xxhash.xxh64(f"gn_{ip}".encode()).hexdigest()
        cp = self._cache_dir / f"{key}.json"
        if cp.exists() and (time.time() - cp.stat().st_mtime < self._CACHE_TTL):
            return orjson.loads(cp.read_bytes())

        await self._throttle()
        try:
            async with session.get(
                self._API_URL.format(ip=ip),
                timeout=aiohttp.ClientTimeout(total=8),
                headers={"User-Agent": "Mozilla/5.0 (compatible; OSINT-Research)"},
            ) as r:
                if r.status == 404:
                    return {"ip": ip, "classification": "unknown"}
                if r.status == 429:
                    logger.debug(f"GreyNoise rate limit: {ip}")
                    return {"ip": ip, "classification": "rate_limited"}
                r.raise_for_status()
                data = await r.json(content_type=None)
        except Exception as e:
            logger.debug(f"GreyNoise {ip}: {e}")
            return {"ip": ip, "classification": "error"}

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(orjson.dumps(data))
        return data

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_S:
            await asyncio.sleep(self._RATE_S - elapsed)
        self._last_req = time.time()

