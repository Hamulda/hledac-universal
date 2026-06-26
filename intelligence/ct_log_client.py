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
    F266: Secondary CT provider chain:
      1. crt.sh primary (domain circuit breaker OPEN → skip to certspotter)
      2. certspotter.io (REST API, free tier, no auth, JSON array with dns_names)
      3. crt.sh identity search (last resort, same provider different query)
    ct_provider_selected field: "crtsh" | "certspotter" | "crtsh_identity" | "all_failed"
    """

    _CACHE_TTL = 86400  # 24h
    _RATE_LIMIT_S = 5.0  # per-source rate limit (crt.sh: 1 req / 5s)
    _CERTSPOTTER_RATE_LIMIT_S = 3.0  # certspotter: 1 req / 3s

    # F266: CT provider URLs
    _CRT_SH_URL = "https://crt.sh/?q=%25.{domain}&output=json"
    # certspotter.io — free REST API, returns JSON array of {dns_names: [...], ...}
    _CERTSPOTTER_URL = "https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names"
    # Last resort: crt.sh identity search (same provider, different query pattern)
    _CRT_SH_IDENTITY_URL = "https://crt.sh/?q={domain}&output=json"

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

            # F266: Try crt.sh first, fall back to certspotter.io + crt.sh identity on failure
            try:
                raw, provider = await self._fetch_ct_with_fallback(domain, session)
                if raw is None:
                    # All providers failed
                    logger.warning(f"CT log {domain}: all providers failed")
                    return {
                        "domain": domain,
                        "san_names": [],
                        "cert_count": 0,
                        "issuers": [],
                        "first_cert": 0.0,
                        "last_cert": 0.0,
                        "ct_provider_selected": "all_failed",
                    }
            finally:
                self._last_request = time.time()

        result = self._parse_crt_response(domain, raw)
        result["ct_provider_selected"] = provider or "unknown"

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
    ) -> tuple[list | None, str | None]:
        """F266: Chain: crt.sh → certspotter.io → crt.sh identity search.

        If crt.sh circuit breaker is OPEN (rate-limited), skip it and try certspotter.
        If certspotter fails, try crt.sh identity search as last resort.
        Returns (raw_entries, provider_name) from first successful provider,
        or (None, None) if all fail.
        """
        import aiohttp

        from hledac.universal.transport.circuit_breaker import (
            checked_aiohttp_get,
            domain_breaker_check,
        )

        # ── Provider 1: crt.sh primary ─────────────────────────────────────────
        crtsh_decision = domain_breaker_check("crt.sh")
        if crtsh_decision.allowed:
            url = self._CRT_SH_URL.format(domain=domain)
            raw, _status, err = await checked_aiohttp_get(
                session,
                url,
                timeout=aiohttp.ClientTimeout(total=5),
                failure_kind="crtsh_ct",
            )
            if not err and isinstance(raw, list) and raw:
                logger.info(f"CT log {domain}: crt.sh succeeded ({len(raw)} entries)")
                return raw, "crtsh"
            logger.warning(f"crt.sh {domain}: {err}, trying certspotter.io")

        # ── Provider 2: certspotter.io (free REST API) ─────────────────────────
        elapsed = time.time() - self._last_certstream_request
        if elapsed < self._CERTSPOTTER_RATE_LIMIT_S:
            await asyncio.sleep(self._CERTSPOTTER_RATE_LIMIT_S - elapsed)

        raw = await self._fetch_certspotter(domain, session)
        if raw is not None:
            logger.info(f"CT log {domain}: certspotter succeeded ({len(raw)} entries)")
            self._last_certstream_request = time.time()
            return raw, "certspotter"
        logger.warning(f"certspotter {domain}: failed, trying crt.sh identity search")

        # ── Provider 3: crt.sh identity (separate circuit breaker: crt.sh.identity) ──
        # P2-2: crt.sh identity uses its OWN circuit breaker domain so that when
        # crt.sh primary is OPEN, the identity fallback is NOT blocked.
        # checked_aiohttp_get extracts domain from URL → we call domain_breaker_record_failure
        # with "crt.sh.identity" to keep it independent from crt.sh primary.
        elapsed = time.time() - self._last_certstream_request
        if elapsed < self._CERTSPOTTER_RATE_LIMIT_S:
            await asyncio.sleep(self._CERTSPOTTER_RATE_LIMIT_S - elapsed)

        crtsh_identity_decision = domain_breaker_check("crt.sh.identity")
        if crtsh_identity_decision.allowed:
            raw, _status, err = await checked_aiohttp_get(
                session,
                self._CRT_SH_IDENTITY_URL.format(domain=domain),
                timeout=aiohttp.ClientTimeout(total=5),
                failure_kind="crtsh_ct_identity",
            )
            if err:
                logger.warning(f"CT crt.sh identity {domain}: {err}")
            elif isinstance(raw, list) and len(raw) > 0:
                logger.info(f"CT log {domain}: crt.sh identity succeeded ({len(raw)} entries)")
                self._last_certstream_request = time.time()
                return raw, "crtsh_identity"
        else:
            logger.warning(
                f"CT crt.sh identity {domain}: circuit breaker open "
                f"({crtsh_identity_decision.reason}), skipping identity fallback"
            )

        return None, None

    async def _fetch_certspotter(
        self, domain: str, session: aiohttp.ClientSession
    ) -> list | None:
        """F266: Fetch CT entries from certspotter.io REST API.

        certspotter returns: [{{dns_names: [...], serial_number: ..., issuer: ...}}]
        We extract dns_names from each entry — these are the SANs.
        Timeout 15s, max 50 items. No circuit breaker (independent provider).
        """
        import aiohttp

        from hledac.universal.transport.circuit_breaker import checked_aiohttp_get

        url = self._CERTSPOTTER_URL.format(domain=domain)
        raw, _status, err = await checked_aiohttp_get(
            session,
            url,
            timeout=aiohttp.ClientTimeout(total=15),
            failure_kind="certspotter_ct",
        )
        if err:
            logger.warning(f"certspotter {domain}: {err}")
            return None
        if not isinstance(raw, list):
            logger.warning(f"certspotter {domain}: unexpected response type {type(raw).__name__}")
            return None

        # F266: Parse certspotter response — each entry has dns_names list
        entries: list[dict] = []
        for item in raw[:50]:  # max 50 items
            dns_names = item.get("dns_names", [])
            if not isinstance(dns_names, list):
                continue
            for name in dns_names:
                name = name.strip().lstrip("*.")
                if name and "." in name and len(name) < 253:
                    # Normalize to crt.sh format: {name_value: ..., issuer_name: ..., not_before: ..., not_after: ...}
                    entries.append({
                        "name_value": name,
                        "issuer_name": item.get("issuer", {}).get("name", ""),
                        "not_before": item.get("not_before", ""),
                        "not_after": item.get("not_after", ""),
                        "serial_number": item.get("serial_number", ""),
                    })

        if not entries:
            logger.warning(f"certspotter {domain}: no valid dns_names in response")
            return None

        return entries

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
            # BUG-FIX: return [] inside try bypasses finally - restructure to avoid early return
            raw = None
            try:
                raw = await self._fetch_certificates_with_fallback(domain, session)
                if raw is None:
                    logger.warning(f"fetch_certificates {domain}: all providers failed")
            finally:
                self._last_request = time.time()
            if raw is None:
                return []

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
        """F266: Chain: crt.sh → certspotter.io → crt.sh identity for fetch_certificates.

        Mirrors _fetch_ct_with_fallback provider chain.
        """
        import aiohttp

        from hledac.universal.transport.circuit_breaker import (
            checked_aiohttp_get,
            domain_breaker_check,
        )

        # Provider 1: crt.sh primary
        crtsh_decision = domain_breaker_check("crt.sh")
        if crtsh_decision.allowed:
            url = self._CRT_SH_URL.format(domain=domain)
            raw, _status, err = await checked_aiohttp_get(
                session,
                url,
                timeout=aiohttp.ClientTimeout(total=5),
                failure_kind="crtsh_certs",
            )
            if not err and isinstance(raw, list) and len(raw) > 0:
                return raw
            logger.warning(f"crt.sh fetch_certificates {domain}: {err}, trying certspotter")

        # Provider 2: certspotter.io
        elapsed = time.time() - self._last_certstream_request
        if elapsed < self._CERTSPOTTER_RATE_LIMIT_S:
            await asyncio.sleep(self._CERTSPOTTER_RATE_LIMIT_S - elapsed)

        entries = await self._fetch_certspotter(domain, session)
        if entries is not None and len(entries) > 0:
            self._last_certstream_request = time.time()
            return entries
        logger.warning(f"certspotter fetch_certificates {domain}: failed, trying crt.sh identity")

        # Provider 3: crt.sh identity (separate circuit breaker: crt.sh.identity)
        # P2-2: Same per-provider isolation as _fetch_ct_with_fallback.
        elapsed = time.time() - self._last_certstream_request
        if elapsed < self._CERTSPOTTER_RATE_LIMIT_S:
            await asyncio.sleep(self._CERTSPOTTER_RATE_LIMIT_S - elapsed)

        crtsh_identity_decision = domain_breaker_check("crt.sh.identity")
        if crtsh_identity_decision.allowed:
            raw, _status, err = await checked_aiohttp_get(
                session,
                self._CRT_SH_IDENTITY_URL.format(domain=domain),
                timeout=aiohttp.ClientTimeout(total=5),
                failure_kind="crtsh_certs_identity",
            )
            if err:
                logger.warning(f"CT crt.sh identity fetch_certificates {domain}: {err}")
            elif isinstance(raw, list) and len(raw) > 0:
                self._last_certstream_request = time.time()
                return raw
        else:
            logger.warning(
                f"CT crt.sh identity fetch_certificates {domain}: "
                f"circuit breaker open ({crtsh_identity_decision.reason})"
            )
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
