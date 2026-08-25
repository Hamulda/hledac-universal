"""
brain/hermes/engine.py — DeepHermes3Engine Orchestrator
===================================================

PEP 698: Modular refactoring of brain/deephermes3_engine.py (5560 LOC → modular package).

This is the reduced orchestrator class that delegates to specialized modules:
- chatml: ChatML formatting
- decisions: Decision making, triage
- synthesis: Report, sprint plan, findings synthesis
- batch: Batch processing
- kv_cache: KV cache, warmup
- structured: Structured output generation
- security: Prompt validation
- lora: LoRA adapter management
- lifecycle: Model lifecycle
- stream: Streaming generation
- planner: Planner execution

Target size: ≤ 400 LOC for the main class.

M1 8GB: Unified memory architecture.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import weakref
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

# Use relative imports within the package
from brain.hermes.batch import (
    PriorityQueueAdapter,
    batch_worker_loop,
    collect_batch_items,
)
from brain.hermes.chatml import format_chatml
from brain.hermes.decisions import decide_next_action
from brain.hermes.kv_cache import (
    get_warmup_cache_path,
    invalidate_prefix_cache,
    restore_warmup_cache_async,
)
from brain.hermes.lifecycle import (
    close_engine,
    ensure_model_loaded,
    unload_model,
)
from brain.hermes.planner import PlannerRuntimeResult, execute_planner_requests
from brain.hermes.security import get_sanitize_function
from brain.hermes.stream import stream_tokens
from brain.hermes.structured import generate_structured
from brain.hermes.synthesis import (
    generate_report,
    generate_sprint_plan,
    synthesize_findings,
)

# FIXED: Use absolute import for utils module
from hledac.universal.utils.hash import xxh3_64_hex

if TYPE_CHECKING:
    from mlx_lm import Model as MLXModel
    from mlx_lm import TokenizerWrapper as MLXTokenizer

logger = logging.getLogger(__name__)
T = TypeVar("T")


class DeepHermesConfig:
    """Configuration for DeepHermes3Engine."""

    __slots__ = ("model_path", "system_prompt", "max_tokens", "temperature")

    def __init__(
        self,
        model_path: str | None = None,
        system_prompt: str = "You are a helpful research assistant.",
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> None:
        self.model_path = model_path or os.getenv(
            "HLEDAC_HERMES_MODEL", "mlx-community/Hermes-3-Llama-3.2-3B-Flash-Bf16"
        )
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.temperature = temperature


class DeepHermes3Engine:
    """
    DeepHermes3Engine — LLM-based decision making with ChatML formatting.

    PEP 698: Refactored from 5560 LOC god-module into modular package.

    This orchestrator delegates to specialized modules while maintaining
    the original API for backward compatibility.
    """

    __slots__ = (
        "_config",
        "_model",
        "_tokenizer",
        "_sanitize_for_llm",
        "_triage_mode",
        "_lora_adapter_path",
        "_lora_cache_stats",
        "_kv_cache_enabled",
        "_prefix_cache",
        "_prefix_cache_maxsize",
        "_prefix_cache_stats",
        "_session_cache_pool",
        "_session_cache_maxsize",
        "_session_cache_stats",
        "_kv_cache_pool",
        "_kv_cache_pool_maxsize",
        "_kv_cache_pool_stats",
        "_batch_queue",
        "_batch_worker_task",
        "_batch_adapter",
        "_outlines_model",
        "_outlines_generators",
        "_warmup_cache",
        "_warmup_prompt_hash",
        "_last_inference_at",
        "_idle_unload_timeout_s",
        "_last_bandit_arm",
        "_prompt_bandit",
        "_closed",
        "_inference_active",
        "_state_observer",
        "_pending_futures",
        "_generation_facade",
        "_kv_cache_mgr",
        "_max_kv_size",
        "_kv_bits",
        "_system_prompt",
        "_system_prompt_hash",
        "_system_prompt_cache",
        "_mlx_batcher",
        "_mlx_worker_thread",
        "_mlx_scheduler",
        "_pipeline",
        "_batch_max_size",
        "_batch_default_flush_interval",
        "_batch_flush_interval",
        "_telemetry_counters",
        "_draft_model_obj",
        "_draft_model_name",
        "_draft_tokenizer",
        "_supports_draft",
        "_supports_kv_quant",
        "_supports_stream_generate",
    )

    def __init__(
        self,
        model_path: str | None = None,
        sanitize_for_llm: Callable[[str], str] | None = None,
    ) -> None:
        """Initialize DeepHermes3Engine."""
        self.config = DeepHermesConfig(model_path=model_path, system_prompt="You are a helpful research assistant.")
        self._sanitize_for_llm = get_sanitize_function(sanitize_for_llm)

        # Model state
        self._model: MLXModel | None = None
        self._tokenizer: MLXTokenizer | None = None
        self._system_prompt = self.config.system_prompt
        self._system_prompt_hash = xxh3_64_hex(self._system_prompt)
        self._system_prompt_cache: Any | None = None
        self._kv_cache_enabled = False
        self._triage_mode = False

        # KV cache pools
        self._kv_cache_pool_maxsize = 4
        self._kv_cache_pool: dict[str, Any] = {}
        self._kv_cache_pool_stats = {"pool_hits": 0, "pool_misses": 0}
        self._session_cache_maxsize = 8
        self._session_cache_pool: dict[str, tuple] = {}
        self._session_cache_stats = {"session_cache_hits": 0, "session_cache_misses": 0}
        self._prefix_cache_maxsize = 64
        self._prefix_cache: dict[str, Any] = {}
        self._prefix_cache_stats = {"prefix_cache_hits": 0, "prefix_cache_misses": 0}

        # Batch processing
        self._batch_queue: asyncio.Queue | None = None
        self._batch_worker_task: asyncio.Task | None = None
        self._batch_adapter: PriorityQueueAdapter | None = None
        self._batch_max_size = 8
        self._batch_default_flush_interval = 2.0

        # Outlines
        self._outlines_model: Any | None = None
        self._outlines_generators: dict[str, Any] = {}

        # Warmup
        self._warmup_cache: Any | None = None
        self._warmup_prompt_hash: str | None = None

        # State
        self._last_inference_at: float | None = None
        self._idle_unload_timeout_s = 1800.0
        self._inference_active = False
        self._closed = False
        self._last_bandit_arm: str | None = None
        self._prompt_bandit: Any | None = None
        self._telemetry_counters: dict[str, int] = {}

        # LoRA
        self._lora_adapter_path: str | None = None
        self._lora_cache_stats = {"lora_cache_hits": 0, "lora_cache_misses": 0}

        # Draft model
        self._draft_model_obj: Any | None = None
        self._draft_model_name: str | None = None
        self._draft_tokenizer: Any | None = None
        self._supports_draft = False
        self._supports_kv_quant = False
        self._supports_stream_generate = False

        # References
        self._pending_futures: set[asyncio.Future] = set()
        self._generation_facade: Any | None = None
        self._kv_cache_mgr: Any | None = None
        self._mlx_batcher: Any | None = None
        self._mlx_worker_thread: Any | None = None
        self._mlx_scheduler: Any | None = None
        self._pipeline: Any | None = None

        # Configuration
        self._max_kv_size = 8192
        self._kv_bits = int(os.getenv("HLEDAC_KV_BITS", os.getenv("GHOST_KV_BITS", "4")))

        # State observer (lazy import to avoid circular deps)
        from brain.model_state import get_state_observer

        self._state_observer = get_state_observer()

        # Register finalizer
        weakref.finalize(self, self._cleanup_sync)

        logger.info("[ENGINE] DeepHermes3Engine initialized (PEP 698 modular)")

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        system_msg: str | None = None,
        temperature: float | None = None,
        *,
        thinking: bool = True,
    ) -> str:
        """Generate text completion."""
        system_msg = system_msg or self._system_prompt
        temperature = temperature or self.config.temperature

        formatted = format_chatml(system_msg, prompt)
        self._inference_active = True
        self._last_inference_at = time.monotonic()

        try:
            return await self._run_inference(formatted, temperature, max_tokens)
        finally:
            self._inference_active = False

    async def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 512,
        system_msg: str | None = None,
        temperature: float | None = None,
        *,
        thinking: bool = True,
    ) -> AsyncIterator[str]:
        """Generate streaming text."""
        system_msg = system_msg or self._system_prompt
        formatted = format_chatml(system_msg, prompt)

        async for token in stream_tokens(self, formatted, max_tokens, temperature or 0.7):
            yield token

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        max_retries: int = 2,
        priority: float = 1.0,
    ) -> T:
        """Generate structured output."""
        return await generate_structured(
            self, prompt, response_model, temperature, max_tokens, system_msg, max_retries, priority
        )

    async def decide_next_action(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Decide next action in research flow.

        When HLEDAC_ENABLE_DECISION_CAPTURE=1, decisions are auto-captured
        to EvidenceLog for audit trail and Haft-style decision records.
        """
        # Check feature flag for decision capture
        try:
            from hledac.universal._core.feature_flags import FeatureFlags, FeatureFlag
            capture_enabled = FeatureFlags.get(FeatureFlag.DECISION_CAPTURE)
        except Exception:
            capture_enabled = False

        if capture_enabled:
            try:
                from hledac.universal.memory.decision_capture import DecisionCapture, get_decision_capture
                capture = get_decision_capture()
                return await capture.decide_next_action(self, context)
            except Exception:
                pass  # Fall through to direct call

        return await decide_next_action(self, context)

    async def generate_report(self, query: str, context: list[str]) -> str:
        """Generate OSINT research report."""
        return await generate_report(self, query, context)

    async def generate_sprint_plan(
        self,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate research sprint plan."""
        return await generate_sprint_plan(self, query, context)

    async def synthesize_findings(
        self,
        query: str,
        findings: list[Any],
        hypotheses: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Synthesize research findings."""
        return await synthesize_findings(self, query, findings, hypotheses, context)

    async def execute_planner_requests(
        self,
        requests,
        response_models=None,
    ) -> list[PlannerRuntimeResult]:
        """Execute planner requests."""
        return await execute_planner_requests(self, requests, response_models)

    def batch_processor(self) -> PriorityQueueAdapter:
        """Get batch processor adapter."""
        if self._batch_adapter is None:
            self._batch_adapter = PriorityQueueAdapter(self)
        return self._batch_adapter

    async def _ensure_batch_worker(self) -> None:
        """Ensure batch worker is running."""
        if self._batch_queue is None:
            self._batch_queue = asyncio.Queue(maxsize=self._batch_max_size * 2)

        if self._batch_worker_task is None or self._batch_worker_task.done():
            self._batch_worker_task = safe_create_task(batch_worker_loop(self))

    async def flush_all(self, timeout: float = 5.0) -> int:
        """Flush all pending batch items."""
        if self._batch_queue is None:
            return 0
        count = self._batch_queue.qsize()
        await collect_batch_items(self)
        return count

    async def initialize(self) -> None:
        """Initialize engine resources."""
        logger.info("[INIT] Starting Hermes3 engine initialization")
        await ensure_model_loaded(self)
        await self._ensure_batch_worker()
        self._notify_state()
        logger.info("[INIT] Hermes3 engine initialized")

    async def unload(self) -> None:
        """Unload model and release resources."""
        await unload_model(self)

    async def aclose(self) -> None:
        """Async context manager exit."""
        await close_engine(self)

    async def __aenter__(self) -> DeepHermes3Engine:
        await self.initialize()
        return self

    async def __aexit__(self, *args) -> None:
        await self.aclose()

    async def _run_inference(
        self,
        formatted_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Run inference on model."""
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("Model not loaded")

        tokens = self._tokenizer.encode(formatted_prompt)
        prompt_tokens = len(tokens)
        tokens = self._model.generate(
            tokens,
            max_tokens=max_tokens,
            temp=temperature,
            kv_bits=self._get_adaptive_kv_bits(),
            max_kv_size=self._max_kv_size,
        )
        return self._tokenizer.decode(tokens[prompt_tokens:])

    def _notify_state(self, state: str | None = None) -> None:
        """Notify state observers."""
        from brain.model_state import ModelLoadState, ModelState

        if state:
            load_state = ModelLoadState[state.upper()]
        elif self._model is None:
            load_state = ModelLoadState.UNLOADED
        elif self._inference_active:
            load_state = ModelLoadState.BUSY
        else:
            load_state = ModelLoadState.LOADED

        state_obj = ModelState(
            model_id="hermes",
            load_state=load_state,
            is_model_loaded=self._model is not None,
        )
        self._state_observer.notify(state_obj)

    def _get_prompt_bandit(self):
        """Lazy init PromptBandit."""
        if self._prompt_bandit is None:
            try:
                from brain.prompt_bandit import PromptBandit

                self._prompt_bandit = PromptBandit(
                    lambda_reg=0.01, persist_path=str(Path.home() / ".hledac" / "hermes_prompt_bandit.json")
                )
            except ImportError:
                self._prompt_bandit = None
        return self._prompt_bandit

    def _compute_system_prompt_hash(self, system_msg: str | None) -> str:
        """Compute hash for system prompt."""
        if system_msg is None:
            return "none"
        return xxh3_64_hex(system_msg)

    def _get_metal_cache_pressure(self) -> float:
        """Get Metal memory pressure."""
        try:
            import mlx.core as mx

            return mx.metal.get_active_memory() / max(1, mx.metal.get_peak_memory())
        except Exception:
            return 0.0

    def _get_adaptive_kv_bits(self) -> int:
        """Get adaptive KV cache quantization bits."""
        return self._kv_bits

    def _cleanup_sync(self) -> None:
        """Synchronous cleanup."""
        if not self._closed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.aclose())
            except Exception:
                pass

    async def warmup_prefix_cache(
        self,
        system_prompt: str = "You are a helpful research assistant.",
        few_shot_examples: list | None = None,
    ) -> bool:
        """Warmup prefix cache."""
        cache_path = get_warmup_cache_path(system_prompt, few_shot_examples)

        if cache_path.exists():
            return await restore_warmup_cache_async(self, cache_path, cache_path.stem.removeprefix("warmup_"))
        return False

    def invalidate_prefix_cache(self) -> None:
        """Invalidate prefix cache."""
        invalidate_prefix_cache(self)

    @property
    def model(self) -> MLXModel | None:
        """Model reference."""
        return self._model

    @property
    def tokenizer(self) -> MLXTokenizer | None:
        """Tokenizer reference."""
        return self._tokenizer

    @property
    def triage_mode(self) -> bool:
        """Triage mode flag."""
        return self._triage_mode

    @triage_mode.setter
    def triage_mode(self, value: bool) -> None:
        """Set triage mode."""
        self._triage_mode = value

    def cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return {
            "prefix_cache": self._prefix_cache_stats,
            "session_cache": self._session_cache_stats,
            "kv_cache_pool": self._kv_cache_pool_stats,
        }

    def get_inference_stats(self) -> dict[str, Any]:
        """Get inference statistics."""
        return {
            "model_loaded": self._model is not None,
            "inference_active": self._inference_active,
            "last_inference_at": self._last_inference_at,
        }

    def reset_session(self, *, keep_cache_pool: bool = True) -> None:
        """Reset conversation session."""
        self._session_cache_pool.clear()
        self._system_prompt_cache = None

        if not keep_cache_pool:
            self._kv_cache_pool.clear()

    def kv_cache_manager(self):
        """Get KV cache manager (lazy init)."""
        if self._kv_cache_mgr is None:
            from brain._cache.kv_cache_manager import KVCacheManager

            self._kv_cache_mgr = KVCacheManager(
                kv_pool=self._kv_cache_pool,
                session_cache=self._session_cache_pool,
                prefix_cache=self._prefix_cache,
            )
        return self._kv_cache_mgr

    def generation_facade(self):
        """Get generation facade (lazy init)."""
        if self._generation_facade is None:
            from brain._inference.generate import GenerationFacade

            self._generation_facade = GenerationFacade(self)
        return self._generation_facade
