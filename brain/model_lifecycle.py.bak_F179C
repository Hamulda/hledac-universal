"""
F6.5: Model Lifecycle Management — Multi-Role Module
=====================================================

F6.5 OWNERSHIP DECLARATION:
  This module is MULTI-ROLE — do NOT treat it as a single owner.
  Each role is explicitly listed below.

F6.5 EXPLICIT ROLES:
  ┌─────────────────────────────────┬──────────────────────────────────────────┐
  │ Role                             │ Canonical Owner                         │
  ├─────────────────────────────────┼──────────────────────────────────────────┤
  │ 1. Emergency seam                │ model_lifecycle (watchdog flag)        │
  │ 2. MLX lazy init helper          │ mlx_cache.init_mlx_buffers()           │
  │ 3. Unload helper (7K SSOT)       │ engine.unload() — delegát, fail-open  │
  │ 4. Lifecycle shadow-state        │ model_lifecycle (O(1), side-effect free)│
  │ 5. Structured-generation sidecar │ class ModelLifecycle (windup-local)    │
  └─────────────────────────────────┴──────────────────────────────────────────┘

F6.5 THIS MODULE IS NOT THE RUNTIME-WIDE LOAD OWNER:
  - load_model() / unload_model() at module level are UNLOAD HELPERS
  - They delegate to engine.unload() (7K SSOT), NOT a separate authority
  - Canonical runtime-wide acquire/load owner: brain.model_manager.ModelManager
  - This module does NOT hold canonical model state for the runtime-wide plane

F6.5 LAYER MAPPING — MUST NOT BE CONFLATED:
  Layer 1 (workflow-level, ModelManager.PHASE_MODEL_MAP):
    PLAN/DECIDE/SYNTHESIZE → hermes
    EMBED/DEDUP/ROUTING → modernbert
    NER/ENTITY → gliner
    Strings: PLAN, DECIDE, SYNTHESIZE, EMBED, DEDUP, ROUTING, NER, ENTITY
  Layer 2 (coarse-grained, ModelLifecycleManager):
    BRAIN/TOOLS/SYNTHESIS/CLEANUP — entirely different strings
  Layer 3 (windup-local, windup_engine.SynthesisRunner):
    Own isolated model plane with Qwen/SmolLM

F6.5 HARD INVARIANTS:
  - acquire ≠ phase enforcement
  - unload ≠ phase policy
  - workflow phases (Layer 1) ≠ coarse phases (Layer 2)
  - SYNTHESIZE (Layer 1) ≠ SYNTHESIS (Layer 2)
  - capability layer MUST NOT become third model truth
  - windup-local model world ≠ runtime-wide model plane

F6.5 structured-generation sidecar (class ModelLifecycle):
  - Windup-local, isolated from runtime-wide model plane
  - Qwen/SmolLM model (separate from Hermes/ModernBERT/GLiNER)
  - NOT part of the runtime-wide model plane
  - Consumers needing phase facts should use brain.model_phase_facts.is_same_layer()
"""

# Transitional Czech prose follows after blank line below.

# Kanonické místo pro model lifecycle operace.
# Zajišťuje konzistentní pořadí při unload modelů.
#
# Pro Hermes-3: Canonical 7K unload order (SSOT — Hermes3Engine.unload()):
#   1. _shutdown_batch_worker(timeout=3.0)
#   2. _batch_queue = None + _batch_worker_task = None
#   3. _warmup_cache eviction
#   4. _save_cache()
#   5. _prompt_cache / _system_prompt_cache eviction
#   6. invalidate_prefix_cache()
#   7. _model = None + _tokenizer = None + _outlines_model = None
#   8. gc.collect()
#   9. mx.eval([]) + mx.metal.clear_cache()
#
# Pro ostatní modely bez unload() method: legacy direct eviction.
#
# Features:
# - unload_model() helper s fail-open, deleguje na engine.unload() pokud existuje
# - is_safe_to_clear_emergency() — 7K safe-clear preconditions
# - Idempotentní operace
# - Bounded memory cleanup
#
# Použití:
#   from hledac.universal.brain.model_lifecycle import unload_model
#   await unload_model(model=hermes_engine, tokenizer=tokenizer, prompt_cache=cache)

from __future__ import annotations

import gc
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sprint 7H: Safe Emergency Unload Seam
# Watchdog sets this flag; safe consumer checks before next inference.
# NEVER call unload_model() directly from watchdog loop.
# ---------------------------------------------------------------------------
_emergency_unload_requested: bool = False
_emergency_callback: Optional[Callable[[], None]] = None
# F162F: Emergency wait counter — prevents infinite wait on M1 when
# is_safe_to_clear_emergency keeps returning False due to inaccessible engine attrs.
_EMERGENCY_WAIT_ATTEMPTS: int = 0
_MAX_EMERGENCY_WAIT_ATTEMPTS: int = 5


def request_emergency_unload() -> None:
    """
    Set emergency unload flag. Called by UmaWatchdog EMERGENCY callback.

    This is a SAFE pattern: watchdog sets flag, safe seam consumes it
    before next inference. Never blocks the watchdog loop.
    Failsafe: callback errors are caught and logged, never propagate.
    """
    global _emergency_unload_requested
    _emergency_unload_requested = True
    logger.warning("[LIFECYCLE] Emergency unload requested (watchdog flag set)")
    # Failsafe: invoke registered callback, never let callback errors propagate
    cb = _emergency_callback
    if cb is not None:
        try:
            cb()
        except Exception as e:
            logger.warning(f"[LIFECYCLE] Emergency callback raised (ignored): {e}")


def is_emergency_unload_requested() -> bool:
    """Return True if emergency unload has been requested by watchdog."""
    return _emergency_unload_requested


def clear_emergency_unload_request() -> None:
    """Clear emergency unload flag after it has been consumed."""
    global _emergency_unload_requested
    _emergency_unload_requested = False


def is_safe_to_clear_emergency(engine) -> bool:
    """
    Sprint 8C: 7K safe-clear preconditions — EXACT 7K conditions.

    F162F: Tracks _EMERGENCY_WAIT_ATTEMPTS. Returns True when ALL of:
    1. _batch_worker_task is None or done()
    2. _batch_queue is None
    3. len(_pending_futures) == 0
    OR when _EMERGENCY_WAIT_ATTEMPTS >= _MAX_EMERGENCY_WAIT_ATTEMPTS (M1 bounded wait).

    This is the canonical check BEFORE clearing emergency flag.
    If not safe, leave clear_emergency_unload_request() to caller/manual.
    """
    global _EMERGENCY_WAIT_ATTEMPTS
    if engine is None:
        return True
    try:
        batch_done = (
            getattr(engine, '_batch_worker_task', None) is None
            or (hasattr(engine._batch_worker_task, 'done') and engine._batch_worker_task.done())
        )
        queue_none = getattr(engine, '_batch_queue', None) is None
        no_pending = len(getattr(engine, '_pending_futures', set())) == 0
        if batch_done and queue_none and no_pending:
            _EMERGENCY_WAIT_ATTEMPTS = 0
            return True
        # Increment wait counter
        _EMERGENCY_WAIT_ATTEMPTS += 1
        if _EMERGENCY_WAIT_ATTEMPTS >= _MAX_EMERGENCY_WAIT_ATTEMPTS:
            logger.warning(
                f"[LIFECYCLE] Emergency wait exhausted ({_EMERGENCY_WAIT_ATTEMPTS} attempts) — "
                "forcing clear on M1"
            )
            _EMERGENCY_WAIT_ATTEMPTS = 0
            return True
        return False
    except Exception:
        # Fail-safe: if we can't determine, assume NOT safe
        _EMERGENCY_WAIT_ATTEMPTS += 1
        if _EMERGENCY_WAIT_ATTEMPTS >= _MAX_EMERGENCY_WAIT_ATTEMPTS:
            _EMERGENCY_WAIT_ATTEMPTS = 0
            return True
        return False


def set_emergency_callback(callback: Callable[[], None]) -> None:
    """
    Register a callback to be called when emergency unload is requested.
    The callback is invoked by the safe seam consumer, not by watchdog directly.
    """
    global _emergency_callback
    _emergency_callback = callback


def get_emergency_callback() -> Optional[Callable[[], None]]:
    """Return the registered emergency callback, if any."""
    return _emergency_callback

# MLX lazy import — single shared module-level state
_mlx: Any = None
_MLX_AVAILABLE_SAFETY: bool = False


def _get_mlx_safe() -> Any:
    """Lazy MLX accessor — single MLX helper for entire module."""
    global _mlx, _MLX_AVAILABLE_SAFETY
    if _mlx is None:
        try:
            import mlx.core as mx
            _mlx = mx
            _MLX_AVAILABLE_SAFETY = True
        except ImportError:
            _mlx = None
            _MLX_AVAILABLE_SAFETY = False
    return _mlx


# ---------------------------------------------------------------------------
# Sprint 8Y: Shadow-state for lifecycle introspection
# O(1), side-effect free — reads only lightweight Python variables.
# ---------------------------------------------------------------------------
_lifecycle_state: dict = {
    "loaded": False,
    "current_model": None,
    "initialized": False,
    "last_error": None,
}

# Sprint 8Y: Store the actual model object reference so we can call unload()
# on it when switching models.
# F162F FIX: Now uses weakref to avoid preventing GC — model is released
# immediately after lifecycle considers it unloaded.
import weakref
_weak_model_ref: Optional[weakref.ref] = None


def _get_current_model_unsafe() -> Optional[Any]:
    """Dereference weak ref, returning model or None. Must not be called after GC."""
    if _weak_model_ref is None:
        return None
    return _weak_model_ref()


def _set_current_model_ref(model: Any) -> None:
    """Set weak ref to model. None clears it."""
    global _weak_model_ref
    if model is None:
        _weak_model_ref = None
    else:
        _weak_model_ref = weakref.ref(model)


def get_model_lifecycle_status() -> dict:
    """
    Sprint 8Y: Return current lifecycle state as a dict.

    This is the canonical status surface. O(1), side-effect free.
    Reads only shadow-state Python variables — never introspects
    MLX/CoreML objects directly.

    Returns:
        dict with keys:
        - loaded: bool
        - current_model: str | None
        - initialized: bool
        - last_error: str | None
    """
    return {
        "loaded": _lifecycle_state["loaded"],
        "current_model": _lifecycle_state["current_model"],
        "initialized": _lifecycle_state["initialized"],
        "last_error": _lifecycle_state["last_error"],
    }


def ensure_mlx_runtime_initialized() -> bool:
    """
    Sprint 7D: Ensure MLX runtime is properly initialized before model load.

    This is the canonical MLX init call point - uses mlx_cache.init_mlx_buffers()
    as the authority. Call this before the first model load in the lifecycle path.

    Returns:
        True if MLX available and initialized, False otherwise
    """
    # Delegate to canonical mlx_cache authority
    try:
        from ..utils.mlx_cache import init_mlx_buffers
        result = init_mlx_buffers()
        if result:
            logger.info("[LIFECYCLE] MLX runtime initialized via mlx_cache authority")
        return result
    except Exception as e:
        logger.warning(f"[LIFECYCLE] MLX init failed: {e}")
        return _MLX_AVAILABLE_SAFETY


def load_model(
    model: Any,
    model_name: Optional[str] = None,
    tokenizer: Any = None,
    prompt_cache: Any = None,
) -> None:
    """
    Sprint 8Y: Load a model into the lifecycle — idempotent, state-tracked.

    Contract:
    - Double-load of the SAME model is a no-op (does NOT reload).
    - Load of a DIFFERENT model implicitly calls unload_model() first.
    - Updates _lifecycle_state shadow-state.
    - Delegates to engine.load() if available.

    Args:
        model: Model/engine object (or raw model)
        model_name: Human-readable name for the model (used for state tracking)
        tokenizer: Tokenizer object (extracted from engine if needed)
        prompt_cache: Prompt/KV cache (extracted from engine if needed)

    Returns:
        None
    """
    global _lifecycle_state

    # Resolve model name
    resolved_name = model_name
    if resolved_name is None:
        if model is not None and hasattr(model, 'model_name'):
            resolved_name = model.model_name
        elif model is not None and hasattr(model, 'name'):
            resolved_name = model.name
        else:
            resolved_name = type(model).__name__ if model else "unknown"

    # Sprint 8Y Invariant §B.8: double-load same model = no-op
    if _lifecycle_state["loaded"] and _lifecycle_state["current_model"] == resolved_name:
        logger.debug(f"[LIFECYCLE] load_model('{resolved_name}') — already loaded, no-op")
        return

    # If a different model is currently loaded, unload it first
    if _lifecycle_state["loaded"] and _lifecycle_state["current_model"] != resolved_name:
        logger.info(f"[LIFECYCLE] Switching model: {_lifecycle_state['current_model']} → {resolved_name}")
        # Use weak ref to unload the OLD model, not the new one
        old_model = _get_current_model_unsafe()
        if old_model is not None:
            unload_model(model=old_model)

    # Initialize MLX runtime if needed
    mlx_ready = ensure_mlx_runtime_initialized()
    _lifecycle_state["initialized"] = mlx_ready

    # Delegate to engine.load() if available
    if model is not None and hasattr(model, 'load'):
        import inspect
        if inspect.iscoroutinefunction(model.load):
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # F650G H2-FIX: Cannot use run_until_complete in a running loop.
                    # Caller must await the engine.load() themselves. Do NOT claim
                    # loaded=True since the load did NOT actually happen.
                    logger.warning(
                        "[LIFECYCLE] Async load() in running loop — "
                        "caller must await engine.load() directly; "
                        "shadow-state NOT updated (load deferred)"
                    )
                    _lifecycle_state["last_error"] = "async_load_deferred"
                    return
                loop.run_until_complete(model.load())
                _lifecycle_state["loaded"] = True
                _lifecycle_state["current_model"] = resolved_name
                _lifecycle_state["last_error"] = None
                _set_current_model_ref(model)
                logger.info(f"[LIFECYCLE] Engine async load() completed: {resolved_name}")
                return
            except Exception as e:
                logger.warning(f"[LIFECYCLE] Async load failed: {e}")
                _lifecycle_state["last_error"] = str(e)
                return
        else:
            # Sync load
            try:
                model.load()
                _lifecycle_state["loaded"] = True
                _lifecycle_state["current_model"] = resolved_name
                _lifecycle_state["last_error"] = None
                _set_current_model_ref(model)
                logger.info(f"[LIFECYCLE] Engine sync load() completed: {resolved_name}")
                return
            except Exception as e:
                logger.warning(f"[LIFECYCLE] Sync load failed: {e}")
                _lifecycle_state["last_error"] = str(e)
                return

    # No engine.load() — treat as already loaded (raw model)
    _lifecycle_state["loaded"] = True
    _lifecycle_state["current_model"] = resolved_name
    _lifecycle_state["last_error"] = None
    _set_current_model_ref(model)
    logger.info(f"[LIFECYCLE] Model registered: {resolved_name}")


def unload_model(
    model: Any = None,
    tokenizer: Any = None,
    prompt_cache: Any = None,
    aggressive: bool = False
) -> None:
    """
    Sprint 8C: Unload model — delegates to engine.unload() if available (7K SSOT).

    If model has an async unload() method (e.g. Hermes3Engine),
    that method is awaited INLINE (no parallel concerns here).
    Otherwise falls back to legacy direct eviction.

    Canonical 7K order is handled INSIDE engine.unload().
    This function no longer duplicates that order.

    Args:
        model: Model/engine object (or raw model)
        tokenizer: Tokenizer object (extracted from engine if needed)
        prompt_cache: Prompt/KV cache (extracted from engine if needed)
        aggressive: If True, also reduces MLX cache limit temporarily

    Returns:
        None (operace je idempotentní, fail-open)
    """
    # Sprint 8Y §B.7: Early return when nothing is loaded — avoids
    # unnecessary gc.collect() and asyncio.get_event_loop() calls.
    if not _lifecycle_state["loaded"]:
        logger.debug("[LIFECYCLE] unload_model — nothing loaded, no-op")
        return

    # F162F: If model=None but we have a tracked model, use it.
    # This fixes the silent-no-op bug where unload_model(None) was called
    # after a load that didn't pass the model reference.
    if model is None:
        model = _get_current_model_unsafe()
        if model is None:
            # Nothing to unload and no tracked model — clear state and return
            _lifecycle_state["loaded"] = False
            _lifecycle_state["current_model"] = None
            _set_current_model_ref(None)
            return

    # Sprint 8C: Prefer engine.unload() if available — respects 7K SSOT
    if model is not None and hasattr(model, 'unload'):
        import inspect
        if inspect.iscoroutinefunction(model.unload):
            # Sync wrapper for async unload — we don't have loop here
            # This is safe because model_lifecycle is called from sync contexts
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # F650G H3-FIX: Cannot use run_until_complete in a running loop.
                    # Caller must await the engine.unload() themselves. Do NOT claim
                    # unload completed via legacy fallback since that path does NOT
                    # correctly unload async engines — it would lie about shadow-state.
                    logger.warning(
                        "[LIFECYCLE] Async unload() in running loop — "
                        "caller must await engine.unload() directly; "
                        "shadow-state NOT updated (unload deferred)"
                    )
                    return
                loop.run_until_complete(model.unload())
                logger.info("[LIFECYCLE] Engine unload() completed via loop")
                _lifecycle_state["loaded"] = False
                _lifecycle_state["current_model"] = None
                _set_current_model_ref(None)
                return
            except Exception as e:
                logger.warning(f"[LIFECYCLE] Async unload failed: {e}")
                return
        else:
            # Sync unload
            try:
                model.unload()
                logger.info("[LIFECYCLE] Engine sync unload() completed")
                _lifecycle_state["loaded"] = False
                _lifecycle_state["current_model"] = None
                _set_current_model_ref(None)
                return
            except Exception as e:
                logger.warning(f"[LIFECYCLE] Sync unload failed: {e}")
                return

    # Legacy fallback: direct eviction (only for non-Hermes models)
    _unload_model_legacy(model, tokenizer, prompt_cache, aggressive)
    _lifecycle_state["loaded"] = False
    _lifecycle_state["current_model"] = None
    _set_current_model_ref(None)


def _unload_model_legacy(
    model: Any,
    tokenizer: Any,
    prompt_cache: Any,
    aggressive: bool
) -> None:
    """
    Legacy direct eviction — used ONLY for models without unload() method.

    Canonically, Hermes-3 should always use engine.unload() which handles
    all 7K order internally. This function exists for non-engine models
    (raw _model objects, tokenizers) that don't have unload().
    """
    # Extract model from engine if needed
    if model is not None and hasattr(model, '_model'):
        _model = model._model
    else:
        _model = model

    # Extract tokenizer from engine if needed
    if tokenizer is None and model is not None and hasattr(model, '_tokenizer'):
        tokenizer = model._tokenizer

    # Extract prompt_cache from engine if needed
    if prompt_cache is None and model is not None and hasattr(model, '_prompt_cache'):
        prompt_cache = model._prompt_cache
    if prompt_cache is None and model is not None and hasattr(model, '_system_prompt_cache'):
        prompt_cache = model._system_prompt_cache

    try:
        # Krok 1: Evict prompt_cache
        if prompt_cache is not None:
            try:
                del prompt_cache
                logger.debug("[LIFECYCLE] prompt_cache evicted")
            except Exception as e:
                logger.debug(f"[LIFECYCLE] prompt_cache eviction: {e}")
            prompt_cache = None

        # Krok 2: Del model
        if _model is not None:
            try:
                del _model
                logger.debug("[LIFECYCLE] model evicted")
            except Exception as e:
                logger.debug(f"[LIFECYCLE] model eviction: {e}")
            _model = None

        # Krok 3: Del tokenizer
        if tokenizer is not None:
            try:
                del tokenizer
                logger.debug("[LIFECYCLE] tokenizer evicted")
            except Exception as e:
                logger.debug(f"[LIFECYCLE] tokenizer eviction: {e}")
            tokenizer = None

        # Krok 4: gc.collect()
        gc.collect()

        mx = _get_mlx_safe()
        if mx is not None:
            # Krok 5: mx.eval([]) bezprostředně před clear_cache (M1 invariant §F178D)
            try:
                mx.eval([])
            except Exception as e:
                logger.debug(f"[LIFECYCLE] mx.eval([]): {e}")
            # Krok 6: mx.metal.clear_cache()
            try:
                if hasattr(mx.metal, 'clear_cache'):
                    mx.metal.clear_cache()
                elif hasattr(mx, 'clear_cache'):
                    mx.clear_cache()
            except Exception as e:
                logger.debug(f"[LIFECYCLE] clear_cache: {e}")

            # Aggressive: temporarily reduce cache limit
            if aggressive:
                try:
                    if hasattr(mx.metal, 'set_cache_limit'):
                        mx.metal.set_cache_limit(64 * 1024 * 1024)  # 64MB
                        mx.clear_cache()
                        # Restore
                        mx.metal.set_cache_limit(2684354560)  # 2.5GB
                except Exception:
                    pass

        gc.collect()
        logger.info("[LIFECYCLE] Model lifecycle cleanup complete")

    except Exception as e:
        # Fail-open: nikdy nevyhazovat výjimku z lifecycle
        logger.warning(f"[LIFECYCLE] Unload error (non-critical): {e}")
        gc.collect()


def preload_model_hint(model_path: str) -> None:
    """
    Hint pro preload modelu (optimalizace pro budoucí načtení).

    Args:
        model_path: Cesta k modelu

    Note:
        Toto je placeholder pro budoucí implementaci prediktivního preloadu.
        Momentálně jen loguje hint.
    """
    logger.debug(f"[LIFECYCLE] Preload hint: {model_path}")


# =============================================================================
# Sprint 8QC: Structured Generation with Outlines MLX
# =============================================================================

import asyncio
from pathlib import Path

try:
    from ..utils.executors import CPU_EXECUTOR
except Exception:
    import concurrent.futures
    CPU_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="hledac_cpu")


class ModelLifecycle:
    """
    F6.5: Structured-generation sidecar (windup-local).

    This class is a WINDUP-LOCAL sidecar — it is NOT part of the runtime-wide
    model plane. It uses Qwen/SmolLM models (separate from Hermes/ModernBERT/GLiNER).

    Role: Structured-generation only — Outlines MLX constrained generation.
    This class does NOT participate in the runtime-wide model lifecycle.

    3-tier model discovery:
      Tier 1: Qwen3-0.6B
      Tier 2: jakýkoli ≤1B model
      Tier 3: žádný model → structured_generate() vrací None

    OSINTReport je msgspec.Struct — vrací se přímo z Outlines constrained generation.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._model_path: Optional[Path] = None
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Model discovery — 3-tier
    # ------------------------------------------------------------------

    def _discover_model_path(self) -> Optional[Path]:
        """
        3-tier model discovery.

        Tier 1: ~/.cache/huggingface/hub/**/Qwen*0.6B*/config.json
        Tier 2: ~/.cache/huggingface/hub/**/*[05]00M*/config.json nebo *1B*
        Tier 3: žádný model → vrací None
        """
        search_base = Path.home() / ".cache" / "huggingface" / "hub"

        if not search_base.exists():
            return None

        # Tier 1: Qwen3-0.6B
        for config_path in search_base.glob("**/Qwen*0.6B*/config.json"):
            logger.info("[LIFECYCLE] Found Qwen3-0.6B at %s", config_path.parent)
            return config_path.parent

        # Tier 2: jakýkoli ≤1B model
        for pattern in ["**/*0.5B*/config.json", "**/*500M*/config.json", "**/*1B*/config.json"]:
            matches = list(search_base.glob(pattern))
            if matches:
                logger.info("[LIFECYCLE] Found fallback model at %s", matches[0].parent)
                return matches[0].parent

        logger.warning("[LIFECYCLE] No local model found — structured generation disabled")
        return None

    # ------------------------------------------------------------------
    # Lazy load
    # ------------------------------------------------------------------

    async def _ensure_loaded(self) -> tuple[Any, Any, Path | None]:
        """Lazy load s 3-tier fallback. Volá se před každým generate."""
        if self._loaded and self._model is not None:
            return (self._model, self._tokenizer, self._model_path)

        if self._model_path is None:
            self._model_path = self._discover_model_path()

        if self._model_path is None:
            raise RuntimeError("No model available for structured generation")

        mx = _get_mlx_safe()
        if mx is None:
            raise RuntimeError("MLX not available")

        # B.1: mx.metal.cache_limit(2_500_000_000) PŘED load
        if hasattr(mx.metal, "cache_limit"):
            mx.metal.cache_limit(2_500_000_000)

        # B.9: QoS USER_INITIATED
        self._set_qos_user_initiated()

        try:
            import mlx_lm
            model_path_str = str(self._model_path)
            result = mlx_lm.load(model_path_str)
            # mlx_lm.load returns (model, tokenizer) or (model, tokenizer, config)
            if isinstance(result, tuple) and len(result) >= 2:
                self._model, self._tokenizer = result[0], result[1]
            else:
                self._model, self._tokenizer = result, None
            self._loaded = True
            logger.info("[LIFECYCLE] Model loaded: %s", model_path_str)
            assert self._model_path is not None
            return (self._model, self._tokenizer, self._model_path)
        except Exception as e:
            logger.error("[LIFECYCLE] Model load failed: %s", e)
            raise

    # ------------------------------------------------------------------
    # Structured generation — Outlines PRIMÁRNÍ path
    # ------------------------------------------------------------------

    async def structured_generate(
        self,
        prompt: str,
        json_schema: str | None = None,
        system_prompt: str = (
            "You are a cybersecurity analyst. "
            "Extract IOC entities from findings. "
            "Respond with valid JSON matching the schema exactly."
        ),
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> tuple[dict | None, bool] | None:
        """
        Sprint 8TA B.1: Outlines json_schema dict as PRIMARY path.

        Primární: outlines.generate.json s json_schema dict (ne msgspec.Struct)
        Fallback: mlx_lm.generate + regex JSON extract

        Returns:
            (dict | None, outlines_used: bool) — volá se přes CPU_EXECUTOR
        """
        loop = asyncio.get_running_loop()

        # Lazy load
        try:
            model, tokenizer, _model_path = await self._ensure_loaded()
        except RuntimeError as e:
            logger.warning("[LIFECYCLE] structured_generate skipped: %s", e)
            return None

        full_prompt = f"<|system|>{system_prompt}<|user|>{prompt}<|assistant|>"

        # Sprint 8TA B.1: PRIMÁRNÍ PATH — Outlines json_schema dict
        if json_schema is not None:
            try:
                import outlines

                def _run_constrained_generation() -> tuple[dict | None, bool]:
                    outlines_model = self._load_outlines_model(model, tokenizer)
                    generator = outlines.generate.json(outlines_model, json_schema)
                    result = generator(full_prompt, max_tokens=max_tokens, temperature=temperature)
                    if isinstance(result, dict):
                        return (result, True)
                    # Try parse if result is not dict
                    try:
                        import msgspec
                        parsed = msgspec.json.decode(result.encode()) if isinstance(result, str) else result
                        return (parsed if isinstance(parsed, dict) else None, True)
                    except Exception:
                        return (None, True)
                return await loop.run_in_executor(CPU_EXECUTOR, _run_constrained_generation)
            except Exception as outlines_err:
                logger.warning("[LIFECYCLE] Outlines json_schema failed (%s), fallback to mlx_lm", outlines_err)

        # Sprint 8TA B.1: FALLBACK — mlx_lm.generate + regex JSON extract
        try:
            import mlx_lm
            import re as _re

            if hasattr(tokenizer, "apply_chat_template"):
                m = _re.search(r"<\|system\|>(.*?)<\|user\|>(.*?)<\|assistant\|>", full_prompt, _re.DOTALL)
                if m:
                    system_text = m.group(1).strip()
                    user_text = m.group(2).strip()
                else:
                    system_text = "You are a cybersecurity analyst. Respond with JSON only."
                    user_text = full_prompt
                messages = [
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text},
                ]
                formatted = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            else:
                formatted = full_prompt

            def _mlx_generate_raw() -> str:
                result = ""
                try:
                    result = mlx_lm.generate(model, tokenizer, prompt=formatted, max_tokens=max_tokens, verbose=False)
                finally:
                    # Sprint 8UD B.2: Clear MLX Metal cache after inference
                    try:
                        import mlx.core as _mx
                        if _mx.metal.is_available():
                            _mx.metal.clear_cache()
                    except Exception:
                        pass  # Non-fatal
                return result

            raw = await loop.run_in_executor(CPU_EXECUTOR, _mlx_generate_raw)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                clean = raw[start:end].strip().lstrip("`").strip()
                try:
                    import msgspec
                    parsed = msgspec.json.decode(clean.encode())
                    return (parsed if isinstance(parsed, dict) else None, False)
                except Exception:
                    pass
            return (None, False)
        except Exception as fallback_err:
            logger.warning("[LIFECYCLE] Fallback mlx_lm failed (%s)", fallback_err)
            return (None, False)

    def _load_outlines_model(self, model: Any, tokenizer: Any) -> Any:
        """Load Outlines MLX model with (model, tokenizer)."""
        from outlines import from_mlxlm
        return from_mlxlm(model, tokenizer)

    # ------------------------------------------------------------------
    # Unload
    # ------------------------------------------------------------------

    async def unload(self) -> None:
        """
        B.4: Unload po syntéze — přesné pořadí:
        1. mx.eval([]) + mx.metal.clear_cache()
        2. del self._model + del self._tokenizer
        3. gc.collect()
        4. B.9: set_thread_qos(BACKGROUND)
        """
        if not self._loaded:
            return

        mx = _get_mlx_safe()

        # 1. mx.eval([]) + clear cache
        if mx is not None:
            try:
                mx.eval([])
            except Exception:
                pass
            try:
                if hasattr(mx.metal, "clear_cache"):
                    mx.metal.clear_cache()
            except Exception:
                pass

        # 2. Evict model/tokenizer refs
        self._model = None
        self._tokenizer = None
        self._loaded = False

        # 3. gc.collect()
        gc.collect()

        # 4. B.9: QoS BACKGROUND
        self._set_qos_background()

        logger.info("[LIFECYCLE] Model unloaded after structured generation")

    # ------------------------------------------------------------------
    # QoS helpers (Darwin only — platform-specific, fail-open)
    # ------------------------------------------------------------------

    def _set_qos_user_initiated(self) -> None:
        """B.9: Set thread QoS to USER_INITIATED before load. Fail-open."""
        try:
            import os
            os.setpriority(os.PRIO_PROCESS, 0, -5)  # HIGH priority
        except Exception:
            pass

    def _set_qos_background(self) -> None:
        """B.9: Set thread QoS to BACKGROUND after unload. Fail-open."""
        try:
            import os
            os.setpriority(os.PRIO_PROCESS, 0, 10)  # LOW priority
        except Exception:
            pass
