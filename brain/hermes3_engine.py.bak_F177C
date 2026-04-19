"""
✅ CANONICAL - Hermes3Engine pro Decision Making
=================================================

Toto je CANONICAL implementace pro decision making a orchestraci.

Používá Hermes-3-Llama-3.2-3B-4bit jako hlavní model.
Používá Hermes-3-Llama-3.2-3B-4bit pro decision making.

Helper moduly (pouze pomocné funkce):
- brain/moe_router.py - Mixture of Experts routing
- brain/decision_engine.py - Základní decision logika

Pro decision making vždy používejte tento modul:
    from hledac.universal.brain.hermes3_engine import Hermes3Engine

Features:
- ChatML formátování
- AI-driven query analysis
- Research synthesis

NOTE (Sprint 8VH): brain/inference_engine.py je FUNKČNĚ ODLIŠNÝ:
  - inference_engine: abductive reasoning, evidence chaining, entity resolution, inference rules
  - hermes3_engine: LLM-based decision making, ChatML, structured generation
  Obě jsou kanonické pro své domain — žádná deduplikace není nutná.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import hashlib
import inspect
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, Field

T = TypeVar('T', bound=BaseModel)

# SECURITY: Import fallback sanitizer for LLM input sanitization (failsafe)
from ..security.pii_gate import fallback_sanitize

# Sprint 7H/7I: Emergency unload seam consumer
try:
    from .model_lifecycle import is_emergency_unload_requested
except ImportError:
    is_emergency_unload_requested = None  # type: ignore

# Sprint 33: outlines for grammar-constrained decoding
try:
    import outlines
    from outlines import generate as outlines_generate
    OUTLINES_AVAILABLE = True
except ImportError:
    OUTLINES_AVAILABLE = False

# Sprint 37: KV-cache for prompt prefix (lazy import to avoid loading mlx_lm at cold-start)
KV_CACHE_AVAILABLE = False  # Set to True only when cache is actually initialized

logger = logging.getLogger(__name__)

# Sprint 81: MLX memory management
try:
    from ..utils.mlx_utils import mlx_managed, get_mlx_memory_stats, reset_metal_peak
except ImportError:
    mlx_managed = None  # Fallback - decorator not available

# Sprint 7B: MLX availability flag (imported from mlx_cache for consistency)
try:
    from ..utils.mlx_cache import MLX_AVAILABLE as _MLX_AVAILABLE_GLOBAL
except ImportError:
    try:
        import mlx.core as mx
        _MLX_AVAILABLE_GLOBAL = True
    except ImportError:
        _MLX_AVAILABLE_GLOBAL = False

# Hard limit for LLM prompt (no user toggles)
MAX_LLM_PROMPT_CHARS = 8192


@dataclass
class HermesConfig:
    """Konfigurace pro Hermes-3"""
    model_path: str = "mlx-community/Hermes-3-Llama-3.2-3B-4bit"
    temperature: float = 0.3
    max_tokens: int = 2048
    context_window: int = 8192


# Sprint 33: Private Pydantic schemas for structured output
class _DecisionOutput(BaseModel):
    action: str = Field(description="Action to take")
    params: dict = Field(default_factory=dict, description="Action parameters")
    reasoning: str = Field(description="Why this action")
    complete: bool = Field(False, description="Whether research is complete")


class _SynthesisOutput(BaseModel):
    report: str = Field(description="Final synthesized report")
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence")


class Hermes3Engine:
    """
    Engine pro Hermes-3 s ChatML formátováním.

    ChatML Format:
        <|im_start|>system
        {system_message}<|im_end|>
        <|im_start|>user
        {user_message}<|im_end|>
        <|im_start|>assistant
    """

    def __init__(
        self,
        model_path: str = None,
        sanitize_for_llm: Optional[Callable[[str], str]] = None
    ):
        """
        Initialize Hermes3Engine.

        Args:
            model_path: Path to model (default from config)
            sanitize_for_llm: Optional callback for LLM input sanitization.
                               If provided, used instead of fallback_sanitize.
                               Signature: Callable[[str], str]
        """
        self.config = HermesConfig(
            model_path=model_path or HermesConfig.model_path,
        )

        # Sanitizer injection - centralizes security in orchestrator
        self._sanitize_for_llm = sanitize_for_llm

        self._model = None
        self._tokenizer = None

        # Sprint 36: Conditional MLX cache - disabled by default
        self._kv_cache_enabled = False
        self._prompt_cache = None  # Prompt cache for generation

        # Sprint 33: outlines model for grammar-constrained decoding
        self._outlines_model = None

        # Sprint 35 FIX 1: outlines generator cache to avoid re-creating generator for same schema
        self._outlines_generators = {}

        # Sprint 75: Draft model with memory guard
        self._draft_model_obj = None
        self._draft_model_name = None
        self._speculative_enabled = False
        self._num_draft_tokens = 4
        self._supports_stream_generate = False
        self._supports_draft = False
        self._supports_kv_quant = False
        self._kv_cache_stats = {'cache_uses': 0, 'cache_prefills': 1, 'quantized_count': 0}

        # Sprint 75: Persistent system-prompt cache
        self._system_prompt = "You are a helpful research assistant."
        self._system_prompt_cache = None   # built KV cache object
        self._system_prompt_hash = None    # MD5 of last system prompt

        # Sprint 41: Shared prefix cache for tokenization
        self._prefix_cache: Dict[str, Any] = {}

        # Single-thread executor for MLX inference (M1 8GB safe)
        self._inference_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._inference_semaphore = asyncio.Semaphore(1)

        # Sprint 71/7E: Continuous batching — schema-aware PriorityQueue
        self._batch_queue: asyncio.PriorityQueue = None
        self._batch_worker_task: Optional[asyncio.Task] = None
        self._batch_max_size = 8  # Max batch size
        self._batch_default_flush_interval = 2.0  # seconds (Sprint 7I: corrected from 0.5)
        self._batch_flush_interval = self._batch_default_flush_interval
        self._batch_medium_pressure_depth = 64   # trigger medium flush at this depth (Sprint 7I)
        self._batch_high_pressure_depth = 192  # trigger fast flush at this depth

        # Sprint 7E: EMA telemetry (Sprint 7G: extended with counters)
        self._telemetry_ema = {
            'enqueue_to_dispatch_ms': 0.0,
            'dispatch_to_result_ms': 0.0,
            'batch_size': 0,
            'queue_depth': 0,
        }
        # Sprint 7G: Counters for batch routing
        self._telemetry_counters = {
            'batch_submitted': 0,
            'batch_executed': 0,
            'batch_fallback_single': 0,
            'schema_mismatch_flushes': 0,
            'length_bin_mismatch_flushes': 0,
            'batch_shattered': 0,
            'prompt_mismatch_flushes': 0,
            # Sprint 7I: Emergency counters
            'emergency_guard_triggered': 0,
            'emergency_batch_rejected': 0,
            'emergency_single_rejected': 0,
            'emergency_pending_failed': 0,
            'adaptive_flush_default_entries': 0,
            'adaptive_flush_medium_entries': 0,
            'adaptive_flush_fast_entries': 0,
        }
        # Sprint 7I: Pending batch futures registry (for emergency failure)
        self._pending_futures: set = set()
        self._ema_alpha = 0.3

        # Sprint 7E: Age bump for anti-starvation
        self._flush_cycle_count = 0
        self._age_bump_interval = 3  # bump every N flush cycles
        self._last_age_bump = 0

        # Sprint 7E: Warmup cache SEPARATE from production cache
        self._warmup_cache: Any = None  # isolated warmup KV cache
        self._batch_worker_shutting_down = False  # Sprint 7K: poison pill flag

        # GPU memory tracking
        self._last_gpu_memory: int = 0

    async def _ensure_batch_worker(self) -> None:
        """Ensure batch worker is started (lazy start)."""
        if self._batch_worker_task is None:
            self._batch_queue = asyncio.PriorityQueue(maxsize=256)
            import itertools
            self._batch_tie_breaker = itertools.count()
            self._pending_futures: set = set()
            self._batch_worker_shutting_down = False  # Sprint 7K: reset poison pill
            self._batch_worker_task = asyncio.create_task(self._batch_worker())
            logger.debug("Batch worker started")

    async def _shutdown_batch_worker(self, timeout: float = 3.0) -> None:
        """
        Sprint 7K: Bounded batch worker shutdown — max 3.0s, fail-pending-futures.

        Post-conditions after this method:
        - All pending futures have result or exception
        - _pending_futures is empty
        - _batch_worker_task is None
        - _batch_queue is None (Sprint 7K: explicitly cleared)
        """
        if self._batch_worker_task is None:
            self._batch_queue = None
            return
        # Fail all pending futures before cancelling
        for fut in list(self._pending_futures):
            if not fut.done():
                fut.set_exception(RuntimeError("emergency_unload_requested"))
                self._telemetry_counters['emergency_pending_failed'] += 1
        self._pending_futures.clear()
        # Sprint 7K: Signal worker to exit cleanly before cancelling
        self._batch_worker_shutting_down = True
        # Cancel worker with bounded timeout
        self._batch_worker_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(self._batch_worker_task), timeout=timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        self._batch_worker_task = None
        # Sprint 7K: Clear queue AFTER worker is confirmed stopped
        self._batch_queue = None
        logger.debug("Batch worker shutdown complete (Sprint 7K)")

    async def _submit_structured_batch(
        self,
        prompt: str,
        response_model: type,
        priority: float = 1.0,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        system_msg: str = None
    ) -> Any:
        """
        Sprint 7E: Submit a structured output request to the batch queue.

        Returns a Future that resolves when the result is available.

        Args:
            prompt: Input prompt
            response_model: Pydantic model to generate
            priority: Lower = higher priority (0 = highest)
            temperature: Temperature setting
            max_tokens: Max tokens to generate
            system_msg: Optional system message

        Returns:
            Future that resolves to the structured result
        """
        # Sprint 7I: Emergency guard — reject new batch enqueue
        if is_emergency_unload_requested is not None and is_emergency_unload_requested():
            self._telemetry_counters['emergency_batch_rejected'] += 1
            raise RuntimeError("emergency_unload_requested")

        import itertools

        await self._ensure_batch_worker()

        schema_key = response_model.__name__
        payload = {
            'type': 'structured',
            'prompt': prompt,
            'response_model': response_model,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'system_msg': system_msg,
            'future': None,
        }
        future = asyncio.Future()
        payload['future'] = future
        # Sprint 7I: Track pending future for emergency failure
        self._pending_futures.add(future)
        future.add_done_callback(lambda f: self._pending_futures.discard(f) if f in self._pending_futures else None)

        # Tie-breaker counter — module-level to avoid per-call overhead
        if not hasattr(self.__class__, '_batch_tie_breaker'):
            self.__class__._batch_tie_breaker = itertools.count()
        tie = next(self._batch_tie_breaker)

        t_enqueue = time.monotonic()
        await self._batch_queue.put((priority, tie, schema_key, payload))

        # Update enqueue-to-dispatch EMA on dispatch (captured in worker)
        self._telemetry_ema['enqueue_to_dispatch_ms'] = (
            self._ema_alpha * 0.0 +
            (1 - self._ema_alpha) * self._telemetry_ema.get('enqueue_to_dispatch_ms', 0.0)
        )

        return future

    async def _batch_worker(self) -> None:
        """Background worker that processes batches with schema-awareness + prompt/length segregation."""
        import itertools
        tie_breaker = itertools.count()

        while True:
            # Sprint 7I: Emergency check at top of each cycle
            if is_emergency_unload_requested is not None and is_emergency_unload_requested():
                for fut in list(self._pending_futures):
                    if not fut.done():
                        fut.set_exception(RuntimeError("emergency_unload_requested"))
                        self._telemetry_counters['emergency_pending_failed'] += 1
                self._pending_futures.clear()
                break  # Worker exits

            # Sprint 7K: Poison pill guard — exit if shutdown flag is set
            if getattr(self, '_batch_worker_shutting_down', False):
                for fut in list(self._pending_futures):
                    if not fut.done():
                        fut.set_exception(RuntimeError("engine_unloaded"))
                self._pending_futures.clear()
                break  # Worker exits cleanly

            try:
                items = []
                current_schema_key = None
                current_prompt_hash = None
                current_length_bin = None

                # Sprint 7I: Adaptive flush interval with 3-tier policy
                flush_interval = self._current_flush_interval()
                # Sprint 7I: Telemetry for flush tier selection
                if flush_interval >= 1.9:
                    self._telemetry_counters['adaptive_flush_default_entries'] += 1
                elif flush_interval >= 0.9:
                    self._telemetry_counters['adaptive_flush_medium_entries'] += 1
                else:
                    self._telemetry_counters['adaptive_flush_fast_entries'] += 1

                # Sprint 7E: wait_for pattern with flush_interval timeout
                try:
                    first_item = await asyncio.wait_for(
                        self._batch_queue.get(),
                        timeout=flush_interval
                    )
                    current_schema_key = first_item[2]  # schema_key from (priority, tie, schema, item)
                    items.append(first_item)

                    # Extract prompt_hash and length_bin from first item payload
                    first_payload = first_item[3]
                    first_prompt = first_payload.get('prompt', '')
                    first_system_msg = first_payload.get('system_msg')
                    current_prompt_hash = self._compute_system_prompt_hash(first_system_msg)
                    current_length_bin = self._compute_length_bin(first_prompt)

                    # Try to get more items up to max batch, respecting all boundaries
                    while len(items) < self._batch_max_size:
                        try:
                            item = await asyncio.wait_for(
                                self._batch_queue.get_nowait(),
                                timeout=0.01
                            )
                            item_schema = item[2]
                            item_payload = item[3]
                            item_prompt = item_payload.get('prompt', '')
                            item_system_msg = item_payload.get('system_msg')
                            item_prompt_hash = self._compute_system_prompt_hash(item_system_msg)
                            item_length_bin = self._compute_length_bin(item_prompt)

                            # Schema boundary check — don't mix schemas
                            if item_schema != current_schema_key:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['schema_mismatch_flushes'] += 1
                                break
                            # Prompt hash boundary — don't mix system prompts
                            if item_prompt_hash != current_prompt_hash:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['prompt_mismatch_flushes'] += 1
                                break
                            # Length bin boundary — don't mix short/long (padding waste)
                            if item_length_bin != current_length_bin:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['length_bin_mismatch_flushes'] += 1
                                break
                            items.append(item)
                        except asyncio.TimeoutError:
                            break

                except asyncio.TimeoutError:
                    # No items available — skip this cycle
                    continue

                # Anti-starvation: age bump every _age_bump_interval cycles
                self._flush_cycle_count += 1
                if self._flush_cycle_count - self._last_age_bump >= self._age_bump_interval:
                    self._last_age_bump = self._flush_cycle_count
                    await self._age_bump_queue()

                # Update queue depth EMA
                self._telemetry_ema['queue_depth'] = self._batch_queue.qsize()

                # Process batch with timing
                t0 = time.monotonic()
                await self._process_batch(items)
                dispatch_ms = (time.monotonic() - t0) * 1000

                # Update EMAs
                self._telemetry_ema['batch_size'] = len(items)
                self._telemetry_ema['dispatch_to_result_ms'] = (
                    self._ema_alpha * dispatch_ms +
                    (1 - self._ema_alpha) * self._telemetry_ema['dispatch_to_result_ms']
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Batch worker error: {e}")

    def _current_flush_interval(self) -> float:
        """Sprint 7I: Adaptive flush interval — 3-tier policy based on queue depth.

        - depth > 192  → 0.5s (high pressure)
        - depth > 64   → 1.0s (medium pressure)
        - otherwise     → 2.0s (default)
        """
        if self._batch_queue is None:
            return self._batch_default_flush_interval
        depth = self._batch_queue.qsize()
        if depth > self._batch_high_pressure_depth:
            return 0.5
        if depth > self._batch_medium_pressure_depth:
            return 1.0
        return self._batch_default_flush_interval

    def _is_batch_safe(
        self,
        response_model: Any,
        priority: float,
        stream: bool,
        timeout_s: Optional[float],
    ) -> bool:
        """
        Sprint 7G: Batch-safe eligibility check.

        Routing criteria:
        - schema type must be detectable (msgspec or pydantic)
        - not streaming
        - not urgent priority (priority == 0)
        - timeout must allow for batching (>= 2x flush interval)
        """
        # Never batch streaming
        if stream:
            return False
        # Urgent = single path
        if priority == 0:
            return False
        # No schema = can't segregate
        if response_model is None:
            return False
        # Short timeout = single path
        if timeout_s is not None and timeout_s <= self._current_flush_interval() * 2:
            return False
        # Schema must be msgspec or pydantic
        schema_cls = response_model if isinstance(response_model, type) else type(response_model)
        if not hasattr(schema_cls, '__struct_fields__') and \
           not hasattr(schema_cls, 'model_validate_json'):
            return False
        return True

    def _compute_length_bin(self, prompt: str) -> str:
        """Sprint 7G: Length binning — short/medium/long to prevent padding waste."""
        tokens_est = len(prompt) // 4  # rough estimate
        if tokens_est < 256:
            return 'short'
        elif tokens_est < 1024:
            return 'medium'
        return 'long'

    def _compute_system_prompt_hash(self, system_msg: Optional[str]) -> str:
        """Sprint 7G: Hash of system prompt for segregation."""
        if not system_msg:
            return 'default'
        return hashlib.md5(system_msg.encode(), usedforsecurity=False).hexdigest()[:8]

    async def _age_bump_queue(self) -> None:
        """Age-bump: improve priority of waiting items by 1 without O(n) rebuild."""
        if self._batch_queue.empty():
            return
        # Extract all items, re-enqueue with bumped priority
        items = []
        while not self._batch_queue.empty():
            try:
                items.append(self._batch_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for item in items:
            priority, tie, schema, payload = item
            new_priority = max(0, priority - 1)
            await self._batch_queue.put((new_priority, tie, schema, payload))

    async def _process_batch(self, items: list) -> None:
        """Process a batch of structured-output items."""
        if not items:
            return

        # Group by schema_key for batch processing
        # items are (priority, tie, schema_key, payload)
        by_schema: Dict[str, list] = {}
        for priority, tie, schema_key, payload in items:
            if schema_key not in by_schema:
                by_schema[schema_key] = []
            by_schema[schema_key].append((payload, priority))

        # Process each schema group sequentially (GPU constraint)
        # group entries are (payload_dict, priority)
        for schema_key, group in by_schema.items():
            try:
                if group[0][0].get('type') == 'structured':
                    await self._process_structured_batch(group)
                elif group[0][0].get('type') == 'generate':
                    for payload, _ in group:
                        future = payload.get('future')
                        if future and not future.done():
                            future.set_result({'processed': True})
            except Exception as e:
                logger.debug(f"Batch process error for schema {schema_key}: {e}")

    async def _process_structured_batch(self, items: list) -> None:
        """
        Sprint 7G: Process a batch of structured output requests for same schema.
        Batch shattering: if entire batch parse fails, retry each item individually.
        """
        # Sprint 7G: Try batch execution first, shatter on total failure
        try:
            # Process items in parallel-ish fashion within the batch
            results = await self._execute_structured_batch(items)
            # If we got here, batch succeeded — resolve futures
            for payload, result in zip([p for p, _ in items], results):
                future = payload.get('future')
                if future and not future.done():
                    future.set_result(result)
            self._telemetry_counters['batch_executed'] += 1
        except Exception as batch_error:
            # Sprint 7G: Batch shattering — entire batch failed, retry each item individually
            logger.debug(f"[STRUCTURED] Batch shattered: {batch_error}")
            self._telemetry_counters['batch_shattered'] += 1
            for payload, _ in items:
                try:
                    result = await self._run_structured_single(payload)
                    future = payload.get('future')
                    if future and not future.done():
                        future.set_result(result)
                except Exception as item_error:
                    logger.debug(f"Structured batch item error: {item_error}")
                    future = payload.get('future')
                    if future and not future.done():
                        future.set_exception(item_error)

    async def _execute_structured_batch(self, items: list) -> list:
        """
        Sprint 7G: Execute batch of structured items.
        Returns list of results if batch succeeds, raises if batch fails.
        Sequential processing per schema group (GPU constraint).
        """
        results = []
        for payload, _ in items:
            result = await self._run_structured_single(payload)
            results.append(result)
        return results

    async def _run_structured_single(self, payload: dict):
        """Run a single structured output request (canonical path)."""
        prompt = payload.get('prompt')
        response_model = payload.get('response_model')
        temperature = payload.get('temperature', 0.1)
        max_tokens = payload.get('max_tokens', 1024)
        system_msg = payload.get('system_msg')

        if system_msg:
            prompt = self._format_chatml(system_msg, prompt)
        else:
            prompt = self._format_chatml("You are a helpful assistant.", prompt)

        # generate_structured_safe is sync — run in executor to avoid blocking
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self._inference_executor,
            lambda: self.generate_structured_safe(
                prompt=prompt,
                response_model=response_model,
                temperature=temperature,
                max_tokens=max_tokens,
                system_msg=None
            )
        )
        return result

    async def flush_all(self, timeout: float = 5.0) -> int:
        """
        Drain all pending items from the batch queue.

        Args:
            timeout: Maximum seconds to wait for drain

        Returns:
            Number of items drained
        """
        if self._batch_queue is None or self._batch_queue.empty():
            return 0

        drained = 0
        deadline = time.monotonic() + timeout
        items = []

        while not self._batch_queue.empty() and time.monotonic() < deadline:
            try:
                item = self._batch_queue.get_nowait()
                items.append(item)
                drained += 1
            except asyncio.QueueEmpty:
                break

        if items:
            await self._process_batch(items)

        return drained

    def _get_gpu_memory(self) -> int:
        """Get current GPU memory usage."""
        if not _MLX_AVAILABLE_GLOBAL:
            return 0

        try:
            import mlx.core as mx
            # Try to get active memory
            # Sprint 8AE: prefer top-level mx API (MLX 0.31+)
            if hasattr(mx, 'get_active_memory'):
                return mx.get_active_memory()
            elif hasattr(mx.metal, 'get_active_memory'):
                return mx.metal.get_active_memory()
        except Exception:
            pass

        return 0

    async def initialize(self) -> None:
        """Inicializovat model"""
        global KV_CACHE_AVAILABLE
        try:
            from mlx_lm import load

            logger.info(f"Loading Hermes-3 from {self.config.model_path}...")
            self._model, self._tokenizer = load(self.config.model_path)
            logger.info("✓ Hermes-3 loaded successfully")

            # Sprint 36: Initialize prompt cache only if KV_CACHE_AVAILABLE
            if KV_CACHE_AVAILABLE:
                try:
                    from mlx_lm.utils import make_prompt_cache
                    self._prompt_cache = make_prompt_cache(self._model)
                    self._kv_cache_enabled = True
                    KV_CACHE_AVAILABLE = True
                    logger.info("✓ Prompt cache initialized (MLX)")
                except Exception as e:
                    logger.warning(f"Prompt cache init failed: {e}, continuing without it")
                    self._prompt_cache = None
                    self._kv_cache_enabled = False
            else:
                logger.info("[HERMES] KV_CACHE not available – KV cache disabled")
                self._prompt_cache = None
                self._kv_cache_enabled = False

            # Sprint 33: Initialize outlines model (reuse loaded model/tokenizer)
            if OUTLINES_AVAILABLE:
                try:
                    self._outlines_model = outlines.from_mlxlm(self._model, self._tokenizer)
                    logger.info("✓ Outlines model initialized")
                except Exception as e:
                    logger.warning(f"Outlines init failed: {e}, continuing without it")
                    self._outlines_model = None

            # Sprint 75: Initialize draft model with memory guard
            await self._init_draft_model()

            # Sprint 75: Initialize persistent system-prompt cache
            await self._init_system_prompt_cache()

            # Sprint 7D: Warmup prefix cache after model load
            # This builds clean prefill cache before first inference
            await self.warmup_prefix_cache(
                system_prompt=self._system_prompt,
                few_shot_examples=[
                    {"user": "What is 2+2?", "assistant": "4"},
                    {"user": "Capital of France?", "assistant": "Paris"},
                ]
            )

        except Exception as e:
            logger.error(f"Failed to load Hermes-3: {e}")
            raise

    async def _init_draft_model(self) -> None:
        """Initialize draft model with memory guard (Sprint 75)."""
        try:
            import psutil
            import inspect
            from mlx_lm import load, generate as _mlx_generate

            available_gb = psutil.virtual_memory().available / (1024**3)

            # Detect stream_generate and draft model support
            import mlx_lm
            self._supports_stream_generate = hasattr(mlx_lm, 'stream_generate')
            self._supports_draft = 'draft_model' in inspect.signature(_mlx_generate).parameters

            # Select draft model based on available memory
            if available_gb > 5.5:
                self._draft_model_name = "mlx-community/Hermes-3-Llama-3.2-1B-4bit"
                self._speculative_enabled = True
                self._num_draft_tokens = 6
            elif available_gb > 4.0:
                self._draft_model_name = "mlx-community/Phi-1.5-100M-4bit"
                self._speculative_enabled = True
                self._num_draft_tokens = 4
            else:
                self._speculative_enabled = False
                self._draft_model_name = None
                logger.info(f"[SPEC] Insufficient RAM ({available_gb:.1f}GB), speculative decoding disabled")
                return

            if self._draft_model_name:
                self._draft_model_obj, self._draft_tokenizer = await asyncio.to_thread(
                    load, self._draft_model_name, tokenizer_config={"trust_remote_code": True}
                )
                logger.info(f"[SPEC] Draft model loaded: {self._draft_model_name}")

        except Exception as e:
            logger.warning(f"[SPEC] Draft model init failed: {e}")
            self._speculative_enabled = False

    async def _init_system_prompt_cache(self) -> None:
        """Initialize persistent system-prompt cache (Sprint 75)."""
        if not KV_CACHE_AVAILABLE or self._model is None:
            return

        try:
            from mlx_lm.models.cache import make_prompt_cache
            import mlx.core as mx

            self._system_prompt_cache = make_prompt_cache(self._model, max_kv_size=512)

            # Prefill the cache
            if self._supports_stream_generate:
                import mlx_lm

                def _prefill():
                    try:
                        for _ in mlx_lm.stream_generate(
                            model=self._model,
                            tokenizer=self._tokenizer,
                            prompt=self._system_prompt,
                            prompt_cache=self._system_prompt_cache,
                            max_tokens=1
                        ):
                            pass
                    finally:
                        # Sprint 8UD B.2: Clear MLX Metal cache after inference
                        try:
                            import mlx.core as _mx
                            if _mx.metal.is_available():
                                _mx.metal.clear_cache()
                        except Exception:
                            pass  # Non-fatal

                await asyncio.to_thread(_prefill)
                self._kv_cache_stats['cache_prefills'] = 1

            # Detect KV quantization support
            for layer in self._system_prompt_cache:
                if hasattr(layer, 'quantize'):
                    self._supports_kv_quant = True
                    break

            # Try to load cache from disk
            await self._load_cache()

            logger.info("[CACHE] System prompt cache initialized")

        except Exception as e:
            logger.warning(f"[CACHE] System prompt cache init failed: {e}")

    async def _save_cache(self) -> None:
        """Save system prompt cache to disk (best-effort)."""
        try:
            from pathlib import Path

            cache_path = Path.home() / '.hledac' / 'cache' / 'system_prompt_cache.npz'
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            if self._system_prompt_cache:
                import mlx.core as mx

                data = {}
                for i, layer in enumerate(self._system_prompt_cache):
                    if hasattr(layer, 'state'):
                        data[f'layer_{i}'] = mx.array(layer.state)

                if data:
                    mx.savez(str(cache_path), **data)
                    logger.debug(f"[CACHE] Saved to {cache_path}")

        except Exception as e:
            logger.debug(f"[CACHE] Save failed (non-critical): {e}")

    async def _load_cache(self) -> bool:
        """Try to load cache from disk."""
        try:
            from pathlib import Path
            import mlx.core as mx

            cache_path = Path.home() / '.hledac' / 'cache' / 'system_prompt_cache.npz'
            if not cache_path.exists():
                return False

            data = mx.load(str(cache_path))
            logger.info(f"[CACHE] Found existing cache at {cache_path}, size {len(data)} layers")
            return True

        except Exception as e:
            logger.debug(f"[CACHE] Load failed: {e}")
            return False

    def _format_chatml(
        self,
        system_msg: str,
        user_msg: str,
        history: List[Dict[str, str]] = None
    ) -> str:
        """
        Formátovat zprávu do ChatML formátu.
        
        Args:
            system_msg: Systémová zpráva
            user_msg: Uživatelská zpráva
            history: Historie konverzace
            
        Returns:
            Formátovaný prompt
        """
        parts = []
        
        # Systémová zpráva
        parts.append(f"<|im_start|>system\n{system_msg}<|im_end|>")
        
        # Historie
        if history:
            for entry in history:
                role = entry.get("role", "user")
                content = entry.get("content", "")
                parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
        
        # Uživatelská zpráva
        parts.append(f"<|im_start|>user\n{user_msg}<|im_end|>")
        
        # Assistant začátek
        parts.append("<|im_start|>assistant\n")
        
        return "\n".join(parts)

    def _get_prefix_cache(self, system_prompt: str):
        """
        Build or return cached KV state for system prompt.
        Returns SAME object (not deepcopy) - protected by semaphore in generate().
        Thread-safe: only one inference runs at a time due to _inference_semaphore.
        """
        if not KV_CACHE_AVAILABLE or self._model is None or not system_prompt:
            return None
        try:
            import mlx.core as mx
            from mlx_lm.models.cache import make_prompt_cache
            prompt_hash = hashlib.md5(system_prompt.encode()).hexdigest()
            if self._system_prompt_cache is None or self._system_prompt_hash != prompt_hash:
                tokens = self._tokenizer.encode(system_prompt)
                cache = make_prompt_cache(self._model)
                _ = self._model(mx.array([tokens]), cache=cache)
                mx.eval(cache)  # force MLX lazy evaluation
                self._system_prompt_cache = cache
                self._system_prompt_hash = prompt_hash
                logger.debug("[KV-CACHE] System prompt cache built/rebuilt")
            # Vracíme STEJNÝ objekt - semaphore v generate() chrání před corruption
            return self._system_prompt_cache
        except Exception as e:
            logger.warning(f"[KV-CACHE] Prefix cache failed: {e}")
            return None

    def _run_inference(self, formatted_prompt: str, temp: float, max_tok: int, prefix_cache=None) -> str:
        """
        Run MLX inference synchronously in thread pool (Sprint 75).

        Args:
            formatted_prompt: Formatted prompt for generation
            temp: Temperature setting
            max_tok: Maximum tokens to generate
            prefix_cache: Optional KV cache for prompt prefix

        Returns:
            Generated text
        """
        from mlx_lm import generate as mlx_generate
        from mlx_lm.models.cache import make_prompt_cache

        # Always create new cache (thread-safe)
        kv_cache = make_prompt_cache(self._model, max_kv_size=max_tok)

        # Sprint 75: KV quantization (capability-based)
        if self._supports_kv_quant:
            for layer in kv_cache:
                if hasattr(layer, 'quantize'):
                    try:
                        layer.quantize(group_size=64, bits=4)
                        self._kv_cache_stats['quantized_count'] += 1
                    except Exception:
                        pass

        generate_kwargs = {
            "model": self._model,
            "tokenizer": self._tokenizer,
            "prompt": formatted_prompt,
            "temp": temp,
            "max_tokens": max_tok,
            "max_kv_size": 8192,
            "kv_bits": 4,
            "prompt_cache": kv_cache,
            "verbose": False,
        }

        # Sprint 75: Speculative decoding with memory guard
        if self._speculative_enabled and self._draft_model_obj is not None and self._supports_draft:
            generate_kwargs["draft_model"] = self._draft_model_obj
            generate_kwargs["num_draft_tokens"] = self._num_draft_tokens

        # Sprint 37: Add prefix KV cache if provided
        if prefix_cache is not None:
            generate_kwargs["cache"] = prefix_cache

        self._kv_cache_stats['cache_uses'] += 1
        response = mlx_generate(**generate_kwargs)
        return response.strip()

    async def generate(
        self,
        prompt: str,
        temperature: float = None,
        max_tokens: int = None,
        system_msg: str = None
    ) -> str:
        """
        Generovat text pomocí Hermes-3.

        Args:
            prompt: Vstupní prompt
            temperature: Teplota (0-1)
            max_tokens: Maximální počet tokenů
            system_msg: Systémová zpráva

        Returns:
            Vygenerovaný text
        """
        if self._model is None:
            raise RuntimeError("Model not initialized")

        try:
            temp = temperature or self.config.temperature
            max_tok = max_tokens or self.config.max_tokens

            # SECURITY: Sanitize prompt before inference (sanitize first, then bound)
            # Priority: injected callback > fallback (failsafe)
            if self._sanitize_for_llm is not None:
                # Use injected sanitizer from orchestrator (preferred path)
                sanitized_prompt = self._sanitize_for_llm(prompt)[:MAX_LLM_PROMPT_CHARS]
            else:
                # Failsafe: use fallback when no callback injected
                sanitized_prompt = fallback_sanitize(prompt, max_length=MAX_LLM_PROMPT_CHARS)[:MAX_LLM_PROMPT_CHARS]

            system = system_msg or "You are a helpful research assistant."

            # Sprint 41: Shared prefix cache for tokenization
            cache_key = hashlib.sha256((system or "").encode()).hexdigest()
            if cache_key in self._prefix_cache:
                logger.debug(f"[CACHE] Prefix cache hit for key {cache_key[:8]}")
            else:
                # Tokenize and cache
                if self._tokenizer:
                    prefix_tokens = self._tokenizer.encode(system)
                    self._prefix_cache[cache_key] = prefix_tokens

            formatted_prompt = self._format_chatml(system, sanitized_prompt)

            # HARD LIMIT post-wrap (final prompt to mlx_lm.generate must be <= 8192)
            formatted_prompt = formatted_prompt[:MAX_LLM_PROMPT_CHARS]

            logger.debug(f"Generating with temp={temp}, max_tokens={max_tok}")

            # Sprint 36: Get prefix KV cache for system prompt only if enabled
            prefix_cache = None
            if self._kv_cache_enabled and system_msg:
                try:
                    prefix_cache = self._get_prefix_cache(system)
                except Exception:
                    pass

            # Use semaphore for serialization + executor for thread offload
            async with self._inference_semaphore:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    self._inference_executor,
                    lambda: self._run_inference(formatted_prompt, temp, max_tok, prefix_cache)
                )

            return response

        except Exception as e:
            logger.error(f"Generation failed: {e}")
            return f"Error: {str(e)}"

    async def decide_next_action(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Rozhodnout o dalším kroku ve výzkumu.
        
        Args:
            context: Kontext aktuálního stavu výzkumu
            
        Returns:
            Rozhodnutí o další akci
        """
        query = context.get("query", "")
        step = context.get("step", 0)
        max_steps = context.get("max_steps", 20)
        history = context.get("history", [])
        
        system_msg = """You are a research orchestrator. Decide the next action to progress the research.

Available actions:
- search: Search for information
- google: Google search
- download: Download a file
- deep_read: Read content from URL (secure)
- research_paper: Search academic papers
- osint_discovery: Discover hidden sources
- archive_fallback: Check Wayback Machine
- fact_check: Verify a claim
- synthesize: Complete research and synthesize findings

Respond in JSON format:
{
  "action": "action_name",
  "params": {"key": "value"},
  "reasoning": "why this action",
  "complete": false
}

Set "complete": true when research is sufficiently comprehensive."""

        prompt = f"""Research query: {query}
Step: {step}/{max_steps}

History:
{json.dumps(history[-3:], indent=2) if history else "No previous actions"}

What should be the next action?"""

        # Sprint 33: Use structured generation with outlines
        decision_model = await self.generate_structured(
            prompt,
            _DecisionOutput,
            system_msg=system_msg,
            temperature=0.2
        )
        return decision_model.model_dump()
    
    # =========================================================================
    # Sprint F150G: Runtime-Facing Wrappers
    # =========================================================================

    # Hard limits for runtime-facing wrappers (M1 8GB safe)
    _PLAN_MAX_QUERY_CHARS = 2048
    _PLAN_MAX_HISTORY_ITEMS = 5
    _PLAN_MAX_CONTEXT_CHARS = 4096

    _SYNTH_MAX_QUERY_CHARS = 1024
    _SYNTH_MAX_FINDINGS = 50
    _SYNTH_MAX_FINDING_CHARS = 800
    _SYNTH_MAX_HYPOTHESES = 10
    _SYNTH_MAX_OUTPUT_CHARS = 8192

    async def generate_sprint_plan(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Sprint F150G: Thin runtime-facing wrapper for sprint planning.

        Built on top of existing decide_next_action(), not a separate engine.
        Lazy: model loaded on demand via existing initialize() path.

        Bounds:
        - query truncated to _PLAN_MAX_QUERY_CHARS
        - history limited to _PLAN_MAX_HISTORY_ITEMS items
        - context truncated to _PLAN_MAX_CONTEXT_CHARS

        Args:
            query: Sprint/research query
            context: Optional runtime context (step, max_steps, history, goals)

        Returns:
            Stable parseable dict with keys:
            - action, params, reasoning, complete (from decide_next_action)
            - plan_id (generated)
            - bounded (True if input was truncated)
        """
        # Fail-soft: if model not loaded, return skeleton
        if self._model is None:
            return {
                "action": "initialize",
                "params": {"reason": "model_not_loaded"},
                "reasoning": "Hermes model not initialized",
                "complete": False,
                "plan_id": None,
                "bounded": False,
            }

        ctx = context or {}
        step = min(ctx.get("step", 0), 9999)
        max_steps = min(ctx.get("max_steps", 20), 9999)
        history = (ctx.get("history", []) or [])[-self._PLAN_MAX_HISTORY_ITEMS:]
        goals = ctx.get("goals", "")

        # Bound query
        bounded_query = str(query)[:self._PLAN_MAX_QUERY_CHARS]
        query_was_truncated = len(str(query)) > self._PLAN_MAX_QUERY_CHARS

        # Bound history — guard against non-dict items (Sprint F150H fix)
        bounded_history = []
        for h in history:
            if not isinstance(h, dict):
                h = {"action": str(h)[:200] if h else ""}
            entry = {
                "action": str(h.get("action", ""))[:200],
                "result": str(h.get("result", ""))[:300] if h.get("result") else None,
            }
            bounded_history.append(entry)

        # Build runtime context for decide_next_action
        runtime_ctx = {
            "query": bounded_query,
            "step": step,
            "max_steps": max_steps,
            "history": bounded_history,
        }
        if goals:
            runtime_ctx["goals"] = str(goals)[:self._PLAN_MAX_CONTEXT_CHARS]

        try:
            result = await self.decide_next_action(runtime_ctx)

            # Validate result structure (fail-soft)
            if not isinstance(result, dict):
                result = {"action": None, "params": {}, "reasoning": str(result), "complete": False}

            # Ensure required keys present
            for key in ("action", "params", "reasoning", "complete"):
                if key not in result:
                    result[key] = None if key != "complete" else False

            result["bounded"] = query_was_truncated
            result["plan_id"] = f"plan_{int(time.time() * 1000)}"

            return result

        except Exception as e:
            logger.warning(f"[SPRINT_PLAN] Failed: {e}")
            return {
                "action": "error",
                "params": {"error": str(e)[:200]},
                "reasoning": "generate_sprint_plan failed",
                "complete": False,
                "plan_id": None,
                "bounded": query_was_truncated,
            }

    async def synthesize_findings(
        self,
        query: str,
        findings: List[Any],
        hypotheses: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Sprint F150G: Thin runtime-facing wrapper for synthesis.

        Built on top of existing synthesize(), not a separate engine.
        Returns structured dict instead of raw text.

        Bounds:
        - query truncated to _SYNTH_MAX_QUERY_CHARS
        - findings limited to _SYNTH_MAX_FINDINGS items
        - each finding truncated to _SYNTH_MAX_FINDING_CHARS
        - hypotheses limited to _SYNTH_MAX_HYPOTHESES

        Args:
            query: Research question
            findings: List of finding dicts/objects
            hypotheses: Optional list of hypothesis strings
            context: Optional context (history, goals)

        Returns:
            Stable report-like dict with keys:
            - report (str) - synthesized text
            - confidence (float) - 0.0-1.0
            - sources_count (int) - number of findings used
            - hypotheses_evaluated (int) - number of hypotheses
            - bounded (bool) - True if input was truncated
            - synthesis_id (str)
        """
        # Fail-soft: if model not loaded, return skeleton
        if self._model is None:
            return {
                "report": "Model not loaded",
                "confidence": 0.0,
                "sources_count": 0,
                "hypotheses_evaluated": 0,
                "bounded": False,
                "synthesis_id": None,
            }

        # Bound query
        bounded_query = str(query)[:self._SYNTH_MAX_QUERY_CHARS]
        query_truncated = len(str(query)) > self._SYNTH_MAX_QUERY_CHARS

        # Bound findings
        bounded_findings = []
        for f in findings[:self._SYNTH_MAX_FINDINGS]:
            if isinstance(f, dict):
                finding_str = json.dumps(f, ensure_ascii=False)[:self._SYNTH_MAX_FINDING_CHARS]
            else:
                finding_str = str(f)[:self._SYNTH_MAX_FINDING_CHARS]
            bounded_findings.append(finding_str)

        findings_truncated = len(findings) > self._SYNTH_MAX_FINDINGS

        # Bound hypotheses
        bounded_hypotheses = []
        if hypotheses:
            bounded_hypotheses = [str(h)[:500] for h in hypotheses[:self._SYNTH_MAX_HYPOTHESES]]
        hypotheses_truncated = len(hypotheses or []) > self._SYNTH_MAX_HYPOTHESES

        # Build context for synthesize()
        history = (context or {}).get("history", [])
        goals = (context or {}).get("goals", "")

        runtime_ctx = {
            "query": bounded_query,
            "history": history[-10:] if history else [],
            "data": bounded_findings,
        }
        if goals:
            runtime_ctx["goals"] = str(goals)[:self._SYNTH_MAX_CONTEXT_CHARS]

        try:
            raw_report = await self.synthesize(runtime_ctx)

            # Truncate output if needed
            bounded_report = str(raw_report)[:self._SYNTH_MAX_OUTPUT_CHARS]
            output_truncated = len(str(raw_report)) > self._SYNTH_MAX_OUTPUT_CHARS

            # Estimate confidence based on findings coverage
            confidence = min(1.0, len(bounded_findings) / max(1, self._SYNTH_MAX_FINDINGS))

            return {
                "report": bounded_report,
                "confidence": confidence,
                "sources_count": len(bounded_findings),
                "hypotheses_evaluated": len(bounded_hypotheses),
                "bounded": query_truncated or findings_truncated or hypotheses_truncated or output_truncated,
                "synthesis_id": f"synth_{int(time.time() * 1000)}",
            }

        except Exception as e:
            logger.warning(f"[SYNTHESIZE] Failed: {e}")
            return {
                "report": f"Synthesis failed: {str(e)[:500]}",
                "confidence": 0.0,
                "sources_count": len(bounded_findings),
                "hypotheses_evaluated": len(bounded_hypotheses),
                "bounded": True,
                "synthesis_id": None,
            }

    async def synthesize(self, context: Dict[str, Any]) -> str:
        """
        Syntetizovat výsledky výzkumu do finální odpovědi.

        Args:
            context: Kontext s nasbíranými daty

        Returns:
            Syntetizovaná odpověď
        """
        query = context.get("query", "")
        history = context.get("history", [])
        data = context.get("data", [])

        system_msg = """You are a research synthesis expert. Create a comprehensive, well-structured answer based on the collected research data.

Your answer should:
- Be thorough and detailed
- Cite sources where possible
- Acknowledge limitations or gaps
- Be objective and balanced
- Use markdown formatting"""

        # Připravit souhrn dat
        data_summary = []
        for i, item in enumerate(data[-10:], 1):  # Posledních 10 položek
            data_summary.append(f"{i}. {json.dumps(item, indent=2)[:500]}")

        prompt = f"""Research Query: {query}

Collected Data:
{chr(10).join(data_summary)}

Execution History:
{json.dumps(history, indent=2)[:2000]}

Synthesize a comprehensive research report answering the query."""

        # Sprint 33: Use structured generation with outlines
        synthesis_model = await self.generate_structured(
            prompt,
            _SynthesisOutput,
            system_msg=system_msg,
            max_tokens=4096
        )
        return synthesis_model.report

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        temperature: float = None,
        max_tokens: int = None,
        system_msg: str = None,
        max_retries: int = 2,
        priority: float = 1.0
    ) -> T:
        """
        Sprint 33+75+7G: Generate structured output using batch routing when safe.

        Batch routing (Sprint 7G):
        - If _is_batch_safe() returns True, submit to batch queue and await result
        - Otherwise, fall through to direct outlines/JSON path

        Args:
            prompt: Input prompt
            response_model: Pydantic model to generate
            temperature: Temperature setting
            max_tokens: Max tokens to generate
            system_msg: System message
            max_retries: Number of retries for JSON parsing (default 2)
            priority: Lower = higher priority (0 = highest, default 1.0)

        Returns:
            Instance of response_model
        """
        # Sprint 7I: Emergency guard — fail fast before any inference
        if is_emergency_unload_requested is not None and is_emergency_unload_requested():
            self._telemetry_counters['emergency_guard_triggered'] += 1
            raise RuntimeError("emergency_unload_requested")

        # Sprint 7G: Batch-safe routing
        timeout_s = max_tokens / 10.0 if max_tokens else None  # rough estimate
        if self._is_batch_safe(response_model, priority, stream=False, timeout_s=timeout_s):
            try:
                self._telemetry_counters['batch_submitted'] += 1
                future = await self._submit_structured_batch(
                    prompt=prompt,
                    response_model=response_model,
                    priority=priority,
                    temperature=temperature or 0.1,
                    max_tokens=max_tokens or 1024,
                    system_msg=system_msg,
                )
                result = await future
                # Shatter validation: ensure result is the right type
                schema_cls = response_model if isinstance(response_model, type) else type(response_model)
                if hasattr(schema_cls, '__struct_fields__'):
                    # msgspec path — result already decoded
                    return result
                else:
                    # Pydantic path — ensure it's an instance
                    if isinstance(result, schema_cls):
                        return result
                    # Fallback: try to construct
                    return schema_cls.model_construct(**result) if isinstance(result, dict) else result
            except Exception as e:
                logger.debug(f"[STRUCTURED] Batch path failed: {e}, falling back to direct")
                self._telemetry_counters['batch_fallback_single'] += 1

        # Sprint 75: Outlines first (if available)
        if OUTLINES_AVAILABLE and self._outlines_model is not None and self._model is not None:
            try:
                schema_key = response_model.__name__
                if schema_key not in self._outlines_generators:
                    self._outlines_generators[schema_key] = outlines_generate.json(
                        self._outlines_model, response_model
                    )
                generator = self._outlines_generators[schema_key]
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    self._inference_executor,
                    lambda: generator(prompt)
                )
                return response_model.model_validate_json(result)
            except Exception as e:
                logger.debug(f"[STRUCTURED] Outlines failed: {e}, falling back to JSON")

        # Sprint 75: JSON prompt + retry
        import json
        import re
        temp = temperature or self.config.temperature
        max_tok = max_tokens or self.config.max_tokens

        for attempt in range(max_retries + 1):
            json_prompt = f"""{prompt}

Respond ONLY with valid JSON matching this schema:
{json.dumps(response_model.model_json_schema(), indent=2)}

Do not include any other text. Output valid JSON only."""

            text = await self.generate(json_prompt, temperature=0.1, max_tokens=2048, system_msg=system_msg)

            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                    return response_model(**data)
                except Exception as e:
                    if attempt < max_retries:
                        logger.debug(f"JSON parse failed (attempt {attempt+1}): {e}")
                        continue

        # Sprint 75: Heuristic fallback
        logger.warning(f"[STRUCTURED] All attempts failed, using fallback for {response_model.__name__}")
        fields = {name: None for name in response_model.model_fields.keys()}
        return response_model.model_construct(**fields)

    # Sprint 41: Invalidate prefix cache
    def invalidate_prefix_cache(self) -> None:
        """Clear the prefix cache (e.g., on model change)."""
        self._prefix_cache.clear()
        logger.info("[CACHE] Prefix cache invalidated")

    # Sprint 8N: Planner → runtime bridge helper
    # Takes typed PlannerRuntimeRequest from htn_planner, executes via existing generate_structured path.
    # Chunk size for bounded batch submission (invariant B.12)
    _BRIDGE_CHUNK_SIZE = 10

    async def execute_planner_requests(
        self, requests, response_models=None
    ):
        """
        Execute a list of PlannerRuntimeRequest objects via Hermes generate_structured.

        Fail-open: if Hermes is not initialized (model not loaded), returns typed
        PlannerRuntimeResult with executed=False, error="model_not_loaded".

        Chunked submission (invariant B.12): submits in chunks of _BRIDGE_CHUNK_SIZE,
        yields between chunks via asyncio.sleep(0).

        Args:
            requests: List of PlannerRuntimeRequest from htn_planner.build_runtime_requests()
            response_models: Optional dict mapping response_model_name → Pydantic model class.
                            If None, uses GenericResult fallback.

        Returns:
            List of PlannerRuntimeResult (same length as input requests,
            but skipped panic tasks have executed=False, skipped_panic=True).
        """
        # Local import to avoid circular dependency (htn_planner imports hermes3_engine)
        from hledac.universal.planning.htn_planner import PlannerRuntimeResult

        # Fail-open: Hermes not initialized
        if self._model is None:
            return [
                PlannerRuntimeResult(
                    task_id=r.task_id,
                    executed=False,
                    skipped_panic=False,
                    hermes_output=None,
                    error="model_not_loaded",
                )
                for r in requests
            ]

        # Default response model registry (Pydantic models for each task type)
        from pydantic import BaseModel, Field

        class GenericResult(BaseModel):
            result: str = Field(description="Result text")
            confidence: float = Field(ge=0.0, le=1.0, default=0.5)

        class FetchResult(GenericResult):
            url: str = Field(description="Fetched URL")

        class DeepReadResult(GenericResult):
            url: str = Field(description="Source URL")
            depth: int = Field(default=1)

        class AnalyseResult(GenericResult):
            source: str = Field(description="Analysis source")

        class SynthesizeResult(GenericResult):
            sources: list[str] = Field(default_factory=list)

        class BranchResult(GenericResult):
            branches: int = Field(default=1)

        class ExplainResult(GenericResult):
            topic: str = Field(description="Explained topic")

        class HypothesisResult(GenericResult):
            hypothesis: str = Field(description="Hypothesis text")

        _MODEL_REGISTRY = {
            'FetchResult': FetchResult,
            'DeepReadResult': DeepReadResult,
            'AnalyseResult': AnalyseResult,
            'SynthesizeResult': SynthesizeResult,
            'BranchResult': BranchResult,
            'ExplainResult': ExplainResult,
            'HypothesisResult': HypothesisResult,
            'GenericResult': GenericResult,
        }

        if response_models is None:
            response_models = _MODEL_REGISTRY

        results: List[PlannerRuntimeResult] = []
        pending_tasks: List = []

        async def execute_single(req) -> PlannerRuntimeResult:
            """Execute a single PlannerRuntimeRequest via generate_structured."""
            # Skip panic tasks (invariant B.10)
            if req.is_panic_deprioritized:
                return PlannerRuntimeResult(
                    task_id=req.task_id,
                    executed=False,
                    skipped_panic=True,
                    hermes_output=None,
                    error=None,
                )

            # Get response model
            model_cls = response_models.get(
                req.response_model_name, GenericResult
            )

            try:
                result = await self.generate_structured(
                    prompt=req.prompt,
                    response_model=model_cls,
                    priority=req.priority,
                    system_msg="You are a helpful research assistant.",
                    max_tokens=1024,
                )
                # Extract output string from Pydantic model
                output_text = result.result if hasattr(result, 'result') else str(result)
                return PlannerRuntimeResult(
                    task_id=req.task_id,
                    executed=True,
                    skipped_panic=False,
                    hermes_output=output_text,
                    error=None,
                )
            except Exception as exc:
                return PlannerRuntimeResult(
                    task_id=req.task_id,
                    executed=False,
                    skipped_panic=False,
                    hermes_output=None,
                    error=str(exc),
                )

        # Chunked submission (invariant B.12 + B.13)
        for i in range(0, len(requests), self._BRIDGE_CHUNK_SIZE):
            chunk = requests[i:i + self._BRIDGE_CHUNK_SIZE]
            # Execute chunk in parallel via gather (invariant B.13)
            chunk_tasks = [execute_single(req) for req in chunk]
            chunk_results = await asyncio.gather(*chunk_tasks, return_exceptions=True)

            # Handle exceptions (invariant B.16: fail-open for unsupported task)
            for req, result in zip(chunk, chunk_results):
                if isinstance(result, Exception):
                    results.append(PlannerRuntimeResult(
                        task_id=req.task_id,
                        executed=False,
                        skipped_panic=False,
                        hermes_output=None,
                        error=f"bridge_exception:{result}",
                    ))
                else:
                    results.append(result)

            # Yield between chunks (invariant B.12)
            if i + self._BRIDGE_CHUNK_SIZE < len(requests):
                await asyncio.sleep(0)

        return results

    async def unload(self) -> None:
        """
        Sprint 7K: Unload model with FULL lifecycle closure.

        NEW ORDER (Sprint 7K):
        1. _shutdown_batch_worker(timeout=3.0) — bounded, fail-pending-futures
        2. _batch_queue = None + _batch_worker_task = None (done by shutdown)
        3. _warmup_cache eviction
        4. _prompt_cache / _system_prompt_cache eviction
        5. _model = None + _tokenizer = None
        6. gc.collect()
        7. mx.eval([]) + mx.metal.clear_cache()

        Safe-clear: Emergency flag is NOT auto-cleared here — caller decides.
        """
        # Step 1: Shutdown batch worker (bounded 3s) — fails pending futures
        await self._shutdown_batch_worker(timeout=3.0)

        # Step 2: Explicitly clear queue and task references
        # (worker is cancelled above; these ensure reload is clean)
        self._batch_queue = None
        self._batch_worker_task = None

        # Step 3: Evict warmup cache
        if self._warmup_cache is not None:
            self._warmup_cache = None
            logger.debug("[LIFECYCLE] _warmup_cache evicted")

        # Sprint 75: Save cache before shutdown
        await self._save_cache()

        # Step 4: Evict all prompt caches
        if self._prompt_cache is not None:
            self._prompt_cache = None
            logger.debug("[LIFECYCLE] _prompt_cache evicted")
        if self._system_prompt_cache is not None:
            self._system_prompt_cache = None
            logger.debug("[LIFECYCLE] _system_prompt_cache evicted")

        # Sprint 41: Clear prefix cache
        self.invalidate_prefix_cache()

        logger.info("Unloading Hermes-3...")

        # Shutdown inference executor
        self._inference_executor.shutdown(wait=True)

        # Step 5: Null model and tokenizer
        self._model = None
        self._tokenizer = None
        self._outlines_model = None

        # Step 6: gc.collect()
        import gc
        gc.collect()

        # Step 7: mx.eval([]) + mx.metal.clear_cache()
        try:
            import mlx.core as mx
            try:
                mx.eval([])
            except Exception:
                pass
            try:
                if hasattr(mx.metal, 'clear_cache'):
                    mx.metal.clear_cache()
                elif hasattr(mx, 'clear_cache'):
                    mx.clear_cache()
            except Exception:
                pass
        except Exception:
            pass

        logger.info("✓ Hermes-3 unloaded (Sprint 7K lifecycle closed)")

    # =========================================================================
    # Sprint 30: KV Cache Compression with CommVQ 2-bit Quantization
    # =========================================================================

    def _get_cache_size_mb(self) -> float:
        """Get current KV cache size in MB using tree flatten."""
        if not self._prompt_cache:
            return 0.0
        try:
            import mlx.core as mx
            import sys

            # Handle compressed format
            if isinstance(self._prompt_cache, tuple) and self._prompt_cache[0] == 'commvq_compressed':
                # For compressed cache, estimate from centroids + indices
                compressed_groups = self._prompt_cache[1]
                total_bytes = 0
                for centroids, indices in compressed_groups:
                    total_bytes += centroids.nbytes + indices.nbytes
                return total_bytes / (1024 * 1024)

            # Original cache size
            leaves = mx.tree_flatten(self._prompt_cache)
            total_bytes = sum(l.nbytes if hasattr(l, 'nbytes') else sys.getsizeof(l) for l in leaves)
            return total_bytes / (1024 * 1024)
        except Exception:
            return 0.0

    async def _compress_kv_cache(self) -> bool:
        """Apply CommVQ 2-bit quantization to KV cache (87.5% savings)."""
        if not MLX_AVAILABLE:
            return False

        try:
            from ..utils.sketches import commvq_quantize

            if not self._prompt_cache:
                return False

            # Check cache dtype before compression (invariant 2)
            import mlx.core as mx
            try:
                mx.eval(self._prompt_cache)
                if hasattr(self._prompt_cache, 'dtype'):
                    if self._prompt_cache.dtype not in (mx.bfloat16, mx.float16, mx.float32):
                        logger.debug(f"[KV-CACHE] Skip: cache dtype is {self._prompt_cache.dtype}")
                        return False
            except Exception as e:
                logger.warning(f"[KV-CACHE] Cannot evaluate cache: {e}")
                return False

            # Apply 2-bit quantization
            compressed = commvq_quantize(self._prompt_cache, bits=2)
            if compressed is self._prompt_cache:
                logger.debug("[KV-CACHE] Quantization returned original (fail-safe)")
                return False

            old_size = self._get_cache_size_mb()
            self._prompt_cache = compressed
            mx.eval(self._prompt_cache)
            new_size = self._get_cache_size_mb()

            savings = ((old_size - new_size) / old_size * 100) if old_size > 0 else 0
            logger.info(f"[KV-CACHE] Compressed: {old_size:.1f} MB -> {new_size:.1f} MB ({savings:.1f}% savings)")
            return True

        except Exception as e:
            logger.warning(f"[KV-CACHE] Compression failed: {e}")
            # Invariant 4: Fallback to original cache
            return False

    async def _prune_kv_cache(self) -> bool:
        """
        Sprint 37: Prune KV cache resetem offsetu pokud kontext > 1024 tokenů.
        mlx_lm PromptCache nepodporuje přímý token mask – offset je jediný bezpečný způsob.
        """
        if not self._kv_cache_enabled or self._prompt_cache is None:
            return False

        try:
            # Zjistíme aktuální délku kontextu z cache
            # PromptCache v mlx_lm má atribut 'offset' (počet tokenů v cache)
            if not hasattr(self._prompt_cache, 'offset'):
                return False

            context_len = self._prompt_cache.offset
            if context_len <= 1024:
                return False

            # Prune = ponecháme prvních 80 % tokenů, zbytek zahodíme
            new_offset = int(context_len * 0.8)
            self._prompt_cache.offset = new_offset

            logger.info(f"[PRUNE] Context {context_len} → {new_offset} tokens (saved {context_len - new_offset})")
            return True

        except Exception as e:
            logger.warning(f"[PRUNE] Failed: {e}, falling back to compression")
            return False

    # =========================================================================
    # Sprint 8BI: Ghost Hermes Sustain Mode for M1 8GB
    # =========================================================================

    @staticmethod
    def _build_sustain_generate_kwargs_for_test(generate_fn: Callable) -> dict:
        """
        Build MLX generate kwargs for sustain mode using runtime introspection.

        Uses GHOST_HERMES_SUSTAIN=1 env flag and inspects generate_fn signature
        to add only supported kwargs.
        """
        sustain_flag = os.getenv("GHOST_HERMES_SUSTAIN", "0")
        if sustain_flag != "1":
            return {}

        try:
            sig = inspect.signature(generate_fn)
            param_names = set(sig.parameters.keys())
            has_var_keyword = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            )
        except Exception:
            param_names = set()
            has_var_keyword = False

        kwargs = {}

        # max_kv_size supported if explicit in signature or function has **kwargs
        if "max_kv_size" in param_names or has_var_keyword:
            kwargs["max_kv_size"] = int(os.getenv("GHOST_KV_SIZE", "4096"))

        # Optional kwargs - only add if parameter exists in signature
        if "kv_cache_type" in param_names:
            kwargs["kv_cache_type"] = "rotating"

        if "attention_sink_size" in param_names:
            kwargs["attention_sink_size"] = 4

        return kwargs

    def _run_sustain_inference(self, formatted_prompt: str, temp: float, max_tok: int) -> str:
        """Run MLX inference with sustain mode (M1 8GB optimization)."""
        from mlx_lm import generate as mlx_generate

        # Try to configure MLX limits (best-effort)
        try:
            from ..utils.mlx_memory import configure_mlx_limits, format_mlx_memory_snapshot
            configure_mlx_limits(cache_limit_mb=1536, memory_limit_mb=None)
            logger.debug(f"[SUSTAIN] PRE: {format_mlx_memory_snapshot()}")
        except Exception as e:
            logger.debug(f"[SUSTAIN] MLX limits configure failed: {e}")

        # Build sustain kwargs via introspection
        sustain_kwargs = self._build_sustain_generate_kwargs_for_test(mlx_generate)

        generate_kwargs = {
            "model": self._model,
            "tokenizer": self._tokenizer,
            "prompt": formatted_prompt,
            "temp": temp,
            "max_tokens": max_tok,
            "verbose": False,
        }

        # Merge sustain kwargs (only supported ones)
        for k, v in sustain_kwargs.items():
            generate_kwargs[k] = v

        # Prefix/prompt cache experiment: ONLY when explicitly enabled
        if os.getenv("GHOST_PREFIX_CACHE_EXPERIMENT", "0") == "1":
            try:
                from mlx_lm.models.cache import make_prompt_cache
                kv_cache = make_prompt_cache(self._model, max_kv_size=max_tok)
                generate_kwargs["prompt_cache"] = kv_cache
            except Exception as e:
                logger.debug(f"[SUSTAIN] prompt_cache experiment failed: {e}")

        response = mlx_generate(**generate_kwargs)

        # Log memory snapshot (best-effort)
        try:
            from ..utils.mlx_memory import format_mlx_memory_snapshot
            logger.debug(f"[SUSTAIN] POST: {format_mlx_memory_snapshot()}")
        except Exception:
            pass

        return response.strip()

    # =========================================================================
    # Sprint 7B: Prefix Cache Warmup Seam
    # =========================================================================

    async def warmup_prefix_cache(
        self,
        system_prompt: str = "You are a helpful research assistant.",
        few_shot_examples: list = None
    ) -> bool:
        """
        Prefix-cache warmup: prefill KV cache s system prompt + few-shot examples.

        Warmup pattern:
        1. System prompt (~200 tokens)
        2. 2-3 few-shot examples (~300 tokens each)
        3. 1 generation call with max_tokens=1

        Args:
            system_prompt: System prompt to cache
            few_shot_examples: List of {"user": "...", "assistant": "..."} examples

        Returns:
            True if warmup successful, False otherwise
        """
        if self._model is None or self._tokenizer is None:
            logger.warning("[WARMUP] Model not loaded, skipping warmup")
            return False

        if few_shot_examples is None:
            few_shot_examples = [
                {"user": "What is 2+2?", "assistant": "4"},
                {"user": "Capital of France?", "assistant": "Paris"},
            ]

        try:
            logger.info("[WARMUP] Starting prefix cache warmup...")

            # Build warmup prompt in ChatML format
            parts = [f"<|im_start|>system\n{system_prompt}<|im_end|>"]
            for ex in few_shot_examples[:3]:  # Max 3 examples
                parts.append(f"<|im_start|>user\n{ex.get('user', '')}<|im_end|>")
                parts.append(f"<|im_start|>assistant\n{ex.get('assistant', '')}<|im_end|>")
            warmup_prompt = "\n".join(parts)

            # Tokenize to estimate size
            tokens = self._tokenizer.encode(warmup_prompt)
            token_count = len(tokens)
            logger.info(f"[WARMUP] Warmup prompt: ~{token_count} tokens")

            if token_count > 1000:
                logger.warning(f"[WARMUP] Warmup prompt too long ({token_count} tokens), truncating")
                warmup_prompt = self._tokenizer.decode(tokens[:1000])

            # Run generation with max_tokens=1 to populate KV cache
            async with self._inference_semaphore:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self._inference_executor,
                    lambda: self._run_inference(warmup_prompt, temp=0.3, max_tok=1)
                )

            logger.info("[WARMUP] Prefix cache warmup complete")
            return True

        except Exception as e:
            logger.warning(f"[WARMUP] Warmup failed: {e}")
            return False

    # =========================================================================
    # Sprint 7B: Structured Output Capability Wrapper
    # =========================================================================

    def _probe_outlines_capability(self) -> bool:
        """
        Probe outlines + MLX path availability.

        Returns:
            True if outlines.generate.json works with mlx_lm model
        """
        if not OUTLINES_AVAILABLE:
            return False
        if self._outlines_model is None:
            return False
        try:
            # Quick probe with minimal schema
            import outlines.generate as og
            from pydantic import BaseModel

            class _ProbeSchema(BaseModel):
                ok: bool

            gen = og.json(self._outlines_model, _ProbeSchema)
            # Don't actually run, just check it compiles
            return callable(gen)
        except Exception:
            return False

    def _probe_xgrammar_capability(self) -> bool:
        """
        Probe xgrammar CPU path availability.

        Returns:
            True if xgrammar is available and functional
        """
        try:
            import xgrammar as xg
            return hasattr(xg, 'CompiledGrammar')
        except ImportError:
            return False

    def generate_structured_safe(
        self,
        prompt: str,
        response_model: type,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        system_msg: str = None
    ) -> Any:
        """
        Sprint 7B: Structured output with guaranteed fallback chain.

        Fallback chain:
        1. outlines MLX path (if available)
        2. xgrammar CPU path (if available)
        3. prompt + orjson.loads() + retry max 3x with backoff 0.5/1/2s

        Args:
            prompt: Input prompt
            response_model: Pydantic model to generate
            temperature: Temperature setting
            max_tokens: Max tokens to generate
            system_msg: Optional system message

        Returns:
            Instance of response_model (or fallback with default fields)
        """
        import re
        import time
        import orjson

        # Path 1: Outlines
        if self._probe_outlines_capability():
            try:
                import outlines.generate as og
                schema_key = response_model.__name__
                if schema_key not in self._outlines_generators:
                    self._outlines_generators[schema_key] = og.json(
                        self._outlines_model, response_model
                    )
                generator = self._outlines_generators[schema_key]

                # Run in executor
                loop = asyncio.get_running_loop()
                result_str = loop.run_in_executor(
                    self._inference_executor,
                    lambda: generator(prompt)
                ).result(timeout=30)

                # Sprint 7D: Dual-dispatch for schema type
                # msgspec path: has __struct_fields__
                # pydantic path: has model_validate_json
                if hasattr(response_model, '__struct_fields__'):
                    # msgspec path
                    import msgspec
                    return msgspec.decode(result_str, type=response_model)
                else:
                    # Pydantic path
                    return response_model.model_validate_json(result_str)
            except Exception as e:
                logger.debug(f"[STRUCTURED] Outlines path failed: {e}")

        # Path 2: xgrammar
        if self._probe_xgrammar_capability():
            logger.debug("[STRUCTURED] xgrammar path not implemented, falling back to JSON")
            # xgrammar integration would go here when implemented

        # Path 3: JSON prompt + orjson.loads() + retry with backoff
        backoffs = [0.5, 1.0, 2.0]

        for attempt in range(3):
            try:
                json_prompt = f"""{prompt}

Respond ONLY with valid JSON matching this schema:
{orjson.dumps(response_model.model_json_schema()).decode()}

Do not include any other text. Output valid JSON only."""

                text = self._run_inference(
                    self._format_chatml(system_msg or "You are a helpful assistant.", json_prompt),
                    temp=temperature,
                    max_tok=max_tokens
                )

                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    data = orjson.loads(match.group())
                    return response_model(**data)
            except Exception as e:
                logger.debug(f"[STRUCTURED] JSON parse attempt {attempt + 1} failed: {e}")

            if attempt < 2:
                time.sleep(backoffs[attempt])

        # Final fallback: return model with default fields
        logger.warning(f"[STRUCTURED] All attempts failed for {response_model.__name__}, using defaults")
        fields = {name: None for name in response_model.model_fields.keys()}
        return response_model.model_construct(**fields)
