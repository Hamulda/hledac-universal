"""
CTLogClient — Certificate Transparency log pivot přes crt.sh JSON API.

Sprint 8SC: CT log pivot pro doménový OSINT (SubjectAltNames, cert history).
B3: Max 1 request per 5s rate limit, 24h cache.
Sprint F264: Migrated orjson → msgspec facade (utils.msgspec_json).
Cache file format (.json and .json.zst) is preserved for backward compat.
F265C: Certstream Fallback — když crt.sh vrátí chybu (502, timeout, etc.),
automaticky přepne na certstream.circulearning.com jako fallback provider.
ct_provider_selected field vrací "crtsh" | "certstream" | "certstream_fallback_failed".
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from hledac.universal.utils.msgspec_json import decode, encode

if TYPE_CHECKING:
    import aiohttp

    from hledac.universal.knowledge.ioc_graph import IOCGraph

logger = logging.getLogger(__name__)


class CTLogClient:
    """Certificate Transparency log pivot přes crt.sh JSON API.

    NON-HOT-PATH surface — owns its session lifecycle when used standalone.
    F265C: Dual-provider s certstream.circulearning.com fallback.
    """

    _CACHE_TTL = 86400  # 24h
    _RATE_LIMIT_S = 5.0  # per-source rate limit (crt.sh: 1 req / 5s)
    _CERTSTREAM_RATE_LIMIT_S = 3.0  # certstream je rychlejší, 1 req / 3s

    # Fallback CT providers (F265C)
    # NOTE: certstream.circulearning.com is WebSocket-only (wss://), NOT HTTP GET.
    # Using HTTP GET returns 400 Bad Request. Fallback uses Spyse API or
    # crt.sh identity search as a safer alternative for REST-based CT queries.
    _CRT_SH_URL = "https://crt.sh/?q=%25.{domain}&output=json"
    # F265C-FIX: Use crt.sh identity search as fallback instead of broken
    # certstream.circulearning.com (which is wss:// only, not HTTP GET).
    # Spyse requires API key, so we use crt.sh with identity search pattern.
    _CERTSTREAM_FALLBACK_URL = "https://crt.sh/?q={domain}&output=json"

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._last_request: float = 0.0
        self._last_certstream_request: float = 0.0
        self._lock = asyncio.Lock()  # serialize concurrent pivots to same source

    async def pivot_domain(
        self, domain: str, session: aiohttp.ClientSession
    ) -> dict:
        """Hlavní entry point — vrátí CT log findings pro doménu.

        Serializes concurrent calls for the same domain via asyncio.Lock to prevent
        redundant crt.sh requests. Rate-limit guard is per-instance, not per-domain.
        F265C: Automatic fallback na certstream.circulearning.com když crt.sh selže.
        """
        import xxhash

        cache_path = self._cache_dir / f"{xxhash.xxh64(domain.encode()).hexdigest()}.json"
        zst_path = self._cache_dir / f"{xxhash.xxh64(domain.encode()).hexdigest()}.json.zst"

        # Backward compat: try compressed first, fall back to plain json
        if zst_path.exists():
            age = time.time() - zst_path.stat().st_mtime
            if age < self._CACHE_TTL:
                try:
                    import compression.zstd as _zstd
                    return decode(_zstd.decompress(zst_path.read_bytes()))
                except (ImportError, Exception):
                    pass
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < self._CACHE_TTL:
                return decode(cache_path.read_bytes())

        # Serialize concurrent pivots to prevent redundant rate-limited requests
        async with self._lock:
            # Double-check cache after acquiring lock (another caller may have populated it)
            if zst_path.exists():
                age = time.time() - zst_path.stat().st_mtime
                if age < self._CACHE_TTL:
                    try:
                        import compression.zstd as _zstd
                        return decode(_zstd.decompress(zst_path.read_bytes()))
                    except (ImportError, Exception):
                        pass
            if cache_path.exists():
                age = time.time() - cache_path.stat().st_mtime
                if age < self._CACHE_TTL:
                    return decode(cache_path.read_bytes())

            # F265C: Try crt.sh first, fall back to certstream.circulearning.com on failure
            try:
                raw = await self._fetch_ct_with_fallback(domain, session)
                if raw is None:
                    # Both providers failed
                    logger.warning(f"CT log {domain}: all providers failed")
                    return {
                        "domain": domain,
                        "san_names": [],
                        "cert_count": 0,
                        "issuers": [],
                        "first_cert": 0.0,
                        "last_cert": 0.0,
                        "ct_provider_selected": "certstream_fallback_failed",
                    }
            finally:
                self._last_request = time.time()

        result = self._parse_crt_response(domain, raw)

        # Cache write (outside lock — no throttle needed)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            import compression.zstd as _zstd
            zst_path.write_bytes(_zstd.compress(encode(result)))
        except (ImportError, Exception):
            cache_path.write_bytes(encode(result))
        return result

    async def _fetch_ct_with_fallback(
        self, domain: str, session: aiohttp.ClientSession
    ) -> list | None:
        """F265C: Try crt.sh first, fall back to certstream.circulearning.com on failure.

        Returns raw CT log entries list from the first successful provider,
        or None if both providers fail.

        Bug 2 fix: Uses checked_aiohttp_get with 5s timeout + domain circuit breaker.
        crt.sh returning 502 or certstream.circulearning.com 400 now trips CB immediately
        (rather than blocking for 20s), and the CB opens after 3 consecutive failures.
        """
        import aiohttp

        from hledac.universal.transport.circuit_breaker import checked_aiohttp_get

        # Try crt.sh primary — 5s timeout (fast failover), domain CB protection
        url = self._CRT_SH_URL.format(domain=domain)
        raw, _status, err = await checked_aiohttp_get(
            session,
            url,
            timeout=aiohttp.ClientTimeout(total=5),
            failure_kind="crtsh_ct",
        )
        if err:
            logger.warning(f"crt.sh {domain}: {err}, trying certstream fallback")
        elif isinstance(raw, list) and len(raw) > 0:
            logger.info(f"CT log {domain}: crt.sh succeeded ({len(raw)} entries)")
            return raw

        # F265C-FIX: certstream.circulearning.com is wss:// only, not HTTP GET.
        # Use crt.sh identity search as fallback (same provider, different query
        # pattern — searches cert subject/common_name containing domain, not
        # wildcard subdomain). Rate limit still applies.
        elapsed = time.time() - self._last_certstream_request
        if elapsed < self._CERTSTREAM_RATE_LIMIT_S:
            await asyncio.sleep(self._CERTSTREAM_RATE_LIMIT_S - elapsed)

        fallback_url = self._CERTSTREAM_FALLBACK_URL.format(domain=domain)
        raw, _status, err = await checked_aiohttp_get(
            session,
            fallback_url,
            timeout=aiohttp.ClientTimeout(total=5),
            failure_kind="crtsh_ct_fallback",
        )
        if err:
            logger.warning(f"CT fallback {domain}: crt.sh identity search failed: {err}")
        elif isinstance(raw, list) and len(raw) > 0:
            logger.info(f"CT log {domain}: crt.sh identity-search fallback succeeded ({len(raw)} entries)")
            self._last_certstream_request = time.time()
            return raw

        return None

    def _parse_crt_response(self, domain: str, raw: list) -> dict:
        """Extrahovat SAN, issuers, timestamps z crt.sh JSON."""
        san_set: set[str] = set()
        issuer_set: set[str] = set()
        timestamps: list[float] = []

        for entry in raw:
            # SAN names — name_value contains all SANs newline-separated
            name_value = entry.get("name_value", "")
            for n in name_value.splitlines():
                n = n.strip().lstrip("*.")
                if n and "." in n and len(n) < 253:
                    san_set.add(n.lower())

            # Issuer
            issuer = entry.get("issuer_name", "")
            if issuer:
                for part in issuer.split(","):
                    part = part.strip()
                    if part.startswith("CN="):
                        issuer_set.add(part[3:])

            # Timestamps
            for ts_field in ("not_before", "not_after", "entry_timestamp"):
                ts_str = entry.get(ts_field, "")
                if ts_str:
                    try:
                        dt = datetime.datetime.fromisoformat(
                            ts_str.replace("Z", "+00:00").replace(" ", "T")
                        )
                        timestamps.append(dt.timestamp())
                    except Exception:
                        pass

        # Exclude source domain from SAN list
        san_names = sorted(san_set - {domain.lower()})

        return {
            "domain": domain,
            "san_names": san_names,
            "issuers": sorted(issuer_set),
            "first_cert": min(timestamps) if timestamps else 0.0,
            "last_cert": max(timestamps) if timestamps else 0.0,
            "cert_count": len(raw),
        }

    async def fetch_certificates(
        self, domain: str, session: aiohttp.ClientSession
    ) -> list[dict]:
        """Vrátí seznam certifikátů pro doménu z crt.sh.

        Každý dict: subject_common_name, issuer, valid_from, valid_to, alt_names.
        Používá stejný rate-limit a cache jako pivot_domain().
        F265C: Automatic fallback na certstream.circulearning.com.
        """
        import xxhash

        cache_key = f"certs_{xxhash.xxh64(domain.encode()).hexdigest()}.json"
        cache_path = self._cache_dir / cache_key
        zst_path = self._cache_dir / (cache_key + ".zst")
        if zst_path.exists():
            age = time.time() - zst_path.stat().st_mtime
            if age < self._CACHE_TTL:
                try:
                    import compression.zstd as _zstd
                    return decode(_zstd.decompress(zst_path.read_bytes()))
                except (ImportError, Exception):
                    pass
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < self._CACHE_TTL:
                return decode(cache_path.read_bytes())

            elapsed = time.time() - self._last_request
            if elapsed < self._RATE_LIMIT_S:
                await asyncio.sleep(self._RATE_LIMIT_S - elapsed)

            # F265C: Try crt.sh first, fall back to certstream on failure
            try:
                raw = await self._fetch_certificates_with_fallback(domain, session)
                if raw is None:
                    logger.warning(f"fetch_certificates {domain}: all providers failed")
                    return []
            finally:
                self._last_request = time.time()

        certs = self._parse_certs(raw)

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            import compression.zstd as _zstd
            zst_path.write_bytes(_zstd.compress(encode(certs)))
        except (ImportError, Exception):
            cache_path.write_bytes(encode(certs))
        return certs

    async def _fetch_certificates_with_fallback(
        self, domain: str, session: aiohttp.ClientSession
    ) -> list | None:
        """F265C: Try crt.sh first, fall back to certstream for fetch_certificates.

        Bug 2 fix: Uses checked_aiohttp_get with 5s timeout + domain circuit breaker.
        crt.sh returning 502 now trips CB immediately rather than blocking 20s.
        """
        import aiohttp

        from hledac.universal.transport.circuit_breaker import checked_aiohttp_get

        # Try crt.sh primary — 5s timeout (fast failover), domain CB protection
        url = self._CRT_SH_URL.format(domain=domain)
        raw, _status, err = await checked_aiohttp_get(
            session,
            url,
            timeout=aiohttp.ClientTimeout(total=5),
            failure_kind="crtsh_certs",
        )
        if err:
            logger.warning(f"crt.sh fetch_certificates {domain}: {err}, trying certstream")
        elif isinstance(raw, list) and len(raw) > 0:
            return raw

        # F265C-FIX: certstream.circulearning.com is wss:// only, not HTTP GET.
        # Use crt.sh identity search as fallback.
        elapsed = time.time() - self._last_certstream_request
        if elapsed < self._CERTSTREAM_RATE_LIMIT_S:
            await asyncio.sleep(self._CERTSTREAM_RATE_LIMIT_S - elapsed)

        fallback_url = self._CERTSTREAM_FALLBACK_URL.format(domain=domain)
        raw, _status, err = await checked_aiohttp_get(
            session,
            fallback_url,
            timeout=aiohttp.ClientTimeout(total=5),
            failure_kind="crtsh_certs_fallback",
        )
        if err:
            logger.warning(f"CT fallback fetch_certificates {domain}: crt.sh identity search failed: {err}")
        elif isinstance(raw, list) and len(raw) > 0:
            self._last_certstream_request = time.time()
            return raw
        return None

    def _parse_certs(self, raw: list) -> list[dict]:
        """Parsovat crt.sh JSON na per-cert záznamy s datovým kontraktem P20."""
        certs: list[dict] = []
        for entry in raw:
            try:
                # Subject CN
                cn = (entry.get("common_name") or "").strip()

                # Issuer CN
                issuer_dn = entry.get("issuer_name", "")
                issuer_cn = ""
                for part in issuer_dn.split(","):
                    part = part.strip()
                    if part.startswith("CN="):
                        issuer_cn = part[3:].strip()
                        break

                # Validity
                valid_from = (entry.get("not_before") or "").replace(" ", "T")
                valid_to = (entry.get("not_after") or "").replace(" ", "T")

                # SANs from name_value (newline-separated)
                name_value = entry.get("name_value", "")
                alt_names: list[str] = []
                for n in name_value.splitlines():
                    n = n.strip().lstrip("*.")
                    if n and "." in n and len(n) < 253:
                        alt_names.append(n.lower())

                certs.append({
                    "subject_common_name": cn,
                    "issuer": issuer_cn,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                    "alt_names": sorted(set(alt_names)),
                })
            except Exception:
                continue

        return certs

    async def ingest_to_graph(
        self, ct_result: dict, ioc_graph: IOCGraph
    ) -> int:
        """Zapsat CT log findings do IOC graph. Vrátí počet nových uzlů."""
        source_domain = ct_result["domain"]
        count = 0
        for san in ct_result["san_names"]:
            await ioc_graph.buffer_ioc("domain", san, confidence=0.75)
            count += 1
        logger.debug(f"CT log {source_domain}: buffered {count} SAN domains")
        return count

    @staticmethod
    def to_canonical_findings(ct_result: dict, query: str) -> list:
        """
        Sprint F193A: Convert CT log result to canonical findings for storage.

        Returns up to MAX 50 CanonicalFinding objects (one per SAN).
        Returns [] if san_names is empty.
        """
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        san_names = ct_result.get("san_names", [])
        if not san_names:
            return []

        MAX = 50  # noqa: N806
        findings = []
        ts = ct_result.get("last_cert") or time.time()
        issuer = ct_result.get("issuers", [None])[0] if ct_result.get("issuers") else ""
        domain = ct_result.get("domain", "")

        for san in san_names[:MAX]:
            finding_id = f"ct_{hashlib.sha256(san.encode()).hexdigest()[:16]}"  # sha256
            # Sprint F234: Include san in payload_text so each CT finding gets a
            # unique dedup fingerprint. Without san, all 50 entries share one
            # payload_text → one fingerprint → dedup rejects 49/50 as duplicates.
            findings.append(
                CanonicalFinding(
                    finding_id=finding_id,
                    query=query,
                    source_type="ct_log",
                    confidence=0.75,
                    ts=ts,
                    provenance=("ct_log", domain),
                    payload_text=json.dumps(
                        {"issuer": issuer, "cert_count": ct_result.get("cert_count", 0), "domain": domain, "san": san},
                        ensure_ascii=False,
                    ),
                )
            )
        return findings
