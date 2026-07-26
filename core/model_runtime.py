"""
core/model_runtime.py — Windup-Local Structured-Generation Sidecar
==============================================================

F6.5: Structured-generation sidecar for sprint windup phase.

Migrated from brain/model_lifecycle.py (F350M-R W6 refactor).
This module is the canonical home for the windup-local ModelLifecycle class.
It is isolated from the runtime-wide model plane (Hermes/ModernBERT/GLiNER)
and uses Qwen/SmolLM models for structured generation only.

Consumers:
  - runtime/scheduler_v2/acquisition.py:_run_synthesis_sidecar()
  - brain/synthesis_runner.py:SynthesisRunner
  - pipeline/live_public_pipeline.py (synthesis sidecar)

迁移 (F350M-R):
  class ModelLifecycle přesunuto z brain/model_lifecycle.py do core/model_runtime.py.
  brain/model_lifecycle.py nyní obsahuje pouze roles 1-4 (emergency seam, MLX helpers, shadow-state).
"""

import asyncio
import gc
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers used by ModelLifecycle
# ---------------------------------------------------------------------------

def _get_mlx_safe() -> Any:
    """Return mlx.core if available, else None."""
    try:
        import mlx.core as _mx
        return _mx
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ModelLifecycle — windup-local structured-generation sidecar
# ---------------------------------------------------------------------------

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

    # F314-4: __slots__ for M1 8GB RAM optimization
    __slots__ = ('_model', '_tokenizer', '_model_path', '_loaded')

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._model_path: Path | None = None
        self._loaded: bool = False

    # ------------------------------------------------------------------
    # Model discovery — 3-tier
    # ------------------------------------------------------------------

    def _discover_model_path(self) -> Path | None:
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
    # Memory pre-flight check (E4: M1 8GB UMA safety)
    # ------------------------------------------------------------------

    def _check_mlx_memory_before_load(self) -> None:
        """
        Pre-flight memory check before mlx_lm.load().

        ISSUE E4: MLX model loading without memory check risks OOM on M1 8GB UMA.
        This method checks:
          1. HLEDAC_MLX_MAX_MEMORY ceiling (default 3GB)
          2. Available system memory via os.proc_available_memory() or psutil
          3. Rust backend check_available_memory() if available

        Raises RuntimeError if insufficient memory — prevents OOM crash.
        """
        from core.env_config import ENV

        max_memory_bytes = ENV.get_memory_bytes("HLEDAC_MLX_MAX_MEMORY", default="3GB")
        if max_memory_bytes <= 0:
            max_memory_bytes = 3 * 1024 * 1024 * 1024  # 3GB default floor

        available_bytes = 0
        # Try os.proc_available_memory first (Python 3.11+)
        try:
            if hasattr(os, "proc_available_memory"):
                available_bytes = os.proc_available_memory()
            else:
                import psutil
                available_bytes = psutil.virtual_memory().available
        except Exception:
            pass

        # Try Rust backend for sysctl HW_MEMSIZE check
        # check_available_memory_py is registered directly on the Rust module (rust.raw)
        try:
            from core.rust_backend import rust
            if rust.is_available:
                raw = rust.raw
                if hasattr(raw, "check_available_memory"):
                    allowed, _avail, reason = raw.check_available_memory(
                        max_memory_bytes, available_bytes
                    )
                    if not allowed:
                        raise RuntimeError(f"[E4-MEMORY] MLX model load rejected: {reason}")
                    return
        except RuntimeError:
            raise  # Re-raise our own RuntimeError
        except Exception:
            pass  # Fall through to Python-side check

        # Python-side fallback check
        if available_bytes < max_memory_bytes:
            raise RuntimeError(
                f"[E4-MEMORY] Insufficient memory for MLX load: "
                f"available={available_bytes} < required={max_memory_bytes}"
            )

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

        # B.9: QoS USER_INITIATED
        self._set_qos_user_initiated()

        # E4: Pre-flight memory check before mlx_lm.load()
        # Raises RuntimeError if insufficient memory — prevents OOM crash on M1 8GB
        self._check_mlx_memory_before_load()

        try:
            import mlx_lm

            # Issue #21 FIX: Dynamic Metal cache limit PŘED load (ne PO)
            # MLX alokuje Metal cache při load() na základě aktivního limitu.
            # Nastavení PO load() nepomůže - model už má přidělenou paměť.
            mx = _get_mlx_safe()
            if mx is not None and hasattr(mx, "metal") and hasattr(mx.metal, "set_cache_limit"):
                from utils.mlx_cache import get_dynamic_metal_cache_limit

                mx.metal.set_cache_limit(get_dynamic_metal_cache_limit())

            model_path_str = str(self._model_path)
            # C2-FIX: mlx_lm.load() is blocking I/O (disk read + Metal kernel compilation).
            # Wrapped in asyncio.to_thread() to avoid blocking the event loop.
            result = await asyncio.to_thread(mlx_lm.load, model_path_str)
            # mlx_lm.load returns (model, tokenizer) or (model, tokenizer, config)
            if isinstance(result, tuple) and len(result) >= 2:
                self._model, self._tokenizer = result[0], result[1]
            else:
                self._model, self._tokenizer = result, None
            # Sprint OPT-3: Half-precision optimizer state — convert model to float16
            # after load for 2× memory savings (2GB → 1GB for 3B model weights).
            # Model weights are 4-bit quantized on disk; during inference they are
            # dequantized to float16 internally by MLX — keeping the model in float16
            # reduces the dequantization scratch space by 2×.
            try:
                if os.getenv("HLEDAC_HALF_PRECISION", "1") != "0":
                    self._model.set_dtype(mx.float16)
                    logger.info("[LIFECYCLE] Model dtype set to float16 (half precision)")
            except Exception as e:
                logger.warning("[LIFECYCLE] Could not set float16 dtype: %s", e)
            self._loaded = True
            logger.info("[LIFECYCLE] Model loaded: %s", model_path_str)

            # Sprint #8: MLX Metal pre-warm — allocate 48 MB buffer pool AFTER load
            # to avoid first-inference allocation latency. MetalBufferPool is the
            # internal MLX allocator; priming it after model load forces the Metal
            # heap to map 48 MB of unified memory pages before any real inference runs,
            # eliminating first-call latency. Safe: MLX UMA shares with CPU, overall
            # M1 8GB budget remains under 6.25 GB ceiling.
            try:
                _warm_buffer = mx.zeros([12_000_000], dtype=mx.float32)  # 48 MB
                mx.eval(_warm_buffer)  # Force allocation — MLX lazy evaluation requires barrier
                del _warm_buffer  # release immediately; page mapping persists
                logger.debug("[LIFECYCLE] MLX Metal pre-warmed (48 MB buffer)")
            except Exception as e:
                logger.debug("[LIFECYCLE] MLX Metal pre-warm skipped: %s", e)

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
                from hledac.universal.runtime.worker_pool import get_rust_pool
                pool = get_rust_pool("cpu")
                return await pool.submit(_run_constrained_generation)
            except Exception as outlines_err:
                logger.warning("[LIFECYCLE] Outlines json_schema failed (%s), fallback to mlx_lm", outlines_err)

        # Sprint 8TA B.1: FALLBACK — mlx_lm.generate + regex JSON extract
        try:
            import re as _re

            import mlx_lm

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
                # L-01: Globální MLX Metal lock — serializuje všechny mlx_lm.generate() volání
                from hledac.universal.core.mlx_inference_lock import _get_mlx_inference_lock

                _lock = _get_mlx_inference_lock()
                with _lock:
                    gen_result: str = ""
                    try:
                        gen_result = mlx_lm.generate(
                            model, tokenizer,
                            prompt=formatted,
                            max_tokens=max_tokens,
                            kv_bits=4,          # F179C: KV cache 4-bit quantization (M1 8GB RAM budget)
                            max_kv_size=8192,   # F179C: KV cache size cap
                            verbose=False,
                        )
                    finally:
                        # F179C: mx.eval([]) + gc.collect() + clear_cache (správné pořadí dle moe_router.py)
                        try:
                            import mlx.core as _mx
                            if hasattr(_mx, "eval"):
                                _mx.eval([])  # 1. settle lazy eval
                            gc.collect()       # 2. reclaim Python memory BEFORE clear_cache
                            if hasattr(_mx, "clear_cache"):
                                _mx.clear_cache()  # 3. clear Metal cache
                        except Exception:  # noqa: BLE001
                            pass  # noqa: BLE001  # Non-fatal
                return gen_result

            pool = get_rust_pool("cpu")
            raw = await pool.submit(_mlx_generate_raw)
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                clean = raw[start:end].strip().lstrip("`").strip()
                try:
                    import msgspec
                    parsed = msgspec.json.decode(clean.encode())
                    return (parsed if isinstance(parsed, dict) else None, False)
                except Exception:  # noqa: BLE001
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

        # 1. mx.eval([]) + clear cache — F266 METAL LEAK FIX: modern-first
        if mx is not None:
            try:
                mx.eval([])
            except Exception:  # noqa: BLE001
                pass
            try:
                if hasattr(mx, "clear_cache"):
                    mx.clear_cache()
            except Exception:  # noqa: BLE001
                pass

        # 2. Evict model/tokenizer refs
        self._model = None
        self._tokenizer = None
        self._loaded = False

        # 3. gc.freeze() — M1-safe bez stop-the-world
        try:
            gc.freeze()
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Python <3.12

        # 4. B.9: QoS BACKGROUND
        self._set_qos_background()

        logger.info("[LIFECYCLE] Model unloaded after structured generation")

    # ------------------------------------------------------------------
    # QoS helpers (Darwin only — platform-specific, fail-open)
    # ------------------------------------------------------------------

    def _set_qos_user_initiated(self) -> None:
        """B.9: Set thread QoS to USER_INITIATED before load. Fail-open."""
        try:
            os.setpriority(os.PRIO_PROCESS, 0, -5)  # HIGH priority
        except Exception:  # noqa: BLE001
            pass

    def _set_qos_background(self) -> None:
        """B.9: Set thread QoS to BACKGROUND after unload. Fail-open."""
        try:
            os.setpriority(os.PRIO_PROCESS, 0, 10)  # LOW priority
        except Exception:  # noqa: BLE001
            pass
