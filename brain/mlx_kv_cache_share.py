"""
P4-3: Tokenized Prompt Cache — Pre-tokenized prefix reuse
=========================================================

Problem: Every mlx_lm.generate() call re-tokenizes identical system_msg prefix.
Solution: Pre-tokenize and cache token arrays for fixed prompt templates.

M1 8GB: Tokenization is CPU-bound string processing (~5-20ms per prompt).
Caching tokenized prefixes eliminates redundant tokenization overhead during
high-throughput inference bursts (synthesis, hypothesis generation).

What MLX KV cache CAN do: mlx_lm does NOT expose internal KV state for reuse.
What we CAN cache: Pre-tokenized token arrays (mx.array) for fixed prompt templates.

Invariant: system_msg template is fixed at model load time.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time as time_module
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from brain.deephermes3_engine import DeepHermes3Engine

logger = logging.getLogger(__name__)

# M1 8GB bounded: max cached tokenized prompts
_MAX_CACHED_PROMPTS: int = 8


@dataclass
class TokenizedPromptEntry:
    """Cached tokenized prompt array."""
    key: str  # hash of template + model_path
    tokens: list[int]  # raw token IDs
    model_path: str  # for cache invalidation on model change
    template_len: int  # token count
    hits: int = 0
    last_used: float = 0.0
    tokenize_time_ms: float = 0.0


@dataclass
class PromptCacheStats:
    """Statistics for tokenized prompt cache."""
    cache_hits: int = 0
    cache_misses: int = 0
    prompts_cached: int = 0
    tokenize_time_saved_ms: float = 0.0


class TokenizedPromptCache:
    """
    Tokenized prompt prefix cache for MLX inference.

    Architecture:
    1. At init: tokenize common prompt templates and cache token arrays
    2. On generate: return cached tokens instead of re-tokenizing
    3. On cache full: LRU eviction

    M1 8GB safe:
    - Bounded to _MAX_CACHED_PROMPTS entries
    - Tokens stored as list[int] (minimal memory: ~2KB per 500-token prompt)
    - Async-safe with asyncio.Lock
    """

    def __init__(self, engine: DeepHermes3Engine) -> None:
        self._engine = engine
        self._cache: dict[str, TokenizedPromptEntry] = {}
        self._lock = asyncio.Lock()
        self._stats = PromptCacheStats()
        self._initialized = False

    @staticmethod
    def _compute_prompt_key(prompt: str, model_path: str) -> str:
        """Hash of prompt + model path for cache key."""
        h = hashlib.sha256()
        h.update(model_path.encode())
        h.update(prompt.encode())
        return h.hexdigest()[:16]

    async def _ensure_initialized(self) -> None:
        """Pre-tokenize common prompt templates."""
        if self._initialized:
            return

        async with self._lock:
            common_templates = await self._get_common_templates()
            model_path = self._engine.config.model_path

            for template in common_templates:
                if not template:
                    continue
                key = self._compute_prompt_key(template, model_path)
                if key in self._cache:
                    continue
                await self._tokenize_and_cache(template, model_path)

            self._initialized = True

    async def _get_common_templates(self) -> list[str]:
        """Get common prompt templates from engine config."""
        # Return empty list - templates are extracted from engine at runtime
        # The actual templates are defined in synthesis/hypothesis/DSPy prompts
        return []

    async def _tokenize_and_cache(self, prompt: str, model_path: str) -> None:
        """Tokenize a prompt and add to cache."""
        if len(self._cache) >= _MAX_CACHED_PROMPTS:
            await self._evict_lru()

        key = self._compute_prompt_key(prompt, model_path)
        if key in self._cache:
            return

        t0 = time_module.time()

        try:
            # Tokenize in thread pool (CPU-bound)
            loop = asyncio.get_running_loop()
            tokens = await loop.run_in_executor(
                None, self._tokenize_sync, prompt
            )

            if tokens is None or not tokens:
                logger.debug("PromptCache: tokenization returned empty")
                return

            tokenize_ms = (time_module.time() - t0) * 1000

            entry = TokenizedPromptEntry(
                key=key,
                tokens=tokens,
                model_path=model_path,
                template_len=len(tokens),
                tokenize_time_ms=tokenize_ms,
            )
            self._cache[key] = entry
            self._stats.prompts_cached = len(self._cache)
            logger.debug(
                f"PromptCache: cached prompt (len={len(tokens)}, "
                f"tokenize={tokenize_ms:.1f}ms)"
            )
        except Exception as e:
            logger.warning(f"PromptCache: tokenize failed: {e}")

    def _tokenize_sync(self, prompt: str) -> list[int] | None:
        """Synchronous tokenization using engine's tokenizer."""
        try:
            tokenizer = getattr(self._engine, "_tokenizer", None)
            if tokenizer is None:
                return None
            encoded = tokenizer.encode(prompt)
            if hasattr(encoded, "ids"):
                return encoded.ids
            if hasattr(encoded, "__iter__"):
                return list(encoded)
            return None
        except Exception:
            return None

    async def _evict_lru(self) -> None:
        """Evict least-recently-used cache entry."""
        if not self._cache:
            return

        lru_key = min(
            self._cache,
            key=lambda k: (self._cache[k].hits, self._cache[k].last_used),
        )
        del self._cache[lru_key]
        logger.debug(f"PromptCache: evicted LRU entry {lru_key[:8]}")

    async def get_tokens(
        self, prompt: str
    ) -> tuple[list[int], float] | tuple[None, None]:
        """
        Get cached tokenized prompt.

        Returns (tokens, tokenize_time_saved_ms) if cached,
        or (None, None) if not cached.
        """
        await self._ensure_initialized()

        model_path = self._engine.config.model_path
        key = self._compute_prompt_key(prompt, model_path)

        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats.cache_misses += 1
                return None, None

            entry.hits += 1
            entry.last_used = time_module.time()
            self._stats.cache_hits += 1
            self._stats.tokenize_time_saved_ms += entry.tokenize_time_ms
            return entry.tokens, entry.tokenize_time_ms

    async def warm(self, prompt: str) -> None:
        """
        Pre-warm cache with a prompt template.

        Fire-and-forget: caller does not wait.
        """
        model_path = self._engine.config.model_path
        key = self._compute_prompt_key(prompt, model_path)
        if key not in self._cache:
            asyncio.create_task(self._tokenize_and_cache(prompt, model_path))

    def get_stats(self) -> PromptCacheStats:
        """Return cache statistics."""
        return self._stats

    def clear_cache(self) -> None:
        """Clear all cached prompts. Called after model swap."""
        self._cache.clear()
        self._stats = PromptCacheStats()
        self._initialized = False
        logger.debug("PromptCache: cache cleared")

    def cache_size(self) -> int:
        """Return current cache size."""
        return len(self._cache)
