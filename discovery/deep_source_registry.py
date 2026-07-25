"""
discovery/deep_source_registry.py — Curated, self-updating registry of beyond-surface OSINT sources.

Sprint F270: DeepSourceRegistry.

GOAL:
    Provide a curated catalog of OSINT-relevant sources BEYOND the indexed web
    — dark web (.onion / .i2p), archives, paste sites, academic mirrors,
    code-intelligence search, leak DBs, and P2P gateways. The registry is
    self-updating via async HEAD probes, persists last-verified timestamps
    through LMDB, and exposes a transport-aware filter so the orchestrator
    can ask "what can I reach right now?" given the current transport
    capabilities.

INVARIANTS (M1 8GB UMA, always-on):
    - Frozen, slots-only DTOs (DeepSource) — zero accidental mutation, GC off.
    - MAX_SOURCES_IN_REGISTRY=200 hard cap (curated list is bounded).
    - LMDB_MAP_SIZE = 1 MiB (one binary blob per source_id; tiny).
    - No persistent network connections in __init__ — purely in-memory catalog.
    - verify_source() uses a Semaphore(10) to bound concurrent HEAD probes,
      and asyncio.timeout(5) on each — bounded, fail-safe, M1-friendly.
    - Fail-soft on every external I/O: any exception returns False / [].
    - BLAKE2b 8-byte source_id (hex) — collision-safe enough for 200 entries.
    - Persistence via canonical paths.open_lmdb() — single-retry, writemap=False,
      sync=False (Sprint 8AG §1.4 lock recovery).

PROVIDER-OWNED SEAM:
    DeepSourceRegistry is a read-side overlay. It does NOT crawl sources —
    it only classifies and verifies them. Higher layers (acquisition lanes,
    hypothesis engine) consume get_available_sources() to plan pivots.

WIRE-UP:
    - enhanced_research.py: UnifiedResearchEngine._task_source_discovery() (Phase 2.7)
    - core/__main__.py: --list-sources [--tier ...] CLI flag
"""
import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass
import msgspec
from typing import Any, Literal
import orjson
logger = logging.getLogger(__name__)
MAX_SOURCES_IN_REGISTRY: int = 200
LMDB_MAP_SIZE: int = 1 * 1024 * 1024
LMDB_DB_NAME: bytes = b'deep_sources'
MAX_CONCURRENT_HEAD: int = 10
HEAD_TIMEOUT_S: float = 5.0
MAX_RELIABILITY: float = 1.0
MIN_RELIABILITY: float = 0.0
SourceTier = Literal['surface', 'dark', 'archive', 'p2p', 'academic']
TransportRequired = Literal['direct', 'tor', 'i2p', 'none']
DataType = Literal['ct_logs', 'passive_dns', 'leak_db', 'academic', 'forum', 'paste', 'repo']

class DeepSource(msgspec.Struct, frozen=True):
    """Immutable description of a single beyond-surface OSINT source.

    source_id is a BLAKE2b 8-byte digest of base_url (16 hex chars) — stable
    across processes and humans-readable enough for log triage.

    reliability is manually curated in [0.0, 1.0]; we never auto-boost it.
    last_verified is a Unix epoch (None = never).
    """
    source_id: str
    name: str
    base_url: str
    source_tier: SourceTier
    transport_required: TransportRequired
    data_type: DataType
    reliability: float
    last_verified: float | None

    def __post_init__(self) -> None:
        if not MIN_RELIABILITY <= self.reliability <= MAX_RELIABILITY:
            raise ValueError(f'DeepSource.reliability must be in [{MIN_RELIABILITY}, {MAX_RELIABILITY}], got {self.reliability} for {self.source_id}')
        if not self.base_url or not isinstance(self.base_url, str):
            raise ValueError(f'DeepSource.base_url must be non-empty str: {self.source_id}')
        if _onion_tor_required(self.base_url) and self.transport_required != 'tor':
            raise ValueError(f"DeepSource {self.source_id}: .onion URL requires transport_required='tor', got {self.transport_required!r}")
        if _i2p_required(self.base_url) and self.transport_required != 'i2p':
            raise ValueError(f"DeepSource {self.source_id}: .i2p URL requires transport_required='i2p', got {self.transport_required!r}")

def _compute_source_id(base_url: str) -> str:
    """Stable BLAKE2b 8-byte digest of base_url → 16-char hex.

    F270 invariant: source_id is deterministic and collision-safe for ≤200
    curated entries (BLAKE2b-64 has 2^64 collision space).
    """
    h = hashlib.blake2b(base_url.encode('utf-8'), digest_size=8)
    return h.hexdigest()

def _onion_tor_required(base_url: str) -> bool:
    """B6/C.4: .onion → Transport.TOR (mandatory, never DIRECT).

    Mirrors SourceTransportMap logic in transport/transport_resolver.py.
    Kept local to avoid import cycles — DeepSourceRegistry must remain
    importable from any context (including pure-Python tests).
    """
    host = base_url.split('://', 1)[-1].split('/', 1)[0].lower()
    return host.endswith('.onion')

def _i2p_required(base_url: str) -> bool:
    """Mirror of SourceTransportMap for .i2p / .b32.i2p."""
    host = base_url.split('://', 1)[-1].split('/', 1)[0].lower()
    return host.endswith('.i2p') or host.endswith('.b32.i2p')
_CURATED_SOURCES: tuple[tuple[str, str, SourceTier, TransportRequired, DataType, float], ...] = (('crt.sh', 'https://crt.sh/', 'surface', 'none', 'ct_logs', 0.95), ('CertStream', 'https://certstream.calidog.io/', 'surface', 'none', 'ct_logs', 0.85), ('Facebook CT', 'https://ct.facebook.com/', 'surface', 'none', 'ct_logs', 0.9), ('Google Pilot CT', 'https://ct.googleapis.com/logs/eu1/xenon2025h1/', 'surface', 'none', 'ct_logs', 0.9), ('Google Argon CT', 'https://ct.googleapis.com/logs/eu1/argon2025h1/', 'surface', 'none', 'ct_logs', 0.9), ('Cloudflare Nimbus', 'https://ct.cloudflare.com/logs/nimbus2025/', 'surface', 'none', 'ct_logs', 0.92), ("Let's Encrypt Oak", 'https://oak.ct.letsencrypt.org/', 'surface', 'none', 'ct_logs', 0.95), ('SecurityTrails pDNS', 'https://securitytrails.com/list/apex_domain/', 'surface', 'none', 'passive_dns', 0.85), ('CIRCL pDNS', 'https://www.circl.lu/services/passive-dns/', 'surface', 'none', 'passive_dns', 0.9), ('Robtex pDNS', 'https://passive.robtex.com/', 'surface', 'none', 'passive_dns', 0.8), ('MnemonicPDNS', 'https://pdns.circl.lu/', 'surface', 'none', 'passive_dns', 0.85), ('DNSlytics', 'https://search.dnslytics.com/', 'surface', 'none', 'passive_dns', 0.75), ('Wayback CDX', 'https://web.archive.org/cdx/search/cdx', 'surface', 'none', 'repo', 0.95), ('CommonCrawl Index', 'https://index.commoncrawl.org/', 'surface', 'none', 'repo', 0.9), ('archive.is', 'https://archive.is/', 'surface', 'none', 'repo', 0.85), ('CachedView', 'https://cachedview.nl/', 'surface', 'none', 'repo', 0.65), ('Mementos Web', 'http://timetravel.mementoweb.org/', 'surface', 'none', 'repo', 0.8), ('Pastebin (scraping)', 'https://pastebin.com/', 'surface', 'none', 'paste', 0.75), ('Ghostbin', 'https://ghostbin.org/', 'surface', 'none', 'paste', 0.6), ('Privatebin (known instance)', 'https://privatebin.net/', 'surface', 'none', 'paste', 0.65), ('Throwbin', 'https://throwbin.io/', 'surface', 'none', 'paste', 0.5), ('Rentry', 'https://rentry.co/', 'surface', 'none', 'paste', 0.7), ('Semantic Scholar', 'https://api.semanticscholar.org/graph/v1/', 'academic', 'none', 'academic', 0.95), ('arXiv API', 'http://export.arxiv.org/api/query?', 'academic', 'none', 'academic', 0.95), ('CORE.ac.uk', 'https://core.ac.uk/search/', 'academic', 'none', 'academic', 0.85), ('CrossRef REST', 'https://api.crossref.org/', 'academic', 'none', 'academic', 0.95), ('OpenAlex', 'https://api.openalex.org/', 'academic', 'none', 'academic', 0.9), ('PubMed E-utilities', 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/', 'academic', 'none', 'academic', 0.95), ('grep.app', 'https://grep.app/api/search?', 'surface', 'none', 'repo', 0.9), ('Sourcegraph public', 'https://sourcegraph.com/.api/search/stream?', 'surface', 'none', 'repo', 0.9), ('GitHub code search', 'https://api.github.com/search/code?', 'surface', 'none', 'repo', 0.95), ('GitLab search', 'https://gitlab.com/api/v4/search?', 'surface', 'none', 'repo', 0.8), ('Codeberg search', 'https://codeberg.org/api/v1/repos/search?', 'surface', 'none', 'repo', 0.7), ('searchcode', 'https://searchcode.com/api/codesearch_I/', 'surface', 'none', 'repo', 0.75), ('Have I Been Pwned', 'https://haveibeenpwned.com/api/v3/', 'surface', 'none', 'leak_db', 0.9), ('DeHashed (gated)', 'https://dehashed.com/', 'surface', 'none', 'leak_db', 0.85), ('LeakCheck', 'https://leakcheck.io/', 'surface', 'none', 'leak_db', 0.7), ('IntelligenceX', 'https://intelx.io/', 'surface', 'none', 'leak_db', 0.85), ('Leak-Lookup', 'https://leak-lookup.com/', 'surface', 'none', 'leak_db', 0.6), ('Ahmia.fi (Tor search mirror)', 'https://ahmia.fi/', 'surface', 'none', 'forum', 0.8), ('dark.fail', 'https://dark.fail/', 'surface', 'none', 'forum', 0.75), ('onion.live', 'https://onion.live/', 'surface', 'none', 'forum', 0.6), ('DarkOwl Vision (commercial)', 'https://www.darkowl.com/', 'surface', 'none', 'leak_db', 0.85), ('TorBot (clearnet link list)', 'https://github.com/DedSecInside/TorBot', 'surface', 'none', 'forum', 0.55), ('IPFS public gateway (ipfs.io)', 'https://ipfs.io/', 'p2p', 'none', 'repo', 0.9), ('IPFS gateway (dweb.link)', 'https://dweb.link/', 'p2p', 'none', 'repo', 0.85), ('IPFS gateway (cloudflare)', 'https://cf-ipfs.com/', 'p2p', 'none', 'repo', 0.8), ('IPFS gateway (nftstorage)', 'https://nftstorage.link/', 'p2p', 'none', 'repo', 0.7), ('DHT bootstrap (already wired)', 'dht://bootstrap/', 'p2p', 'none', 'repo', 0.95), ('Ahmia .onion', 'http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/', 'dark', 'tor', 'forum', 0.7), ('Dark.fail .onion', 'http://darkfailenbsdla5mal2mxn2uz66od5vtzd5q5slngbgx6wvpfzh7umtid.onion/', 'dark', 'tor', 'forum', 0.55), ('ProtonMail .onion', 'https://protonmailrmez3lotccipshtkleegetolb73fuirgj7r4o4vfu7ozyd.onion/', 'dark', 'tor', 'paste', 0.85), ('BBC .onion', 'https://www.bbcnewsd73hkzno2ini43t4gblxvycyac5aw4gnv7t2rccijh7745uqd.onion/', 'dark', 'tor', 'forum', 0.8), ('NYT .onion', 'https://www.nytimesn7cgmftshazwhfgzm37qxb44r64ytbb2dj3x62d2lljsciiyd.onion/', 'dark', 'tor', 'forum', 0.8), ('Facebook .onion', 'https://www.facebookwkhpilnemxj7asaniu7vnjjbiltxjqhye3mhbshg7kx5tfyd.onion/', 'dark', 'tor', 'forum', 0.85), ('Dread .onion', 'http://dreadytofatroptsdj6c7mkr3q62p5l3qb66pfnhlfnn3gg4g4nh2qd.onion/', 'dark', 'tor', 'forum', 0.65), ('Hidden Wiki .onion', 'http://zqktlwi4fecvo6ri.onion/wiki/index.php/Main_Page', 'dark', 'tor', 'forum', 0.55), ('Tor Metrics .onion', 'http://hctxrmjzvyvmadqzhf7j5wga67vjwfuw7jirzkrom2pgjyt5xv7i5jid.onion/', 'dark', 'tor', 'repo', 0.8), ('IntelX .onion', 'http://intelexioou7w3mp.onion/', 'dark', 'tor', 'leak_db', 0.75), ('I2P Project', 'http://i2p-projekt.i2p/', 'dark', 'i2p', 'forum', 0.75), ('I2P Pastebin', 'http://paste.i2p/', 'dark', 'i2p', 'paste', 0.6), ('I2P Bug Tracker', 'http://trac.i2p2.i2p/', 'dark', 'i2p', 'repo', 0.7), ('I2P Namecoin (mirror)', 'http://ncp.i2p/', 'dark', 'i2p', 'forum', 0.55))

class DeepSourceRegistry:
    """Curated + self-updating registry of beyond-surface OSINT sources.

    Lifecycle:
        1. __init__() — load curated catalog into memory (no I/O).
        2. attach_lmdb(path) — bind a persistence directory (lazy).
        3. hydrate_from_lmdb() — overlay last-verified timestamps on top of catalog.
        4. get_sources(...) / get_available_sources(...) — sync read filters.
        5. verify_source(source_id) — async HEAD probe, persists result.
        6. verify_all() — async batch verification with bounded concurrency.

    M1 bounds: pure in-memory catalog, LMDB store is opt-in, HEAD probes are
    semaphore-bounded (10) and timeout-bounded (5s), all operations fail-soft.
    """
    __slots__ = tuple(('_db', '_env', '_lmdb_path', '_semaphore', '_sources'))

    def __init__(self) -> None:
        self._sources: dict[str, DeepSource] = {}
        self._env: Any | None = None
        self._db: Any | None = None
        self._lmdb_path: str | None = None
        self._semaphore: asyncio.Semaphore | None = None
        for entry in _CURATED_SOURCES:
            name, url, tier, transport, data_type, reliability = entry
            sid = _compute_source_id(url)
            if len(self._sources) >= MAX_SOURCES_IN_REGISTRY:
                logger.warning('DeepSourceRegistry: MAX_SOURCES_IN_REGISTRY (%d) reached, dropping %s', MAX_SOURCES_IN_REGISTRY, name)
                break
            self._sources[sid] = DeepSource(source_id=sid, name=name, base_url=url, source_tier=tier, transport_required=transport, data_type=data_type, reliability=reliability, last_verified=None)
        logger.debug('DeepSourceRegistry: %d curated sources loaded (cap=%d)', len(self._sources), MAX_SOURCES_IN_REGISTRY)

    def attach_lmdb(self, path) -> None:
        """Bind LMDB persistence directory. Opens lazily on first read/write.

        path: pathlib.Path or str. Safe to call multiple times (idempotent).
        """
        try:
            self._lmdb_path = str(path)
            os.makedirs(self._lmdb_path, exist_ok=True)
            logger.debug('DeepSourceRegistry: LMDB path bound → %s', self._lmdb_path)
        except Exception as exc:
            logger.warning('DeepSourceRegistry: attach_lmdb failed: %s', exc)
            self._lmdb_path = None

    def _open_env(self):
        """Lazy LMDB env opener. Returns None on failure (in-memory fallback)."""
        if self._env is not None:
            return self._env
        if self._lmdb_path is None:
            return None
        try:
            # Lazy import uvnitř — open_lmdb je sám lazy wrapper,
            # ale import samotného paths modulu může mít side-effects.
            # Kompletní lazy loading: nic se nestane dokud není _open_env zavolána.
            from pathlib import Path as _Path
            from hledac.universal.paths import open_lmdb as _open_lmdb
            self._env = _open_lmdb(_Path(self._lmdb_path), map_size=LMDB_MAP_SIZE, max_dbs=2)
            return self._env
        except Exception as exc:
            logger.warning('DeepSourceRegistry: LMDB open failed: %s', exc)
            self._env = None
            return None

    def _get_db(self) -> Any:
        """Lazy sub-DB handle opener. Caches the handle for the env's lifetime."""
        env = self._open_env()
        if env is None:
            return None
        if self._db is None:
            try:
                self._db = env.open_db(LMDB_DB_NAME, create=True)
            except Exception as exc:
                logger.debug('_get_db: open_db failed: %s', exc)
                return None
        return self._db

    def hydrate_from_lmdb(self) -> int:
        """Overlay persisted last_verified timestamps on the in-memory catalog.

        Returns number of sources hydrated. Safe to call without attach_lmdb().
        """
        env = self._open_env()
        if env is None:
            return 0
        db = self._get_db()
        if db is None:
            return 0
        try:
            with env.begin() as txn:
                with txn.cursor(db=db) as cur:
                    count = 0
                    for k, v in cur:
                        try:
                            sid = k.decode('utf-8')  # S-02: key IS the source_id (already plain str), decode only once
                            payload = orjson.loads(v)
                            ts = payload.get('last_verified')
                            existing = self._sources.get(sid)
                            if existing is not None and isinstance(ts, (int, float)):
                                self._sources[sid] = DeepSource(source_id=existing.source_id, name=existing.name, base_url=existing.base_url, source_tier=existing.source_tier, transport_required=existing.transport_required, data_type=existing.data_type, reliability=existing.reliability, last_verified=float(ts))
                                count += 1
                        except Exception as exc:
                            logger.debug('hydrate: skip %s (%s)', k, exc)
            return count
        except Exception as exc:
            logger.warning('DeepSourceRegistry: hydrate_from_lmdb failed: %s', exc)
            return 0

    def _persist_timestamp(self, source_id: str, ts: float) -> None:
        """Persist a single source's last_verified. Fail-soft."""
        env = self._open_env()
        if env is None:
            return
        db = self._get_db()
        if db is None:
            return
        try:
            payload = orjson.dumps({'last_verified': ts})
            with env.begin(write=True) as txn:
                txn.put(source_id.encode('utf-8'), payload, db=db)
        except Exception as exc:
            logger.debug('persist_timestamp: %s failed: %s', source_id, exc)

    def __len__(self) -> int:
        return len(self._sources)

    def __iter__(self):
        return iter(self._sources.values())

    def get_source(self, source_id: str) -> DeepSource | None:
        return self._sources.get(source_id)

    def get_sources(self, tier: SourceTier | None=None, transport: TransportRequired | None=None, data_type: DataType | None=None) -> list[DeepSource]:
        """Filtered list query. All filters are AND-combined.

        Returns a fresh list (safe to mutate).
        """
        result: list[DeepSource] = []
        for src in self._sources.values():
            if tier is not None and src.source_tier != tier:
                continue
            if transport is not None and src.transport_required != transport:
                continue
            if data_type is not None and src.data_type != data_type:
                continue
            result.append(src)
        return result

    def get_available_sources(self, transport_capabilities: set[str]) -> list[DeepSource]:
        """Return sources reachable given the current transport capabilities.

        transport_capabilities is a set of strings, e.g.
          {"direct", "tor", "i2p", "curl_cffi"}.

        A source is "available" iff:
          - transport_required == "none"  (always reachable)
          - transport_required == "direct" and "direct" in capabilities
          - transport_required == "tor" and "tor" in capabilities
          - transport_required == "i2p" and "i2p" in capabilities

        Note: "none" is treated as "no special transport needed" and always passes.
        """
        result: list[DeepSource] = []
        for src in self._sources.values():
            req = src.transport_required
            if req == 'none':
                result.append(src)
            elif req in transport_capabilities:
                result.append(src)
        return result

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_HEAD)
        return self._semaphore

    async def _head_probe(self, url: str) -> bool:
        """Single HEAD probe with 5s timeout. Returns True on 2xx/3xx/4xx.

        4xx is treated as "reachable" (server responded) — only 5xx and
        network errors count as unreachable.
        """
        try:
            import httpx
        except Exception as exc:
            logger.debug('_head_probe: httpx unavailable: %s', exc)
            return False
        if not (url.startswith('http://') or url.startswith('https://')):
            return False
        try:
            async with self._get_semaphore():
                async with httpx.AsyncClient(timeout=httpx.Timeout(total=HEAD_TIMEOUT_S)) as session:
                    async with session.head(url, follow_redirects=True) as resp:
                        return resp.status_code < 500
        except TimeoutError:
            return False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug('_head_probe(%s): %s', url, exc)
            return False

    async def verify_source(self, source_id: str) -> bool:
        """Probe a single source, update last_verified on success.

        Returns True iff the probe succeeded (2xx/3xx/4xx response).
        Persists the timestamp through LMDB (fail-soft).
        """
        src = self._sources.get(source_id)
        if src is None:
            return False
        ok = await self._head_probe(src.base_url)
        if ok:
            ts = time.time()
            self._sources[source_id] = DeepSource(source_id=src.source_id, name=src.name, base_url=src.base_url, source_tier=src.source_tier, transport_required=src.transport_required, data_type=src.data_type, reliability=src.reliability, last_verified=ts)
            self._persist_timestamp(source_id, ts)
        return ok

    async def verify_all(self) -> dict[str, bool]:
        """Verify every source concurrently (bounded by MAX_CONCURRENT_HEAD).

        Returns a dict {source_id: ok}. Always completes (best-effort).
        """
        results: dict[str, bool] = {}

        async def _one(sid: str) -> tuple[str, bool]:
            try:
                return (sid, await self.verify_source(sid))
            except Exception:
                return (sid, False)
        tasks = [_one(sid) for sid in self._sources]
        if not tasks:
            return results
        from hledac.universal.utils.async_helpers import safe_gather_ok
        outcomes = await safe_gather_ok(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                continue
            if isinstance(outcome, tuple) and len(outcome) == 2:
                sid, ok = outcome
                results[sid] = ok
        return results

    def close(self) -> None:
        """Close LMDB env. Idempotent. Safe to call multiple times."""
        env = self._env
        if env is None:
            return
        try:
            env.close()
        except Exception as exc:
            logger.debug('DeepSourceRegistry.close: %s', exc)
        finally:
            self._env = None
            self._db = None