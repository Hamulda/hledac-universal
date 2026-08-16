"""
generate.py — Inference Engine
==============================




PEP 698: Extracted from DeepHermes3Engine inference orchestration.
Central facade for all inference operations.

M1 8GB: Unified inference interface with memory-aware scheduling.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from hledac.universal.brain._metal.metal_device import MetalDevice
    from hledac.universal.brain._cache.kv_cache_manager import KVCacheManager

logger = logging.getLogger(__name__)


@dataclass
class GenerateConfig:
    """Configuration for inference generation."""
    max_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 0.9
    repetition_penalty: float = 1.1
    repetition_decay: float = 0.99
    max_kv_size: int = 8192
    kv_bits: int = 4


@dataclass
class GenerateResult:
    """Result of generate operation."""
    text: str
    tokens_generated: int
    cached: bool
    duration_ms: float
    model_name: str | None = None


class GenerationFacade:
    """
    MLX generation facade — coordinates streaming, KV cache, and structured output.

    Extracted from DeepHermes3Engine to:
    1. Provide single interface for all generation types
    2. Integrate Metal device, KV cache, and streaming
    3. Enable independent testing of generation logic

    M1 8GB: Coordinates with MetalDevice for GPU memory management.

    NOTE: This is NOT brain.inference_engine.InferenceEngine (abductive reasoning).
    This facade is for MLX token generation only.
    """

    def __init__(
        self,
        model_getter: Any | None = None,
        tokenizer_getter: Any | None = None,
        metal_device: MetalDevice | None = None,
        kv_cache: KVCacheManager | None = None,
    ) -> None:
        self._model_getter = model_getter
        self._tokenizer_getter = tokenizer_getter
        self._metal = metal_device
        self._kv_cache = kv_cache
        self._config = GenerateConfig()
        self._semaphore = asyncio.Semaphore(1)  # One inference at a time

    def set_model_accessors(
        self,
        model_getter: Any,
        tokenizer_getter: Any,
    ) -> None:
        """Set model and tokenizer accessors."""
        self._model_getter = model_getter
        self._tokenizer_getter = tokenizer_getter

    def set_config(self, config: GenerateConfig) -> None:
        """Update generate configuration."""
        self._config = config

    async def generate(
        self,
        prompt: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> GenerateResult:
        """
        Generate text from prompt.

        UNIFIED-001: Acquires admission from GlobalPeakLoadCoordinator before
        MLX generation to prevent OOM when multiple subsystems compete for memory.
        MLX 3B model typically allocates ~2.5 GB during inference.

        Args:
            prompt: Input prompt
            max_tokens: Override max tokens
            temperature: Override temperature
            **kwargs: Additional generation kwargs

        Returns:
            GenerateResult with generated text and metadata
        """
        import time
        start = time.time()

        # UNIFIED-001: Acquire admission from peak load coordinator
        peak_guard = None
        try:
            from hledac.universal._core.peak_load_coordinator import (
                ResourceClass,
                TaskPriority,
                get_peak_coordinator,
            )
            coordinator = get_peak_coordinator()
            if coordinator is not None:
                # MLX 3B model: ~2500 MB peak allocation
                peak_guard = await coordinator.acquire(
                    ResourceClass.MLX_GENERATION,
                    estimated_mb=2500.0,
                    priority=TaskPriority.HIGH,
                    owner=f"generate:{prompt[:32]}",
                    timeout_s=10.0,
                )
        except (ImportError, TimeoutError) as e:
            logger.debug(f"[UNIFIED-001] MLX generation admission failed: {e}")
            # Fail-open: proceed without admission if coordinator unavailable
        except Exception as e:
            logger.debug(f"[UNIFIED-001] MLX generation admission error (fail-open): {e}")

        # UNIFIED-001: Wrap actual work in peak_guard context to ensure release
        if peak_guard is not None:
            async with peak_guard:
                return await self._generate_with_model(prompt, max_tokens, temperature, start, **kwargs)
        else:
            return await self._generate_with_model(prompt, max_tokens, temperature, start, **kwargs)

    async def _generate_with_model(
        self,
        prompt: str,
        max_tokens: int | None,
        temperature: float | None,
        start: float,
        **kwargs: Any,
    ) -> GenerateResult:
        """Internal generation logic, optionally wrapped in peak_guard context."""
        async with self._semaphore:
            try:
                model = self._model_getter() if self._model_getter else None
                tokenizer = self._tokenizer_getter() if self._tokenizer_getter else None

                if model is None or tokenizer is None:
                    return GenerateResult(
                        text="",
                        tokens_generated=0,
                        cached=False,
                        duration_ms=(time.time() - start) * 1000,
                    )

                # Check KV cache first
                cached = False
                if self._kv_cache:
                    cached_result = self._kv_cache.get_session_cache(prompt)
                    if cached_result:
                        cached = True

                # Build generation kwargs
                gen_kwargs = self._build_kwargs(
                    prompt,
                    max_tokens or self._config.max_tokens,
                    temperature or self._config.temperature,
                    **kwargs,
                )

                # Run inference
                text = await self._run_mlx_generate(model, tokenizer, gen_kwargs)
                tokens = len(tokenizer.encode(text))

                return GenerateResult(
                    text=text,
                    tokens_generated=tokens,
                    cached=cached,
                    duration_ms=(time.time() - start) * 1000,
                    model_name=getattr(model, 'path', None),
                )

            except Exception as e:
                logger.warning(f'[InferenceEngine] Generate failed: {e}')
                return GenerateResult(
                    text="",
                    tokens_generated=0,
                    cached=False,
                    duration_ms=(time.time() - start) * 1000,
                )

    async def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """
        Generate text with streaming.

        Args:
            prompt: Input prompt
            max_tokens: Maximum tokens
            temperature: Sampling temperature
            **kwargs: Additional kwargs

        Yields:
            Generated tokens
        """
        from hledac.universal.brain._inference.stream_handler import StreamHandler

        model = self._model_getter() if self._model_getter else None
        tokenizer = self._tokenizer_getter() if self._tokenizer_getter else None

        if model is None or tokenizer is None:
            return

        handler = StreamHandler()
        gen_kwargs = self._build_kwargs(prompt, max_tokens, temperature, **kwargs)

        async def generator() -> AsyncIterator[str]:
            import mlx_lm
            for token in mlx_lm.generate(model, tokenizer, **gen_kwargs):
                yield token

        async for delta in handler.stream_tokens(generator):
            yield delta

    async def generate_structured(
        self,
        prompt: str,
        response_model: type,
        max_tokens: int = 512,
        temperature: float = 0.3,  # Lower temp for structured
        **kwargs: Any,
    ) -> Any:
        """
        Generate structured output using Outlines.

        Args:
            prompt: Input prompt
            response_model: msgspec.Struct type for output
            max_tokens: Maximum tokens
            temperature: Sampling temperature
            **kwargs: Additional kwargs

        Returns:
            Structured output (instance of response_model)
        """
        import msgspec

        model = self._model_getter() if self._model_getter else None
        tokenizer = self._tokenizer_getter() if self._tokenizer_getter else None

        if model is None or tokenizer is None:
            return None

        try:
            # Try outlines first
            from outlines import models as outline_models
            from outlines import generate as outline_generate

            # Build outline model
            outline_model = outline_models.mlx(model_path=str(getattr(model, 'path', '')))
            outline_model = outline_models.make_outlines(model)

            # Generate with structure
            generator = outline_generate.json(outline_model, response_model)
            result = generator(prompt, max_tokens=max_tokens)

            # Decode result
            return msgspec.json.decode(result, type=response_model)

        except ImportError:
            logger.warning('[InferenceEngine] Outlines not available, falling back')
            # Fallback to regular generate + parse
            text = await self.generate(prompt, max_tokens, temperature)
            return msgspec.json.decode(text.text, type=response_model)
        except Exception as e:
            logger.warning(f'[InferenceEngine] Structured generation failed: {e}')
            return None

    def _build_kwargs(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build mlx_lm.generate kwargs."""
        from mlx_lm.sample_utils import make_sampler

        base_kwargs = {
            'prompt': prompt,
            'max_tokens': max_tokens,
            'sampler': make_sampler(
                temp=temperature,
                top_p=self._config.top_p,
                repetition_penalty=self._config.repetition_penalty,
                repetition_decay=self._config.repetition_decay,
            ),
        }

        # Add KV cache settings
        if self._metal and self._metal._mlx_available:
            base_kwargs['kv_bits'] = self._config.kv_bits
            base_kwargs['max_kv_size'] = self._config.max_kv_size

        base_kwargs.update(kwargs)
        return base_kwargs

    async def _run_mlx_generate(
        self,
        model: Any,
        tokenizer: Any,
        kwargs: dict[str, Any],
    ) -> str:
        """Run MLX generation in thread pool."""
        import mlx_lm

        def generate() -> str:
            return mlx_lm.generate(
                model=model,
                tokenizer=tokenizer,
                **kwargs,
            )

        return await asyncio.to_thread(generate)

    def get_inference_stats(self) -> dict[str, Any]:
        """Get inference statistics."""
        stats = {
            'metal_device': None,
            'kv_cache': None,
            'config': {
                'max_tokens': self._config.max_tokens,
                'temperature': self._config.temperature,
                'kv_bits': self._config.kv_bits,
            },
        }

        if self._metal:
            metal_stats = self._metal.get_stats()
            stats['metal_device'] = {
                'active_gb': metal_stats.active_gb,
                'peak_gb': metal_stats.peak_gb,
                'tier': metal_stats.metal_tier,
            }

        if self._kv_cache:
            cache_stats = self._kv_cache.get_stats()
            stats['kv_cache'] = {
                'pool_size': cache_stats.pool_size,
                'session_cache_size': cache_stats.session_cache_size,
            }

        return stats
