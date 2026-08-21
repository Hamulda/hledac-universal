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

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

try:
    import orjson as _json
except ImportError:
    import json as _json

# S-01: Use UnifiedLMDB via get_unified_lmdb + SubDB
from hledac.universal._core.lmdb_unified import SubDB, get_unified_lmdb
from hledac.universal.recon._http_helpers import get_intelligence_session
from hledac.universal.utils.asyncx import parallel
from hledac.universal.utils.domain_executors import get_exposure_db_executor
from hledac.universal.utils.msgspec_json import decode, encode

logger = logging.getLogger(__name__)


async def _aclose_stream(stream) -> None:
    """P15: Close aiohttp AsyncBufferedReader on early break."""
    try:
        await stream.aclose()
    except Exception:  # noqa: BLE001
        pass


EXPOSURE_CACHE_ROOT = Path.home() / ".hledac" / "lmdb" / "exposure_cache.lmdb"
_EXPOSURE_CACHE_TTL = 7 * 24 * 60 * 60
# ISSUE #016: NVD data changes daily — 24h TTL instead of 6h
_CVE_CACHE_TTL = 24 * 60 * 60

# ISSUE-027: Replaced direct ThreadPoolExecutor with domain_executors registry.
# Single-writer LMDB executor — MUST stay single-threaded for LMDB consistency.
_DB_EXECUTOR = get_exposure_db_executor()


def _default_serializer(obj: Any):
    """Default JSON serializer pro LMDB cache — orjson returns bytes directly."""
    return _json.dumps(obj)


def _default_deserializer(data: bytes) -> Any:
    """Default JSON deserializer pro LMDB cache."""
    return _json.loads(data)


class ExposureCache:
    """
    LMDB-backed cache pro exposure klienty.
    Single-writer přes DB_EXECUTOR.
    TTL: 7 dní.
    """

    __slots__ = ("_cache_path", "_env", "_lock", "_prefix")

    # F320-REFACTOR: Use canonical close() from _patterns
    from hledac.universal.utils._patterns import make_close_method

    close = make_close_method("_env")

    def __init__(self, cache_path: Path = EXPOSURE_CACHE_ROOT, prefix: str = "exp") -> None:
        self._cache_path = cache_path
        self._prefix = prefix
        self._env = None
        self._lock = asyncio.Lock()

    def _open_env(self) -> tuple[Any, Any]:
        """Otevře UnifiedLMDB env + sub-db lazy. Returns (env, sub_db)."""
        if self._env is None:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            # S-01: Use UnifiedLMDB singleton instead of separate env
            _store = get_unified_lmdb()
            self._env = _store.env()
            self._sub_db = _store.open_db(SubDB.EXPOSURE_DATA)
        return self._env, self._sub_db

    def _make_key(self, key: str) -> bytes:
        return f"{self._prefix}:{key}".encode()

    def get(self, key: str) -> dict[str, Any] | None:
        """
        Synchroní LMDB get. Vrací cached data nebo None.
        Kontroluje TTL.
        """
        try:
            env, sub_db = self._open_env()
            db_key = self._make_key(key)
            with env.begin(db=sub_db) as txn:
                raw = txn.get(db_key)
                if raw is None:
                    return None
            try:
                cached = _default_deserializer(raw)
            except Exception:
                return None
            ts = cached.get("_cached_at", 0)
            if time.monotonic() - ts > _EXPOSURE_CACHE_TTL:
                return None
            result = {k: v for k, v in cached.items() if k != "_cached_at"}
            return result
        except Exception as e:
            logger.debug(f"ExposureCache get error for {key}: {e}")
            return None

    def set(self, key: str, data: dict[str, Any]) -> bool:
        """
        Synchroní LMDB set. Vrací True při úspěchu.
        Single-writer přes DB_EXECUTOR.
        """
        try:
            env, sub_db = self._open_env()
            db_key = self._make_key(key)
            to_store = dict(data)
            to_store["_cached_at"] = time.monotonic()
            raw = _default_serializer(to_store)

            def _write() -> None:
                with env.begin(write=True, db=sub_db) as txn:
                    txn.put(db_key, raw)

            future = _DB_EXECUTOR.submit(_write)
            future.result(timeout=5.0)
            return True
        except Exception as e:
            logger.debug(f"ExposureCache set error for {key}: {e}")
            return False


class ShodanClient:
    """
    Shodan API client s LMDB cache.

    Cache key: shodan:{ip}
    TTL: 7 dní

    Bez SHODAN_API_KEY: LMDB-only mode, žádné HTTP volání.
    """

    __slots__ = ("_api_key", "_cache", "_injected_session")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self._api_key = os.environ.get("SHODAN_API_KEY", "")
        self._cache = ExposureCache(prefix="shodan")
        self._injected_session: httpx.AsyncClient | None = session

    async def _get_session(self) -> httpx.AsyncClient:
        if self._injected_session is not None and (not self._injected_session.is_closed):
            return self._injected_session
        return await get_intelligence_session()

    async def query_host(self, ip: str) -> dict[str, Any] | None:
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
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Shodan cache hit for {ip}")
            return cached
        if not self._api_key:
            logger.info(f"Shodan cache miss for {ip}, no API key configured")
            return None
        logger.debug(f"Shodan API call for {ip}")
        try:
            session = await self._get_session()
            url = f"https://api.shodan.io/shodan/host/{ip}"
            params = {"key": self._api_key}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(_DB_EXECUTOR, lambda: self._cache.set(cache_key, data))
                    return data
                elif resp.status == 404:
                    none_data = {"_not_found": True, "ip": ip}
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(_DB_EXECUTOR, lambda: self._cache.set(cache_key, none_data))
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
        self._cache.close()


class CensysClient:
    """
    Censys API client s LMDB cache.

    Cache key: censys:{query_hash}
    TTL: 7 dní

    Bez CENSYS_API_ID/CENSYS_API_SECRET: LMDB-only mode.
    """

    __slots__ = ("_api_id", "_api_secret", "_cache", "_injected_session")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self._api_id = os.environ.get("CENSYS_API_ID", "")
        self._api_secret = os.environ.get("CENSYS_API_SECRET", "")
        self._cache = ExposureCache(prefix="censys")
        self._injected_session: httpx.AsyncClient | None = session

    async def _get_session(self) -> httpx.AsyncClient:
        if self._injected_session is not None and (not self._injected_session.is_closed):
            return self._injected_session
        return await get_intelligence_session()

    async def search_hosts(self, query: str) -> list[dict[str, Any]] | None:
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
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Censys cache hit for query: {query[:50]}")
            return cached.get("results")
        if not self._api_id or not self._api_secret:
            logger.info("Censys cache miss for query, no API credentials configured")
            return None
        logger.debug(f"Censys API call for query: {query[:50]}")
        try:
            session = await self._get_session()
            url = "https://search.censys.io/api/v1/search/ipv4"
            auth = httpx.BasicAuth(self._api_id, self._api_secret)
            params = {"q": query}
            async with session.get(url, auth=auth, params=params) as resp:
                if resp.status_code == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(_DB_EXECUTOR, lambda: self._cache.set(cache_key, {"results": results}))
                    return results
                else:
                    logger.warning(f"Censys API error: {resp.status}")
                    return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Censys search_hosts error: {e}")
            return None

    async def view_host(self, ip: str) -> dict[str, Any] | None:
        """
        View Censys host details.

        1. LMDB lookup (censys:view:{ip})
        2. Cache hit → return
        3. Cache miss + API credentials → HTTP GET
        4. Cache miss + no credentials → None
        """
        cache_key = f"view:{ip}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Censys cache hit for view: {ip}")
            return cached
        if not self._api_id or not self._api_secret:
            logger.info(f"Censys cache miss for view {ip}, no API credentials configured")
            return None
        logger.debug(f"Censys API view call for {ip}")
        try:
            session = await self._get_session()
            url = f"https://search.censys.io/api/v1/view/ipv4/{ip}"
            auth = httpx.BasicAuth(self._api_id, self._api_secret)
            async with session.get(url, auth=auth) as resp:
                if resp.status_code == 200:
                    data = await resp.json()
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(_DB_EXECUTOR, lambda: self._cache.set(cache_key, data))
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
        self._cache.close()


class GitHubCodeSearchClient:
    """
    GitHub Code Search API — CVE PoC + malware samples.

    M1: aiohttp async, 1h xxhash cache, orjson serialization.
    Without GITHUB_TOKEN: 60 req/h unauthenticated limit.
    """

    _RATE_UNAUTH = 60.0
    _RATE_AUTH = 6.0
    _CACHE_TTL = 3600
    __slots__ = ("_cache_dir", "_last_req", "_rate_s", "_token")

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._token = os.environ.get("GITHUB_TOKEN", "")
        self._rate_s = self._RATE_AUTH if self._token else self._RATE_UNAUTH
        self._last_req = 0.0

    async def search_cve(self, cve_id: str, session: httpx.AsyncClient) -> list[dict]:
        """
        Search GitHub code for CVE PoC samples.

        Returns [{repo, url, path, stars}] — max 10 results.
        """
        import xxhash

        key = xxhash.xxh3_64(f"ghcs_{cve_id}".encode()).hexdigest()
        zst_path = self._cache_dir / f"{key}.json.zst"
        json_path = self._cache_dir / f"{key}.json"
        if zst_path.exists() and time.time() - zst_path.stat().st_mtime < self._CACHE_TTL:
            try:
                import compression.zstd as _zstd

                return decode(_zstd.decompress(zst_path.read_bytes()))
            except ImportError, Exception:  # noqa: BLE001
                pass
        if json_path.exists() and time.time() - json_path.stat().st_mtime < self._CACHE_TTL:
            return decode(json_path.read_bytes())
        await self._throttle()
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        params = {"q": f"{cve_id} language:Python OR language:C exploit OR poc", "per_page": 10, "sort": "indexed"}
        try:
            async with session.get(
                "https://api.github.com/search/code", params=params, headers=headers, timeout=httpx.Timeout(total=12)
            ) as r:
                if r.status_code == 403:
                    logger.warning(f"GitHub rate limit hit for {cve_id}")
                    return []
                r.raise_for_status()
                data = await r.json()
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
        zst_path.write_bytes(_zstd.compress(encode(items)))
        return items

    async def close(self) -> None:
        """No-op — kept for API consistency with other clients."""

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._rate_s:
            await asyncio.sleep(self._rate_s - elapsed)
        self._last_req = time.time()


class MalwareBazaarClient:
    """
    Abuse.ch MalwareBazaar — hash intel + malware family tags.

    M1: pure aiohttp, 1h cache, orjson.
    """

    _API_URL = "https://mb-api.abuse.ch/api/v1/"
    _RATE_S = 2.0
    _CACHE_TTL = 3600
    __slots__ = ("_cache_dir", "_last_req")

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._last_req = 0.0

    async def query_hash(self, file_hash: str, session: httpx.AsyncClient) -> dict:
        """
        Query MalwareBazaar for file hash intelligence.

        Returns raw MB response dict with query_status and data.
        """
        import xxhash

        key = xxhash.xxh3_64(f"mb_{file_hash}".encode()).hexdigest()
        zst_path = self._cache_dir / f"{key}.json.zst"
        json_path = self._cache_dir / f"{key}.json"
        if zst_path.exists() and time.time() - zst_path.stat().st_mtime < self._CACHE_TTL:
            try:
                import compression.zstd as _zstd

                return decode(_zstd.decompress(zst_path.read_bytes()))
            except ImportError, Exception:  # noqa: BLE001
                pass
        if json_path.exists() and time.time() - json_path.stat().st_mtime < self._CACHE_TTL:
            return decode(json_path.read_bytes())
        await self._throttle()
        try:
            async with session.post(
                self._API_URL, json={"query": "get_info", "hash": file_hash}, timeout=httpx.Timeout(total=12)
            ) as r:
                r.raise_for_status()
                data = await r.json()
        except Exception as e:
            logger.warning(f"MalwareBazaar {file_hash}: {e}")
            return {"query_status": "error", "data": []}
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        zst_path.write_bytes(_zstd.compress(encode(data)))
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
            for tag in entry.get("tags") or []:
                out.append((str(tag), "malware_family"))
            for vendor_data in (entry.get("vendor_intel") or {}).values():
                if isinstance(vendor_data, dict) and (ip := vendor_data.get("ip")):
                    out.append((ip, "ipv4"))
        return out

    async def close(self) -> None:
        """No-op — kept for API consistency."""

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_S:
            await asyncio.sleep(self._RATE_S - elapsed)
        self._last_req = time.time()


class GreyNoiseClient:
    """GreyNoise Community API — IP classification bez API klíče.
    https://api.greynoise.io/v3/community/{ip}
    Klasifikuje IP jako: malicious / benign / unknown.
    Enrichment dat: scanner_type, tags, organization."""

    _API_URL = "https://api.greynoise.io/v3/community/{ip}"
    _RATE_S = 1.5
    _CACHE_TTL = 3600 * 4
    __slots__ = ("_cache_dir", "_last_req")

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._last_req = 0.0

    async def classify_ip(self, ip: str, session: httpx.AsyncClient) -> dict:
        """Vrátí {"ip", "classification", "name", "link", "noise", "riot"}"""
        import xxhash

        key = xxhash.xxh3_64(f"gn_{ip}".encode()).hexdigest()
        zst_path = self._cache_dir / f"{key}.json.zst"
        json_path = self._cache_dir / f"{key}.json"
        if zst_path.exists() and time.time() - zst_path.stat().st_mtime < self._CACHE_TTL:
            try:
                import compression.zstd as _zstd

                return decode(_zstd.decompress(zst_path.read_bytes()))
            except ImportError, Exception:  # noqa: BLE001
                pass
        if json_path.exists() and time.time() - json_path.stat().st_mtime < self._CACHE_TTL:
            return decode(json_path.read_bytes())
        await self._throttle()
        try:
            async with session.get(
                self._API_URL.format(ip=ip),
                timeout=httpx.Timeout(total=8),
                headers={"User-Agent": "Mozilla/5.0 (compatible; OSINT-Research)"},
            ) as r:
                if r.status == 404:
                    return {"ip": ip, "classification": "unknown"}
                if r.status == 429:
                    logger.debug(f"GreyNoise rate limit: {ip}")
                    return {"ip": ip, "classification": "rate_limited"}
                r.raise_for_status()
                data = await r.json()
        except Exception as e:
            logger.debug(f"GreyNoise {ip}: {e}")
            return {"ip": ip, "classification": "error"}
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        zst_path.write_bytes(_zstd.compress(encode(data)))
        return data

    async def _throttle(self) -> None:
        elapsed = time.time() - self._last_req
        if elapsed < self._RATE_S:
            await asyncio.sleep(self._RATE_S - elapsed)
        self._last_req = time.time()


class CVIntelligenceClient:
    """
    CVE/Vulnerability Intelligence via OSV.dev + NVD API 2.0 + EPSS.

    OSV.dev Batch API (priority):
      POST https://api.osv.dev/v1/querybatch
      Streaming response, max 200 CVEs, batches of 20.

    NVD API 2.0 fallback (if OSV returns 0 results):
      GET https://services.nvd.nist.gov/rest/json/cves/2.0
      Rate limited: Rust NvdRateLimiter token bucket (5 req/30s bez API key,
      50 req/30s s API key) — ISSUE #016.

    EPSS enrichment:
      GET https://api.first.org/data/v1/epss?cve={cve_id}
      Adds epss_score, percentile; EPSS >0.7 → IMMEDIATE_ACTION flag.

    M1 invariants:
      - get_intelligence_session() for HTTP (shared aiohttp session)
      - LMDB cache with 24h TTL (NVD data changes daily)
      - AsyncIterator[dict] for streaming results
      - No asyncio.run() inside async functions
      - Generator pattern with chunk processing
      - Rust NvdRateLimiter (crossbeam-channel, ~zero RAM, no GIL)
    """

    _OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
    _NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    _EPSS_URL = "https://api.first.org/data/v1/epss"
    _MAX_CVES = 200
    _BATCH_SIZE = 20
    _NVD_RATE_LIMIT = 5
    _NVD_WINDOW_S = 30.0
    _NVD_ACQUIRE_TIMEOUT = 35.0  # ISSUE #016: slightly > window to allow refill
    _ECOSYSTEM_MAP = {
        "python": "PyPI",
        "pip": "PyPI",
        "node": "npm",
        "npm": "npm",
        "js": "npm",
        "java": "Maven",
        "maven": "Maven",
        "go": "Go",
        "golang": "Go",
        "rust": "crates.io",
        "ruby": "RubyGems",
        "php": "Packagist",
        "dotnet": "NuGet",
        "nuget": "NuGet",
        "c": "OSS-Fuzz",
        "cpp": "OSS-Fuzz",
    }
    __slots__ = (
        "_EPSS_CACHE_EVICT_BATCH",
        "_EPSS_CACHE_MAX_SIZE",
        "_cache",
        "_epss_cache",
        "_epss_cache_order",
        "_nvd_limiter",
    )

    def __init__(self) -> None:
        self._cache = ExposureCache(prefix="cve")
        # ISSUE #016: Rust token bucket — precision bez GIL overhead
        has_api_key = bool(os.environ.get("NVD_API_KEY"))
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust

        create_nvd_limiter = rust.raw.create_nvd_limiter
        if create_nvd_limiter is not None:
            self._nvd_limiter = create_nvd_limiter(has_api_key=has_api_key)
        else:
            # Fallback: Python asyncio.Semaphore (degraded precision)
            logger.warning("Rust NvdRateLimiter unavailable — using asyncio.Semaphore fallback")
            self._nvd_limiter = asyncio.Semaphore(self._NVD_RATE_LIMIT)
        self._epss_cache: dict[str, dict[str, float]] = {}
        self._epss_cache_order: list[str] = []
        self._EPSS_CACHE_MAX_SIZE = 1000
        self._EPSS_CACHE_EVICT_BATCH = 100

    def _map_ecosystem(self, pkg: str) -> tuple[str, str]:
        """
        Map package name/tech stack entry to (ecosystem, package_name).
        Returns (ecosystem, package_name) tuple.
        """
        lower = pkg.lower()
        if eco := self._ECOSYSTEM_MAP.get(lower):
            return (eco, pkg)
        if lower.startswith("pip ") or lower.startswith("pip-"):
            return ("PyPI", pkg.replace("pip ", "").replace("pip-", ""))
        if any(lower.startswith(x) for x in ["npm ", "@", "node-", "jsx", "tsx"]):
            return ("npm", pkg)
        if any(lower.startswith(x) for x in ["maven ", "org.", "com.", "io."]):
            return ("Maven", pkg)
        if lower.startswith("go ") or "/" in pkg:
            return ("Go", pkg)
        if lower.startswith("cargo ") or lower.startswith("crates.io/"):
            return ("crates.io", pkg)
        return ("PyPI", pkg)

    async def _get_session(self) -> httpx.AsyncClient:
        return await get_intelligence_session()

    async def _fetch_osv_batch(self, tech_stack: list[str], session: httpx.AsyncClient) -> AsyncIterator[dict]:
        """
        Fetch CVEs via OSV.dev batch API.
        Yields dicts with CVE data. Falls back to NVD on 0 results.
        """
        queries = []
        for pkg in tech_stack:
            eco, name = self._map_ecosystem(pkg)
            queries.append({"package": {"name": name, "ecosystem": eco}})
        cache_key = f"osv_batch:{','.join(sorted(tech_stack))}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"CVE cache hit for OSV batch: {cache_key[:50]}")
            for cve in cached.get("cves", [])[: self._MAX_CVES]:
                yield cve
            return
        try:
            async with session.post(
                self._OSV_BATCH_URL, json={"queries": queries}, timeout=httpx.Timeout(total=60)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"OSV batch API error: {resp.status}")
                    async for cve in self._fetch_nvd_fallback(tech_stack, session):
                        yield cve
                    return
                buffer = bytearray()
                cves_yielded = 0
                iter_chunks = resp.content.iter_chunked(8192)
                try:
                    async for chunk in iter_chunks:
                        buffer.extend(chunk)
                        while b"\n" in buffer:
                            line_bytes, buffer[:] = buffer.split(b"\n", 1)
                            if not line_bytes.strip():
                                continue
                            try:
                                data = decode(line_bytes)
                                vulns = data.get("vulns", []) if isinstance(data, dict) else []
                                for vuln in vulns:
                                    if cves_yielded >= self._MAX_CVES:
                                        return
                                    cve = self._osv_to_cve(vuln)
                                    if cve:
                                        yield cve
                                        cves_yielded += 1
                            except Exception:
                                continue
                finally:
                    await _aclose_stream(iter_chunks)
                if cves_yielded == 0:
                    logger.debug("OSV returned 0 CVEs, falling back to NVD")
                    async for cve in self._fetch_nvd_fallback(tech_stack, session):
                        yield cve
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"OSV batch fetch error: {e}")
            async for cve in self._fetch_nvd_fallback(tech_stack, session):
                yield cve

    async def _fetch_single_nvd(self, tech: str, session: httpx.AsyncClient) -> list[dict]:
        """
        Fetch CVEs for a single tech from NVD (rate-limited, cached).
        Returns list of CVE dicts for yield.

        ISSUE #016: Unified rate limiter interface — Rust NvdRateLimiter (token bucket)
        or Python asyncio.Semaphore fallback.
        - Rust try_acquire() non-blocking → cooperative async sleep loop
        - Python Semaphore → async context manager
        """
        cache_key = f"nvd:{tech}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached.get("cves", [])
        # ISSUE #016: Unified acquire — Rust try_acquire() or Python Semaphore
        if hasattr(self._nvd_limiter, "try_acquire"):
            # Rust NvdRateLimiter: non-blocking try_acquire + cooperative async sleep
            # Cooperative = don't block event loop; yield to event loop between checks
            max_wait = self._NVD_ACQUIRE_TIMEOUT
            waited = 0.0
            while not self._nvd_limiter.try_acquire():
                await asyncio.sleep(0.1)  # cooperative yield, 100ms chunks
                waited += 0.1
                if waited >= max_wait:
                    logger.warning(f"NVD rate limit timeout for {tech}")
                    return []
        else:
            # Python asyncio.Semaphore fallback
            async with self._nvd_limiter:  # type: ignore[union-attr]
                pass
        try:
            async with session.get(
                self._NVD_API_URL, params={"keywordSearch": tech, "resultsPerPage": 20}, timeout=httpx.Timeout(total=30)
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"NVD API error for {tech}: {resp.status}")
                    return []
                data = await resp.json(content_type=None)
                cves = data.get("vulnerabilities", [])
                stored = {"cves": cves[:20]}
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(_DB_EXECUTOR, lambda k=cache_key, v=stored: self._cache.set(k, v))
                return [self._nvd_to_cve(cve) for cve in cves[:20]]
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"NVD fetch error for {tech}: {e}")
            return []

    async def _fetch_nvd_fallback(self, tech_stack: list[str], session: httpx.AsyncClient) -> AsyncIterator[dict]:
        """
        NVD API 2.0 fallback - parallelized with bounded concurrency.

        ISSUE-003: Replaced sequential `for tech in tech_stack` with parallel().
        Yields CVEs as they complete (not in order) for better UX.
        """
        if not tech_stack:
            return
        result = await parallel(
            [self._fetch_single_nvd(tech, session) for tech in tech_stack],
            concurrency=min(5, len(tech_stack)),
            policy="collect",
            ctx="nvd_fallback",
        )
        for cve_list in result.ok:
            for cve in cve_list:
                yield cve
        for exc in result.errors:
            logger.warning(f"NVD fallback parallel error: {exc}")

    async def _enrich_epss(self, cve_id: str, session: httpx.AsyncClient) -> dict[str, float] | None:
        """
        Fetch EPSS score for a CVE.
        Returns {"epss_score": float, "percentile": float} or None.
        """
        if cve_id in self._epss_cache:
            return self._epss_cache[cve_id]
        try:
            async with session.get(f"{self._EPSS_URL}?cve={cve_id}", timeout=httpx.Timeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                epss = data.get("epss", "")
                percentile = data.get("percentile", "")
                result = {
                    "epss_score": float(epss) if epss else 0.0,
                    "percentile": float(percentile) if percentile else 0.0,
                }
                self._epss_cache[cve_id] = result
                self._epss_cache_order.append(cve_id)
                if len(self._epss_cache) > self._EPSS_CACHE_MAX_SIZE:
                    evict_count = self._EPSS_CACHE_EVICT_BATCH
                    for _ in range(evict_count):
                        old_key = self._epss_cache_order.pop(0)
                        self._epss_cache.pop(old_key, None)
                return result
        except Exception:
            return None

    def _osv_to_cve(self, vuln: dict) -> dict | None:
        """Convert OSV vulnerability format to our CVE dict."""
        try:
            cve_id = vuln.get("id", "")
            if not cve_id:
                return None
            aliases = vuln.get("aliases", [])
            for alias in aliases:
                if alias.startswith("CVE-"):
                    cve_id = alias
                    break
            return {
                "cve_id": cve_id,
                "source": "osv",
                "summary": vuln.get("summary", ""),
                "severity": self._osv_severity(vuln),
                "published": vuln.get("published", ""),
                "modified": vuln.get("modified", ""),
                "references": vuln.get("references", []),
                "affected": self._osv_affected(vuln),
            }
        except Exception:
            return None

    def _nvd_to_cve(self, vuln: dict) -> dict:
        """Convert NVD vulnerability format to our CVE dict."""
        cve = vuln.get("cve", {})
        cve_id = cve.get("id", "")
        metrics = cve.get("metrics", {})
        cvss_v3 = metrics.get("cvssMetricV31", []) or metrics.get("cvssMetricV30", []) or []
        severity = "UNKNOWN"
        if cvss_v3:
            severity = cvss_v3[0].get("cvssData", {}).get("baseSeverity", "UNKNOWN")
        return {
            "cve_id": cve_id,
            "source": "nvd",
            "summary": cve.get("descriptions", [{}])[0].get("value", ""),
            "severity": severity,
            "published": cve.get("published", ""),
            "modified": cve.get("lastModified", ""),
            "references": [r.get("url", "") for r in cve.get("references", []) if r.get("url")],
            "affected": [],
        }

    async def _enrich_batch_epss(self, cves: list[dict], session: httpx.AsyncClient) -> list[dict]:
        """
        ISSUE-003: Parallelize EPSS enrichment for a batch of CVEs.
        Replaces sequential `await _enrich_epss` per CVE with parallel().
        Returns list of CVEs with EPSS fields populated.
        """
        if not cves:
            return []
        cve_ids = [cve.get("cve_id", "") for cve in cves if cve.get("cve_id")]
        if not cve_ids:
            return cves
        # parallel() for bounded concurrent EPSS requests
        result = await parallel(
            [self._enrich_epss(cve_id, session) for cve_id in cve_ids],
            concurrency=min(5, len(cve_ids)),
            policy="collect",
            ctx="epss_enrichment",
        )
        epss_map: dict[str, dict[str, float]] = {}
        for cve_id, epss_result in zip(cve_ids, result.ok, strict=False):
            if epss_result:
                epss_map[cve_id] = epss_result
        # Apply enrichment to CVEs
        for cve in cves:
            cve_id = cve.get("cve_id", "")
            if cve_id and cve_id in epss_map:
                epss = epss_map[cve_id]
                cve["epss_score"] = epss["epss_score"]
                cve["epss_percentile"] = epss["percentile"]
                if epss["epss_score"] > 0.7:
                    cve["action_flag"] = "IMMEDIATE_ACTION"
        return cves

    def _osv_severity(self, vuln: dict) -> str:
        """Extract severity from OSV format."""
        severity = vuln.get("severity", [])
        for s in severity:
            if s.get("type", "").upper() in ("CVSS_V3", "CVSS_V2"):
                return s.get("score", "UNKNOWN")
        return "UNKNOWN"

    def _osv_affected(self, vuln: dict) -> list:
        """Extract affected packages from OSV format."""
        affected = vuln.get("affected", [])
        return [
            {
                "package": a.get("package", {}).get("name", ""),
                "ecosystem": a.get("package", {}).get("ecosystem", ""),
                "ranges": a.get("ranges", []),
            }
            for a in affected
        ]

    async def fetch_cve_intelligence(self, tech_stack: list[str]) -> AsyncIterator[dict]:
        """
        Fetch CVE intelligence for a tech stack.

        1. OSV.dev Batch API (priority) with streaming
        2. NVD API 2.0 fallback (if OSV returns 0)
        3. EPSS score enrichment per CVE

        Yields dicts with CVE data + EPSS enrichment.
        EPSS >0.7 flags CVE as IMMEDIATE_ACTION.

        Memory bounded: max 200 CVEs, batches of 20.
        LMDB cache: 6h TTL for CVE data.
        """
        session = await self._get_session()
        pending_epss: list[dict] = []
        cves_yielded = 0
        try:
            async for cve in self._fetch_osv_batch(tech_stack, session):
                if cves_yielded >= self._MAX_CVES:
                    break
                pending_epss.append(cve)
                cves_yielded += 1
                if len(pending_epss) >= self._BATCH_SIZE:
                    # ISSUE-003: Parallelize EPSS enrichment per batch (was sequential per CVE)
                    enriched = await self._enrich_batch_epss(pending_epss, session)
                    yield {"cves": enriched, "batch_complete": True}
                    pending_epss.clear()
            if pending_epss:
                enriched = await self._enrich_batch_epss(pending_epss, session)
                yield {"cves": enriched, "batch_complete": True}
            yield {"cves": [], "batch_complete": False, "total_cves": cves_yielded}
        finally:
            await session.close()

    async def close(self) -> None:
        self._cache.close()
