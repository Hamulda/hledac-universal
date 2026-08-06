"""
Blockchain Forensics Module
===========================








PROMOTION GATE — EXPERIMENTAL / HEAVY / HARD CONTAINMENT
==========================================================
Advanced blockchain analysis and forensics for cryptocurrency investigations.

STATUS: EXPERIMENTAL-HARD / NOT PROMOTED
  - 1513 lines, 0 call sites in production code (grep audit: legacy only)
  - Etherscan API key a Blockchair API key required, not in standard config
  - httpx.AsyncClient with rate limiting — third-party network dependency
  - KademliaNode uses this module? NO — dht/kademlia_node.py is fully independent
  - crawl_dht_for_keyword() does NOT call BlockchainForensics — confirmed by grep

M1 8GB MEMORY CEILING:
  - httpx.AsyncClient: max_connections=10, max_keepalive=5
  - _cache: MAX_CACHE_SIZE=1000 hard upper bound (F184F fix — OrderedDict LRU eviction)
  - Transaction tracing: depth-first, max 100 tx, visited set for dedup
  - clustering: O(n) API calls — one call per address (bounded by semaphore)
  - NO unbounded in-memory growth after F184F MAX_CACHE_SIZE fix

ALLOWED PURPOSE: Offline blockchain forensics research tool
  - Requires external API keys (Etherscan/Blockchair)
  - Primary use case: post-factum analysis of known addresses
  - NOT part of real-time OSINT pipeline
  - NOT integrated into autonomous_orchestrator.py canonical path

PROMOTION ELIGIBILITY: NO
  - Zero production call sites (legacy autonomous_orchestrator.py only)
  - Not integrated into canonical orchestrator path
  - API-dependent (Etherscan rate limits, Blockchair paid tier)
  - httpx client (not curl_cffi from fetch_coordinator) — separate transport
  - Dedicated httpx client = separate connection pool from main HTTP transport

SECURITY NOTES:
  - This module does NOT store API keys — caller provides them
  - Network traffic goes directly to Etherscan/Blockchair — no anonymization
  - This is a FORENSICS tool, not an OSINT stealth tool

HARD CONTAINMENT:
  - Do NOT import into canonical sprint/knowledge/prefetch paths
  - Do NOT wire into autonomous_orchestrator.py active pipeline
  - May only be used in explicit research/demo contexts with user-provided API keys
"""
import asyncio
import hashlib
import logging
import re
import time
from collections import OrderedDict, defaultdict, deque
from itertools import combinations
from dataclasses import dataclass, field
import msgspec
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from urllib.parse import urlparse
import httpx
from hledac.universal.utils.async_helpers import parallel

# --- Lazy UTXO graph import (ISSUE-009) ---
_UTXO_GRAPH_AVAILABLE = False
_UTXOGraph: Any = None
try:
    from hledac.universal.recon.bitcoin_utxo_analyzer import UTXOGraph as _UTXOGraph
    _UTXO_GRAPH_AVAILABLE = True
except ImportError:
    pass
logger = logging.getLogger(__name__)
MAX_CACHE_SIZE = 1000
try:
    from hledac.universal.transport.http_utils import fetch_json, safe_fetch
    HTTP_UTILS_AVAILABLE = True
except ImportError:
    HTTP_UTILS_AVAILABLE = False
    logger.debug('transport.http_utils not available, using direct httpx')
_circuit_breaker_module = None

def _get_circuit_breaker_module():
    """Lazily import circuit_breaker to avoid import-time session creation."""
    global _circuit_breaker_module
    if _circuit_breaker_module is None:
        try:
            from hledac.universal.transport.circuit_breaker import domain_breaker_check
            _circuit_breaker_module = domain_breaker_check
        except ImportError:
            _circuit_breaker_module = None
    return _circuit_breaker_module

def _extract_domain(url: str) -> str:
    """Extract domain from URL for circuit breaker check."""
    try:
        return urlparse(url).netloc
    except Exception:
        return ''

def _try_domain_breaker_check(domain: str) -> Any:
    """Fail-soft circuit breaker check. Returns None if breaker unavailable."""
    if not domain:
        return None
    try:
        cb_check = _get_circuit_breaker_module()
        if cb_check is not None:
            return cb_check(domain)
        return None
    except Exception:
        return None

class ChainType(Enum):
    """Supported blockchain types."""
    ETHEREUM = 'ethereum'
    BITCOIN = 'bitcoin'
    LITECOIN = 'litecoin'
    BITCOIN_CASH = 'bitcoin_cash'
    POLYGON = 'polygon'
    ARBITRUM = 'arbitrum'
    OPTIMISM = 'optimism'

class EntityType(Enum):
    """Types of entities that can be identified."""
    EXCHANGE = 'exchange'
    MIXER = 'mixer'
    DEFI_PROTOCOL = 'defi_protocol'
    INDIVIDUAL = 'individual'
    CONTRACT = 'contract'
    MINING_POOL = 'mining_pool'
    PAYMENT_PROCESSOR = 'payment_processor'
    UNKNOWN = 'unknown'

class PatternType(Enum):
    """Types of transaction patterns."""
    PEEL_CHAIN = 'peel_chain'
    ROUND_AMOUNT = 'round_amount'
    MIXING = 'mixing'
    LAYERING = 'layering'
    EXCHANGE_DEPOSIT = 'exchange_deposit'
    EXCHANGE_WITHDRAWAL = 'exchange_withdrawal'
    DUSTING = 'dusting'
    SLEEPING = 'sleeping'
    RAPID_TRADING = 'rapid_trading'

class RiskScore(Enum):
    """Float-based risk score (0.0–1.0) for addresses/transactions.

    Renamed from `RiskLevel` to disambiguate from canonical
    `project_types.RiskLevel` (str-valued enum). Float semantics
    are intentional — callers need numeric comparison, not ordinal.
    Use `project_types.RiskLevel` for categorical risk tagging.
    """
    CRITICAL = 1.0
    HIGH = 0.75
    MEDIUM = 0.5
    LOW = 0.25
    MINIMAL = 0.0

class Transaction(msgspec.Struct, gc=False):
    """Represents a blockchain transaction."""
    tx_hash: str
    timestamp: datetime
    from_address: str
    to_address: str
    value: float
    gas_used: int | None = None
    gas_price: int | None = None
    fee: float | None = None
    block_number: int | None = None
    confirmations: int = 0
    chain: str = 'ethereum'
    is_contract_creation: bool = False
    input_data: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

class WalletAnalysis(msgspec.Struct, frozen=True, gc=False):
    """Comprehensive analysis of a wallet address."""
    address: str
    chain: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    total_received: float = 0.0
    total_sent: float = 0.0
    transaction_count: int = 0
    incoming_count: int = 0
    outgoing_count: int = 0
    linked_addresses: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    entity_type: EntityType = EntityType.UNKNOWN
    risk_score: float = 0.0
    balance: float = 0.0
    known_associations: list[str] = field(default_factory=list)

class TransactionPattern(msgspec.Struct, frozen=True, gc=False):
    """Detected pattern in transactions."""
    pattern_type: PatternType
    confidence: float
    transactions: list[str]
    description: str
    addresses_involved: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

class Cluster(msgspec.Struct, frozen=True, gc=False):
    """A cluster of related addresses."""
    cluster_id: str
    addresses: list[str]
    entity_type: EntityType
    confidence: float
    label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

class CrossChainResult(msgspec.Struct, frozen=True, gc=False):
    """Result of cross-chain analysis."""
    primary_address: str
    related_addresses: dict[str, list[str]]
    potential_links: list[tuple[str, str, float]]
    risk_assessment: str
    overall_risk_score: float

class APIResponse(msgspec.Struct, frozen=True, gc=False):
    """Cached API response wrapper."""
    data: Any
    timestamp: datetime
    expires_at: datetime
KNOWN_SERVICES: dict[str, dict[str, Any]] = {'0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE': {'name': 'Binance', 'type': EntityType.EXCHANGE, 'tags': ['exchange', 'major']}, '0x742d35Cc6634C0532925a3b844Bc9e7595f8dEe': {'name': 'Coinbase', 'type': EntityType.EXCHANGE, 'tags': ['exchange', 'major', 'us_regulated']}, '0x8ba1f109551bD432803012645Hac136c82C3e8C9': {'name': 'Kraken', 'type': EntityType.EXCHANGE, 'tags': ['exchange', 'major']}, '0x7FF9cFad3877F21d41Da833E2F775dB0569eE3D9': {'name': 'Tornado.Cash', 'type': EntityType.MIXER, 'tags': ['mixer', 'privacy', 'sanctioned'], 'risk_multiplier': 1.0}, '0x1F98431c8aD98523631AE4a59f267346ea31F984': {'name': 'Uniswap V3', 'type': EntityType.DEFI_PROTOCOL, 'tags': ['defi', 'dex', 'amm']}, '0xE592427A0AEce92De3Edee1F18E0157C05861564': {'name': 'Uniswap V3 Router', 'type': EntityType.DEFI_PROTOCOL, 'tags': ['defi', 'dex', 'router']}}
BITCOIN_PATTERNS = {'p2pkh': re.compile('^1[a-km-zA-HJ-NP-Z1-9]{25,34}$'), 'p2sh': re.compile('^3[a-km-zA-HJ-NP-Z1-9]{25,34}$'), 'bech32': re.compile('^bc1[a-z0-9]{39,59}$')}
ETHEREUM_PATTERN = re.compile('^0x[a-fA-F0-9]{40}$')

class BlockchainForensics:
    """
    Advanced blockchain forensics and analysis tool.

    M1 8GB Optimized:
    - Async API calls with connection pooling
    - LRU caching for API responses (5 min TTL)
    - Streaming processing for large transaction histories
    - Minimal memory footprint
    """
    __slots__ = tuple(('_blockchair_delay', '_cache', '_cache_lock', '_client', '_etherscan_delay', '_fetch_func', '_last_blockchair_call', '_last_circuit_decision', '_last_etherscan_call', '_semaphore', 'blockchair_api_key', 'cache_ttl', 'etherscan_api_key', 'max_concurrent', 'transport_policy'))

    def __init__(self, etherscan_api_key: str | None=None, blockchair_api_key: str | None=None, cache_ttl_seconds: int=300, max_concurrent_requests: int=5, fetch_func: Any | None=None):
        """
        Initialize BlockchainForensics.

        Args:
            etherscan_api_key: API key for Etherscan (Ethereum)
            blockchair_api_key: API key for Blockchair (Bitcoin, others)
            cache_ttl_seconds: Cache time-to-live in seconds (default: 300)
            max_concurrent_requests: Max concurrent API requests (default: 5)
            fetch_func: Optional async fetch function(url: str) -> dict.
                When provided, takes precedence over internal httpx client.
                Enables canonical transport seam (circuit breaker, shared session).
        """
        self.etherscan_api_key = etherscan_api_key
        self.blockchair_api_key = blockchair_api_key
        self.cache_ttl = cache_ttl_seconds
        self.max_concurrent = max_concurrent_requests
        self._fetch_func = fetch_func
        self.transport_policy = 'injected' if fetch_func else 'bypass_legacy'
        self._cache: OrderedDict[str, APIResponse] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._last_circuit_decision: Any | None = None
        self._last_etherscan_call = 0.0
        self._last_blockchair_call = 0.0
        self._etherscan_delay = 0.2
        self._blockchair_delay = 0.5

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5, keepalive_expiry=30.0))
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._client

    async def _cached_request(self, cache_key: str, fetch_func, *args, **kwargs) -> Any:
        """Make a cached API request. F184F: LRU eviction when cache exceeds MAX_CACHE_SIZE."""
        async with self._cache_lock:
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                if datetime.now(UTC) < cached.expires_at:
                    self._cache.move_to_end(cache_key)
                    logger.debug(f'Cache hit: {cache_key}')
                    return cached.data
                else:
                    del self._cache[cache_key]
        data = await fetch_func(*args, **kwargs)
        async with self._cache_lock:
            if len(self._cache) >= MAX_CACHE_SIZE:
                evict_count = MAX_CACHE_SIZE // 2
                for _ in range(evict_count):
                    self._cache.popitem(last=False)
                logger.debug(f'[F184F] Cache evicted {evict_count} entries (size limit {MAX_CACHE_SIZE})')
            self._cache[cache_key] = APIResponse(data=data, timestamp=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(seconds=self.cache_ttl))
            self._cache.move_to_end(cache_key)
        return data

    async def _rate_limited_etherscan(self, url: str) -> dict[str, Any]:
        """Make rate-limited Etherscan API call."""
        now = time.time()
        elapsed = now - self._last_etherscan_call
        if elapsed < self._etherscan_delay:
            await asyncio.sleep(self._etherscan_delay - elapsed)
        self._last_etherscan_call = time.time()
        domain = _extract_domain(url)
        circuit_decision = _try_domain_breaker_check(domain)
        if circuit_decision is not None:
            self._last_circuit_decision = circuit_decision
            if not circuit_decision.allowed:
                logger.debug(f'Etherscan circuit breaker blocked {domain}: {circuit_decision.reason} (retry in {circuit_decision.retry_after_s:.1f}s)')
                return {'status': '0', 'message': f'circuit_breaker_blocked:{domain}'}
        await self._get_client()
        async with self._semaphore:
            try:
                if self._fetch_func is not None:
                    return await self._fetch_func(url) or {}
                elif HTTP_UTILS_AVAILABLE:
                    return await fetch_json(url) or {}
                else:
                    client = await self._get_client()
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.json()
            except Exception as e:
                logger.warning(f'Etherscan API error: {e}')
                return {'status': '0', 'message': str(e)}

    async def _rate_limited_blockchair(self, url: str) -> dict[str, Any]:
        """Make rate-limited Blockchair API call."""
        now = time.time()
        elapsed = now - self._last_blockchair_call
        if elapsed < self._blockchair_delay:
            await asyncio.sleep(self._blockchair_delay - elapsed)
        self._last_blockchair_call = time.time()
        domain = _extract_domain(url)
        circuit_decision = _try_domain_breaker_check(domain)
        if circuit_decision is not None:
            self._last_circuit_decision = circuit_decision
            if not circuit_decision.allowed:
                logger.debug(f'Blockchair circuit breaker blocked {domain}: {circuit_decision.reason} (retry in {circuit_decision.retry_after_s:.1f}s)')
                return {'status': '0', 'message': f'circuit_breaker_blocked:{domain}'}
        await self._get_client()
        async with self._semaphore:
            try:
                if self._fetch_func is not None:
                    return await self._fetch_func(url) or {}
                elif HTTP_UTILS_AVAILABLE:
                    return await fetch_json(url) or {}
                else:
                    client = await self._get_client()
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.json()
            except Exception as e:
                logger.warning(f'Blockchair API error: {e}')
                return {'data': {}, 'error': str(e)}

    def _generate_cluster_id(self, addresses: list[str]) -> str:
        """Generate a unique cluster ID from addresses."""
        sorted_addrs = sorted(addresses)
        hash_input = ''.join(sorted_addrs).encode()
        return hashlib.sha256(hash_input).hexdigest()[:16]

    def _is_valid_address(self, address: str, chain: str='ethereum') -> bool:
        """Validate address format for given chain."""
        if chain in ('ethereum', 'polygon', 'arbitrum', 'optimism'):
            return bool(ETHEREUM_PATTERN.match(address))
        elif chain == 'bitcoin':
            return any((pattern.match(address) for pattern in BITCOIN_PATTERNS.values()))
        return True

    async def analyze_wallet(self, address: str, chain: str='ethereum') -> WalletAnalysis:
        """
        Perform comprehensive wallet analysis.

        Args:
            address: Wallet address to analyze
            chain: Blockchain type (ethereum, bitcoin, etc.)

        Returns:
            WalletAnalysis with comprehensive metrics
        """
        if not self._is_valid_address(address, chain):
            raise ValueError(f'Invalid address format for {chain}: {address}')
        analysis = WalletAnalysis(address=address, chain=chain)
        if chain == 'ethereum':
            await self._analyze_ethereum_wallet(analysis)
        elif chain == 'bitcoin':
            await self._analyze_bitcoin_wallet(analysis)
        else:
            logger.warning(f'Chain {chain} not fully supported, using generic analysis')
            await self._analyze_generic_wallet(analysis)
        analysis.tags = self.identify_known_services(address)
        analysis.risk_score = self.calculate_risk_score(analysis)
        return analysis

    async def _analyze_ethereum_wallet(self, analysis: WalletAnalysis) -> None:
        """Analyze Ethereum wallet using Etherscan."""
        if not self.etherscan_api_key:
            logger.warning('No Etherscan API key provided')
            return
        base_url = 'https://api.etherscan.io/api'
        address = analysis.address
        balance_url = f'{base_url}?module=account&action=balance&address={address}&tag=latest&apikey={self.etherscan_api_key}'
        balance_data = await self._cached_request(f'eth_balance_{address}', self._rate_limited_etherscan, balance_url)
        if balance_data.get('status') == '1':
            balance_wei = int(balance_data.get('result', 0))
            analysis.balance = balance_wei / 1e+18
        tx_url = f'{base_url}?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&page=1&offset=100&sort=asc&apikey={self.etherscan_api_key}'
        tx_data = await self._cached_request(f'eth_tx_{address}_page1', self._rate_limited_etherscan, tx_url)
        if tx_data.get('status') == '1' and 'result' in tx_data:
            transactions = tx_data['result']
            analysis.transaction_count = len(transactions)
            if transactions:
                first_tx = transactions[0]
                last_tx = transactions[-1]
                analysis.first_seen = datetime.fromtimestamp(int(first_tx.get('timeStamp', 0)))
                analysis.last_seen = datetime.fromtimestamp(int(last_tx.get('timeStamp', 0)))
                for tx in transactions:
                    value_eth = int(tx.get('value', 0)) / 1e+18
                    from_addr = tx.get('from', '').lower()
                    to_addr = tx.get('to', '').lower()
                    if from_addr == address.lower():
                        analysis.total_sent += value_eth
                        analysis.outgoing_count += 1
                    elif to_addr == address.lower():
                        analysis.total_received += value_eth
                        analysis.incoming_count += 1

    async def _analyze_bitcoin_wallet(self, analysis: WalletAnalysis) -> None:
        """Analyze Bitcoin wallet using Blockchair."""
        address = analysis.address
        base_url = 'https://api.blockchair.com/bitcoin/dashboards/address'
        url = f'{base_url}/{address}'
        if self.blockchair_api_key:
            url += f'?key={self.blockchair_api_key}'
        data = await self._cached_request(f'btc_address_{address}', self._rate_limited_blockchair, url)
        if 'data' in data and address in data['data']:
            addr_data = data['data'][address]['address']
            analysis.balance = addr_data.get('balance', 0) / 100000000.0
            analysis.transaction_count = addr_data.get('transaction_count', 0)
            analysis.total_received = addr_data.get('received', 0) / 100000000.0
            analysis.total_sent = addr_data.get('spent', 0) / 100000000.0
            if addr_data.get('first_seen_receiving'):
                analysis.first_seen = datetime.fromtimestamp(addr_data['first_seen_receiving'])
            if addr_data.get('last_seen_spending'):
                analysis.last_seen = datetime.fromtimestamp(addr_data['last_seen_spending'])

    async def _analyze_generic_wallet(self, analysis: WalletAnalysis) -> None:
        """Generic wallet analysis when specific API unavailable."""
        logger.info(f'Performing generic analysis for {analysis.address}')
        pass

    async def trace_transactions(self, address: str, chain: str='ethereum', depth: int=2, max_transactions: int=100) -> list[Transaction]:
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
        all_transactions: list[Transaction] = []
        visited: set[str] = set()
        queue: deque = deque([(address, 0)])
        while queue and len(all_transactions) < max_transactions:
            current_addr, current_depth = queue.popleft()
            if current_addr in visited or current_depth > depth:
                continue
            visited.add(current_addr)
            txs = await self._fetch_transactions(current_addr, chain)
            for tx_data in txs:
                tx = self._parse_transaction(tx_data, chain)
                all_transactions.append(tx)
                if current_depth < depth:
                    if tx.from_address != current_addr:
                        queue.append((tx.from_address, current_depth + 1))
                    if tx.to_address != current_addr:
                        queue.append((tx.to_address, current_depth + 1))
        return all_transactions[:max_transactions]

    async def _fetch_transactions(self, address: str, chain: str) -> list[dict[str, Any]]:
        """Fetch raw transactions for an address."""
        if chain == 'ethereum' and self.etherscan_api_key:
            return await self._fetch_ethereum_transactions(address)
        elif chain == 'bitcoin':
            return await self._fetch_bitcoin_transactions(address)
        return []

    async def _fetch_ethereum_transactions(self, address: str) -> list[dict[str, Any]]:
        """Fetch Ethereum transactions from Etherscan."""
        base_url = 'https://api.etherscan.io/api'
        url = f'{base_url}?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&page=1&offset=100&sort=desc&apikey={self.etherscan_api_key}'
        data = await self._cached_request(f'eth_txlist_{address}', self._rate_limited_etherscan, url)
        if data.get('status') == '1' and 'result' in data:
            return data['result']
        return []

    async def _fetch_bitcoin_transactions(self, address: str) -> list[dict[str, Any]]:
        """Fetch Bitcoin transactions from Blockchair."""
        base_url = 'https://api.blockchair.com/bitcoin/dashboards/address'
        url = f'{base_url}/{address}?limit=100'
        if self.blockchair_api_key:
            url += f'&key={self.blockchair_api_key}'
        data = await self._cached_request(f'btc_txlist_{address}', self._rate_limited_blockchair, url)
        transactions = []
        if 'data' in data and address in data['data']:
            tx_data = data['data'][address].get('transactions', [])
            if tx_data:
                # ISSUE-XXX: parallel tx detail fetches — up to 100 tx hashes fetched concurrently.
                # Prior: sequential for-loop (100 × ~100ms = 10s). New: bounded parallel at concurrency=8.
                _TX_SEM = asyncio.Semaphore(8)

                async def _fetch_one(tx_hash: str) -> dict[str, Any] | None:
                    async with _TX_SEM:
                        return await self._fetch_bitcoin_transaction_detail(tx_hash)

                results = await parallel([_fetch_one(tx) for tx in tx_data[:100]], policy="collect", ctx="blockchain:tx_detail")
                for tx_detail in results:
                    if tx_detail:
                        transactions.append(tx_detail)
        return transactions

    async def _fetch_bitcoin_transaction_detail(self, tx_hash: str) -> dict[str, Any] | None:
        """Fetch detailed Bitcoin transaction."""
        url = f'https://api.blockchair.com/bitcoin/dashboards/transaction/{tx_hash}'
        if self.blockchair_api_key:
            url += f'?key={self.blockchair_api_key}'
        data = await self._cached_request(f'btc_tx_{tx_hash}', self._rate_limited_blockchair, url)
        if 'data' in data and tx_hash in data['data']:
            return data['data'][tx_hash].get('transaction', {})
        return None

    def _parse_transaction(self, tx_data: dict[str, Any], chain: str) -> Transaction:
        """Parse raw transaction data into Transaction object."""
        if chain == 'ethereum':
            timestamp = datetime.fromtimestamp(int(tx_data.get('timeStamp', 0)))
            return Transaction(tx_hash=tx_data.get('hash', ''), timestamp=timestamp, from_address=tx_data.get('from', ''), to_address=tx_data.get('to', ''), value=int(tx_data.get('value', 0)) / 1e+18, gas_used=int(tx_data.get('gasUsed', 0)), gas_price=int(tx_data.get('gasPrice', 0)), block_number=int(tx_data.get('blockNumber', 0)), confirmations=int(tx_data.get('confirmations', 0)), chain=chain, is_contract_creation=tx_data.get('contractAddress') is not None, input_data=tx_data.get('input'))
        elif chain == 'bitcoin':
            timestamp = datetime.fromtimestamp(tx_data.get('time', 0) or tx_data.get('block_time', 0))
            return Transaction(tx_hash=tx_data.get('hash', ''), timestamp=timestamp, from_address='', to_address='', value=tx_data.get('output_total', 0) / 100000000.0, fee=tx_data.get('fee', 0) / 100000000.0, block_number=tx_data.get('block_id'), chain=chain)
        else:
            return Transaction(tx_hash=str(tx_data.get('hash', '')), timestamp=datetime.now(UTC), from_address='', to_address='', value=0.0, chain=chain)

    async def detect_patterns(self, transactions: list[Transaction]) -> list[TransactionPattern]:
        """
        Detect suspicious patterns in transactions.

        Args:
            transactions: List of transactions to analyze

        Returns:
            List of detected TransactionPattern objects
        """
        patterns: list[TransactionPattern] = []
        if not transactions:
            return patterns
        sorted_txs = sorted(transactions, key=lambda x: x.timestamp)
        peel_chain = self._detect_peel_chain(sorted_txs)
        if peel_chain:
            patterns.append(peel_chain)
        round_amounts = self._detect_round_amounts(sorted_txs)
        if round_amounts:
            patterns.append(round_amounts)
        mixing = self._detect_mixing_patterns(sorted_txs)
        if mixing:
            patterns.append(mixing)
        layering = self._detect_layering(sorted_txs)
        if layering:
            patterns.append(layering)
        rapid_trading = self._detect_rapid_trading(sorted_txs)
        if rapid_trading:
            patterns.append(rapid_trading)
        return patterns

    def _detect_peel_chain(self, transactions: list[Transaction]) -> TransactionPattern | None:
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
        for tx1, tx2 in zip(transactions, transactions[1:]):
            time_diff = (tx2.timestamp - tx1.timestamp).total_seconds()
            if time_diff > 3600:
                continue
            if tx1.value > tx2.value > 0:
                peel_candidates.append(tx1.tx_hash)
        if len(peel_candidates) >= 3:
            return TransactionPattern(pattern_type=PatternType.PEEL_CHAIN, confidence=min(0.9, 0.5 + len(peel_candidates) * 0.1), transactions=peel_candidates, description=f'Peel chain detected: {len(peel_candidates)} transactions with decreasing amounts in quick succession')
        return None

    def _detect_round_amounts(self, transactions: list[Transaction]) -> TransactionPattern | None:
        """Detect round amount patterns (common in exchange withdrawals)."""
        round_txs = []
        for tx in transactions:
            value = tx.value
            if value > 0:
                rounded = round(value, 6)
                if rounded in [1.0, 0.1, 0.5, 2.0, 5.0, 10.0, 0.01, 0.001]:
                    round_txs.append(tx.tx_hash)
                elif value == int(value):
                    round_txs.append(tx.tx_hash)
        if len(round_txs) >= 3:
            return TransactionPattern(pattern_type=PatternType.ROUND_AMOUNT, confidence=min(0.8, 0.4 + len(round_txs) * 0.05), transactions=round_txs, description=f'Round amount pattern: {len(round_txs)} transactions with round or whole number amounts')
        return None

    def _detect_mixing_patterns(self, transactions: list[Transaction]) -> TransactionPattern | None:
        """Detect potential mixing/tumbling patterns."""
        time_windows: dict[str, list[Transaction]] = defaultdict(list)
        for tx in transactions:
            window_key = tx.timestamp.strftime('%Y-%m-%d-%H')
            time_windows[window_key].append(tx)
        mixing_candidates = []
        for _window, txs in time_windows.items():
            if len(txs) >= 5:
                amounts = [tx.value for tx in txs if tx.value > 0]
                if len(amounts) >= 3:
                    avg = sum(amounts) / len(amounts)
                    variance = sum(((a - avg) ** 2 for a in amounts)) / len(amounts)
                    if variance < avg * 0.1:
                        mixing_candidates.extend([tx.tx_hash for tx in txs])
        if len(mixing_candidates) >= 5:
            return TransactionPattern(pattern_type=PatternType.MIXING, confidence=0.6, transactions=list(set(mixing_candidates)), description=f'Potential mixing detected: {len(mixing_candidates)} transactions with similar amounts in tight time windows')
        return None

    def _detect_layering(self, transactions: list[Transaction]) -> TransactionPattern | None:
        """Detect layering pattern (multiple hops to obscure trail)."""
        if len(transactions) < 5:
            return None
        addresses: set[str] = set()
        for tx in transactions:
            addresses.add(tx.from_address)
            addresses.add(tx.to_address)
        time_span = transactions[-1].timestamp - transactions[0].timestamp
        if len(addresses) >= 5 and time_span < timedelta(hours=24):
            tx_hashes = [tx.tx_hash for tx in transactions]
            return TransactionPattern(pattern_type=PatternType.LAYERING, confidence=min(0.7, 0.3 + len(addresses) * 0.05), transactions=tx_hashes, description=f'Layering pattern: {len(addresses)} unique addresses in {time_span.total_seconds() / 3600:.1f} hours')
        return None

    def _detect_rapid_trading(self, transactions: list[Transaction]) -> TransactionPattern | None:
        """Detect rapid trading pattern."""
        if len(transactions) < 10:
            return None
        time_span = transactions[-1].timestamp - transactions[0].timestamp
        tx_rate = len(transactions) / max(time_span.total_seconds() / 3600, 0.001)
        if tx_rate > 10:
            return TransactionPattern(pattern_type=PatternType.RAPID_TRADING, confidence=min(0.85, 0.4 + tx_rate * 0.02), transactions=[tx.tx_hash for tx in transactions], description=f'Rapid trading: {len(transactions)} transactions ({tx_rate:.1f} per hour)')
        return None

    async def cluster_addresses(self, addresses: list[str], chain: str='ethereum', use_local: bool=False, raw_transactions: list[dict[str, Any]] | None=None) -> list[Cluster]:
        """
        Cluster addresses using heuristics or local UTXO graph analysis.

        Args:
            addresses: List of addresses to cluster
            chain: Blockchain type
            use_local: If True, use local UTXO graph analysis (no API required).
            raw_transactions: Raw Bitcoin transaction data for local analysis.
                Required when use_local=True and chain='bitcoin'.

        Returns:
            List of Cluster objects
        """
        # ISSUE-009: Local UTXO graph analysis mode (no API dependency)
        if use_local and chain == 'bitcoin' and raw_transactions is not None:
            return await self._cluster_addresses_local(addresses, raw_transactions)
        if use_local and not raw_transactions:
            logger.warning('use_local=True but no raw_transactions provided — falling back to API mode')
        clusters: list[Cluster] = []
        if len(addresses) < 2:
            return clusters
        address_txs: dict[str, list[Transaction]] = {}
        if len(addresses) >= 2:
            # ISSUE-XXX: parallel address clustering — trace multiple addresses concurrently.
            # Prior: sequential for-loop (N × API latency). New: bounded parallel at concurrency=4.
            _CLUSTER_SEM = asyncio.Semaphore(4)

            async def _trace_one(addr: str) -> tuple[str, list[Transaction]]:
                async with _CLUSTER_SEM:
                    txs = await self.trace_transactions(addr, chain, depth=1, max_transactions=50)
                return (addr, txs)

            traced = await parallel([_trace_one(addr) for addr in addresses], policy="collect", ctx="blockchain:cluster_trace")
            for addr, txs in traced:
                address_txs[addr] = txs
        common_input_clusters = self._cluster_by_common_input(addresses, address_txs)
        clusters.extend(common_input_clusters)
        temporal_clusters = self._cluster_by_temporal_correlation(addresses, address_txs)
        clusters.extend(temporal_clusters)
        amount_clusters = self._cluster_by_amount_patterns(addresses, address_txs)
        clusters.extend(amount_clusters)
        merged = self._merge_clusters(clusters)
        return merged

    def _cluster_by_common_input(self, addresses: list[str], address_txs: dict[str, list[Transaction]]) -> list[Cluster]:
        """
        Cluster by common input ownership.

        If two addresses appear as inputs to the same transaction,
        they likely belong to the same entity.
        """
        tx_addresses: dict[str, set[str]] = defaultdict(set)
        for addr, txs in address_txs.items():
            for tx in txs:
                tx_addresses[tx.tx_hash].add(addr)
        shared: dict[tuple[str, str], int] = defaultdict(int)
        for _tx_hash, addrs in tx_addresses.items():
            addr_list = sorted(addrs)
            for addr_a, addr_b in combinations(addr_list, 2):
                shared[addr_a, addr_b] += 1
        clusters = []
        processed: set[str] = set()
        for (addr1, addr2), count in shared.items():
            if count >= 2 and addr1 not in processed and (addr2 not in processed):
                cluster_addrs = [addr1, addr2]
                processed.add(addr1)
                processed.add(addr2)
                clusters.append(Cluster(cluster_id=self._generate_cluster_id(cluster_addrs), addresses=cluster_addrs, entity_type=EntityType.INDIVIDUAL, confidence=0.7, metadata={'shared_transactions': count}))
        return clusters

    def _cluster_by_temporal_correlation(self, addresses: list[str], address_txs: dict[str, list[Transaction]]) -> list[Cluster]:
        """
        Cluster by temporal correlation.

        Addresses with similar transaction timing patterns
        may belong to the same entity.
        """
        profiles: dict[str, list[int]] = {}
        for addr, txs in address_txs.items():
            hours = [0] * 24
            for tx in txs:
                hour = tx.timestamp.hour
                hours[hour] += 1
            profiles[addr] = hours
        clusters = []
        processed: set[str] = set()
        for i, addr1 in enumerate(addresses):
            if addr1 in processed:
                continue
            cluster_addrs = [addr1]
            profile1 = profiles.get(addr1, [0] * 24)
            for addr2 in addresses[i + 1:]:
                if addr2 in processed:
                    continue
                profile2 = profiles.get(addr2, [0] * 24)
                if sum(profile1) > 0 and sum(profile2) > 0:
                    correlation = self._calculate_correlation(profile1, profile2)
                    if correlation > 0.8:
                        cluster_addrs.append(addr2)
            if len(cluster_addrs) >= 2:
                for addr in cluster_addrs:
                    processed.add(addr)
                clusters.append(Cluster(cluster_id=self._generate_cluster_id(cluster_addrs), addresses=cluster_addrs, entity_type=EntityType.INDIVIDUAL, confidence=0.6, metadata={'correlation_type': 'temporal'}))
        return clusters

    def _cluster_by_amount_patterns(self, addresses: list[str], address_txs: dict[str, list[Transaction]]) -> list[Cluster]:
        """
        Cluster by similar amount patterns.

        Addresses with similar transaction amount distributions
        may belong to the same entity.
        """
        stats: dict[str, dict[str, float]] = {}
        for addr, txs in address_txs.items():
            amounts = [tx.value for tx in txs if tx.value > 0]
            if amounts:
                stats[addr] = {'mean': sum(amounts) / len(amounts), 'median': sorted(amounts)[len(amounts) // 2], 'max': max(amounts), 'min': min(amounts)}
        clusters = []
        processed: set[str] = set()
        for addr1 in stats:
            if addr1 in processed:
                continue
            cluster_addrs = [addr1]
            stat1 = stats[addr1]
            for addr2 in stats:
                if addr2 in processed or addr2 == addr1:
                    continue
                stat2 = stats[addr2]
                if stat1['mean'] > 0 and stat2['mean'] > 0:
                    ratio = min(stat1['mean'], stat2['mean']) / max(stat1['mean'], stat2['mean'])
                    if ratio > 0.9:
                        cluster_addrs.append(addr2)
            if len(cluster_addrs) >= 2:
                for addr in cluster_addrs:
                    processed.add(addr)
                clusters.append(Cluster(cluster_id=self._generate_cluster_id(cluster_addrs), addresses=cluster_addrs, entity_type=EntityType.INDIVIDUAL, confidence=0.5, metadata={'correlation_type': 'amount'}))
        return clusters

    def _calculate_correlation(self, a: list[int], b: list[int]) -> float:
        """Calculate Pearson correlation coefficient."""
        n = len(a)
        if n != len(b) or n == 0:
            return 0.0
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        numerator = sum(((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)))
        denom_a = sum(((x - mean_a) ** 2 for x in a)) ** 0.5
        denom_b = sum(((x - mean_b) ** 2 for x in b)) ** 0.5
        if denom_a == 0 or denom_b == 0:
            return 0.0
        return numerator / (denom_a * denom_b)

    def _merge_clusters(self, clusters: list[Cluster]) -> list[Cluster]:
        """Merge overlapping clusters."""
        if not clusters:
            return clusters
        merged: list[Cluster] = []
        for cluster in clusters:
            found_merge = False
            for existing in merged:
                if set(cluster.addresses) & set(existing.addresses):
                    existing.addresses = list(set(existing.addresses + cluster.addresses))
                    existing.confidence = max(existing.confidence, cluster.confidence)
                    found_merge = True
                    break
            if not found_merge:
                merged.append(cluster)
        for cluster in merged:
            cluster.cluster_id = self._generate_cluster_id(cluster.addresses)
        return merged

    async def _cluster_addresses_local(self, addresses: list[str], raw_transactions: list[dict[str, Any]]) -> list[Cluster]:
        """ISSUE-009: Local UTXO graph analysis — no API dependency.

        Uses UTXOGraph (igraph C-core) for native Bitcoin UTXO graph traversal,
        change address detection, and multi-input clustering via connected components.

        Args:
            addresses: Bitcoin addresses to cluster.
            raw_transactions: Raw BTC transaction data (dicts with inputs/outputs).

        Returns:
            List of Cluster objects (compatible with existing cluster_addresses() output).
        """
        import asyncio

        if not _UTXO_GRAPH_AVAILABLE:
            logger.warning('UTXO graph analysis not available — igraph missing')
            return []

        try:
            analyzer = _UTXOGraph()
            utxo_clusters = await asyncio.to_thread(
                analyzer.cluster_addresses_graph, addresses, raw_transactions
            )
        except Exception as e:
            logger.error(f'UTXO graph clustering failed: {e}')
            return []

        clusters: list[Cluster] = []
        for uc in utxo_clusters:
            clusters.append(Cluster(
                cluster_id=uc.cluster_id,
                addresses=uc.addresses,
                entity_type=EntityType.INDIVIDUAL,
                confidence=uc.confidence,
                metadata={
                    'algorithm': 'utxo_graph_connected_components',
                    'cluster_type': uc.cluster_type,
                    'shared_tx_count': uc.metadata.get('shared_tx_count', 0),
                    'member_count': uc.metadata.get('member_count', 0),
                },
            ))

        logger.info(f'Local UTXO clustering: {len(clusters)} clusters from {len(addresses)} addresses')
        return clusters

    def identify_known_services(self, address: str) -> list[str]:
        """
        Identify known services associated with an address.

        Args:
            address: Wallet address

        Returns:
            List of service tags
        """
        tags = []
        normalized = address.lower()
        for known_addr, info in KNOWN_SERVICES.items():
            if known_addr.lower() == normalized:
                tags.extend(info.get('tags', []))
                break
        if self._is_likely_exchange(address):
            tags.append('likely_exchange')
        if self._is_likely_contract(address):
            tags.append('contract')
        return list(set(tags))

    def _is_likely_exchange(self, address: str) -> bool:
        """Heuristic: check if address is likely an exchange."""
        return False

    def _is_likely_contract(self, address: str) -> bool:
        """Heuristic: check if address is likely a contract."""
        if address.startswith('0x'):
            pass
        return False

    async def cross_chain_analysis(self, addresses: dict[str, str]) -> CrossChainResult:
        """
        Perform cross-chain analysis.

        Args:
            addresses: Dictionary mapping chain to address

        Returns:
            CrossChainResult with findings
        """
        related: dict[str, list[str]] = {}
        potential_links: list[tuple[str, str, float]] = []
        primary_chain = list(addresses.keys())[0] if addresses else 'ethereum'
        primary_address = addresses.get(primary_chain, '')
        analyses: dict[str, WalletAnalysis] = {}
        for chain, address in addresses.items():
            try:
                analysis = await self.analyze_wallet(address, chain)
                analyses[chain] = analysis
                related[chain] = analysis.linked_addresses
            except Exception as e:
                logger.warning(f'Failed to analyze {chain}:{address}: {e}')
                related[chain] = []
        for chain1, analysis1 in analyses.items():
            for chain2, analysis2 in analyses.items():
                if chain1 >= chain2:
                    continue
                if analysis1.last_seen and analysis2.first_seen:
                    time_diff = abs((analysis1.last_seen - analysis2.first_seen).total_seconds())
                    if time_diff < 3600:
                        confidence = 0.5 + min(0.4, 3600 / max(time_diff, 1))
                        potential_links.append((analysis1.address, analysis2.address, confidence))
        max_risk = max((a.risk_score for a in analyses.values()), default=0.0)
        risk_assessment = self._risk_score_to_level(max_risk)
        return CrossChainResult(primary_address=primary_address, related_addresses=related, potential_links=potential_links, risk_assessment=risk_assessment, overall_risk_score=max_risk)

    def _risk_score_to_level(self, score: float) -> str:
        """Convert risk score to level string."""
        if score >= 0.9:
            return 'CRITICAL'
        elif score >= 0.7:
            return 'HIGH'
        elif score >= 0.5:
            return 'MEDIUM'
        elif score >= 0.3:
            return 'LOW'
        return 'MINIMAL'

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
        if 'mixer' in analysis.tags or 'tornado' in analysis.tags:
            score += 0.5
            factors.append('mixer')
        if 'exchange' in analysis.tags:
            score -= 0.2
            factors.append('exchange')
        if analysis.transaction_count > 1000:
            score += 0.1
            factors.append('high_volume')
        if analysis.balance > 1000:
            score += 0.1
            factors.append('large_balance')
        if len(analysis.linked_addresses) > 10:
            score += 0.1
            factors.append('many_links')
        if analysis.first_seen:
            age_days = (datetime.now(UTC) - analysis.first_seen).days
            if age_days < 30:
                score += 0.2
                factors.append('new_wallet')
            elif age_days > 365:
                score -= 0.1
                factors.append('established')
        score = max(0.0, min(1.0, score))
        logger.debug(f'Risk score for {analysis.address}: {score} ({factors})')
        return score

    async def close(self):
        """Close HTTP client and cleanup resources."""
        if self._client and (not self._client.is_closed):
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

async def analyze_blockchain_address(address: str, chain: str='ethereum', etherscan_api_key: str | None=None, blockchair_api_key: str | None=None) -> WalletAnalysis:
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
    async with BlockchainForensics(etherscan_api_key=etherscan_api_key, blockchair_api_key=blockchair_api_key) as forensics:
        return await forensics.analyze_wallet(address, chain)

async def detect_transaction_patterns(address: str, chain: str='ethereum', depth: int=2, etherscan_api_key: str | None=None, blockchair_api_key: str | None=None) -> list[TransactionPattern]:
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
    async with BlockchainForensics(etherscan_api_key=etherscan_api_key, blockchair_api_key=blockchair_api_key) as forensics:
        transactions = await forensics.trace_transactions(address, chain, depth)
        return await forensics.detect_patterns(transactions)

def get_blockchain_forensics(etherscan_api_key: str | None=None, blockchair_api_key: str | None=None) -> BlockchainForensics:
    """
    Get configured BlockchainForensics instance.

    Args:
        etherscan_api_key: Etherscan API key
        blockchair_api_key: Blockchair API key

    Returns:
        BlockchainForensics instance
    """
    return BlockchainForensics(etherscan_api_key=etherscan_api_key, blockchair_api_key=blockchair_api_key)