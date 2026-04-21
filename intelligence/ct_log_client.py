"""
CTLogClient — Certificate Transparency log pivot přes crt.sh JSON API.

Sprint 8SC: CT log pivot pro doménový OSINT (SubjectAltNames, cert history).
B3: Max 1 request per 5s rate limit, 24h cache.
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

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)


class CTLogClient:
    """Certificate Transparency log pivot přes crt.sh JSON API.

    NON-HOT-PATH surface — owns its session lifecycle when used standalone.
    """

    _CACHE_TTL = 86400  # 24h
    _RATE_LIMIT_S = 5.0  # per-source rate limit (crt.sh: 1 req / 5s)

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._last_request: float = 0.0
        self._lock = asyncio.Lock()  # serialize concurrent pivots to same source

    async def pivot_domain(
        self, domain: str, session: "aiohttp.ClientSession"
    ) -> dict:
        """Hlavní entry point — vrátí CT log findings pro doménu.

        Serializes concurrent calls for the same domain via asyncio.Lock to prevent
        redundant crt.sh requests. Rate-limit guard is per-instance, not per-domain.
        """
        import aiohttp
        import xxhash

        cache_path = self._cache_dir / f"{xxhash.xxh64(domain.encode()).hexdigest()}.json"

        # Cache check (read-only, no lock needed)
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < self._CACHE_TTL:
                import orjson
                return orjson.loads(cache_path.read_bytes())

        # Serialize concurrent pivots to prevent redundant rate-limited requests
        async with self._lock:
            # Double-check cache after acquiring lock (another caller may have populated it)
            if cache_path.exists():
                age = time.time() - cache_path.stat().st_mtime
                if age < self._CACHE_TTL:
                    import orjson
                    return orjson.loads(cache_path.read_bytes())

            # Rate limit
            elapsed = time.time() - self._last_request
            if elapsed < self._RATE_LIMIT_S:
                await asyncio.sleep(self._RATE_LIMIT_S - elapsed)

            url = f"https://crt.sh/?q=%.{domain}&output=json"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    raw = await resp.json(content_type=None)
            except Exception as e:
                logger.warning(f"crt.sh {domain}: {e}")
                return {
                    "domain": domain,
                    "san_names": [],
                    "cert_count": 0,
                    "issuers": [],
                    "first_cert": 0.0,
                    "last_cert": 0.0,
                }
            finally:
                self._last_request = time.time()

        result = self._parse_crt_response(domain, raw)

        # Cache write (outside lock — no throttle needed)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        import orjson
        cache_path.write_bytes(orjson.dumps(result))
        return result

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
        self, domain: str, session: "aiohttp.ClientSession"
    ) -> list[dict]:
        """Vrátí seznam certifikátů pro doménu z crt.sh.

        Každý dict: subject_common_name, issuer, valid_from, valid_to, alt_names.
        Používá stejný rate-limit a cache jako pivot_domain().
        """
        import aiohttp
        import xxhash

        cache_key = f"certs_{xxhash.xxh64(domain.encode()).hexdigest()}.json"
        cache_path = self._cache_dir / cache_key

        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < self._CACHE_TTL:
                import orjson
                return orjson.loads(cache_path.read_bytes())

        async with self._lock:
            if cache_path.exists():
                age = time.time() - cache_path.stat().st_mtime
                if age < self._CACHE_TTL:
                    import orjson
                    return orjson.loads(cache_path.read_bytes())

            elapsed = time.time() - self._last_request
            if elapsed < self._RATE_LIMIT_S:
                await asyncio.sleep(self._RATE_LIMIT_S - elapsed)

            url = f"https://crt.sh/?q=%.{domain}&output=json"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    resp.raise_for_status()
                    raw = await resp.json(content_type=None)
            except Exception as e:
                logger.warning(f"crt.sh fetch_certificates {domain}: {e}")
                return []
            finally:
                self._last_request = time.time()

        certs = self._parse_certs(raw)

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        import orjson
        cache_path.write_bytes(orjson.dumps(certs))
        return certs

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
        self, ct_result: dict, ioc_graph: "IOCGraph"
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

        MAX = 50
        findings = []
        ts = ct_result.get("last_cert") or time.time()
        issuer = ct_result.get("issuers", [None])[0] if ct_result.get("issuers") else ""
        domain = ct_result.get("domain", "")

        for san in san_names[:MAX]:
            finding_id = f"ct_{hashlib.sha1(san.encode()).hexdigest()[:16]}"
            findings.append(
                CanonicalFinding(
                    finding_id=finding_id,
                    query=query,
                    source_type="ct_log",
                    confidence=0.75,
                    ts=ts,
                    provenance=("ct_log", domain),
                    payload_text=json.dumps(
                        {"issuer": issuer, "cert_count": ct_result.get("cert_count", 0), "domain": domain},
                        ensure_ascii=False,
                    ),
                )
            )
        return findings
