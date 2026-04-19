"""
CTLogClient — Certificate Transparency log pivot přes crt.sh JSON API.

Sprint 8SC: CT log pivot pro doménový OSINT (SubjectAltNames, cert history).
B3: Max 1 request per 5s rate limit, 24h cache.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)


class CTLogClient:
    """Certificate Transparency log pivot přes crt.sh JSON API."""

    _CACHE_TTL = 86400  # 24h
    _RATE_LIMIT_S = 5.0

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._last_request: float = 0.0

    async def pivot_domain(
        self, domain: str, session: "aiohttp.ClientSession"
    ) -> dict:
        """Hlavní entry point — vrátí CT log findings pro doménu."""
        import aiohttp
        import xxhash

        cache_path = self._cache_dir / f"{xxhash.xxh64(domain.encode()).hexdigest()}.json"

        # Cache check
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

        # Cache write
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
