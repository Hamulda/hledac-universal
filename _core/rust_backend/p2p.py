"""
P2P Harvest — Native Tokio DHT Crawler pro IPFS/TOR/I2P OSINT.

NEXTGEN-01: OSINT Game-Changer — Penetration neindexovaných darknet zdrojů

## Modulární P2P harvest API

Tento modul poskytuje přístup k nativním P2P harvesterům v Rust Tokio runtime:

1. **BT DHT (BitTorrent)**: dht_crawl_async()
   - BEP-5 DHT crawler v nativním Tokio (žádný GIL contention)
   - Bootstrap z veřejných DHT routerů
   - Hledání info_hash podle klíčového slova

2. **IPFS Kademlia**: ipfs_bitswap_crawl_async()
   - IPFS DHT crawler přes libp2p
   - CID → PeerID mapping
   - Multiplexed stream fetching

3. **Tor Consensus**: tor_consensus_scrape_async()
   - Tor directory authority scraper
   - Parsování consensus dokumentu
   - Získání seznamu relayů

4. **I2P LeaseSet**: i2p_leaseset_resolve_async()
   - I2P SAMv3 LeaseSet resolver
   - B32 address → Destination mapping
   - Přímé TCP bez SOCKS5 proxy

## Usage

```python
from hledac.universal._core.rust_backend.p2p import P2PHarvester

# Unified API
harvester = P2PHarvester()
findings = await harvester.harvest(
    keyword="ransomware",
    protocols=["ipfs", "tor", "i2p", "bt_dht"],
    duration_s=120,
    )

# Individual protocols
bt_findings = await harvester.dht_crawl("malware", duration_s=60)
ipfs_findings = await harvester.ipfs_crawl("darknet", duration_s=60)
tor_findings = await harvester.tor_scrape("onion", duration_s=60)
i2p_findings = await harvester.i2p_resolve("example.b32.i2p")
```

## Paměť (M1 8GB safe)

| Komponenta | Rezident |
|------------|----------|
| Tokio runtime | ~10MB |
| libp2p swarm | ~3MB |
| Arrow buffers | ~1MB |
| **Total** | **~16MB** |

## Závislosti

- Rust p2p_harvest module (p2p_harvest feature)
- shared_tokio runtime
- Python fallback: dht/kademlia_node.py (simulated mode)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator

from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.utils.source_types import SourceType
from _core._util import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Feature gate
_P2P_HARVEST_ENABLED: bool = os.getenv(
    "HLEDAC_ENABLE_P2P_HARVEST", "0"
).lower() in ("1", "true", "yes", "on")


def _get_rust_p2p_module():
    """Get the Rust p2p_harvest module if available."""
    try:
        import rust_extensions.p2p_harvest as _mod
        return _mod
    except ImportError:
        return None


def _get_stealth_bridge_module():
    """Get the stealth_bridge module if available."""
    try:
        import rust_extensions.stealth_bridge as _mod
        return _mod
    except ImportError:
        return None


class P2PHarvester:
    """
    Native P2P Harvester — unified API for IPFS/TOR/I2P/BT DHT OSINT.

    NEXTGEN-01: Implements native Tokio-based P2P crawlers with:
    - Zero GIL contention (native async/await)
    - SIMD IOC extraction in hot path
    - Arrow IPC streaming to Python
    - M1 8GB memory safety (bounded concurrency)
    """

    def __init__(
        self,
        max_concurrent_peers: int = 20,
        default_duration_s: int = 120,
        default_max_results: int = 100,
    ):
        """
        Initialize P2P Harvester.

        Args:
            max_concurrent_peers: Maximum concurrent peers per protocol (M1 8GB safe)
            default_duration_s: Default crawl duration
            default_max_results: Default max results per protocol
        """
        self.max_concurrent_peers = max_concurrent_peers
        self.default_duration_s = default_duration_s
        self.default_max_results = default_max_results

        # Check Rust module availability
        self._rust_module = _get_rust_p2p_module()
        self._bridge_module = _get_stealth_bridge_module()

        if self._rust_module is None and self._bridge_module is None:
            logger.warning(
                "[P2P] Rust p2p_harvest module not available. "
                "Set HLEDAC_ENABLE_P2P_HARVEST=1 or compile with --features p2p_harvest"
    )

    @property
    def is_available(self) -> bool:
        """Check if Rust P2P harvest module is available."""
        return self._rust_module is not None or self._bridge_module is not None

    def get_protocol_status(self) -> dict[str, bool]:
        """
        Get status of P2P protocols.

        Returns:
            Dict of protocol -> available status
        """
        if self._bridge_module is not None:
            try:
                return dict(self._bridge_module.get_p2p_protocol_status())
            except Exception as e:
                logger.debug(f"[P2P] get_protocol_status error: {e}")

        # Fallback: check environment
        return {
            "bt_dht": True,  # Always available (Python fallback)
            "ipfs": _P2P_HARVEST_ENABLED,
            "tor": _P2P_HARVEST_ENABLED,
            "i2p": _P2P_HARVEST_ENABLED,
        }

    async def harvest(
        self,
        keyword: str,
        protocols: list[str] | None = None,
        duration_s: int | None = None,
        max_results: int | None = None,
    ) -> list[CanonicalFinding]:
        """
        Unified P2P harvest — searches multiple protocols concurrently.

        This is the main entry point for P2P OSINT harvesting.

        Args:
            keyword: Search keyword
            protocols: List of protocols to search
                      ["ipfs", "tor", "i2p", "bt_dht"]
                      Default: ["bt_dht"] (always available)
            duration_s: Crawl duration (default: 120)
            max_results: Max results per protocol (default: 100)

        Returns:
            List of CanonicalFinding from all protocols
        """
        if protocols is None:
            protocols = ["bt_dht"]

        duration_s = duration_s or self.default_duration_s
        max_results = max_results or self.default_max_results

        if not self.is_available:
            logger.warning("[P2P] Rust module unavailable, using Python fallback")
            return await self._python_fallback_harvest(keyword, protocols, duration_s, max_results)

        # Use Rust implementation
        if self._rust_module is not None:
            try:
                findings = await self._rust_module.harvest(
                    keyword=keyword,
                    protocols=protocols,
                    duration_s=duration_s,
                    max_results=max_results,
    )
                return self._convert_to_canonical_findings(findings, keyword)
            except Exception as e:
                logger.warning(f"[P2P] Rust harvest failed: {e}, falling back to Python")
                return await self._python_fallback_harvest(keyword, protocols, duration_s, max_results)

        # Use stealth_bridge delegation
        if self._bridge_module is not None:
            try:
                findings = await self._bridge_module.p2p_harvest_bridge(
                    keyword=keyword,
                    protocols=protocols,
                    duration_s=duration_s,
                    max_results=max_results,
    )
                return self._convert_to_canonical_findings(findings, keyword)
            except Exception as e:
                logger.warning(f"[P2P] Bridge harvest failed: {e}, falling back to Python")
                return await self._python_fallback_harvest(keyword, protocols, duration_s, max_results)

        return []

    async def dht_crawl(
        self,
        keyword: str,
        duration_s: int | None = None,
        max_results: int | None = None,
    ) -> list[CanonicalFinding]:
        """
        BitTorrent DHT crawler — native Tokio implementation.

        Args:
            keyword: Search keyword
            duration_s: Crawl duration (default: 120)
            max_results: Max results (default: 100)

        Returns:
            List of CanonicalFinding from BT DHT
        """
        return await self.harvest(
            keyword=keyword,
            protocols=["bt_dht"],
            duration_s=duration_s,
            max_results=max_results,
    )

    async def ipfs_crawl(
        self,
        keyword: str,
        duration_s: int | None = None,
        max_results: int | None = None,
    ) -> list[CanonicalFinding]:
        """
        IPFS Kademlia + BitSwap crawler.

        Args:
            keyword: Search keyword
            duration_s: Crawl duration (default: 120)
            max_results: Max results (default: 100)

        Returns:
            List of CanonicalFinding from IPFS network
        """
        return await self.harvest(
            keyword=keyword,
            protocols=["ipfs"],
            duration_s=duration_s,
            max_results=max_results,
    )

    async def tor_scrape(
        self,
        keyword: str,
        duration_s: int | None = None,
        max_results: int | None = None,
    ) -> list[CanonicalFinding]:
        """
        Tor consensus directory scraper.

        Args:
            keyword: Search keyword (used for finding tagging)
            duration_s: Crawl duration (default: 120)
            max_results: Max results (default: 100)

        Returns:
            List of CanonicalFinding from Tor network
        """
        return await self.harvest(
            keyword=keyword,
            protocols=["tor"],
            duration_s=duration_s,
            max_results=max_results,
    )

    async def i2p_resolve(
        self,
        b32_addr: str,
        duration_s: int | None = None,
    ) -> list[CanonicalFinding]:
        """
        I2P LeaseSet resolver.

        Args:
            b32_addr: I2P B32 address (e.g., "example.b32.i2p")
            duration_s: Resolution timeout (default: 30)

        Returns:
            List of CanonicalFinding from I2P network
        """
        duration_s = duration_s or 30

        if not self.is_available:
            logger.warning("[P2P] Rust module unavailable for I2P")
            return []

        if self._rust_module is not None:
            try:
                findings = await self._rust_module.i2p_leaseset_resolve_async(
                    b32_addr=b32_addr,
                    duration_s=duration_s,
    )
                return self._convert_to_canonical_findings(findings, b32_addr)
            except Exception as e:
                logger.warning(f"[P2P] I2P resolve failed: {e}")
                return []

        return []

    def _convert_to_canonical_findings(
        self,
        raw_findings: list[dict[str, Any]],
        keyword: str,
    ) -> list[CanonicalFinding]:
        """
        Convert raw Rust findings to CanonicalFinding list.

        Args:
            raw_findings: Raw findings from Rust module
            keyword: Original search keyword

        Returns:
            List of CanonicalFinding
        """
        findings: list[CanonicalFinding] = []
        import time

        for raw in raw_findings:
            try:
                source_type = self._protocol_to_source_type(raw.get("protocol", "bt_dht"))
                confidence = float(raw.get("confidence", 0.5))

                finding = CanonicalFinding(
                    finding_id=raw.get("finding_id", f"p2p-{keyword[:8]}"),
                    query=keyword,
                    source_type=source_type,
                    confidence=confidence,
                    ts=float(raw.get("timestamp", time.time())),
                    provenance=(f"p2p:{raw.get('protocol', 'unknown')}",),
                    payload_text=raw.get("payload", ""),
    )
                findings.append(finding)
            except Exception as e:
                logger.debug(f"[P2P] Finding conversion error: {e}")

        return findings

    def _protocol_to_source_type(self, protocol: str) -> SourceType:
        """Map P2P protocol to SourceType."""
        mapping = {
            "bt_dht": SourceType.DHT_METADATA,
            "ipfs": SourceType.DHT_METADATA,  # TODO: Add IPFS source type
            "tor": SourceType.DHT_METADATA,   # TODO: Add Tor source type
            "i2p": SourceType.DHT_METADATA,  # TODO: Add I2P source type
        }
        return mapping.get(protocol, SourceType.DHT_METADATA)

    async def _python_fallback_harvest(
        self,
        keyword: str,
        protocols: list[str],
        duration_s: int,
        max_results: int,
    ) -> list[CanonicalFinding]:
        """
        Python fallback for P2P harvest when Rust module unavailable.

        Uses dht/kademlia_node.py in simulated mode.
        """
        findings: list[CanonicalFinding] = []

        if "bt_dht" in protocols:
            try:
                from hledac.universal.dht.kademlia_node import crawl_dht_for_keyword
                results = await crawl_dht_for_keyword(
                    keyword=keyword,
                    duration_s=duration_s,
                    max_results=max_results,
                    harvest_metadata=False,
    )
                import time
                for r in results:
                    finding = CanonicalFinding(
                        finding_id=f"bt-dht-{r.get('info_hash', '')[:16]}",
                        query=keyword,
                        source_type=SourceType.DHT_METADATA,
                        confidence=0.5,  # Lower confidence for Python fallback
                        ts=time.time(),
                        provenance=("bt_dht:python_fallback",),
                        payload_text=str(r),
    )
                    findings.append(finding)
            except Exception as e:
                logger.warning(f"[P2P] Python DHT fallback failed: {e}")

        return findings[:max_results]


# Convenience function for quick harvest
async def harvest_p2p(
    keyword: str,
    protocols: list[str] | None = None,
    duration_s: int = 120,
    max_results: int = 100,
) -> list[CanonicalFinding]:
    """
    Quick P2P harvest — convenience function.

    Args:
        keyword: Search keyword
        protocols: Protocols to search (default: ["bt_dht"])
        duration_s: Crawl duration (default: 120)
        max_results: Max results (default: 100)

    Returns:
        List of CanonicalFinding
    """
    harvester = P2PHarvester(default_duration_s=duration_s, default_max_results=max_results)
    return await harvester.harvest(keyword, protocols, duration_s, max_results)


def get_harvester_status() -> dict[str, Any]:
    """Get P2P harvester status for monitoring."""
    harvester = P2PHarvester()
    return {
        "available": harvester.is_available,
        "rust_module": _get_rust_p2p_module() is not None,
        "bridge_module": _get_stealth_bridge_module() is not None,
        "protocol_status": harvester.get_protocol_status(),
        "max_concurrent_peers": harvester.max_concurrent_peers,
        "gate": "HLEDAC_ENABLE_P2P_HARVEST",
    }
