"""
Universal Tools - Lightweight and Memory-Efficient

Tools optimized for M1 8GB RAM with minimal memory footprint.
"""

# Sprint 80: OSINT adapters
from .adapters.commoncrawl_adapter import CommonCrawlAdapter, RawFinding
from .adapters.wayback_adapter import WaybackAdapter

# Content mining
from .content_miner import MiningResult, RustMiner, create_rust_miner

# File caching
from .file_cache import F_NOCACHE, NOCACHE_THRESHOLD_BYTES, apply_fcntl_nocache, apply_nocache_to_path

# Browser pool
from .lightpanda_manager import LightpandaManager
from .lightpanda_pool import LightpandaPool

# Reranker
from .reranker import LightweightReranker, RerankerConfig, RerankerFactory, RerankRequest, RerankResult, create_reranker

# Sprint F214AD: URL deduplication — both adapters exported for Protocol testing
from .url_dedup import DeduplicationStrategy, PersistentSetAdapter, RotatingBloomFilterAdapter

# Sprint 45 refactor: Extracted from coordinators/fetch_coordinator.py
from .zstd_compressor import ZstdCompressor

__all__ = [
    # Reranker
    "LightweightReranker",
    "RerankResult",
    "RerankRequest",
    "RerankerConfig",
    "RerankerFactory",
    "create_reranker",
    # Miner
    "RustMiner",
    "MiningResult",
    "create_rust_miner",
    # Sprint 80: OSINT adapters
    "CommonCrawlAdapter",
    "WaybackAdapter",
    "RawFinding",
    # Sprint 45 refactor: Browser pool and compression
    "ZstdCompressor",
    "LightpandaManager",
    "LightpandaPool",
    "apply_fcntl_nocache",
    "apply_nocache_to_path",
    "NOCACHE_THRESHOLD_BYTES",
    "F_NOCACHE",
    # Sprint F214AD: URL deduplication
    "RotatingBloomFilterAdapter",
    "PersistentSetAdapter",
    "DeduplicationStrategy",
]
