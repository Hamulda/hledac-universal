"""Open Storage Scanner — discovers exposed S3, Firebase, Elasticsearch, Mongo buckets.

HEIST-08 (2026-07): Extended with native protocol extraction mode.
After positive HTTP detection, also probes common database ports and,

when HLEDAC_ENABLE_NATIVE_EXTRACTION=1, performs wire-protocol data
extraction via Rust native_db or pure-Python ES HTTP.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from hledac.universal.network.session_runtime import async_get_httpx_session

logger = logging.getLogger(__name__)

# ── Database ports for extraction mode ──────────────────────────────────────
# Mirrors DatabasePortScanner.DATABASE_PORTS in recon/exposed_service_hunter.py
# but scoped to the three databases with native extraction support.
_DB_EXTRACTION_PORTS: dict[int, str] = {
    27017: "mongodb",
    27018: "mongodb",
    6379: "redis",
    6380: "redis",
    9200: "elasticsearch",
}


class _OpenStorageScanner:
    """Scans for exposed cloud storage buckets and (optionally) open databases."""

    MAX_GUESSES_PER_DOMAIN = 15
    # F185D: use session_runtime canonical constants
    _CONNECT_TIMEOUT_S: float = 10.0  # canonical HTML connect
    _READ_TIMEOUT_S: float = 5.0  # HEAD request — short read
    _PORT_SCAN_TIMEOUT_S: float = 3.0  # TCP port probe — quick

    # ── URL Guess Generation ───────────────────────────────────────────────

    def _generate_guesses(self, domain: str) -> list[str]:
        """Generate a list of potential bucket URLs (only external services)."""
        domain = domain.split(":")[0]
        parts = domain.split(".")
        base_domain = parts[-2] + "." + parts[-1] if len(parts) >= 2 else domain
        name = parts[0] if parts else base_domain

        guesses = [
            # S3
            f"https://{name}.s3.amazonaws.com",
            f"https://{base_domain}.s3.amazonaws.com",
            f"https://s3.amazonaws.com/{name}/",
            f"https://{domain}-assets.s3.amazonaws.com",
            f"https://{domain}-backup.s3.amazonaws.com",
            # Firebase
            f"https://{name}.firebaseio.com",
            f"https://{base_domain}.firebaseio.com",
            # Elasticsearch
            f"https://{name}.es.amazonaws.com",
            f"https://{base_domain}.es.amazonaws.com",
            # MongoDB Atlas
            f"https://{name}.mongodb.net",
            f"https://{base_domain}.mongodb.net",
        ]
        return list(dict.fromkeys(guesses))[: self.MAX_GUESSES_PER_DOMAIN]

    # ── Cloud Storage HTTP Scan (existing) ─────────────────────────────────

    async def scan_domain(
        self,
        domain: str,
        *,
        extraction_mode: bool = False,
    ) -> list[dict[str, Any]]:
        """Scan a single domain for open storage.

        Args:
            domain: Domain name to scan.
            extraction_mode: If True, also probes database ports and attempts
                native protocol extraction (via native_extraction module).

        Returns:
            List of found URLs/databases with metadata. Each dict has at minimum
            ``url``/``host``, ``type``/``service``, and ``status``/``success``.
        """
        guesses = self._generate_guesses(domain)
        if not guesses:
            return []

        session = await async_get_httpx_session()

        # P1-02: Parallelizace — 15 URL guesses paralelně místo sekvenčně
        from hledac.universal.utils.asyncx import parallel

        async def _check_url(url: str) -> dict[str, Any] | None:
            """Check single URL for open bucket. Returns result dict or None."""
            try:
                async with asyncio.timeout(self._READ_TIMEOUT_S):
                    resp = await session.head(url)
                    if resp.status_code == 200:
                        content_type = resp.headers.get("Content-Type", "")
                        if "xml" in content_type or "json" in content_type or "html" in content_type:
                            return {
                                "url": url,
                                "status": resp.status_code,
                                "type": self._classify_bucket(url),
                                "headers": dict(resp.headers),
                            }
            except TimeoutError:  # noqa: BLE001
                pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass
            return None

        # P1-02: Parallel scan — concurrency=5 for M1 safety, collect all results
        results = await parallel(
            [_check_url(url) for url in guesses],
            policy="collect",
            concurrency=5,
            ctx="open_storage:scan_domain",
        )

        findings: list[dict[str, Any]] = [r for r in results.ok if r is not None]

        # ── HEIST-08: Native extraction mode ───────────────────────────────
        if extraction_mode:
            db_findings = await self._extract_from_domain(domain)
            findings.extend(db_findings)

        return findings

    # ── HEIST-08: Database port scan + extraction ──────────────────────────

    async def _extract_from_domain(self, domain: str) -> list[dict[str, Any]]:
        """Probe database ports and attempt native protocol extraction.

        Only runs when HLEDAC_ENABLE_NATIVE_EXTRACTION=1.
        Fail-soft: returns [] on any error, never raises.
        """
        try:
            from hledac.universal.network.native_extraction import (
                extract_from_exposed,
                is_native_extraction_enabled,
            )
        except Exception:
            return []

        if not is_native_extraction_enabled():
            return []

        open_ports: list[tuple[int, str]] = []
        host = domain.split(":")[0]  # strip port if present

        async def _probe_port(port: int, service: str) -> None:
            try:
                async with asyncio.timeout(self._PORT_SCAN_TIMEOUT_S):
                    _, writer = await asyncio.open_connection(host, port)
                writer.close()
                await writer.wait_closed()
                open_ports.append((port, service))
            except TimeoutError:  # noqa: BLE001
                pass
            except ConnectionRefusedError:  # noqa: BLE001
                pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass

        await parallel(
            [_probe_port(port, svc) for port, svc in _DB_EXTRACTION_PORTS.items()],
            policy="collect",
            concurrency=10,  # TCP probes are cheap, 10 concurrent is fine
            ctx="open_storage:port_probe",
        )

        if not open_ports:
            return []

        logger.debug(
            "HEIST-08: %d open DB ports on %s: %s",
            len(open_ports),
            domain,
            [(p, s) for p, s in open_ports],
        )

        findings: list[dict[str, Any]] = []
        for port, service in open_ports:
            try:
                result = await extract_from_exposed(host, port, service)
                if result is not None:
                    findings.append(
                        {
                            "host": host,
                            "port": port,
                            "service": service,
                            "success": result.success,
                            "error": result.error,
                            # MongoDB
                            "databases": result.databases,
                            "collections": dict(result.collections) if result.collections else None,
                            # Redis
                            "keys": result.keys,
                            "key_count": result.key_count,
                            # Elasticsearch
                            "indices": result.indices,
                            # Common
                            "auth_required": result.auth_required,
                            "extraction_source": "native_extraction",
                        }
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass

        return findings

    # ── Classification ─────────────────────────────────────────────────────

    def _classify_bucket(self, url: str) -> str:
        """Classify bucket type based on URL."""
        if "s3.amazonaws.com" in url:
            return "s3"
        if "firebaseio.com" in url:
            return "firebase"
        if "es.amazonaws.com" in url:
            return "elasticsearch"
        if "mongodb.net" in url:
            return "mongodb"
        return "unknown"
