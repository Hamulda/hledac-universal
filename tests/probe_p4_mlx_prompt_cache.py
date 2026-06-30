"""
Probe tests for P4-3: Tokenized Prompt Cache
============================================
Tests pre-tokenized prefix reuse for MLX inference.
"""

import pytest

from brain.mlx_kv_cache_share import (
    _MAX_CACHED_PROMPTS,
    PromptCacheStats,
    TokenizedPromptCache,
    TokenizedPromptEntry,
)


class MockEngine:
    """Mock DeepHermes3Engine for testing."""
    def __init__(self) -> None:
        self.config = MockConfig()
        self._tokenizer = MockTokenizer()


class MockConfig:
    """Mock engine config."""
    model_path = "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"


class MockTokenizer:
    """Mock tokenizer for testing."""
    def encode(self, text: str) -> list[int]:
        # Simple word-based tokenization for testing
        words = text.split()
        return [hash(w) % 50000 for w in words]


@pytest.mark.asyncio
async def test_prompt_cache_basic():
    """Test basic cache get/miss."""
    engine = MockEngine()
    cache = TokenizedPromptCache(engine)  # type: ignore

    # Cache miss on first call
    tokens, _ = await cache.get_tokens("Hello world test")
    # No common templates registered, so returns None
    assert tokens is None


@pytest.mark.asyncio
async def test_prompt_cache_stats():
    """Test cache statistics."""
    engine = MockEngine()
    cache = TokenizedPromptCache(engine)  # type: ignore

    stats = cache.get_stats()
    assert stats.cache_hits == 0
    assert stats.cache_misses == 0
    assert stats.prompts_cached == 0


@pytest.mark.asyncio
async def test_prompt_cache_size():
    """Test cache size tracking."""
    engine = MockEngine()
    cache = TokenizedPromptCache(engine)  # type: ignore

    size = cache.cache_size()
    assert size == 0


@pytest.mark.asyncio
async def test_prompt_cache_clear():
    """Test cache clearing."""
    engine = MockEngine()
    cache = TokenizedPromptCache(engine)  # type: ignore

    cache.clear_cache()

    stats = cache.get_stats()
    assert stats.cache_hits == 0
    assert stats.cache_misses == 0


@pytest.mark.asyncio
async def test_prompt_cache_compute_key():
    """Test prompt key computation."""
    key1 = TokenizedPromptCache._compute_prompt_key("test prompt", "model/path")
    key2 = TokenizedPromptCache._compute_prompt_key("test prompt", "model/path")
    key3 = TokenizedPromptCache._compute_prompt_key("different", "model/path")

    # Same inputs → same key
    assert key1 == key2
    # Different inputs → different key
    assert key1 != key3
    # Key is 16 chars (truncated SHA256)
    assert len(key1) == 16


@pytest.mark.asyncio
async def test_prompt_cache_double_init():
    """Test that double initialization is safe (double-checked locking)."""
    engine = MockEngine()
    cache = TokenizedPromptCache(engine)  # type: ignore

    await cache._ensure_initialized()
    await cache._ensure_initialized()  # Second call should be no-op

    assert cache._initialized


@pytest.mark.asyncio
async def test_prompt_cache_tokenize_empty():
    """Test tokenization of empty template list."""
    engine = MockEngine()
    cache = TokenizedPromptCache(engine)  # type: ignore

    templates = await cache._get_common_templates()
    assert templates == []


@pytest.mark.asyncio
async def test_prompt_cache_warm():
    """Test cache warm (fire-and-forget)."""
    engine = MockEngine()
    cache = TokenizedPromptCache(engine)  # type: ignore

    # Warm should not raise
    await cache.warm("test prompt")


def test_tokenized_prompt_entry():
    """Test TokenizedPromptEntry dataclass."""
    entry = TokenizedPromptEntry(
        key="abc123",
        tokens=[1, 2, 3, 4, 5],
        model_path="test/model",
        template_len=5,
        hits=10,
        last_used=123456.0,
        tokenize_time_ms=5.0,
    )

    assert entry.key == "abc123"
    assert entry.template_len == 5
    assert entry.hits == 10


def test_prompt_cache_stats_dataclass():
    """Test PromptCacheStats dataclass."""
    stats = PromptCacheStats(
        cache_hits=100,
        cache_misses=20,
        prompts_cached=5,
        tokenize_time_saved_ms=500.0,
    )

    assert stats.cache_hits == 100
    assert stats.cache_misses == 20


def test_max_cached_prompts_constant():
    """Test _MAX_CACHED_PROMPTS is reasonable."""
    assert _MAX_CACHED_PROMPTS > 0
    assert _MAX_CACHED_PROMPTS <= 16  # Should be small for M1 8GB


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
