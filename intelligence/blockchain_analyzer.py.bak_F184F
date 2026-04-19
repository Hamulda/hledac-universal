"""
Blockchain Forensics Module
===========================

PROMOTION GATE — EXPERIMENTAL / HEAVY / NOT PROMOTED
====================================================
Advanced blockchain analysis and forensics for cryptocurrency investigations.

STATUS: EXPERIMENTAL
  - 1478 lines, 0 call sites outside tests (grep audit: žádné volání v production kódu)
  - Etherscan API key a Blockchair API key vyžadovány, nejsou součástí config
  - async HTTP client (httpx) s rate limiting — síťová závislost na třetích stranách
  - KademliaNode používá tento modul? NE — dht/kademlia_node.py je SAMOSTATNÝ

M1 8GB MEMORY CEILING:
  - httpx.AsyncClient: max_connections=10, max_keepalive=5
  - In-memory cache: _cache dict, TTL=300s, NEBOUNDED (uloží unlimited API responses)
  - Transaction tracing: depth-first, max 100 tx, visited set pro dedup
  - clustering: načítá tx pro KAŽDOU adresu zvlášť (O(n) API calls)
  - ŽÁDNÝ memory ceiling na response cache = potential unbounded growth
  - Při cross_chain_analysis s 10 adresami = 10+ sequenciálních API calls

ALLOWED PURPOSE: Offline blockchain forensics research tool
  - Vyžaduje externí API keys (Etherscan/Blockchair)
  - Primární use case: post-factum analýza známých adres
  - NENÍ součástí real-time OSINT pipeline
  - NENÍ integrován do autonomous_orchestrator.py

PROMOTION ELIGIBILITY: NO
  - Žádné production call sites
  - Neintegrováno do canonical orchestrator path
  - API-dependent (Etherscan rate limits, Blockchair paid tier)
  - Unbounded cache = memory risk na M1 8GB

SECURITY: API keys by byly v .env — tento modul sám o sobě žádné neukládá.
STEALTH: Network traffic jde přímo na Etherscan/Blockchair — žádná anonymizace.
  Toto je FORENZÍ nástroj, ne OSINT stealth tool.

DŮLEŽITÉ POZNÁMKY K IMPLEMENTACI:
- crawl_dht_for_keyword() v kademlia_node.py používá BlockchainForensics? NE
- Tyto dva moduly jsou zcela nezávislé
- BlockchainForensics má vlastní httpx klient, ne používá curl_cffi z fetch_coordinator
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import httpx

logger = logging.getLogger(__name__)

# Optional imports for enhanced functionality
try:
    from hledac.core.http import fetch_json, safe_fetch
    HTTP_UTILS_AVAILABLE = True
except ImportError:
    HTTP_UTILS_AVAILABLE = False
    logger.debug("hledac.core.http not available, using direct httpx")


# =============================================================================
# ENUMS
# =============================================================================

class ChainType(Enum):
    """Supported blockchain types."""
    ETHEREUM = "ethereum"
    BITCOIN = "bitcoin"
    LITECOIN = "litecoin"
    BITCOIN_CASH = "bitcoin_cash"
    POLYGON = "polygon"
    ARBITRUM = "arbitrum"
    OPTIMISM = "optimism"


class EntityType(Enum):
    """Types of entities that can be identified."""
    EXCHANGE = "exchange"
    MIXER = "mixer"
    DEFI_PROTOCOL = "defi_protocol"
    INDIVIDUAL = "individual"
    CONTRACT = "contract"
    MINING_POOL = "mining_pool"
    PAYMENT_PROCESSOR = "payment_processor"
    UNKNOWN = "unknown"


class PatternType(Enum):
    """Types of transaction patterns."""
    PEEL_CHAIN = "peel_chain"
    ROUND_AMOUNT = "round_amount"
    MIXING = "mixing"
    LAYERING = "layering"
    EXCHANGE_DEPOSIT = "exchange_deposit"
    EXCHANGE_WITHDRAWAL = "exchange_withdrawal"
    DUSTING = "dusting"
    SLEEPING = "sleeping"
    RAPID_TRADING = "rapid_trading"


class RiskLevel(Enum):
    """Risk levels for addresses/transactions."""
    CRITICAL = 1.0
    HIGH = 0.75
    MEDIUM = 0.5
    LOW = 0.25
    MINIMAL = 0.0


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class Transaction:
    """Represents a blockchain transaction."""
    tx_hash: str
    timestamp: datetime
    from_address: str
    to_address: str
    value: float
    gas_used: Optional[int] = None
    gas_price: Optional[int] = None
    fee: Optional[float] = None
    block_number: Optional[int] = None
    confirmations: int = 0
    chain: str = "ethereum"
    is_contract_creation: bool = False
    input_data: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WalletAnalysis:
    """Comprehensive analysis of a wallet address."""
    address: str
    chain: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    total_received: float = 0.0
    total_sent: float = 0.0
    transaction_count: int = 0
    incoming_count: int = 0
    outgoing_count: int = 0
    linked_addresses: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    entity_type: EntityType = EntityType.UNKNOWN
    risk_score: float = 0.0
    balance: float = 0.0
    known_associations: List[str] = field(default_factory=list)


@dataclass
class TransactionPattern:
    """Detected pattern in transactions."""
    pattern_type: PatternType
    confidence: float
    transactions: List[str]
    description: str
    addresses_involved: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Cluster:
    """A cluster of related addresses."""
    cluster_id: str
    addresses: List[str]
    entity_type: EntityType
    confidence: float
    label: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CrossChainResult:
    """Result of cross-chain analysis."""
    primary_address: str
    related_addresses: Dict[str, List[str]]  # chain -> addresses
    potential_links: List[Tuple[str, str, float]]  # (addr1, addr2, confidence)
    risk_assessment: str
    overall_risk_score: float


@dataclass
class APIResponse:
    """Cached API response wrapper."""
    data: Any
    timestamp: datetime
    expires_at: datetime


# =============================================================================
# KNOWN SERVICES DATABASE
# =============================================================================

KNOWN_SERVICES: Dict[str, Dict[str, Any]] = {
    # Major Exchanges
    "0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE": {
        "name": "Binance",
        "type": EntityType.EXCHANGE,
        "tags": ["exchange", "major"]
    },
    "0x742d35Cc6634C0532925a3b844Bc9e7595f8dEe": {
        "name": "Coinbase",
        "type": EntityType.EXCHANGE,
        "tags": ["exchange", "major", "us_regulated"]
    },
    "0x8ba1f109551bD432803012645Hac136c82C3e8C9": {
        "name": "Kraken",
        "type": EntityType.EXCHANGE,
        "tags": ["exchange", "major"]
    },
    # Mixers
    "0x7FF9cFad3877F21d41Da833E2F775dB0569eE3D9": {
        "name": "Tornado.Cash",
        "type": EntityType.MIXER,
        "tags": ["mixer", "privacy", "sanctioned"],
        "risk_multiplier": 1.0
    },
    # DeFi Protocols
    "0x1F98431c8aD98523631AE4a59f267346ea31F984": {
        "name": "Uniswap V3",
        "type": EntityType.DEFI_PROTOCOL,
        "tags": ["defi", "dex", "amm"]
    },
    "0xE592427A0AEce92De3Edee1F18E0157C05861564": {
        "name": "Uniswap V3 Router",
        "type": EntityType.DEFI_PROTOCOL,
        "tags": ["defi", "dex", "router"]
    },
}

# Bitcoin address patterns
BITCOIN_PATTERNS = {
    "p2pkh": re.compile(r"^1[a-km-zA-HJ-NP-Z1-9]{25,34}$"),
    "p2sh": re.compile(r"^3[a-km-zA-HJ-NP-Z1-9]{25,34}$"),
    "bech32": re.compile(r"^bc1[a-z0-9]{39,59}$"),
}

# Ethereum address pattern
ETHEREUM_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")


# =============================================================================
# BLOCKCHAIN FORENSICS CLASS
# =============================================================================

class BlockchainForensics:
    """
    Advanced blockchain forensics and analysis tool.

    M1 8GB Optimized:
    - Async API calls with connection pooling
    - LRU caching for API responses (5 min TTL)
    - Streaming processing for large transaction histories
    - Minimal memory footprint
    """

    def __init__(
        self,
        etherscan_api_key: Optional[str] = None,
        blockchair_api_key: Optional[str] = None,
        cache_ttl_seconds: int = 300,
        max_concurrent_requests: int = 5,
    ):
        """
        Initialize BlockchainForensics.

        Args:
            etherscan_api_key: API key for Etherscan (Ethereum)
            blockchair_api_key: API key for Blockchair (Bitcoin, others)
            cache_ttl_seconds: Cache time-to-live in seconds (default: 300)
            max_concurrent_requests: Max concurrent API requests (default: 5)
        """
        self.etherscan_api_key = etherscan_api_key
        self.blockchair_api_key = blockchair_api_key
        self.cache_ttl = cache_ttl_seconds
        self.max_concurrent = max_concurrent_requests

        # In-memory cache
        self._cache: Dict[str, APIResponse] = {}
        self._cache_lock = asyncio.Lock()

        # HTTP client (initialized lazily)
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore: Optional[asyncio.Semaphore] = None

        # Rate limiting
        self._last_etherscan_call = 0.0
        self._last_blockchair_call = 0.0
        self._etherscan_delay = 0.2  # 5 requests/second max
        self._blockchair_delay = 0.5  # 2 requests/second max (free tier)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
            )
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._client

    async def _cached_request(
        self,
        cache_key: str,
        fetch_func,
        *args,
        **kwargs
    ) -> Any:
        """Make a cached API request."""
        async with self._cache_lock:
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                if datetime.now() < cached.expires_at:
                    logger.debug(f"Cache hit: {cache_key}")
                    return cached.data
                else:
                    del self._cache[cache_key]

        # Fetch fresh data
        data = await fetch_func(*args, **kwargs)

        # Cache the result
        async with self._cache_lock:
            self._cache[cache_key] = APIResponse(
                data=data,
                timestamp=datetime.now(),
                expires_at=datetime.now() + timedelta(seconds=self.cache_ttl),
            )

        return data

    async def _rate_limited_etherscan(self, url: str) -> Dict[str, Any]:
        """Make rate-limited Etherscan API call."""
        now = time.time()
        elapsed = now - self._last_etherscan_call
        if elapsed < self._etherscan_delay:
            await asyncio.sleep(self._etherscan_delay - elapsed)

        self._last_etherscan_call = time.time()
        client = await self._get_client()

        async with self._semaphore:
            try:
                if HTTP_UTILS_AVAILABLE:
                    return await fetch_json(url) or {}
                else:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.json()
            except Exception as e:
                logger.warning(f"Etherscan API error: {e}")
                return {"status": "0", "message": str(e)}

    async def _rate_limited_blockchair(self, url: str) -> Dict[str, Any]:
        """Make rate-limited Blockchair API call."""
        now = time.time()
        elapsed = now - self._last_blockchair_call
        if elapsed < self._blockchair_delay:
            await asyncio.sleep(self._blockchair_delay - elapsed)

        self._last_blockchair_call = time.time()
        client = await self._get_client()

        async with self._semaphore:
            try:
                if HTTP_UTILS_AVAILABLE:
                    return await fetch_json(url) or {}
                else:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.json()
            except Exception as e:
                logger.warning(f"Blockchair API error: {e}")
                return {"data": {}, "error": str(e)}

    def _generate_cluster_id(self, addresses: List[str]) -> str:
        """Generate a unique cluster ID from addresses."""
        sorted_addrs = sorted(addresses)
        hash_input = "".join(sorted_addrs).encode()
        return hashlib.sha256(hash_input).hexdigest()[:16]

    def _is_valid_address(self, address: str, chain: str = "ethereum") -> bool:
        """Validate address format for given chain."""
        if chain in ("ethereum", "polygon", "arbitrum", "optimism"):
            return bool(ETHEREUM_PATTERN.match(address))
        elif chain == "bitcoin":
            return any(
                pattern.match(address) for pattern in BITCOIN_PATTERNS.values()
            )
        return True  # Allow unknown formats

    # =========================================================================
    # WALLET ANALYSIS
    # =========================================================================

    async def analyze_wallet(
        self,
        address: str,
        chain: str = "ethereum"
    ) -> WalletAnalysis:
        """
        Perform comprehensive wallet analysis.

        Args:
            address: Wallet address to analyze
            chain: Blockchain type (ethereum, bitcoin, etc.)

        Returns:
            WalletAnalysis with comprehensive metrics
        """
        if not self._is_valid_address(address, chain):
            raise ValueError(f"Invalid address format for {chain}: {address}")

        analysis = WalletAnalysis(address=address, chain=chain)

        # Fetch data based on chain
        if chain == "ethereum":
            await self._analyze_ethereum_wallet(analysis)
        elif chain == "bitcoin":
            await self._analyze_bitcoin_wallet(analysis)
        else:
            logger.warning(f"Chain {chain} not fully supported, using generic analysis")
            await self._analyze_generic_wallet(analysis)

        # Identify known services
        analysis.tags = self.identify_known_services(address)

        # Calculate risk score
        analysis.risk_score = self.calculate_risk_score(analysis)

        return analysis

    async def _analyze_ethereum_wallet(self, analysis: WalletAnalysis) -> None:
        """Analyze Ethereum wallet using Etherscan."""
        if not self.etherscan_api_key:
            logger.warning("No Etherscan API key provided")
            return

        base_url = "https://api.etherscan.io/api"
        address = analysis.address

        # Get balance
        balance_url = (
            f"{base_url}?module=account&action=balance"
            f"&address={address}&tag=latest"
            f"&apikey={self.etherscan_api_key}"
        )
        balance_data = await self._cached_request(
            f"eth_balance_{address}",
            self._rate_limited_etherscan,
            balance_url
        )

        if balance_data.get("status") == "1":
            balance_wei = int(balance_data.get("result", 0))
            analysis.balance = balance_wei / 1e18

        # Get transactions (first page)
        tx_url = (
            f"{base_url}?module=account&action=txlist"
            f"&address={address}&startblock=0&endblock=99999999"
            f"&page=1&offset=100&sort=asc"
            f"&apikey={self.etherscan_api_key}"
        )
        tx_data = await self._cached_request(
            f"eth_tx_{address}_page1",
            self._rate_limited_etherscan,
            tx_url
        )

        if tx_data.get("status") == "1" and "result" in tx_data:
            transactions = tx_data["result"]
            analysis.transaction_count = len(transactions)

            if transactions:
                # First and last seen
                first_tx = transactions[0]
                last_tx = transactions[-1]
                analysis.first_seen = datetime.fromtimestamp(
                    int(first_tx.get("timeStamp", 0))
                )
                analysis.last_seen = datetime.fromtimestamp(
                    int(last_tx.get("timeStamp", 0))
                )

                # Calculate totals
                for tx in transactions:
                    value_eth = int(tx.get("value", 0)) / 1e18
                    from_addr = tx.get("from", "").lower()
                    to_addr = tx.get("to", "").lower()

                    if from_addr == address.lower():
                        analysis.total_sent += value_eth
                        analysis.outgoing_count += 1
                    elif to_addr == address.lower():
                        analysis.total_received += value_eth
                        analysis.incoming_count += 1

    async def _analyze_bitcoin_wallet(self, analysis: WalletAnalysis) -> None:
        """Analyze Bitcoin wallet using Blockchair."""
        address = analysis.address

        # Blockchair doesn't require API key for basic queries
        base_url = "https://api.blockchair.com/bitcoin/dashboards/address"
        url = f"{base_url}/{address}"

        if self.blockchair_api_key:
            url += f"?key={self.blockchair_api_key}"

        data = await self._cached_request(
            f"btc_address_{address}",
            self._rate_limited_blockchair,
            url
        )

        if "data" in data and address in data["data"]:
            addr_data = data["data"][address]["address"]
            analysis.balance = addr_data.get("balance", 0) / 1e8
            analysis.transaction_count = addr_data.get("transaction_count", 0)
            analysis.total_received = addr_data.get("received", 0) / 1e8
            analysis.total_sent = addr_data.get("spent", 0) / 1e8

            # First and last seen
            if addr_data.get("first_seen_receiving"):
                analysis.first_seen = datetime.fromtimestamp(
                    addr_data["first_seen_receiving"]
                )
            if addr_data.get("last_seen_spending"):
                analysis.last_seen = datetime.fromtimestamp(
                    addr_data["last_seen_spending"]
                )

    async def _analyze_generic_wallet(self, analysis: WalletAnalysis) -> None:
        """Generic wallet analysis when specific API unavailable."""
        logger.info(f"Performing generic analysis for {analysis.address}")
        # Placeholder for generic analysis
        pass

    # =========================================================================
    # TRANSACTION TRACING
    # =========================================================================

    async def trace_transactions(
        self,
        address: str,
        chain: str = "ethereum",
        depth: int = 2,
        max_transactions: int = 100
    ) -> List[Transaction]:
        """
        Trace transaction chains from an address.

        Args:
            address: Starting address
            chain: Blockchain type
            depth: How many hops to trace
            max_transactions: Maximum transactions to return

        Returns:
            List of Transaction objects
        """
        all_transactions: List[Transaction] = []
        visited: Set[str] = set()
        queue: List[Tuple[str, int]] = [(address, 0)]  # (address, depth)

        while queue and len(all_transactions) < max_transactions:
            current_addr, current_depth = queue.pop(0)

            if current_addr in visited or current_depth > depth:
                continue

            visited.add(current_addr)

            # Fetch transactions for this address
            txs = await self._fetch_transactions(current_addr, chain)

            for tx_data in txs:
                tx = self._parse_transaction(tx_data, chain)
                all_transactions.append(tx)

                # Add related addresses to queue if within depth
                if current_depth < depth:
                    if tx.from_address != current_addr:
                        queue.append((tx.from_address, current_depth + 1))
                    if tx.to_address != current_addr:
                        queue.append((tx.to_address, current_depth + 1))

        return all_transactions[:max_transactions]

    async def _fetch_transactions(
        self,
        address: str,
        chain: str
    ) -> List[Dict[str, Any]]:
        """Fetch raw transactions for an address."""
        if chain == "ethereum" and self.etherscan_api_key:
            return await self._fetch_ethereum_transactions(address)
        elif chain == "bitcoin":
            return await self._fetch_bitcoin_transactions(address)
        return []

    async def _fetch_ethereum_transactions(
        self,
        address: str
    ) -> List[Dict[str, Any]]:
        """Fetch Ethereum transactions from Etherscan."""
        base_url = "https://api.etherscan.io/api"
        url = (
            f"{base_url}?module=account&action=txlist"
            f"&address={address}&startblock=0&endblock=99999999"
            f"&page=1&offset=100&sort=desc"
            f"&apikey={self.etherscan_api_key}"
        )

        data = await self._cached_request(
            f"eth_txlist_{address}",
            self._rate_limited_etherscan,
            url
        )

        if data.get("status") == "1" and "result" in data:
            return data["result"]
        return []

    async def _fetch_bitcoin_transactions(
        self,
        address: str
    ) -> List[Dict[str, Any]]:
        """Fetch Bitcoin transactions from Blockchair."""
        base_url = "https://api.blockchair.com/bitcoin/dashboards/address"
        url = f"{base_url}/{address}?limit=100"

        if self.blockchair_api_key:
            url += f"&key={self.blockchair_api_key}"

        data = await self._cached_request(
            f"btc_txlist_{address}",
            self._rate_limited_blockchair,
            url
        )

        transactions = []
        if "data" in data and address in data["data"]:
            tx_data = data["data"][address].get("transactions", [])
            for tx_hash in tx_data[:100]:
                tx_detail = await self._fetch_bitcoin_transaction_detail(tx_hash)
                if tx_detail:
                    transactions.append(tx_detail)

        return transactions

    async def _fetch_bitcoin_transaction_detail(
        self,
        tx_hash: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch detailed Bitcoin transaction."""
        url = f"https://api.blockchair.com/bitcoin/dashboards/transaction/{tx_hash}"

        if self.blockchair_api_key:
            url += f"?key={self.blockchair_api_key}"

        data = await self._cached_request(
            f"btc_tx_{tx_hash}",
            self._rate_limited_blockchair,
            url
        )

        if "data" in data and tx_hash in data["data"]:
            return data["data"][tx_hash].get("transaction", {})
        return None

    def _parse_transaction(
        self,
        tx_data: Dict[str, Any],
        chain: str
    ) -> Transaction:
        """Parse raw transaction data into Transaction object."""
        if chain == "ethereum":
            timestamp = datetime.fromtimestamp(
                int(tx_data.get("timeStamp", 0))
            )
            return Transaction(
                tx_hash=tx_data.get("hash", ""),
                timestamp=timestamp,
                from_address=tx_data.get("from", ""),
                to_address=tx_data.get("to", ""),
                value=int(tx_data.get("value", 0)) / 1e18,
                gas_used=int(tx_data.get("gasUsed", 0)),
                gas_price=int(tx_data.get("gasPrice", 0)),
                block_number=int(tx_data.get("blockNumber", 0)),
                confirmations=int(tx_data.get("confirmations", 0)),
                chain=chain,
                is_contract_creation=tx_data.get("contractAddress") is not None,
                input_data=tx_data.get("input"),
            )
        elif chain == "bitcoin":
            timestamp = datetime.fromtimestamp(
                tx_data.get("time", 0) or tx_data.get("block_time", 0)
            )
            return Transaction(
                tx_hash=tx_data.get("hash", ""),
                timestamp=timestamp,
                from_address="",  # Bitcoin has multiple inputs
                to_address="",   # Bitcoin has multiple outputs
                value=tx_data.get("output_total", 0) / 1e8,
                fee=tx_data.get("fee", 0) / 1e8,
                block_number=tx_data.get("block_id"),
                chain=chain,
            )
        else:
            return Transaction(
                tx_hash=str(tx_data.get("hash", "")),
                timestamp=datetime.now(),
                from_address="",
                to_address="",
                value=0.0,
                chain=chain,
            )

    # =========================================================================
    # PATTERN DETECTION
    # =========================================================================

    async def detect_patterns(
        self,
        transactions: List[Transaction]
    ) -> List[TransactionPattern]:
        """
        Detect suspicious patterns in transactions.

        Args:
            transactions: List of transactions to analyze

        Returns:
            List of detected TransactionPattern objects
        """
        patterns: List[TransactionPattern] = []

        if not transactions:
            return patterns

        # Sort by timestamp
        sorted_txs = sorted(transactions, key=lambda x: x.timestamp)

        # Detect peel chains
        peel_chain = self._detect_peel_chain(sorted_txs)
        if peel_chain:
            patterns.append(peel_chain)

        # Detect round amounts
        round_amounts = self._detect_round_amounts(sorted_txs)
        if round_amounts:
            patterns.append(round_amounts)

        # Detect mixing patterns
        mixing = self._detect_mixing_patterns(sorted_txs)
        if mixing:
            patterns.append(mixing)

        # Detect layering
        layering = self._detect_layering(sorted_txs)
        if layering:
            patterns.append(layering)

        # Detect rapid trading
        rapid_trading = self._detect_rapid_trading(sorted_txs)
        if rapid_trading:
            patterns.append(rapid_trading)

        return patterns

    def _detect_peel_chain(
        self,
        transactions: List[Transaction]
    ) -> Optional[TransactionPattern]:
        """
        Detect peel chain pattern.

        A peel chain is a series of transactions where:
        1. A large amount is sent
        2. Change is returned to a new address
        3. Process repeats
        """
        if len(transactions) < 3:
            return None

        peel_candidates = []

        for i in range(len(transactions) - 1):
            tx1 = transactions[i]
            tx2 = transactions[i + 1]

            # Check if tx2 happens shortly after tx1
            time_diff = (tx2.timestamp - tx1.timestamp).total_seconds()
            if time_diff > 3600:  # More than 1 hour gap
                continue

            # Check for decreasing amounts (characteristic of peel chains)
            if tx1.value > tx2.value > 0:
                peel_candidates.append(tx1.tx_hash)

        if len(peel_candidates) >= 3:
            return TransactionPattern(
                pattern_type=PatternType.PEEL_CHAIN,
                confidence=min(0.9, 0.5 + len(peel_candidates) * 0.1),
                transactions=peel_candidates,
                description=(
                    f"Peel chain detected: {len(peel_candidates)} transactions "
                    "with decreasing amounts in quick succession"
                ),
            )

        return None

    def _detect_round_amounts(
        self,
        transactions: List[Transaction]
    ) -> Optional[TransactionPattern]:
        """Detect round amount patterns (common in exchange withdrawals)."""
        round_txs = []

        for tx in transactions:
            # Check if amount is round (e.g., 1.0, 0.5, 10.0)
            value = tx.value
            if value > 0:
                # Check for common round amounts
                rounded = round(value, 6)
                if rounded in [1.0, 0.1, 0.5, 2.0, 5.0, 10.0, 0.01, 0.001]:
                    round_txs.append(tx.tx_hash)
                elif value == int(value):  # Whole number
                    round_txs.append(tx.tx_hash)

        if len(round_txs) >= 3:
            return TransactionPattern(
                pattern_type=PatternType.ROUND_AMOUNT,
                confidence=min(0.8, 0.4 + len(round_txs) * 0.05),
                transactions=round_txs,
                description=(
                    f"Round amount pattern: {len(round_txs)} transactions "
                    "with round or whole number amounts"
                ),
            )

        return None

    def _detect_mixing_patterns(
        self,
        transactions: List[Transaction]
    ) -> Optional[TransactionPattern]:
        """Detect potential mixing/tumbling patterns."""
        # Group by time windows
        time_windows: Dict[str, List[Transaction]] = defaultdict(list)

        for tx in transactions:
            window_key = tx.timestamp.strftime("%Y-%m-%d-%H")
            time_windows[window_key].append(tx)

        mixing_candidates = []

        for window, txs in time_windows.items():
            if len(txs) >= 5:  # Many transactions in same hour
                # Check for similar amounts (characteristic of mixing)
                amounts = [tx.value for tx in txs if tx.value > 0]
                if len(amounts) >= 3:
                    avg = sum(amounts) / len(amounts)
                    variance = sum((a - avg) ** 2 for a in amounts) / len(amounts)
                    # Low variance in amounts suggests mixing
                    if variance < avg * 0.1:
                        mixing_candidates.extend([tx.tx_hash for tx in txs])

        if len(mixing_candidates) >= 5:
            return TransactionPattern(
                pattern_type=PatternType.MIXING,
                confidence=0.6,
                transactions=list(set(mixing_candidates)),
                description=(
                    f"Potential mixing detected: {len(mixing_candidates)} transactions "
                    "with similar amounts in tight time windows"
                ),
            )

        return None

    def _detect_layering(
        self,
        transactions: List[Transaction]
    ) -> Optional[TransactionPattern]:
        """Detect layering pattern (multiple hops to obscure trail)."""
        if len(transactions) < 5:
            return None

        # Count unique addresses
        addresses: Set[str] = set()
        for tx in transactions:
            addresses.add(tx.from_address)
            addresses.add(tx.to_address)

        # High address diversity with short time spans suggests layering
        time_span = transactions[-1].timestamp - transactions[0].timestamp

        if len(addresses) >= 5 and time_span < timedelta(hours=24):
            tx_hashes = [tx.tx_hash for tx in transactions]
            return TransactionPattern(
                pattern_type=PatternType.LAYERING,
                confidence=min(0.7, 0.3 + len(addresses) * 0.05),
                transactions=tx_hashes,
                description=(
                    f"Layering pattern: {len(addresses)} unique addresses "
                    f"in {time_span.total_seconds() / 3600:.1f} hours"
                ),
            )

        return None

    def _detect_rapid_trading(
        self,
        transactions: List[Transaction]
    ) -> Optional[TransactionPattern]:
        """Detect rapid trading pattern."""
        if len(transactions) < 10:
            return None

        # Check for high frequency in short time
        time_span = transactions[-1].timestamp - transactions[0].timestamp
        tx_rate = len(transactions) / max(time_span.total_seconds() / 3600, 0.001)

        if tx_rate > 10:  # More than 10 transactions per hour
            return TransactionPattern(
                pattern_type=PatternType.RAPID_TRADING,
                confidence=min(0.85, 0.4 + tx_rate * 0.02),
                transactions=[tx.tx_hash for tx in transactions],
                description=(
                    f"Rapid trading: {len(transactions)} transactions "
                    f"({tx_rate:.1f} per hour)"
                ),
            )

        return None

    # =========================================================================
    # ADDRESS CLUSTERING
    # =========================================================================

    async def cluster_addresses(
        self,
        addresses: List[str],
        chain: str = "ethereum"
    ) -> List[Cluster]:
        """
        Cluster addresses using heuristics.

        Args:
            addresses: List of addresses to cluster
            chain: Blockchain type

        Returns:
            List of Cluster objects
        """
        clusters: List[Cluster] = []

        if len(addresses) < 2:
            return clusters

        # Fetch transactions for all addresses
        address_txs: Dict[str, List[Transaction]] = {}
        for addr in addresses:
            txs = await self.trace_transactions(addr, chain, depth=1, max_transactions=50)
            address_txs[addr] = txs

        # Common input ownership heuristic
        common_input_clusters = self._cluster_by_common_input(
            addresses, address_txs
        )
        clusters.extend(common_input_clusters)

        # Temporal correlation heuristic
        temporal_clusters = self._cluster_by_temporal_correlation(
            addresses, address_txs
        )
        clusters.extend(temporal_clusters)

        # Amount pattern heuristic
        amount_clusters = self._cluster_by_amount_patterns(
            addresses, address_txs
        )
        clusters.extend(amount_clusters)

        # Merge overlapping clusters
        merged = self._merge_clusters(clusters)

        return merged

    def _cluster_by_common_input(
        self,
        addresses: List[str],
        address_txs: Dict[str, List[Transaction]]
    ) -> List[Cluster]:
        """
        Cluster by common input ownership.

        If two addresses appear as inputs to the same transaction,
        they likely belong to the same entity.
        """
        # Build transaction -> addresses mapping
        tx_addresses: Dict[str, Set[str]] = defaultdict(set)

        for addr, txs in address_txs.items():
            for tx in txs:
                tx_addresses[tx.tx_hash].add(addr)

        # Find addresses that share transactions
        shared: Dict[Tuple[str, str], int] = defaultdict(int)
        for tx_hash, addrs in tx_addresses.items():
            addr_list = sorted(addrs)
            for i in range(len(addr_list)):
                for j in range(i + 1, len(addr_list)):
                    shared[(addr_list[i], addr_list[j])] += 1

        # Create clusters from highly connected addresses
        clusters = []
        processed: Set[str] = set()

        for (addr1, addr2), count in shared.items():
            if count >= 2 and addr1 not in processed and addr2 not in processed:
                cluster_addrs = [addr1, addr2]
                processed.add(addr1)
                processed.add(addr2)

                clusters.append(Cluster(
                    cluster_id=self._generate_cluster_id(cluster_addrs),
                    addresses=cluster_addrs,
                    entity_type=EntityType.INDIVIDUAL,
                    confidence=0.7,
                    metadata={"shared_transactions": count},
                ))

        return clusters

    def _cluster_by_temporal_correlation(
        self,
        addresses: List[str],
        address_txs: Dict[str, List[Transaction]]
    ) -> List[Cluster]:
        """
        Cluster by temporal correlation.

        Addresses with similar transaction timing patterns
        may belong to the same entity.
        """
        # Calculate activity profiles (hour of day)
        profiles: Dict[str, List[int]] = {}

        for addr, txs in address_txs.items():
            hours = [0] * 24
            for tx in txs:
                hour = tx.timestamp.hour
                hours[hour] += 1
            profiles[addr] = hours

        # Find correlated profiles
        clusters = []
        processed: Set[str] = set()

        for i, addr1 in enumerate(addresses):
            if addr1 in processed:
                continue

            cluster_addrs = [addr1]
            profile1 = profiles.get(addr1, [0] * 24)

            for addr2 in addresses[i + 1:]:
                if addr2 in processed:
                    continue

                profile2 = profiles.get(addr2, [0] * 24)

                # Calculate correlation
                if sum(profile1) > 0 and sum(profile2) > 0:
                    correlation = self._calculate_correlation(profile1, profile2)
                    if correlation > 0.8:
                        cluster_addrs.append(addr2)

            if len(cluster_addrs) >= 2:
                for addr in cluster_addrs:
                    processed.add(addr)
                clusters.append(Cluster(
                    cluster_id=self._generate_cluster_id(cluster_addrs),
                    addresses=cluster_addrs,
                    entity_type=EntityType.INDIVIDUAL,
                    confidence=0.6,
                    metadata={"correlation_type": "temporal"},
                ))

        return clusters

    def _cluster_by_amount_patterns(
        self,
        addresses: List[str],
        address_txs: Dict[str, List[Transaction]]
    ) -> List[Cluster]:
        """
        Cluster by similar amount patterns.

        Addresses with similar transaction amount distributions
        may belong to the same entity.
        """
        # Calculate amount statistics
        stats: Dict[str, Dict[str, float]] = {}

        for addr, txs in address_txs.items():
            amounts = [tx.value for tx in txs if tx.value > 0]
            if amounts:
                stats[addr] = {
                    "mean": sum(amounts) / len(amounts),
                    "median": sorted(amounts)[len(amounts) // 2],
                    "max": max(amounts),
                    "min": min(amounts),
                }

        # Find addresses with similar patterns
        clusters = []
        processed: Set[str] = set()

        for addr1 in stats:
            if addr1 in processed:
                continue

            cluster_addrs = [addr1]
            stat1 = stats[addr1]

            for addr2 in stats:
                if addr2 in processed or addr2 == addr1:
                    continue

                stat2 = stats[addr2]

                # Compare mean amounts
                if stat1["mean"] > 0 and stat2["mean"] > 0:
                    ratio = min(stat1["mean"], stat2["mean"]) / max(stat1["mean"], stat2["mean"])
                    if ratio > 0.9:  # Very similar means
                        cluster_addrs.append(addr2)

            if len(cluster_addrs) >= 2:
                for addr in cluster_addrs:
                    processed.add(addr)
                clusters.append(Cluster(
                    cluster_id=self._generate_cluster_id(cluster_addrs),
                    addresses=cluster_addrs,
                    entity_type=EntityType.INDIVIDUAL,
                    confidence=0.5,
                    metadata={"correlation_type": "amount"},
                ))

        return clusters

    def _calculate_correlation(self, a: List[int], b: List[int]) -> float:
        """Calculate Pearson correlation coefficient."""
        n = len(a)
        if n != len(b) or n == 0:
            return 0.0

        mean_a = sum(a) / n
        mean_b = sum(b) / n

        numerator = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
        denom_a = sum((x - mean_a) ** 2 for x in a) ** 0.5
        denom_b = sum((x - mean_b) ** 2 for x in b) ** 0.5

        if denom_a == 0 or denom_b == 0:
            return 0.0

        return numerator / (denom_a * denom_b)

    def _merge_clusters(self, clusters: List[Cluster]) -> List[Cluster]:
        """Merge overlapping clusters."""
        if not clusters:
            return clusters

        # Group by overlapping addresses
        merged: List[Cluster] = []

        for cluster in clusters:
            found_merge = False
            for existing in merged:
                # Check for overlap
                if set(cluster.addresses) & set(existing.addresses):
                    # Merge
                    existing.addresses = list(set(existing.addresses + cluster.addresses))
                    existing.confidence = max(existing.confidence, cluster.confidence)
                    found_merge = True
                    break

            if not found_merge:
                merged.append(cluster)

        # Regenerate IDs
        for cluster in merged:
            cluster.cluster_id = self._generate_cluster_id(cluster.addresses)

        return merged

    # =========================================================================
    # KNOWN SERVICE IDENTIFICATION
    # =========================================================================

    def identify_known_services(self, address: str) -> List[str]:
        """
        Identify known services associated with an address.

        Args:
            address: Wallet address

        Returns:
            List of service tags
        """
        tags = []
        normalized = address.lower()

        # Check known services database
        for known_addr, info in KNOWN_SERVICES.items():
            if known_addr.lower() == normalized:
                tags.extend(info.get("tags", []))
                break

        # Heuristic detection
        if self._is_likely_exchange(address):
            tags.append("likely_exchange")

        if self._is_likely_contract(address):
            tags.append("contract")

        return list(set(tags))

    def _is_likely_exchange(self, address: str) -> bool:
        """Heuristic: check if address is likely an exchange."""
        # This is a placeholder for more sophisticated detection
        # In practice, would analyze transaction patterns
        return False

    def _is_likely_contract(self, address: str) -> bool:
        """Heuristic: check if address is likely a contract."""
        # Ethereum contracts often have specific patterns
        if address.startswith("0x"):
            # Could check for code at address via API
            pass
        return False

    # =========================================================================
    # CROSS-CHAIN ANALYSIS
    # =========================================================================

    async def cross_chain_analysis(
        self,
        addresses: Dict[str, str]  # chain -> address
    ) -> CrossChainResult:
        """
        Perform cross-chain analysis.

        Args:
            addresses: Dictionary mapping chain to address

        Returns:
            CrossChainResult with findings
        """
        related: Dict[str, List[str]] = {}
        potential_links: List[Tuple[str, str, float]] = []

        primary_chain = list(addresses.keys())[0] if addresses else "ethereum"
        primary_address = addresses.get(primary_chain, "")

        # Analyze each chain
        analyses: Dict[str, WalletAnalysis] = {}
        for chain, address in addresses.items():
            try:
                analysis = await self.analyze_wallet(address, chain)
                analyses[chain] = analysis
                related[chain] = analysis.linked_addresses
            except Exception as e:
                logger.warning(f"Failed to analyze {chain}:{address}: {e}")
                related[chain] = []

        # Look for cross-chain links
        for chain1, analysis1 in analyses.items():
            for chain2, analysis2 in analyses.items():
                if chain1 >= chain2:
                    continue

                # Check for temporal correlation
                if analysis1.last_seen and analysis2.first_seen:
                    time_diff = abs(
                        (analysis1.last_seen - analysis2.first_seen).total_seconds()
                    )
                    if time_diff < 3600:  # Within 1 hour
                        confidence = 0.5 + min(0.4, 3600 / max(time_diff, 1))
                        potential_links.append(
                            (analysis1.address, analysis2.address, confidence)
                        )

        # Calculate overall risk
        max_risk = max(
            (a.risk_score for a in analyses.values()),
            default=0.0
        )

        risk_assessment = self._risk_score_to_level(max_risk)

        return CrossChainResult(
            primary_address=primary_address,
            related_addresses=related,
            potential_links=potential_links,
            risk_assessment=risk_assessment,
            overall_risk_score=max_risk,
        )

    def _risk_score_to_level(self, score: float) -> str:
        """Convert risk score to level string."""
        if score >= 0.9:
            return "CRITICAL"
        elif score >= 0.7:
            return "HIGH"
        elif score >= 0.5:
            return "MEDIUM"
        elif score >= 0.3:
            return "LOW"
        return "MINIMAL"

    # =========================================================================
    # RISK SCORING
    # =========================================================================

    def calculate_risk_score(self, analysis: WalletAnalysis) -> float:
        """
        Calculate risk score for a wallet.

        Args:
            analysis: WalletAnalysis object

        Returns:
            Risk score between 0.0 (minimal) and 1.0 (critical)
        """
        score = 0.0
        factors = []

        # Mixer association
        if "mixer" in analysis.tags or "tornado" in analysis.tags:
            score += 0.5
            factors.append("mixer")

        # Exchange association (reduces risk)
        if "exchange" in analysis.tags:
            score -= 0.2
            factors.append("exchange")

        # High transaction volume
        if analysis.transaction_count > 1000:
            score += 0.1
            factors.append("high_volume")

        # Large balance
        if analysis.balance > 1000:  # ETH or BTC
            score += 0.1
            factors.append("large_balance")

        # Many linked addresses
        if len(analysis.linked_addresses) > 10:
            score += 0.1
            factors.append("many_links")

        # Age of wallet (newer = riskier)
        if analysis.first_seen:
            age_days = (datetime.now() - analysis.first_seen).days
            if age_days < 30:
                score += 0.2
                factors.append("new_wallet")
            elif age_days > 365:
                score -= 0.1
                factors.append("established")

        # Normalize to 0-1 range
        score = max(0.0, min(1.0, score))

        logger.debug(f"Risk score for {analysis.address}: {score} ({factors})")

        return score

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    async def close(self):
        """Close HTTP client and cleanup resources."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def analyze_blockchain_address(
    address: str,
    chain: str = "ethereum",
    etherscan_api_key: Optional[str] = None,
    blockchair_api_key: Optional[str] = None,
) -> WalletAnalysis:
    """
    Convenience function for quick address analysis.

    Args:
        address: Wallet address
        chain: Blockchain type
        etherscan_api_key: Etherscan API key
        blockchair_api_key: Blockchair API key

    Returns:
        WalletAnalysis
    """
    async with BlockchainForensics(
        etherscan_api_key=etherscan_api_key,
        blockchair_api_key=blockchair_api_key,
    ) as forensics:
        return await forensics.analyze_wallet(address, chain)


async def detect_transaction_patterns(
    address: str,
    chain: str = "ethereum",
    depth: int = 2,
    etherscan_api_key: Optional[str] = None,
    blockchair_api_key: Optional[str] = None,
) -> List[TransactionPattern]:
    """
    Convenience function for pattern detection.

    Args:
        address: Starting address
        chain: Blockchain type
        depth: Trace depth
        etherscan_api_key: Etherscan API key
        blockchair_api_key: Blockchair API key

    Returns:
        List of TransactionPattern
    """
    async with BlockchainForensics(
        etherscan_api_key=etherscan_api_key,
        blockchair_api_key=blockchair_api_key,
    ) as forensics:
        transactions = await forensics.trace_transactions(address, chain, depth)
        return await forensics.detect_patterns(transactions)


def get_blockchain_forensics(
    etherscan_api_key: Optional[str] = None,
    blockchair_api_key: Optional[str] = None,
) -> BlockchainForensics:
    """
    Get configured BlockchainForensics instance.

    Args:
        etherscan_api_key: Etherscan API key
        blockchair_api_key: Blockchair API key

    Returns:
        BlockchainForensics instance
    """
    return BlockchainForensics(
        etherscan_api_key=etherscan_api_key,
        blockchair_api_key=blockchair_api_key,
    )
