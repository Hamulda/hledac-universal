"""Approximate prompt cache using trigram-based similarity."""
import hashlib
import logging
import math
import threading
import time
from collections import OrderedDict
from pathlib import Path

# Sprint 79b: xxhash for faster hashing
try:
    import xxhash
    XXHASH_AVAILABLE = True
except ImportError:
    XXHASH_AVAILABLE = False

# Sprint F195: numpy for cosine similarity (module-level for performance)
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

# Cache versioning for migration
CACHE_VERSION = "v2"
CACHE_NAMESPACE = "pc"

logger = logging.getLogger(__name__)


def _hash_key(text: str) -> str:
    """Generate cache key with versioned prefix (xxh3 on Apple Silicon)."""
    if XXHASH_AVAILABLE:
        try:
            # xxh3_128 is NEON-optimized on Apple Silicon
            h = xxhash.xxh3_128(text.encode()).hexdigest()
        except AttributeError:
            # Fallback to xxh3_64 if xxh3_128 not available
            h = format(xxhash.xxh3_64(text.encode()).intdigest(), '016x')
        return f"{CACHE_NAMESPACE}:{CACHE_VERSION}:{h}"
    else:
        # Fallback - blake2b is fast on ARM (software optimized)
        h = hashlib.blake2b(text.encode(), digest_size=8).hexdigest()
        return f"{CACHE_NAMESPACE}:{CACHE_VERSION}:{h}"


class PromptCache:
    def __init__(self, max_entries: int = 500, embedding_dim: int = 256):
        self._cache = OrderedDict()           # prompt -> (response, timestamp)
        self._max = max_entries
        self._embeddings = OrderedDict()       # prompt -> embedding (list) - bounded
        self._dim = embedding_dim
        self._ttl = 3600                       # 1 hour default TTL
        self._lock = threading.Lock()         # Sprint 79c: Lock instead of RLock (no reentrancy needed)
        self._check_and_migrate_cache()        # Check cache version

    def _check_and_migrate_cache(self):
        """Clear old cache entries on version change."""
        version_file = Path.home() / '.hledac' / 'prompt_cache_version.txt'
        try:
            if version_file.exists() and version_file.read_text().strip() != CACHE_VERSION:
                self._cache.clear()
                self._embeddings.clear()
                logger.info(f"Cache cleared: version mismatch -> {CACHE_VERSION}")
            version_file.parent.mkdir(parents=True, exist_ok=True)
            version_file.write_text(CACHE_VERSION)
        except Exception as e:
            logger.warning(f"Cache migration check failed: {e}")

    def _get_embedding(self, text: str) -> list:
        """Generate trigram‑based approximate embedding."""
        with self._lock:
            if text in self._embeddings:
                self._embeddings.move_to_end(text)
                return self._embeddings[text]

        # Character trigrams
        trigrams = [text[i:i+3].lower() for i in range(len(text)-2)]
        emb = [0.0] * self._dim

        # Performance tradeoff: limit trigram extraction to first 100.
        # Long prompts (10k chars ≈ 10k trigrams) would be expensive to hash fully.
        _MAX_TRIGRAMS_PER_PROMPT = 100
        for trigram in trigrams[:_MAX_TRIGRAMS_PER_PROMPT]:
            # Sprint 79b: Use xxhash for faster hashing
            if XXHASH_AVAILABLE:
                h = xxhash.xxh3_64(trigram.encode()).intdigest()
            else:
                h = int(hashlib.md5(trigram.encode()).hexdigest()[:8], 16)
            bucket = h % self._dim
            emb[bucket] += 1.0

        # Normalize
        norm = math.sqrt(sum(x*x for x in emb))
        if norm > 0:
            emb = [x/norm for x in emb]

        with self._lock:
            # Bounded embeddings - LRU eviction if over limit
            while len(self._embeddings) > self._max:
                self._embeddings.popitem(last=False)
            self._embeddings[text] = emb
        return emb

    def _cosine_similarity(self, a: list, b: list) -> float:
        """Compute cosine similarity – numpy preferred, MLX optional."""
        if NUMPY_AVAILABLE:
            a_np = np.array(a, dtype=np.float32)
            b_np = np.array(b, dtype=np.float32)
            dot = float(np.dot(a_np, b_np))
            norm_a = float(np.linalg.norm(a_np))
            norm_b = float(np.linalg.norm(b_np))
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)
        else:
            # Fallback – čistý Python (pomalé, ale funkční)
            dot = sum(x*y for x, y in zip(a, b, strict=False))
            norm_a = math.sqrt(sum(x*x for x in a))
            norm_b = math.sqrt(sum(x*x for x in b))
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)

    def get(self, prompt: str, threshold: float = 0.85) -> str | None:
        """Získá odpověď z cache, pokud existuje podobný prompt."""
        now = time.time()

        with self._lock:
            # Exact match (s TTL)
            if prompt in self._cache:
                self._cache.move_to_end(prompt)
                response, ts = self._cache[prompt]
                if now - ts < self._ttl:
                    return response
                else:
                    del self._cache[prompt]
                    if prompt in self._embeddings:
                        del self._embeddings[prompt]
                    return None

        # Approximate similarity – prohledá posledních 100 položek
        prompt_emb = self._get_embedding(prompt)
        best_match = None
        best_sim = 0.0

        with self._lock:
            cache_keys = list(self._cache.keys())[-100:]

        for cached_prompt in cache_keys:
            if now - self._cache[cached_prompt][1] > self._ttl:
                continue

            # MEDIUM-10 fix: reuse cached embedding if available
            # (avoids recomputing trigram hash for every cached prompt)
            with self._lock:
                if cached_prompt in self._embeddings:
                    cached_emb = self._embeddings[cached_prompt]
                else:
                    # Compute and cache for future reuse
                    cached_emb = self._get_embedding(cached_prompt)
            sim = self._cosine_similarity(prompt_emb, cached_emb)
            if sim > best_sim:
                best_sim = sim
                best_match = cached_prompt

        if best_match is not None and best_sim >= threshold:
            with self._lock:
                self._cache.move_to_end(best_match)
                response, ts = self._cache[best_match]
            return response

        return None

    def set(self, prompt: str, response: str):
        """Uloží prompt‑response pár do cache."""
        now = time.time()
        with self._lock:
            if prompt in self._cache:
                self._cache.move_to_end(prompt)
            self._cache[prompt] = (response, now)

            # LRU eviction
            while len(self._cache) > self._max:
                oldest, _ = self._cache.popitem(last=False)
                if oldest in self._embeddings:
                    del self._embeddings[oldest]

    def invalidate_expired(self):
        """Odstraní expirované položky."""
        now = time.time()
        with self._lock:
            expired = [p for p, (_, ts) in self._cache.items() if now - ts > self._ttl]
            for p in expired:
                del self._cache[p]
                if p in self._embeddings:
                    del self._embeddings[p]


# Sprint 8UC B.3: Persistent KV cache for system prompt synthesis


class SystemPromptKVCache:
    """
    Persistent KV cache for system prompt.

    On M1, saves 30-40% synthesis latency for repeated sprints
    with the same system prompt (>200 tokens ≈ 3s on M1).
    Thread-safe: RLock (CPU_EXECUTOR is ThreadPoolExecutor).

    Since make_kv_caches is NOT available in mlx_lm, this uses
    a token-prefix cache as fallback — same prompt returns cached
    tokenization without re-tokenizing.
    """

    def __init__(self) -> None:
        self._cached_prompt: str | None = None
        self._cached_tokens: list[int] | None = None
        self._lock = threading.RLock()

    def get_or_build(
        self,
        model,  # unused — kept for API compatibility
        tokenizer,
        system_prompt: str,
    ) -> tuple[None, int]:
        """
        Returns (None, prefix_token_count) — KV cache not available
        but token prefix is cached for re-use.

        On a future mlx_lm with make_kv_caches support, this would
        return (kv_caches, token_count).
        """
        with self._lock:
            if self._cached_prompt == system_prompt and self._cached_tokens is not None:
                return None, len(self._cached_tokens)

            # Tokenize and cache
            try:
                tokens = tokenizer.encode(system_prompt)
                self._cached_prompt = system_prompt
                self._cached_tokens = tokens
                return None, len(tokens)
            except Exception:
                return None, 0

    def invalidate(self) -> None:
        with self._lock:
            self._cached_prompt = None
            self._cached_tokens = None


# Singleton instance
_SYSTEM_PROMPT_CACHE = SystemPromptKVCache()

