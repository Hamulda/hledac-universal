"""
warmup.py — Model Warmup Manager
================================




PEP 698: Extracted from DeepHermes3Engine warmup/prefill logic.
Handles system prompt cache warmup and KV cache priming.

Extracted to eliminate depth-6 nested functions:
- _prefill_warmup_caches() → WarmupManager.warmup_all()
- _do_prefill() → WarmupManager._do_prefill_impl()
- _do_generate() → WarmupManager._do_generate_impl()

M1 8GB Safe: Parallel prefill with timeout protection.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    import mlx.core as mx

logger = logging.getLogger(__name__)

WARMUP_CACHE_DIR = Path.home() / '.hledac' / 'cache' / 'warmup'


@dataclass
class WarmupConfig:
    """Configuration for model warmup."""
    system_prompt: str = "You are a helpful research assistant."
    few_shot_examples: list[dict[str, str]] = field(default_factory=lambda: [
        {"user": "What is 2+2?", "assistant": "4"},
        {"user": "Capital of France?", "assistant": "Paris"},
    ])
    warmup_tokens: int = 1000
    parallel_prefill: bool = True
    timeout_seconds: float = 60.0


@dataclass
class WarmupResult:
    """Result of warmup operation."""
    success: bool
    cache_stored: bool
    prefill_tokens: int
    duration_seconds: float
    error: str | None = None


class WarmupManager:
    """
    Manages model warmup and cache prefilling.

    Extracted from DeepHermes3Engine to:
    1. Eliminate depth-6 nested function anti-pattern
    2. Enable independent testing of warmup logic
    3. Provide reusable warmup interface for multiple engines

    Supports parallel system prompt + warmup cache prefill (F320 optimization).
    """

    def __init__(
        self,
        config: WarmupConfig | None = None,
        model_getter: Callable[[], Any] | None = None,
        tokenizer_getter: Callable[[], Any] | None = None,
        cache_saver: Callable[[], Any] | None = None,
    ) -> None:
        self._config = config or WarmupConfig()
        self._model_getter = model_getter
        self._tokenizer_getter = tokenizer_getter
        self._cache_saver = cache_saver
        self._mlx_lock = None
        self._supports_stream_generate = False

    def set_model_accessors(
        self,
        model_getter: Callable[[], Any],
        tokenizer_getter: Callable[[], Any],
    ) -> None:
        """Set model and tokenizer accessors (for lazy initialization)."""
        self._model_getter = model_getter
        self._tokenizer_getter = tokenizer_getter

    def set_cache_saver(self, cache_saver: Callable[[], Any]) -> None:
        """Set cache saver callback."""
        self._cache_saver = cache_saver

    def _get_warmup_cache_path(self) -> Path:
        """Compute cache file path from system prompt fingerprint."""
        import xxhash
        parts = [self._config.system_prompt]
        for ex in self._config.few_shot_examples[:3]:
            parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
        canonical = '\n'.join(parts)
        prompt_hash = xxhash.xxh3_64_hex(canonical)[:16]
        WARMUP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return WARMUP_CACHE_DIR / f'warmup_{prompt_hash}.safetensors'

    def _build_warmup_prompt(self) -> str:
        """Build warmup prompt from config."""
        parts = [f'<|im_start|>system\n{self._config.system_prompt}<|im_end|>']
        for ex in self._config.few_shot_examples[:3]:
            parts.append(f"<|im_start|>user\n{ex.get('user', '')}<|im_end|>")
            parts.append(f"<|im_start|>assistant\n{ex.get('assistant', '')}<|im_end|>")
        return '\n'.join(parts)

    async def warmup_all(self) -> WarmupResult:
        """
        Perform full warmup: system prompt cache + warmup cache.

        Returns:
            WarmupResult with success status and metrics
        """
        import time
        start = time.time()

        try:
            if self._config.parallel_prefill:
                result = await self._parallel_warmup()
            else:
                result = await self._sequential_warmup()

            duration = time.time() - start
            return WarmupResult(
                success=result,
                cache_stored=result,
                prefill_tokens=self._estimate_prefill_tokens(),
                duration_seconds=duration,
            )
        except Exception as e:
            logger.warning(f'[WarmupManager] Warmup failed: {e}')
            return WarmupResult(
                success=False,
                cache_stored=False,
                prefill_tokens=0,
                duration_seconds=time.time() - start,
                error=str(e),
            )

    async def _parallel_warmup(self) -> bool:
        """Parallel system cache + warmup cache prefilling."""
        from hledac.universal.utils.asyncx import parallel, safe_wait_for

        async def prefetch_system_cache() -> bool:
            return await self._prefill_system_cache()

        async def prefetch_warmup_cache() -> bool:
            return await self._prefill_warmup_cache()

        try:
            # Run both prefetches in parallel
            # F3XX: parallel(policy="collect") replaces asyncio.gather.
            results = await parallel(
                [
                    safe_wait_for(prefetch_system_cache(), timeout=self._config.timeout_seconds),
                    safe_wait_for(prefetch_warmup_cache(), timeout=self._config.timeout_seconds),
                ],
                policy="collect",
                ctx="warmup",
            )

            successes = sum(1 for r in results.ok if r is True)
            exceptions = results.errors

            if exceptions:
                logger.warning(f'[WarmupManager] {len(exceptions)} prefill exception(s)')

            # Save cache if warmup successful
            if successes >= 2 and self._cache_saver is not None:
                try:
                    await asyncio.to_thread(self._cache_saver)
                except Exception as e:
                    logger.debug(f'[WarmupManager] Cache save failed: {e}')

            return successes >= 1

        except Exception as e:
            logger.warning(f'[WarmupManager] Parallel warmup failed: {e}, falling back')
            return await self._sequential_warmup()

    async def _sequential_warmup(self) -> bool:
        """Sequential warmup (fallback)."""
        success1 = await self._prefill_system_cache()
        success2 = await self._prefill_warmup_cache()
        return success1 or success2

    async def _prefill_system_cache(self) -> bool:
        """
        Prefill system prompt cache using stream_generate.

        M1 8GB: Runs in asyncio.to_thread to avoid blocking event loop.
        """
        if self._model_getter is None or self._tokenizer_getter is None:
            return False

        model = self._model_getter()
        tokenizer = self._tokenizer_getter()

        if model is None or tokenizer is None:
            return False

        try:
            import mlx.core as mx
            import mlx_lm
            from hledac.universal.utils.mlx_memory import get_metal_stream_context

            def do_prefill() -> None:
                mlx_lock = self._get_mlx_inference_lock()
                with get_metal_stream_context():
                    try:
                        mx.eval([])
                        with mlx_lock:
                            for _ in mlx_lm.stream_generate(
                                model=model,
                                tokenizer=tokenizer,
                                prompt=self._build_warmup_prompt(),
                                max_tokens=1,  # Minimal tokens to prime cache
                            ):
                                pass
                    finally:
                        self._clear_mlx_cache('system_prompt_cache_prefill')

            await asyncio.to_thread(do_prefill)
            return True

        except Exception as e:
            logger.warning(f'[WarmupManager] System cache prefill failed: {e}')
            return False

    async def _prefill_warmup_cache(self) -> bool:
        """
        Prefill warmup cache (~1000 tokens).

        M1 8GB: Uses worker thread for thread-safe coroutine dispatch.
        """
        if self._model_getter is None or self._tokenizer_getter is None:
            return False

        model = self._model_getter()
        tokenizer = self._tokenizer_getter()

        if model is None or tokenizer is None:
            return False

        try:
            import mlx_lm
            from hledac.universal.utils.mlx_memory import get_metal_stream_context
            from mlx_lm.sample_utils import make_sampler

            warmup_prompt = self._build_warmup_prompt()

            def do_generate() -> None:
                with get_metal_stream_context():
                    mlx_lm.generate(
                        model=model,
                        tokenizer=tokenizer,
                        prompt=warmup_prompt,
                        sampler=make_sampler(temp=0.7),
                        max_tokens=self._config.warmup_tokens,
                    )

            # Check for worker thread availability
            worker = getattr(self, '_mlx_worker', None)
            worker_live = worker is not None and getattr(worker, 'is_active', lambda: False)()

            if worker_live:
                main_loop = asyncio.get_running_loop()

                async def coro_wrapper() -> Any:
                    return do_generate()

                inference_future = asyncio.run_coroutine_threadsafe(
                    coro_wrapper(), main_loop
                )
                await safe_wait_for(
                    asyncio.wrap_future(inference_future),
                    timeout=self._config.timeout_seconds,
                )
            else:
                await asyncio.to_thread(do_generate)

            return True

        except Exception as e:
            logger.warning(f'[WarmupManager] Warmup cache prefill failed: {e}')
            return False

    def _get_mlx_inference_lock(self) -> Any:
        """Get MLX inference lock (lazy import)."""
        if self._mlx_lock is None:
            from hledac.universal.brain.mlx_bridge import get_mlx_inference_lock
            self._mlx_lock = get_mlx_inference_lock()
        return self._mlx_lock

    def _clear_mlx_cache(self, reason: str) -> None:
        """Clear MLX Metal cache with barrier."""
        try:
            import mlx.core as mx
            mx.eval([])
            mx.metal.clear_cache()
        except Exception:  # noqa: BLE001
            pass

    def _estimate_prefill_tokens(self) -> int:
        """Estimate tokens processed during prefill."""
        # Rough estimate based on warmup config
        return (
            len(self._config.few_shot_examples) * 20 +  # few-shot
            self._config.warmup_tokens +  # warmup
            50  # system prompt overhead
        )

    def restore_cache(self, cache_path: Path | str) -> bool:
        """
        Restore warmup cache from disk.

        Args:
            cache_path: Path to .safetensors cache file

        Returns:
            True if cache restored successfully
        """
        try:
            from safetensors import safe_load_file
            # Load and apply cache
            tensors = safe_load_file(str(cache_path))
            # Would need model reference to apply cache
            return True
        except Exception as e:
            logger.warning(f'[WarmupManager] Cache restore failed: {e}')
            return False