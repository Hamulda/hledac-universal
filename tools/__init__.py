"""
Universal Tools - Lightweight and Memory-Efficient

Tools optimized for M1 8GB RAM with minimal memory footprint.
"""

from .reranker import (
    LightweightReranker,
    RerankResult,
    RerankRequest,
    RerankerConfig,
    RerankerFactory,
    create_reranker
)
from .content_miner import (
    RustMiner,
    MiningResult,
    create_rust_miner
)

# Sprint 80: OSINT adapters
from .commoncrawl_adapter import CommonCrawlAdapter, RawFinding
from .wayback_adapter import WaybackAdapter

# Sprint 45 refactor: Extracted from coordinators/fetch_coordinator.py
from .zstd_compressor import ZstdCompressor
from .lightpanda_manager import LightpandaManager
from .lightpanda_pool import LightpandaPool
from .file_cache import apply_fcntl_nocache, NOCACHE_THRESHOLD_BYTES, F_NOCACHE

__all__ = [
    # Reranker
    'LightweightReranker',
    'RerankResult',
    'RerankRequest',
    'RerankerConfig',
    'RerankerFactory',
    'create_reranker',
    # Miner
    'RustMiner',
    'MiningResult',
    'create_rust_miner',
    # Sprint 80: OSINT adapters
    'CommonCrawlAdapter',
    'WaybackAdapter',
    'RawFinding',
    # Sprint 45 refactor: Browser pool and compression
    'ZstdCompressor',
    'LightpandaManager',
    'LightpandaPool',
    'apply_fcntl_nocache',
    'NOCACHE_THRESHOLD_BYTES',
    'F_NOCACHE',
]
