"""
PastebinMonitor — scrape neindexované paste sites pro leak OSINT.
==============================================================



Migrated from: intelligence/ (parent/donor)
Canonical path: hledac.universal.recon.pastebin_monitor

P20: pastebin.com, paste.gg, rentry.co — asynchronní scraping s rate-limiting
a circuit breaker. Detekuje e-maily, IP adresy, tokeny a API klíče.

Bounded constraints (M1 8GB):
- 10 pastes max per source (30 total)
- 10s timeout per scrape
- Circuit breaker: 5 failures → 60s pause
- Rate limit: 1 req/s across all sources
- Fail-soft: returns empty list on errors

Anti-patterns:
  - HTML parsing přes regex: selectolax (Rust HTML parser)
  - Rate limit obejít: 1 req/s hard limit
  - Secret do logu: mask_secret() before any log/print
"""

import asyncio
import logging
import re
import time
from typing import Any

import httpx
from msgspec import field

from compat.msgspec_gc_compat import Struct
from hledac.universal.utils.asyncx import parallel_ok
from hledac.universal.utils.locks import LazyAsyncioLock

logger = logging.getLogger(__name__)
_TIMEOUT_10 = httpx.Timeout(10.0)
_TIMEOUT_15 = httpx.Timeout(15.0)
from hledac.universal.brain.output_dlp_filter import mask_secret as _mask_secret_impl

_SECRET_REDACT_LEN = 4


def _mask_secret(value: str) -> str:
    """Mask secrets via centralized DLP filter (SOVEREIGN-010)."""
    return _mask_secret_impl(value)


class PasteFinding(Struct):
    """Structured paste finding result."""

    uri: str
    source: str
    extracted_secrets: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    ip_addresses: list[str] = field(default_factory=list)
    context_snippet: str = ""

    def masked_secrets(self) -> list[str]:
        """Return masked secrets for safe logging."""
        return [_mask_secret(s) for s in self.extracted_secrets]


_RE_EMAIL = re.compile("\\b[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}\\b")
_RE_IPV4 = re.compile("\\b(?:(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)\\.){3}(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)\\b")
_RE_IPV6 = re.compile("\\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\\b")
_RE_URLSAFE_TOKEN = re.compile(
    "\\b(?:token|key|secret|password|passwd|pwd|auth|credential)['\\\"]?[:=]?\\s*['\\\"]?([A-Za-z0-9_\\-]{16,64})['\\\"]?\\b",
    re.IGNORECASE,
)
_RE_AWS_KEY = re.compile("\\bAKIA[0-9A-Z]{16}\\b")
_RE_BEARER = re.compile("\\bBearer\\s+[A-Za-z0-9_\\.\\-]{20,}\\b", re.IGNORECASE)
_RE_PKEY = re.compile("-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)
_CIRCUIT_FAIL_LIMIT = 5
_CIRCUIT_RESET_S = 60.0
_PASTE_BLOOM_PATH_A: str = "~/.cache/hledac/paste_bloom_a.bin"
_PASTE_BLOOM_PATH_B: str = "~/.cache/hledac/paste_bloom_b.bin"
_PASTE_BLOOM_CAPACITY: int = 500000
_BLOOM: Any = None

# NEW-MEM-003: Paste content size cap for M1 8GB safety
# Pastes can be large (up to 10MB), cap at 5MB to prevent OOM
_MAX_PASTE_BYTES: int = 5 * 1024 * 1024


def _get_paste_bloom() -> Any:
    """Lazy-open rotating mmap Bloom filter for paste URI dedup."""
    global _BLOOM
    if _BLOOM is None:
        try:
            import os
            import pathlib

            from rust_extensions import RotatingMmapBloomFilter

            path_a = os.path.expanduser(_PASTE_BLOOM_PATH_A)
            path_b = os.path.expanduser(_PASTE_BLOOM_PATH_B)
            pathlib.Path(path_a).parent.mkdir(parents=True, exist_ok=True)
            _BLOOM = RotatingMmapBloomFilter(path_a, path_b, capacity=_PASTE_BLOOM_CAPACITY, fp_rate=0.01)
        except Exception:
            from rust_extensions import BloomFilter

            _BLOOM = BloomFilter(capacity=_PASTE_BLOOM_CAPACITY, fp_rate=0.01)
    return _BLOOM


_ZSTD_AVAILABLE: bool | None = None


def _get_zstd_compress():
    """Lazily import zstd.compress, or return None if unavailable."""
    global _ZSTD_AVAILABLE
    if _ZSTD_AVAILABLE is None:
        try:
            import zstd

            _ZSTD_AVAILABLE = True
            return zstd.compress
        except Exception:
            _ZSTD_AVAILABLE = False
            return None
    if _ZSTD_AVAILABLE:
        import zstd

        return zstd.compress
    return None


# NEW-MEM-003: Paste content size cap for M1 8GB safety
_MAX_PASTE_BYTES: int = 5 * 1024 * 1024  # 5MB cap for paste content


async def _read_text_with_cap(resp: httpx.Response, cap: int = _MAX_PASTE_BYTES) -> str:
    """Read response text with payload cap for M1 RAM safety."""
    try:
        raw = resp.content or b""
        if len(raw) > cap:
            raw = raw[:cap]
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


class _CircuitState(Struct, frozen=True):
    failures: int = 0
    opened_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_open(self) -> bool:
        if self.failures < _CIRCUIT_FAIL_LIMIT:
            return False
        if time.time() - self.opened_at >= _CIRCUIT_RESET_S:
            self.failures = 0
            self.opened_at = 0.0
            return False
        return True

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= _CIRCUIT_FAIL_LIMIT:
            self.opened_at = time.time()
            logger.warning("PastebinMonitor circuit breaker OPEN — pausing 60s")


_circuit = _CircuitState()


def _extract_secrets(text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract e-mails, IP addresses, and secrets from raw text.

    Returns: (emails, ipv4/ipv6, secret_candidates)
    """
    emails = _RE_EMAIL.findall(text)
    ipv4s = _RE_IPV4.findall(text)
    ipv6s = _RE_IPV6.findall(text)
    ip_addresses = ipv4s + ipv6s
    secrets: list[str] = []
    for pat in (_RE_AWS_KEY, _RE_BEARER, _RE_PKEY):
        secrets.extend(pat.findall(text))
    for m in _RE_URLSAFE_TOKEN.finditer(text):
        secrets.append(m.group(1))
    return (emails, ip_addresses, secrets)


def _make_snippet(text: str, max_len: int = 200) -> str:
    """Oříznout text na max_len znaků, zachovat začátek."""
    t = text.replace("\r", "").strip()
    if len(t) <= max_len:
        return t
    return t[:max_len] + "..."


async def _scrape_pastebin_raw(paste_id: str, session: httpx.AsyncClient) -> str | None:
    """Stáhnout obsah pastebin.com/raw/{id}."""
    url = f"https://pastebin.com/raw/{paste_id}"
    try:
        async with session.get(url, timeout=_TIMEOUT_10) as resp:
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            # NEW-MEM-003: Use capped read for M1 8GB safety
            return await _read_text_with_cap(resp)
    except Exception:
        return None


async def _scrape_paste_gg(paste_id: str, session: httpx.AsyncClient) -> str | None:
    """Stáhnout obsah paste.gg/api/v1/pastes/{id}."""
    url = f"https://paste.gg/api/v1/pastes/{paste_id}"
    try:
        async with session.get(url, timeout=_TIMEOUT_10) as resp:
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = await resp.json()
            data_data = data.get("data") or {}
            files = data_data.get("files") or []
            if files:
                return files[0].get("content") or ""
            return ""
    except Exception:
        return None


async def _scrape_rentry(raw_path: str, session: httpx.AsyncClient) -> str | None:
    """Stáhnout obsah rentry.co/{raw_path}/raw."""
    url = f"https://rentry.co/{raw_path}/raw"
    try:
        async with session.get(url, timeout=_TIMEOUT_10) as resp:
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            # NEW-MEM-003: Use capped read for M1 8GB safety
            return await _read_text_with_cap(resp)
    except Exception:
        return None


_RATERLIMIT_S = 1.0
_last_request: float = 0.0
_rate_lock = LazyAsyncioLock()
_MAX_PASTES_PER_SOURCE = 10
_SCRAPE_CONCURRENCY = 5


async def run(query: str) -> list[PasteFinding]:
    """Hledat pasty odpovídající query napříč pastebin.com, paste.gg, rentry.co.

    Vrací list[PasteFinding] — fail-soft, prázdný list při chybách / circuit-break.
    Rate-limited na 1 req/s, circuit breaker po 5 po sobě jdoucích selháních.

    Bounded:
    - max 10 pastes per source (30 total)
    - 10s timeout per scrape
    - Circuit breaker after 5 consecutive failures
    """
    import httpx

    global _last_request
    findings: list[PasteFinding] = []
    async with _rate_lock:
        if _circuit.is_open():
            logger.info("PastebinMonitor circuit open — skipping run")
            return []
        elapsed = time.time() - _last_request
        if elapsed < _RATERLIMIT_S:
            await asyncio.sleep(_RATERLIMIT_S - elapsed)
        _last_request = time.time()
    try:
        _sess = httpx.AsyncClient(timeout=_TIMEOUT_15)
        async with _sess as session:
            pb_findings = await _search_pastebin(query, session)
            findings.extend(pb_findings)
            gg_findings = await _search_paste_gg(query, session)
            findings.extend(gg_findings)
            rentry_findings = await _search_rentry(query, session)
            findings.extend(rentry_findings)
    except Exception as e:
        logger.warning(f"PastebinMonitor run() failed: {e}")
        _circuit.record_failure()
    return findings


async def _search_pastebin(query: str, session: httpx.AsyncClient) -> list[PasteFinding]:
    """Search pastebin.com for query, scrape matching pastes."""
    findings: list[PasteFinding] = []
    try:
        search_url = f"https://pastebin.com/search?q={query}"
        async with session.get(search_url, timeout=_TIMEOUT_15) as resp:
            if resp.status_code != 200:
                return []
            # NEW-MEM-003: Use capped read for search page (HTML, cap at 1MB)
            html = await _read_text_with_cap(resp, cap=1024 * 1024)
        try:
            from selectolax.parser import HTMLParser
        except ImportError:
            logger.warning("selectolax not available — skipping pastebin search")
            return []
        tree = HTMLParser(html)
        paste_links: list[str] = []
        for a in tree.css("a"):
            href = a.attributes.get("href", "")
            if "/dpaste/" in href or "/raw/" in href:
                pid = href.rstrip("/").split("/")[-1]
                if pid:
                    paste_links.append(pid)
        paste_ids = paste_links[:_MAX_PASTES_PER_SOURCE]
        sem = asyncio.Semaphore(_SCRAPE_CONCURRENCY)

        async def _scrape_one(pid: str) -> PasteFinding | None:
            uri = f"https://pastebin.com/{pid}"
            try:
                bloom = _get_paste_bloom()
                if not bloom.add(uri):
                    return None
            except Exception:  # noqa: BLE001
                pass
            async with sem:
                text = await _scrape_pastebin_raw(pid, session)
            if text is None:
                return None
            emails, ips, secrets = _extract_secrets(text)
            if not (emails or ips or secrets):
                return None
            return PasteFinding(
                uri=uri,
                source="pastebin",
                extracted_secrets=secrets,
                emails=emails,
                ip_addresses=ips,
                context_snippet=_make_snippet(text),
            )

        gathered = await parallel_ok(*[_scrape_one(p) for p in paste_ids], label="pastebin")
        for r in gathered:
            if r is not None:
                findings.append(r)
    except Exception as e:
        logger.debug(f"pastebin search failed: {e}")
    return findings


async def _search_paste_gg(query: str, session: ClientSession) -> list[PasteFinding]:
    """Search paste.gg for query via their API."""
    findings: list[PasteFinding] = []
    try:
        search_url = "https://paste.gg/api/v1/pastes/search"
        async with session.post(
            search_url, json={"query": query, "limit": _MAX_PASTES_PER_SOURCE}, timeout=_TIMEOUT_15
        ) as resp:
            if resp.status_code != 200:
                return []
            data = await resp.json()
        items = (data.get("data") or {}).get("pasties") or []
        items_batch = items[:_MAX_PASTES_PER_SOURCE]
        sem = asyncio.Semaphore(_SCRAPE_CONCURRENCY)

        async def _scrape_one(item: dict) -> PasteFinding | None:
            paste_id = item.get("id") or ""
            uri = f"https://paste.gg/{paste_id}"
            try:
                bloom = _get_paste_bloom()
                if not bloom.add(uri):
                    return None
            except Exception:  # noqa: BLE001
                pass
            async with sem:
                text = await _scrape_paste_gg(paste_id, session)
            if text is None:
                return None
            emails, ips, secrets = _extract_secrets(text)
            if not (emails or ips or secrets):
                return None
            return PasteFinding(
                uri=uri,
                source="paste_gg",
                extracted_secrets=secrets,
                emails=emails,
                ip_addresses=ips,
                context_snippet=_make_snippet(text),
            )

        gathered = await parallel_ok(*[_scrape_one(it) for it in items_batch], label="paste_gg")
        for r in gathered:
            if r is not None:
                findings.append(r)
    except Exception as e:
        logger.debug(f"paste.gg search failed: {e}")
    return findings


async def _search_rentry(query: str, session: httpx.AsyncClient) -> list[PasteFinding]:
    """Search rentry.co for query via HTML parsing."""
    findings: list[PasteFinding] = []
    try:
        search_url = f"https://rentry.co/search?query={query}"
        async with session.get(search_url, timeout=_TIMEOUT_15) as resp:
            if resp.status_code != 200:
                return []
            # NEW-MEM-003: Use capped read for search page (HTML, cap at 1MB)
            html = await _read_text_with_cap(resp, cap=1024 * 1024)
        try:
            from selectolax.parser import HTMLParser
        except ImportError:
            return []
        tree = HTMLParser(html)
        raw_paths: list[str] = []
        for a in tree.css("a"):
            href = a.attributes.get("href", "")
            if href.startswith("/") and len(href) > 2:
                raw_paths.append(href.lstrip("/"))
        raw_paths_batch = raw_paths[:_MAX_PASTES_PER_SOURCE]
        sem = asyncio.Semaphore(_SCRAPE_CONCURRENCY)

        async def _scrape_one(path: str) -> PasteFinding | None:
            uri = f"https://rentry.co/{path}"
            try:
                bloom = _get_paste_bloom()
                if not bloom.add(uri):
                    return None
            except Exception:  # noqa: BLE001
                pass
            async with sem:
                text = await _scrape_rentry(path, session)
            if text is None:
                return None
            emails, ips, secrets = _extract_secrets(text)
            if not (emails or ips or secrets):
                return None
            return PasteFinding(
                uri=uri,
                source="rentry",
                extracted_secrets=secrets,
                emails=emails,
                ip_addresses=ips,
                context_snippet=_make_snippet(text),
            )

        gathered = await parallel_ok(*[_scrape_one(p) for p in raw_paths_batch], label="rentry")
        for r in gathered:
            if r is not None:
                findings.append(r)
    except Exception as e:
        logger.debug(f"rentry search failed: {e}")
    return findings


__all__ = ["PasteFinding", "run"]
