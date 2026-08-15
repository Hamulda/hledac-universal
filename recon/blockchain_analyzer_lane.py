"""
intelligence/blockchain_analyzer_lane.py — F320+: Blockchain Analyzer Lane

Thin subclass of BaseIntelligenceLane for blockchain forensics.

Wraps BlockchainForensics from blockchain_analyzer.py.

LaneSpec:
    concurrent_queries=2 (API rate limits + network I/O per chain)
    cost_estimate_per_query=3 (external API calls + parsing cost)
"""


import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any






    BTC_ADDRESS_PATTERN,
    BaseIntelligenceLane,
    ETH_ADDRESS_PATTERN,
    FetchResult,
    LaneContext,
    LaneSpec,
    ParsedResult,
    ResolveResult,
    TX_HASH_PATTERN,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

from _core import aclose

class BlockchainAnalyzerLane(BaseIntelligenceLane):
    """
    Blockchain forensics lane for cryptocurrency address analysis.

    Env gate: HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER
    Priority: 4 (lower priority — supplementary intelligence)
    RAM budget: 60 MB

    Phase implementation:
        resolve: classify as bitcoin/ethereum address, validate format
        fetch: query BlockchainForensics for transactions + risk score
        parse: extract addresses, amounts, labels, risk indicators
        dedup: inherited — uses address+chain as key
        emit: inherited — one finding per IOC type

    Note: Experimental/Hard containment — requires API keys (Etherscan/Blockchair).
    See blockchain_analyzer.py PROMOTION GATE comments.
    """

    __slots__ = ("_forensics", "_api_keys")

    sidecar_id: str = "blockchain"
    env_gate: str = "HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER"
    ram_budget_mb: int = 60
    priority: int = 4
    lane_spec: LaneSpec = LaneSpec(concurrent_queries=2, cost_estimate_per_query=3)

    MAX_BLOOM_ENTRIES: int = 2000
    MAX_CACHE_SIZE: int = 300

    def __init__(self) -> None:
        super().__init__()
        self._forensics: Any | None = None
        self._api_keys: dict[str, str | None] = {}

    def is_available(self) -> bool:
        """Check env gate."""
        return super().is_available()

    # -------------------------------------------------------------------------
    # Phase 1: Resolve
    # -------------------------------------------------------------------------

    async def resolve(self, target: str, ctx: LaneContext) -> ResolveResult:
        """
        Classify and validate a cryptocurrency address.

        Detects: bitcoin (bc1/1/3 prefix), ethereum (0x prefix).
        Returns ResolveResult with chain kind and normalized address.
        Uses sprint_mode from ctx to include risk scoring in aggressive mode.
        """
        target = target.strip()
        aggressive = ctx.sprint_mode == "aggressive"

        # Bitcoin
        btc_matches = BTC_ADDRESS_PATTERN.findall(target)
        if btc_matches:
            addr = btc_matches[0]
            return ResolveResult(
                resolved=addr,
                kind="bitcoin",
                metadata={"chain": "bitcoin", "address": addr, "aggressive": aggressive},
            )

        # Ethereum
        eth_matches = ETH_ADDRESS_PATTERN.findall(target)
        if eth_matches:
            addr = eth_matches[0].lower()
            return ResolveResult(
                resolved=addr,
                kind="ethereum",
                metadata={"chain": "ethereum", "address": addr, "aggressive": aggressive},
            )

        # Unknown — treat as raw address string
        return ResolveResult(
            resolved=target,
            kind="unknown",
            metadata={"original": target},
        )

    # -------------------------------------------------------------------------
    # Phase 2: Fetch
    # -------------------------------------------------------------------------

    async def fetch(self, resolved: ResolveResult, ctx: LaneContext) -> FetchResult:
        """
        Fetch blockchain analysis for the address.

        Uses BlockchainForensics.analyze_wallet() for address analysis.
        Falls back to direct API probe if forensics unavailable.
        """
        if resolved.kind not in ("bitcoin", "ethereum"):
            return FetchResult(
                url=resolved.resolved,
                status_code=0,
                error=f"unsupported_chain:{resolved.kind}",
            )

        forensics = await self._get_forensics()
        if forensics is None:
            return FetchResult(
                url=resolved.resolved,
                status_code=0,
                error="forensics_unavailable",
            )

        chain = resolved.metadata.get("chain", resolved.kind)

        # Load API keys lazily
        if not self._api_keys:
            import os
            self._api_keys = {
                "etherscan": os.getenv("ETHERSCAN_API_KEY"),
                "blockchair": os.getenv("BLOCKCHAIR_API_KEY"),
            }

        semaphore = self._get_semaphore()
        async with semaphore:
            start = time.monotonic()
            try:
                # Adaptive timeout based on memory pressure
                base_timeout = 30.0
                timeout = base_timeout * (1.0 - ctx.memory_pressure * 0.4) if ctx.memory_pressure else base_timeout

                async with asyncio.timeout(timeout):
                    analysis = await forensics.analyze_wallet(
                        resolved.resolved,
                        chain=chain,
                    )
                    elapsed_ms = (time.monotonic() - start) * 1000

                    # Serialize key fields
                    body = self._serialize_analysis(analysis)
                    return FetchResult(
                        url=f"{chain}:{resolved.resolved}",
                        status_code=200,
                        body=body,
                        elapsed_ms=elapsed_ms,
                    )

            except TimeoutError:
                return FetchResult(
                    url=f"{chain}:{resolved.resolved}",
                    status_code=0,
                    error="timeout",
                    elapsed_ms=(time.monotonic() - start) * 1000,
                )
            except Exception as exc:
                logger.debug("blockchain_lane.fetch error: %s", exc)
                return FetchResult(
                    url=f"{chain}:{resolved.resolved}",
                    status_code=0,
                    error=f"fetch_error:{type(exc).__name__}",
                    elapsed_ms=(time.monotonic() - start) * 1000,
                )

    # -------------------------------------------------------------------------
    # Phase 3: Parse
    # -------------------------------------------------------------------------

    async def parse(self, fetch_result: FetchResult, ctx: LaneContext) -> ParsedResult:
        """
        Parse blockchain analysis for IOCs.

        Extracts: addresses, transaction hashes, labels, risk indicators.
        """
        if fetch_result.error:
            return ParsedResult(raw_payload="", confidence=0.0)

        body = fetch_result.body
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="ignore")

        # Memory-pressure-adaptive: cap extraction when under memory pressure
        mp = ctx.memory_pressure
        max_per_type = 50 if mp < 0.5 else (20 if mp < 0.8 else 5)

        import re

        iocs: dict[str, list[str]] = {}

        # Extract BTC addresses (shared pattern from lane.py)
        btc_addrs = BTC_ADDRESS_PATTERN.findall(body)
        if btc_addrs:
            iocs["bitcoin"] = list(set(btc_addrs))[:max_per_type]

        # Extract ETH addresses (shared pattern from lane.py)
        eth_addrs = ETH_ADDRESS_PATTERN.findall(body)
        if eth_addrs:
            iocs["ethereum"] = list(set(eth_addrs))[:max_per_type]

        # Transaction hashes (shared pattern from lane.py)
        txs = TX_HASH_PATTERN.findall(body)
        if txs:
            iocs["tx_hash"] = list(set(txs[:50]))[:max_per_type]

        # Risk-related keywords
        risk_keywords = ["mixer", "darknet", "gambling", "exchange", "known", "suspicious"]
        detected_risks = [kw for kw in risk_keywords if kw.lower() in body.lower()]
        if detected_risks:
            iocs["risk_tag"] = detected_risks[:10]

        # Extract balance if present
        balance_pattern = re.compile(r"balance[:\s]*([0-9.,]+)\s*(?:BTC|ETH|USD)?", re.IGNORECASE)
        balances = balance_pattern.findall(body)
        if balances:
            iocs["balance"] = balances[:10]

        return ParsedResult(
            iocs=iocs,
            raw_payload=body[:3000],
            title=f"Blockchain: {fetch_result.url}",
            confidence=0.85 if iocs else 0.4,
            metadata={"chain": fetch_result.url.split(":")[0] if ":" in fetch_result.url else "unknown"},
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _serialize_analysis(self, analysis: Any) -> str:
        """Serialize a WalletAnalysis object to string."""
        try:
            parts = [
                f"address={getattr(analysis, 'address', 'unknown')}",
                f"balance={getattr(analysis, 'balance', 'unknown')}",
                f"tx_count={getattr(analysis, 'tx_count', 0)}",
            ]
            # Add known labels if available
            labels = getattr(analysis, "labels", []) or []
            if labels:
                parts.append(f"labels={','.join(labels[:10])}")
            # Add risk score
            risk = getattr(analysis, "risk_score", None)
            if risk is not None:
                parts.append(f"risk={risk}")
            return "\n".join(parts)
        except Exception:
            return str(analysis)

    async def _get_forensics(self) -> Any | None:
        """Lazy-initialize BlockchainForensics."""
        if self._forensics is not None:
            return self._forensics
        try:
            from hledac.universal.recon.blockchain_analyzer import BlockchainForensics
            self._forensics = BlockchainForensics(
                etherscan_api_key=self._api_keys.get("etherscan"),
                blockchair_api_key=self._api_keys.get("blockchair"),
            )
            return self._forensics
        except ImportError:
            return None

    async def close(self) -> None:
        """Close forensics client."""
        if self._forensics is not None:
            try:
                await self._forensics.close()
            except Exception:  # noqa: BLE001
                pass
            self._forensics = None


__all__ = ["BlockchainAnalyzerLane"]
