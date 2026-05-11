"""
Filtering Utilities - URL Filtering and Frontier Management
===========================================================

Combines:
- FastFilter: Binary Fuse Filter for URL filtering (memory efficient)
- EfficientFrontier: Quotient Filter for URL deduplication

Optimized for M1 Silicon (8GB RAM).

Usage:
    from hledac.universal.utils.filtering import FastFilter, EfficientFrontier
    
    # URL filtering
    filter = FastFilter()
    if filter.check_url("https://example.com"):
        # URL is allowed
        
    # URL frontier
    frontier = EfficientFrontier()
    if not frontier.contains(url):
        frontier.add(url)
        # Process URL
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set
from urllib.parse import urlparse, urlunparse

try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False

logger = logging.getLogger(__name__)

# =============================================================================
# FILTER STATS
# =============================================================================

@dataclass
class FilterStats:
    """Statistics for fast filter."""
    total_checked: int = 0
    blocked: int = 0
    allowed: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    def block_rate(self) -> float:
        """Calculate block rate."""
        if self.total_checked == 0:
            return 0.0
        return self.blocked / self.total_checked


@dataclass
class FrontierStats:
    """Statistics for frontier operations."""
    total_urls: int = 0
    checked_urls: int = 0
    skipped_urls: int = 0
    added_urls: int = 0
    false_positives: int = 0


# =============================================================================
# SIMPLE SET FILTER (Fallback)
# =============================================================================

class SimpleSetFilter:
    """
    Python set-based filter as fallback.
    Simple but memory-intensive for large datasets.
    """

    def __init__(self):
        self._blocked_domains: Set[str] = set()
        self._blocked_urls: Set[str] = set()
        self._blocked_patterns: List[re.Pattern] = []

    def add_domain(self, domain: str):
        """Add blocked domain."""
        self._blocked_domains.add(domain.lower())

    def add_url(self, url: str):
        """Add blocked URL."""
        self._blocked_urls.add(url.lower())

    def add_pattern(self, pattern: str):
        """Add blocked URL pattern (regex)."""
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            self._blocked_patterns.append(compiled)
        except re.error as e:
            logger.warning(f"Invalid regex pattern: {pattern}, error: {e}")

    def is_blocked(self, url: str) -> bool:
        """Check if URL is blocked."""
        url_lower = url.lower()

        if url_lower in self._blocked_urls:
            return True

        try:
            parsed = urlparse(url_lower)
            domain = parsed.netloc.lower()

            for blocked in self._blocked_domains:
                if domain == blocked or domain.endswith('.' + blocked):
                    return True

            for pattern in self._blocked_patterns:
                if pattern.search(url_lower):
                    return True

        except Exception as e:
            logger.error(f"URL parsing error: {e}")

        return False

    def size(self) -> int:
        """Get filter size."""
        return len(self._blocked_domains) + len(self._blocked_urls) + len(self._blocked_patterns)


# =============================================================================
# BINARY FUSE FILTER
# =============================================================================

class BinaryFuseFilter:
    """
    Binary Fuse Filter wrapper using pyxorfilter.
    Memory-efficient probabilistic filter with 0% false negatives.
    """

    def __init__(self, expected_size: int = 100000):
        self._filter = None
        self._initialized = False
        self._expected_size = expected_size
        self._items: Set[str] = set()
        self._init_filter()

    def _init_filter(self):
        """Initialize pyxorfilter."""
        try:
            from pyxorfilter import FuseFilter
            logger.info("Initializing Binary Fuse Filter")
            self._initialized = True
        except ImportError:
            logger.warning("pyxorfilter not available, using set fallback")

    def add(self, item: str):
        """Add item to filter."""
        self._items.add(item)

    def build(self):
        """Build the filter from added items."""
        if not self._initialized:
            return

        try:
            from pyxorfilter import FuseFilter

            if not self._items:
                logger.warning("No items to build filter")
                return

            items_list = list(self._items)
            self._filter = FuseFilter(items_list)
            logger.info(f"Binary Fuse Filter built with {len(items_list)} items")

        except Exception as e:
            logger.error(f"Failed to build Binary Fuse Filter: {e}")
            self._filter = None

    def contains(self, item: str) -> bool:
        """Check if item is in filter."""
        if self._filter is not None:
            try:
                return item in self._filter
            except Exception as e:
                logger.error(f"Filter lookup error: {e}")

        return item in self._items

    def is_available(self) -> bool:
        """Check if filter is available."""
        return self._initialized and self._filter is not None


# =============================================================================
# FAST FILTER
# =============================================================================

class FastFilter:
    """
    Memory-efficient URL filtering using Binary Fuse Filter.
    Optimized for M1 Silicon (8GB RAM).
    Falls back to Python set if pyxorfilter unavailable.
    """

    DEFAULT_BLOCKED_DOMAINS = [
        'spam.com',
        'advertising.net',
        'malware-site.org',
        'phishing-example.com',
    ]

    DEFAULT_BLOCKED_PATTERNS = [
        r'.*\.exe$',
        r'.*\.dll$',
        r'.*download.*virus.*',
        r'.*free.*crack.*',
    ]

    def __init__(
        self,
        use_bff: bool = True,
        fallback_to_set: bool = True,
        enable_cache: bool = True
    ):
        self._bff: Optional[BinaryFuseFilter] = None
        self._set_filter: Optional[SimpleSetFilter] = None
        self._use_bff = use_bff
        self._fallback_to_set = fallback_to_set

        self._stats = FilterStats()
        self._cache: Dict[str, bool] = {}
        self._cache_size = 1000
        self._enable_cache = enable_cache

        if use_bff:
            self._bff = BinaryFuseFilter()

        if fallback_to_set:
            self._set_filter = SimpleSetFilter()

        self._load_default_blocklists()
        logger.info("FastFilter initialized")

    def _load_default_blocklists(self):
        """Load default blocked domains and patterns."""
        for domain in self.DEFAULT_BLOCKED_DOMAINS:
            self.add_blocked_domain(domain)

        for pattern in self.DEFAULT_BLOCKED_PATTERNS:
            self.add_blocked_pattern(pattern)

        if self._bff:
            self._bff.build()

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for consistent matching."""
        url = url.lower().strip()

        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url

        try:
            parsed = urlparse(url)
            normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            return normalized
        except Exception:
            return url

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        try:
            parsed = urlparse(url)
            return parsed.netloc.lower()
        except Exception:
            return url

    def _check_cache(self, url: str) -> Optional[bool]:
        """Check cache for URL."""
        if not self._enable_cache:
            return None

        normalized = self._normalize_url(url)
        url_hash = hashlib.md5(normalized.encode()).hexdigest()

        if url_hash in self._cache:
            self._stats.cache_hits += 1
            return self._cache[url_hash]

        self._stats.cache_misses += 1
        return None

    def _update_cache(self, url: str, blocked: bool):
        """Update cache with URL result."""
        if not self._enable_cache:
            return

        normalized = self._normalize_url(url)
        url_hash = hashlib.md5(normalized.encode()).hexdigest()

        if len(self._cache) >= self._cache_size:
            self._cache.popitem()

        self._cache[url_hash] = blocked

    def add_blocked_domain(self, domain: str):
        """Add domain to blocklist."""
        domain = domain.lower()

        if self._bff:
            self._bff.add(domain)

        if self._set_filter:
            self._set_filter.add_domain(domain)

    def add_blocked_url(self, url: str):
        """Add URL to blocklist."""
        normalized = self._normalize_url(url)

        if self._bff:
            self._bff.add(normalized)

        if self._set_filter:
            self._set_filter.add_url(normalized)

    def add_blocked_pattern(self, pattern: str):
        """Add regex pattern to blocklist."""
        if self._set_filter:
            self._set_filter.add_pattern(pattern)

    def load_blocklist_file(self, filepath: str):
        """Load blocklist from file (one entry per line)."""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    if line.startswith('http'):
                        self.add_blocked_url(line)
                    else:
                        self.add_blocked_domain(line)

            if self._bff:
                self._bff.build()

            logger.info(f"Loaded blocklist from: {filepath}")

        except Exception as e:
            logger.error(f"Failed to load blocklist: {e}")

    def check_url(self, url: str) -> bool:
        """
        Check if URL is allowed (not blocked).
        
        Returns:
            True if allowed, False if blocked
        """
        self._stats.total_checked += 1

        cached_result = self._check_cache(url)
        if cached_result is not None:
            if not cached_result:
                self._stats.blocked += 1
            else:
                self._stats.allowed += 1
            return cached_result

        normalized = self._normalize_url(url)
        domain = self._get_domain(url)
        blocked = False

        if self._bff and self._bff.is_available():
            if self._bff.contains(normalized) or self._bff.contains(domain):
                blocked = True

        if not blocked and self._set_filter:
            if self._set_filter.is_blocked(normalized):
                blocked = True

        if blocked:
            self._stats.blocked += 1
        else:
            self._stats.allowed += 1

        self._update_cache(url, not blocked)
        return not blocked

    def check_urls_batch(self, urls: List[str]) -> List[bool]:
        """Check multiple URLs."""
        return [self.check_url(url) for url in urls]

    def get_stats(self) -> Dict[str, Any]:
        """Get filter statistics."""
        return {
            'total_checked': self._stats.total_checked,
            'blocked': self._stats.blocked,
            'allowed': self._stats.allowed,
            'block_rate': self._stats.block_rate(),
            'cache_hits': self._stats.cache_hits,
            'cache_misses': self._stats.cache_misses,
            'bff_available': self._bff.is_available() if self._bff else False,
            'set_filter_size': self._set_filter.size() if self._set_filter else 0
        }

    def reset_stats(self):
        """Reset statistics."""
        self._stats = FilterStats()
        self._cache.clear()

    def is_bff_available(self) -> bool:
        """Check if Binary Fuse Filter is available."""
        return self._bff is not None and self._bff.is_available()


# =============================================================================
# QUOTIENT FILTER FRONTIER
# =============================================================================

class QuotientFilterFrontier:
    """
    URL frontier using PyProbables Quotient Filter.
    
    Quotient Filter advantages:
    - Constant-time lookup
    - Minimal false positive rate
    - Lower memory usage than Bloom Filter
    - Supports deletion operations
    """

    def __init__(
        self,
        capacity: int = 1000000,
        filter_size: Optional[int] = None
    ):
        self.capacity = capacity
        self._quotient_filter: Optional[Any] = None
        self._exact_set: Set[str] = set()
        self._stats = FrontierStats(
            total_urls=0,
            checked_urls=0,
            skipped_urls=0,
            added_urls=0
        )

        try:
            self._init_quotient_filter(filter_size)
            logger.info(f"QuotientFilterFrontier initialized (capacity: {capacity})")
        except ImportError:
            logger.warning("PyProbables not available, using set-based fallback")
            self._init_fallback()

    def _init_quotient_filter(self, filter_size: Optional[int]):
        """Initialize quotient filter."""
        try:
            from pyprobables import QuotientFilter

            if filter_size is None:
                filter_size = self.capacity * 2

            self._quotient_filter = QuotientFilter(filter_size=filter_size)

        except Exception as e:
            logger.error(f"Failed to initialize quotient filter: {e}")
            self._init_fallback()

    def _init_fallback(self):
        """Initialize fallback using set."""
        self._quotient_filter = None

    def add(self, url: str):
        """Add URL to frontier."""
        if self._quotient_filter is not None:
            self._quotient_filter.add(url)

        self._exact_set.add(url)
        self._stats.total_urls += 1
        self._stats.added_urls += 1

    def contains(self, url: str) -> bool:
        """Check if URL is in frontier."""
        self._stats.checked_urls += 1
        in_exact = url in self._exact_set

        if self._quotient_filter is not None:
            in_filter = url in self._quotient_filter

            if in_filter and not in_exact:
                self._stats.false_positives += 1

            if in_exact:
                return True

        return in_exact

    def remove(self, url: str):
        """Remove URL from frontier."""
        if url in self._exact_set:
            self._exact_set.remove(url)
            self._stats.total_urls -= 1

        if self._quotient_filter is not None:
            try:
                self._quotient_filter.remove(url)
            except Exception:
                pass

    def get_stats(self) -> FrontierStats:
        """Get frontier statistics."""
        return self._stats

    def get_size(self) -> int:
        """Get current number of URLs in frontier."""
        return len(self._exact_set)

    def clear(self):
        """Clear all URLs from frontier."""
        self._exact_set.clear()
        self._stats = FrontierStats(
            total_urls=0,
            checked_urls=0,
            skipped_urls=0,
            added_urls=0
        )

        if self._quotient_filter is not None:
            try:
                from pyprobables import QuotientFilter
                filter_size = self._quotient_filter.size
                self._quotient_filter = QuotientFilter(filter_size=filter_size)
            except Exception:
                pass


# =============================================================================
# PERSISTENT FRONTIER
# =============================================================================

class PersistentFrontier:
    """
    Persistent URL frontier with disk storage.
    Supports multiple storage backends (JSON, Pickle, SQLite).
    """

    def __init__(
        self,
        storage_path: Optional[Path] = None,
        backend: str = "orjson"
    ):
        if storage_path is None:
            storage_path = Path.home() / ".cache" / "hledac" / "frontier"

        self.storage_path = storage_path
        self.backend = backend
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self._frontier = QuotientFilterFrontier()
        self._load_from_disk()

        logger.info(f"PersistentFrontier initialized (backend: {backend})")

    def _get_storage_file(self) -> Path:
        """Get path to storage file."""
        extension = {
            'orjson': '.json',
            'json': '.json',
            'sqlite': '.db'
        }.get(self.backend, '.json')

        return self.storage_path / f"frontier{extension}"

    def _save_to_disk(self):
        """Save frontier to disk."""
        storage_file = self._get_storage_file()

        try:
            if self.backend == 'orjson':
                data = {
                    'urls': list(self._frontier._exact_set),
                    'stats': {
                        'total_urls': self._frontier._stats.total_urls,
                        'checked_urls': self._frontier._stats.checked_urls,
                        'skipped_urls': self._frontier._stats.skipped_urls,
                        'added_urls': self._frontier._stats.added_urls,
                        'false_positives': self._frontier._stats.false_positives,
                    },
                    'timestamp': datetime.utcnow().isoformat()
                }
                if ORJSON_AVAILABLE:
                    with open(storage_file, 'wb') as f:
                        f.write(orjson.dumps(data))
                else:
                    import json
                    with open(storage_file, 'w') as f:
                        json.dump(data, f)

            elif self.backend == 'json':
                import json
                with open(storage_file, 'w') as f:
                    json.dump({
                        'urls': list(self._frontier._exact_set),
                        'stats': {
                            'total_urls': self._frontier._stats.total_urls,
                            'checked_urls': self._frontier._stats.checked_urls,
                            'skipped_urls': self._frontier._stats.skipped_urls,
                            'added_urls': self._frontier._stats.added_urls,
                            'false_positives': self._frontier._stats.false_positives,
                        },
                        'timestamp': datetime.utcnow().isoformat()
                    }, f)

            elif self.backend == 'sqlite':
                self._save_sqlite()

            logger.info(f"Frontier saved to {storage_file}")

        except Exception as e:
            logger.error(f"Failed to save frontier: {e}")

    def _save_sqlite(self):
        """Save frontier to SQLite."""
        try:
            import sqlite3

            storage_file = self._get_storage_file()
            conn = sqlite3.connect(storage_file)
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS frontier (
                    url TEXT PRIMARY KEY,
                    timestamp TEXT
                )
            ''')

            cursor.execute('DELETE FROM frontier')
            timestamp = datetime.utcnow().isoformat()

            data = [(url, timestamp) for url in self._frontier._exact_set]
            cursor.executemany(
                'INSERT OR REPLACE INTO frontier (url, timestamp) VALUES (?, ?)',
                data
            )

            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"Failed to save SQLite frontier: {e}")

    def _load_from_disk(self):
        """Load frontier from disk."""
        storage_file = self._get_storage_file()

        if not storage_file.exists():
            logger.info("No existing frontier found, starting fresh")
            return

        try:
            if self.backend == 'orjson':
                if ORJSON_AVAILABLE:
                    with open(storage_file, 'rb') as f:
                        data = orjson.loads(f.read())
                else:
                    import json
                    with open(storage_file, 'r') as f:
                        data = json.load(f)
                self._frontier._exact_set = set(data.get('urls', []))

            elif self.backend == 'json':
                import json
                with open(storage_file, 'r') as f:
                    data = json.load(f)
                    self._frontier._exact_set = set(data.get('urls', []))

            elif self.backend == 'sqlite':
                self._load_sqlite()

            logger.info(f"Loaded {len(self._frontier._exact_set)} URLs from {storage_file}")

        except Exception as e:
            logger.error(f"Failed to load frontier: {e}")

    def _load_sqlite(self):
        """Load frontier from SQLite."""
        try:
            import sqlite3

            storage_file = self._get_storage_file()
            conn = sqlite3.connect(storage_file)
            cursor = conn.cursor()

            cursor.execute('SELECT url FROM frontier LIMIT 50000')
            # LIMIT 50000 — cold path (startup only).
            # Add ORDER BY timestamp DESC when frontier table exceeds 100k rows.
            urls = [row[0] for row in cursor.fetchall()]

            self._frontier._exact_set = set(urls)
            self._frontier._stats.total_urls = len(urls)

            conn.close()

        except Exception as e:
            logger.error(f"Failed to load SQLite frontier: {e}")

    def add(self, url: str, persist: bool = True):
        """Add URL to frontier."""
        self._frontier.add(url)

        if persist:
            self._save_to_disk()

    def contains(self, url: str) -> bool:
        """Check if URL is in frontier."""
        result = self._frontier.contains(url)

        if result:
            self._frontier._stats.skipped_urls += 1

        return result

    def remove(self, url: str, persist: bool = True):
        """Remove URL from frontier."""
        self._frontier.remove(url)

        if persist:
            self._save_to_disk()

    def get_stats(self) -> FrontierStats:
        """Get frontier statistics."""
        return self._frontier.get_stats()

    def get_size(self) -> int:
        """Get current number of URLs in frontier."""
        return self._frontier.get_size()

    def clear(self, persist: bool = True):
        """Clear all URLs from frontier."""
        self._frontier.clear()

        if persist:
            self._save_to_disk()

    def iter_urls(self) -> Iterator[str]:
        """Iterate over all URLs in frontier."""
        return iter(self._frontier._exact_set)

    def get_all_urls(self) -> List[str]:
        """Get all URLs in frontier."""
        return list(self._frontier._exact_set)


# =============================================================================
# EFFICIENT FRONTIER
# =============================================================================

class EfficientFrontier(PersistentFrontier):
    """
    High-level frontier interface with smart deduplication.
    Combines quotient filter efficiency with intelligent URL normalization.
    """

    def __init__(
        self,
        storage_path: Optional[Path] = None,
        backend: str = "orjson",
        normalize_urls: bool = True
    ):
        super().__init__(storage_path, backend)
        self.normalize_urls = normalize_urls

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for consistent deduplication."""
        if not self.normalize_urls:
            return url

        parsed = urlparse(url)

        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path or '/'
        params = ''
        query = ''
        fragment = ''

        return urlunparse((scheme, netloc, path, params, query, fragment))

    def add(self, url: str, persist: bool = True):
        """Add normalized URL to frontier."""
        normalized = self._normalize_url(url)
        super().add(normalized, persist)

    def contains(self, url: str) -> bool:
        """Check if normalized URL is in frontier."""
        normalized = self._normalize_url(url)
        return super().contains(normalized)

    def remove(self, url: str, persist: bool = True):
        """Remove normalized URL from frontier."""
        normalized = self._normalize_url(url)
        super().remove(normalized, persist)

    def add_batch(self, urls: List[str], persist: bool = True):
        """Add multiple URLs to frontier."""
        for url in urls:
            self.add(url, persist=False)

        if persist:
            self._save_to_disk()

    def check_batch(self, urls: List[str]) -> List[bool]:
        """Check multiple URLs against frontier."""
        return [self.contains(url) for url in urls]


# =============================================================================
# GLOBAL INSTANCES
# =============================================================================

_global_filter: Optional[FastFilter] = None
_global_frontier: Optional[EfficientFrontier] = None


def get_fast_filter() -> FastFilter:
    """Get global FastFilter instance."""
    global _global_filter

    if _global_filter is None:
        _global_filter = FastFilter()

    return _global_filter


def get_frontier(
    storage_path: Optional[Path] = None,
    backend: str = "orjson"
) -> EfficientFrontier:
    """Get global EfficientFrontier instance."""
    global _global_frontier

    if _global_frontier is None:
        _global_frontier = EfficientFrontier(storage_path, backend)

    return _global_frontier
