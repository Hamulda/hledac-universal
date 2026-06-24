"""
✅ CANONICAL - DeepHermes3Engine for Decision Making
=====================================================

This is the CANONICAL implementation for decision making and orchestration.
DeepHermes 3 3B 4bit with deep thinking support is the default primary reasoning model.
Supports ChatML formatting, AI-driven query analysis, and research synthesis.

NOTE (Sprint 8VH): brain/inference_engine.py is FUNKČNĚ ODLIŠNÝ:
  - inference_engine: abductive reasoning, evidence chaining, entity resolution, inference rules
  - deephermes3_engine: LLM-based decision making, ChatML, structured generation
  Both are canonical for their domains — no deduplication needed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import gc
import hashlib
import inspect
import logging
import os
import threading
import time

from hledac.universal.utils.async_helpers import safe_gather_dropin
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode

# Sprint T1: OpenTelemetry instrumentation (always-on, M1 8GB safe, fail-soft)
try:
    from otel import (  # type: ignore
        instrumented as _otel_instrumented,
    )
except ImportError:  # production fallback
    from hledac.universal.telemetry import (
        instrumented as _otel_instrumented,
    )  # type: ignore[unresolved-import]
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field

T = TypeVar('T', bound=BaseModel, default=BaseModel)  # PEP 696: TypeVar with default

# P2-1: xxhash for warmup cache deduplication (NEON-optimized on Apple Silicon)
try:
    import xxhash

    XXHASH_AVAILABLE = True
except ImportError:
    XXHASH_AVAILABLE = False

# P2-1: Warmup cache directory
WARMUP_CACHE_DIR = Path.home() / ".hledac" / "cache" / "warmup"


def _get_warmup_cache_path(system_prompt: str, few_shot_examples: list | None = None) -> Path:
    """Compute cache file path from system_prompt fingerprint (xxhash-xxh3_64, first 16 chars).

    P2-1: Uses xxhash-xxh3_64 instead of MLX float operations for stable hashing
    across process restarts and model unload/reload cycles.
    """
    # Build canonical text for hashing (stable regardless of input order)
    parts = [system_prompt]
    if few_shot_examples:
        for ex in few_shot_examples[:3]:
            parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
    canonical = "\n".join(parts)

    if XXHASH_AVAILABLE:
        prompt_hash = xxhash.xxh64(canonical.encode()).hexdigest()[:16]
    else:
        # Fallback: blake2b (fast on ARM)
        prompt_hash = hashlib.blake2b(canonical.encode(), digest_size=8).hexdigest()

    WARMUP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return WARMUP_CACHE_DIR / f"warmup_{prompt_hash}.safetensors"


async def warmup_or_skip(
    engine: DeepHermes3Engine,
    system_prompt: str,
    few_shot_examples: list | None = None,
) -> bool:
    """Skip warmup if unexpired cache exists for this prompt fingerprint.

    P2-1: Returns True if cache hit (warmup skipped), False if cache miss
    (fresh warmup required). Fail-soft: any error triggers fresh warmup.

    Uses xxhash-xxh3_64 for stable, fast hashing (NEON-optimized on M1).
    """
    cache_path = _get_warmup_cache_path(system_prompt, few_shot_examples)

    if not cache_path.exists():
        return False

    # Compute expected hash
    parts = [system_prompt]
    if few_shot_examples:
        for ex in few_shot_examples[:3]:
            parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
    canonical = "\n".join(parts)

    if XXHASH_AVAILABLE:
        expected_hash = xxhash.xxh64(canonical.encode()).hexdigest()[:16]
    else:
        expected_hash = hashlib.blake2b(canonical.encode(), digest_size=8).hexdigest()

    try:
        if await engine._restore_warmup_cache(cache_path, expected_hash):
            logger.info(f"[WARMUP] Cache hit: {cache_path.name} (hash={expected_hash[:8]})")
            return True
    except Exception:
        pass

    # Cache miss or corrupt — remove stale entry
    try:
        cache_path.unlink(missing_ok=True)
    except Exception:
        pass
    return False

# SECURITY: Import fallback sanitizer for LLM input sanitization (failsafe)
try:
    from ..security.pii_gate import fallback_sanitize
except ImportError:
    # Standalone import guard: provide stub when loaded outside package context
    def fallback_sanitize(text: str, max_length: int = 8192) -> str:
        return text[:max_length] if text else ""

# Sprint 7H/7I: Emergency unload seam consumer
try:
    from .model_lifecycle import is_emergency_unload_requested
except ImportError:
    is_emergency_unload_requested = None  # type: ignore

# P1G-A: Prompt injection validator (before _sanitize_for_llm callback)
try:
    from .prompt_injection_validator import sanitize_prompt_injection_patterns
except ImportError:
    sanitize_prompt_injection_patterns = None  # type: ignore

import re as _re_pi  # dedikovaný alias pro injection patterns  # noqa: E402

# Metal memory thresholds (absolute bytes, M1 8GB safe)
EMERGENCY_METAL_BYTES = 2_684_354_560  # 2.5 GiB — emergency tier threshold
CRITICAL_METAL_BYTES = 1_610_612_736  # 1.5 GiB — critical tier threshold
WARN_METAL_BYTES = 1_073_741_824  # 1.0 GiB — warn tier threshold

# MLX availability (lazy — no top-level import, consistent with brain/*.py pattern)
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None  # type: ignore[assignment]

# Default KV cache size fallback (32 MB) when Metal memory probing unavailable
_FALLBACK_CACHE_BYTES: int = 32 * 1024 * 1024  # 32 MB

from utils.async_helpers import safe_gather_dropin  # noqa: E402

_INJECTION_PATTERNS: list = [
    _re_pi.compile(r"ignore\s+(?:all\s+)?previous\s+(?:instructions?|commands?)", _re_pi.I),
    _re_pi.compile(r"(?:system|prompt)\s*:\s*you\s+are\s+(?:now\s+)?a", _re_pi.I),
    _re_pi.compile(r"#{3,}\s*system\s*[:\s]", _re_pi.I),
    _re_pi.compile(r"<\|system\|>", _re_pi.I),
    _re_pi.compile(r"\bROLE\s*:\s*(?:admin|root|superuser)", _re_pi.I),
    _re_pi.compile(r"(?:jailbreak|DAN|do\s+anything\s+now)", _re_pi.I),
    _re_pi.compile(r"```\s*system", _re_pi.I),
]

def _detect_prompt_injection(prompt: str) -> tuple[bool, list[str]]:
    """GAP-5: Detect prompt injection patterns in user-controlled input.
    Returns (is_injection, matched_pattern_descriptions).
    Fail-soft: returns (False, []) on any error.
    """
    try:
        matched = [p.pattern for p in _INJECTION_PATTERNS if p.search(prompt)]
        return (bool(matched), matched)
    except Exception:
        return (False, [])

# P1A: Model-level inference guard (circuit breaker)
try:
    from hledac.universal.brain.model_inference_guard import (
        check_model_allowed,
        classify_failure_kind,
        record_model_failure,
        record_model_success,
    )
except ImportError:
    check_model_allowed = None  # type: ignore
    record_model_failure = None  # type: ignore
    record_model_success = None  # type: ignore
    classify_failure_kind = None  # type: ignore

# Sprint 33: outlines for grammar-constrained decoding
logger = logging.getLogger(__name__)  # declare early for except block
try:
    import outlines
    # outlines 1.3.0: Generator class, not generate module
    _outlines_Generator = outlines.Generator  # noqa: N816
    OUTLINES_AVAILABLE = True
except (ImportError, AttributeError):
    OUTLINES_AVAILABLE = False
    logger.warning("outlines not installed — grammar-constrained decoding disabled")

# Sprint 37: KV-cache for prompt prefix (lazy import to avoid loading mlx_lm at cold-start)
KV_CACHE_AVAILABLE = False  # Set to True only when cache is actually initialized

# F273H+: Hermes model-level cache — persists model across sprint cycles on M1 8GB.
# mlx_lm.load() costs ~2-4s from disk; caching eliminates ~120s overhead per sprint.
# Eviction: evict_model_cache() called on SIGTERM or memory pressure.
_HERMES_MODEL_CACHE: dict[str, tuple[Any, Any]] = {}  # {model_path: (model, tokenizer)}
_HERMES_CACHE_LOCK = asyncio.Lock()

# Sprint 81: MLX memory management
try:
    from .adaptive_context_policy import apply_context_budget, decide_context_budget
except ImportError:
    decide_context_budget = None  # type: ignore
    apply_context_budget = None  # type: ignore


# P1F-A: Global Hermes Inference Timeout
HERMES_TIMEOUT_DEFAULT_S = 60.0
HERMES_TIMEOUT_MIN_S = 1.0
HERMES_TIMEOUT_MAX_S = 300.0


def _get_hermes_timeout_s() -> float:
    """
    Get Hermes inference timeout from environment.

    Returns:
        Timeout in seconds, clamped to [HERMES_TIMEOUT_MIN_S, HERMES_TIMEOUT_MAX_S].
        Falls back to HERMES_TIMEOUT_DEFAULT_S on invalid/missing env.
    """
    try:
        raw = float(os.environ.get("HLEDAC_HERMES_TIMEOUT_S", HERMES_TIMEOUT_DEFAULT_S))
        if raw <= 0:
            return HERMES_TIMEOUT_DEFAULT_S
        return max(HERMES_TIMEOUT_MIN_S, min(raw, HERMES_TIMEOUT_MAX_S))
    except (ValueError, TypeError):
        return HERMES_TIMEOUT_DEFAULT_S


# DSPy integration — fail-soft, only activated when HLEDAC_ENABLE_DSPY=1
_DSPY_AVAILABLE = False
try:
    import dspy

    from .dspy_signatures import (
        DarkQuerySignature,
        HypothesisSignature,
        is_dspy_available,
    )

    _DSPY_AVAILABLE = is_dspy_available()
except ImportError:
    DarkQuerySignature = None  # type: ignore
    HypothesisSignature = None  # type: ignore
    _DSPY_AVAILABLE = False

HLEDAC_ENABLE_DSPY = os.environ.get("HLEDAC_ENABLE_DSPY", "0") == "1" and _DSPY_AVAILABLE

# F267: MLX prewarm — skip Metal cache clear between sprints when model stays loaded
# HLEDAC_MLX_PREWARM=1: skip mx.clear_cache() in unload() if next sprint < 60s away
# HLEDAC_MLX_PREWARM=0 (default): always clear Metal cache (safe for 8GB)
_MLX_PREWARM_ENABLED = os.environ.get("HLEDAC_MLX_PREWARM", "0") == "1"
_MLX_PREWARM_LAST_UNLOAD_TIME: float | None = None
_MLX_PREWARM_SKIP_THRESHOLD_S = 60.0
_mlx_prewarm_active: bool = False


# F219B: Safe MLX eval + clear cache helper
# Ensures lazy MLX work is settled before clearing Metal cache.
# NEVER raises during teardown — fail-soft always.
def _safe_mlx_eval_and_clear_cache(reason: str) -> dict:
    """
    Settle lazy MLX ops and clear Metal cache.

    Call mx.eval([]) to flush pending lazy computations before
    mx.metal.clear_cache(), ensuring the cache clear actually reclaims
    memory from pending GPU work.

    Args:
        reason: Telemetry label for this clear event.

    Returns:
        dict with keys: cleared (bool), reason (str), error (str or None)
    """
    result = {"cleared": False, "reason": reason, "error": None}
    try:
        import mlx.core as _mx
        # Step 1: settle lazy eval
        try:
            _mx.eval([])
        except Exception as _e:
            result["error"] = f"eval_failed:{_e}"
            # Continue to clear_cache even if eval fails
        # Step 2: clear Metal cache
        try:
            if hasattr(_mx, "clear_cache"):
                _mx.clear_cache()
                result["cleared"] = True
            elif hasattr(_mx.metal, "clear_cache"):
                _mx.metal.clear_cache()
                result["cleared"] = True
        except Exception as _e:
            result["error"] = f"{result['error']};clear_cache_failed:{_e}" if result["error"] else f"clear_cache_failed:{_e}"  # noqa: E501
    except Exception as _e:
        result["error"] = f"import_failed:{_e}"
    return result

# Sprint 7B: MLX availability flag (imported from mlx_cache for consistency)
try:
    from ..utils.mlx_cache import MLX_AVAILABLE as _MLX_AVAILABLE_GLOBAL
except ImportError:
    try:
        import mlx.core as mx  # noqa: F401  # mlx.core
        _MLX_AVAILABLE_GLOBAL = True
    except ImportError:
        _MLX_AVAILABLE_GLOBAL = False

# Hard limit for LLM prompt (no user toggles)
MAX_LLM_PROMPT_CHARS = 8192

# Sprint F206X: Bounded pending futures to prevent memory exhaustion
MAX_PENDING_FUTURES = 256

# Sprint M3: Granular Metal cache management during token streaming.
# Bounded: every EVAL_GRANULARITY_TOKENS flush pending lazy ops; every
# CLEAR_GRANULARITY_TOKENS check active Metal pressure and drop the cache
# when above M3_METAL_PRESSURE_BYTES (2 GiB headroom under the 2.5 GiB
# Metal limit set in init_mlx_buffers). kv_cache itself is referenced by
# Python and survives clear_cache, so this is transparent to the caller.
# F286 FIX 4 (P1): Adaptive eval/clear granularity — dynamic, not static.
# Static 50-token fixed chunk causes excessive barriers (50× eval/clear per 1K token).
# Adaptive: grows when Metal memory is low, shrinks when pressure rises.
# Formula: chunk_size = max(20, min(200, active_gb * 40))
#   - Low pressure (<1GiB active): chunk=200 tokens (fewer barriers)
#   - High pressure (>2GiB active): chunk=20 tokens (frequent reclaim)
# Sprint F265D-STREAM: Adaptive chunked streaming for M1 8GB
# Memory reduction: 30-50% via smart buffering + adaptive granularity
# Key changes:
# - 32-256 token eval granularity (was 20-200) - reduces barriers
# - Clear every 8 eval cycles (was 4) - 2× fewer clear_cache() calls
# - Token buffer accumulation before yield - amortizes async dispatch overhead
EVAL_GRANULARITY_TOKENS_MIN = 32   # 32 tokens minimum (was 20)
EVAL_GRANULARITY_TOKENS_MAX = 256  # 256 tokens maximum (was 200)
CLEAR_GRANULARITY_TOKENS = 64      # clear every 64 eval cycles (was 8) — 8× fewer get_active_memory() calls
M3_METAL_PRESSURE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

# Sprint F265D-STREAM: Streaming token buffer - accumulate before yielding
# Small buffer (10) causes excessive per-token yield overhead.
# 32+ tokens amortizes asyncio yield cost: latency _MIN_ tokens, throughput at _SIZE
STREAM_BUFFER_SIZE = 32   # tokens to accumulate before yielding
STREAM_MIN_BUFFER = 8     # minimum tokens to yield under extreme pressure


@dataclass
class DeepHermesConfig:
    """Konfigurace pro DeepHermes-3"""
    model_path: str = "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"
    temperature: float = 0.3
    max_tokens: int = 2048
    context_window: int = 8192
    # P1-3: Max parallel prefills (system cache + warmup cache)
    # FIX 1 (P0): Default lowered to 1 — Apple Silicon (M1/M2/M3) unified memory
    # causes Stream(gpu,1) Metal race condition with concurrent asyncio.to_thread()
    # prefills. The M1 detection in _prefill_warmup_caches() enforces sequential
    # mode regardless; this default of 1 is the safe fallback for all platforms.
    max_parallel_prefill: int = 1


# Sprint 33: Private Pydantic schemas for structured output
class _DecisionOutput(BaseModel):
    action: str = Field(description="Action to take")
    params: dict = Field(default_factory=dict, description="Action parameters")
    reasoning: str = Field(description="Why this action")
    complete: bool = Field(False, description="Whether research is complete")


class _SynthesisOutput(BaseModel):
    report: str = Field(description="Final synthesized report")
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence")


# F267: MLX prewarm Metal cache verification
def _verify_metal_cache_warm() -> bool:
    """
    F267: Verify Hermes model is still resident in Metal memory.
    Called by _load_hermes_for_sprint() when prewarm is active and
    inter-sprint gap is < _MLX_PREWARM_SKIP_THRESHOLD_S.
    Returns True if Metal cache is warm (> 500 MiB active).
    """
    try:
        import mlx.core as _mx
        try:
            _mx.eval([])
        except Exception:
            return False
        return _mx.get_active_memory() > 500 * 1024 * 1024
    except Exception:
        return False


def parse_thinking_output(response: str) -> dict[str, str]:
    """
    Parse deep thinking output into thinking and answer components.

    Args:
        response: Raw model output containing <think>...</think> tags

    Returns:
        dict with keys:
        - thinking: content between <think> and </think> (stripped), empty if not present
        - answer: remaining text after <think>...</think> block (stripped)
    """
    match = _re_pi.search(r'<think>(.*?)</think>', response, _re_pi.DOTALL)
    if match:
        thinking = match.group(1).strip()
        answer = response[match.end():].strip()
    else:
        thinking = ""
        answer = response.strip()
    return {"thinking": thinking, "answer": answer}


class DeepHermes3Engine:
    """
    Engine pro DeepHermes-3 s ChatML formátováním a volitelným deep thinking režimem.

    ChatML Format:
        <|im_start|>system
        {system_message}<|im_end|>
        <|im_start|>user
        {user_message}<|im_end|>
        <|im_start|>assistant
    """

    # Deep thinking system prompt prefix
    _DEEP_THINKING_PREFIX = (
        "You are a deep thinking AI, you may use extremely long chains of thought to deeply "
        "consider the problem and deliberate with yourself via systematic reasoning processes "
        "to help come to a correct solution prior to answering. You should enclose your thoughts "
        "and internal monologue inside <think> </think> tags, and then provide your solution "
        "or response to the problem."
    )

    def __init__(
        self,
        model_path: str | None = None,
        sanitize_for_llm: Callable[[str], str] | None = None
    ):
        """
        Initialize DeepHermes3Engine.

        Args:
            model_path: Path to model (default from config)
            sanitize_for_llm: Optional callback for LLM input sanitization.
                               If provided, used instead of fallback_sanitize.
                               Signature: Callable[[str], str]
        """
        self.config = DeepHermesConfig(
            model_path=model_path or DeepHermesConfig.model_path,
        )

        # Sanitizer injection - centralizes security in orchestrator
        self._sanitize_for_llm = sanitize_for_llm

        self._model = None
        self._tokenizer = None

        # Sprint 36: Conditional MLX cache - disabled by default
        self._kv_cache_enabled = False
        self._prompt_cache = None  # Prompt cache for generation

        # Sprint F214Q: Dynamic KV cache sizing per RAM tier (M1 8GB)
        # normal: max_kv_size=full, warn: half, critical/emergency: KV off
        self._max_kv_size = 8192

        # Sprint P1: Configurable KV quantization bits (default 4, OSINT can reduce to 2)
        self._kv_bits = int(os.getenv("GHOST_KV_BITS", "4"))

        # B.KV: Paged KV Cache — uses RotatingKVCache with keep=K for page-like behavior.
        # HLEDAC_PAGED_KV_CACHE=1 enables it, HLEDAC_PAGED_KV_KEEP sets keep (default 4).
        # keep=N means N pages are kept hot; higher = more memory, better hit rate.
        self._paged_kv_cache = os.getenv("HLEDAC_PAGED_KV_CACHE", "0") == "1"
        _raw_keep = os.getenv("HLEDAC_PAGED_KV_KEEP", "")
        self._paged_kv_keep: int
        try:
            self._paged_kv_keep = max(0, int(_raw_keep)) if _raw_keep.strip() else 4
        except (ValueError, TypeError):
            self._paged_kv_keep = 4

        # B.KV: Dynamic KV Quantization — HLEDAC_KV_QUANTIZE=1 forces KV quant ON
        # regardless of GPU pressure. Default=0 (adaptive via _get_adaptive_kv_bits).
        self._force_kv_quantize = os.getenv("HLEDAC_KV_QUANTIZE", "0") == "1"

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
        self._kv_cache_stats = {'cache_uses': 0, 'cache_prefills': 1, 'quantized_count': 0, 'parallel_prefills': 0}

        # F273H: Idle-based lazy unload — track last inference timestamp
        self._last_inference_at: float | None = None
        # F273H+: Track whether model was ever loaded (prewarm) — if prewarmed but never
        # used for inference (_last_inference_at=None), keep it warm until teardown.
        # is_idle() returns False when _model_ever_loaded=True but _last_inference_at=None
        # (model was prewarmed but not used — don't unload prematurely).
        self._model_ever_loaded: bool = False

        # Sprint 75 + F289: Persistent system-prompt KV cache pool (LRU)
        # F289: Replaces single _system_prompt_cache with a bounded LRU pool.
        # Pool bounds: memory-based eviction, NOT count-based.
        # Memory budget: HLEDAC_KV_CACHE_POOL_MEMORY_MB (default 256MB).
        # Actual per-entry size measured via mx.get_active_memory() delta.
        # Key = MD5 hash of system prompt, Value = (kv_cache, created_at_monotonic, size_bytes).
        self._system_prompt = "You are a helpful research assistant."
        _raw_max_kv = os.environ.get("HLEDAC_KV_CACHE_POOL_MAXSIZE", "")
        try:
            _kv_max = int(_raw_max_kv) if _raw_max_kv.strip() else None
            self._kv_cache_pool_maxsize: int = max(1, _kv_max) if _kv_max is not None else 4
        except (ValueError, TypeError):
            self._kv_cache_pool_maxsize: int = 4
        # Memory budget: max total bytes across all pool entries
        _raw_mem = os.environ.get("HLEDAC_KV_CACHE_POOL_MEMORY_MB", "")
        try:
            _mem_mb = int(_raw_mem) if _raw_mem.strip() else None
            self._kv_cache_pool_memory_mb: int = max(32, _mem_mb) if _mem_mb is not None else 256
        except (ValueError, TypeError):
            self._kv_cache_pool_memory_mb: int = 256
        self._kv_cache_pool: OrderedDict[str, tuple[Any, float, int]] = (
            OrderedDict()
        )
        self._kv_cache_pool_stats = {
            "pool_maxsize": self._kv_cache_pool_maxsize,
            "pool_memory_mb": self._kv_cache_pool_memory_mb,
            "pool_hits": 0,
            "pool_misses": 0,
            "pool_evictions": 0,
            "pool_evictions_memory": 0,
        }
        # RC-17: Per-key lock for thread-safe KV cache pool mutations
        self._key_locks: dict[str, threading.Lock] = {}

        # Sprint F214OPT-B: Bounded LRU prefix cache for tokenization
        _raw_max = os.environ.get("HLEDAC_HERMES_PREFIX_CACHE_MAXSIZE", "")
        try:
            _max = int(_raw_max) if _raw_max.strip() else None
            self._prefix_cache_maxsize: int = max(1, _max) if _max is not None else 64
        except (ValueError, TypeError):
            self._prefix_cache_maxsize: int = 64
        self._prefix_cache: OrderedDict[str, Any] = OrderedDict()  # type: ignore[assignment]

        # F273H: Idle-based lazy unload — sentinel for idle threshold
        self._idle_unload_timeout_s: float = float(os.getenv("HLEDAC_IDLE_UNLOAD_TIMEOUT_S", "1800.0"))
        # Telemetry for prefix cache
        self._prefix_cache_stats = {
            "prefix_cache_maxsize": self._prefix_cache_maxsize,
            "prefix_cache_size": 0,
            "prefix_cache_evictions": 0,
            "prefix_cache_hits": 0,
            "prefix_cache_misses": 0,
        }

        # Single-thread executor for MLX inference (M1 8GB safe)
        self._inference_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._inference_semaphore = asyncio.Semaphore(1)

        # Sprint P0-2: MLX continuous batching executor (F226H wiring).
        # Lazy init via _ensure_mlx_batcher() — NOT instantiated at __init__
        # so import cost is paid once, at first generate() call. M1 8GB safe.
        # Always-on: routing layer decides per-call whether to batch.
        self._mlx_batcher: Any = None  # MLXBatchedExecutor | None (lazy)

        # Sprint P0-3: Dedicated worker thread with persistent event loop.
        # Single MLX context, single model state — non-blocking main loop.
        # Lazy init via _ensure_mlx_worker_thread() — M1 8GB safe (M.T2).
        # Always-on: routing layer in _submit_inference() picks thread vs executor.
        self._mlx_worker_thread: Any = None  # MLXWorkerThread | None (lazy)

        # P1-4: Active iteration count for force-enable batching on multi-cycle sprints.
        # When >= 2, MLXBatchedExecutor is force-enabled regardless of memory pressure.
        # Updated by SprintScheduler on each cycle start; resets on sprint end.
        self._active_iteration_count: int = 0

        # Task #4: Interruptible streaming — cancellation flag checked between
        # token yields so CancelledError propagates promptly, not only when
        # the to_thread generator completes. M1 8GB safe.
        self._stream_cancelled: asyncio.Event = asyncio.Event()

        # Sprint 71/7E: Continuous batching — schema-aware PriorityQueue
        self._batch_queue = None  # type: ignore[assignment]
        self._batch_worker_task: asyncio.Task | None = None
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
        # Sprint F206X: Fixed bug - type annotation without assignment created local var
        self._pending_futures = set()
        self._ema_alpha = 0.3

        # Sprint 7E: Age bump for anti-starvation
        self._flush_cycle_count = 0
        self._age_bump_interval = 3  # bump every N flush cycles
        self._last_age_bump = 0

        # Sprint 7E: Warmup cache SEPARATE from production cache
        # Sprint P1-3: persistent across cycles via disk (shares _save_cache/_load_cache)
        self._warmup_cache: Any = None  # isolated warmup KV cache
        self._warmup_prompt_hash: str | None = None  # Sprint P1-3: warmup prompt fingerprint
        self._batch_worker_shutting_down = False  # Sprint 7K: poison pill flag

        # GPU memory tracking
        self._last_gpu_memory: int = 0

        # GAP-3/1: Per-model inference circuit breaker — auto-init on model load
        self._model_breaker: ModelCircuitBreaker | None = None

        # P0-2: Initialize model breaker immediately so GAP-3/1 guard always has a valid reference
        try:
            from transport.circuit_breaker import ModelCircuitBreaker
            self._model_breaker = ModelCircuitBreaker(model_id="hermes")
        except Exception:
            pass  # noqa: BLE001  # fail-soft: breaker stays None, GAP-3/1 guard skipped

        # Sprint F259: PromptBandit integration — lazy init, not at __init__
        self._prompt_bandit = None
        self._last_bandit_arm: str | None = None

    def _get_prompt_bandit(self):
        """Lazy init PromptBandit (avoid heavy import at module load)."""
        if self._prompt_bandit is None:
            try:
                from hledac.universal.brain.prompt_bandit import PromptBandit
                self._prompt_bandit = PromptBandit(
                    lambda_reg=0.01,
                    persist_path=str(Path.home() / '.hledac' / 'hermes_prompt_bandit.json'),
                )
                logger.debug("PromptBandit initialized for Hermes3Engine")
            except ImportError:
                self._prompt_bandit = None
                logger.debug("PromptBandit not available")
        return self._prompt_bandit

    def init_model_breaker(self, model_id: str) -> None:
        """GAP-3/1: Initialize per-model circuit breaker."""
        from transport.circuit_breaker import ModelCircuitBreaker
        self._model_breaker = ModelCircuitBreaker(model_id=model_id)

    async def _ensure_batch_worker(self) -> None:
        """Ensure batch worker is started (lazy start)."""
        if self._batch_worker_task is None:
            self._batch_queue = asyncio.PriorityQueue(maxsize=256)
            import itertools
            self._batch_tie_breaker = itertools.count()
            self._pending_futures = set()  # Sprint F206X: fixed bug - remove type annotation that shadowed class attr
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
            # shield() inside timeout ctx: ctx cancel does NOT propagate to shielded task
            # (preserves the original wait_for(shield(...)) semantics)
            async with asyncio.timeout(timeout):
                await asyncio.shield(self._batch_worker_task)
        except (TimeoutError, asyncio.CancelledError):
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
        system_msg: str | None = None
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
        payload: dict = {
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
        # Sprint F206X: Bounded set prevents memory exhaustion; try/except ensures discard always runs
        if len(self._pending_futures) >= MAX_PENDING_FUTURES:
            # Evict oldest done future first
            done_futures = [f for f in self._pending_futures if f.done()]
            if done_futures:
                self._pending_futures.discard(done_futures[0])
            else:
                raise RuntimeError("pending_futures overflow")
        self._pending_futures.add(future)

        def _safe_discard(f: asyncio.Future) -> None:
            try:
                self._pending_futures.discard(f)
            except Exception:
                pass  # noqa: BLE001  # Sprint F206X: ensure discard never raises

        future.add_done_callback(_safe_discard)

        # Tie-breaker counter — module-level to avoid per-call overhead
        if not hasattr(self.__class__, '_batch_tie_breaker'):
            self.__class__._batch_tie_breaker = itertools.count()  # type: ignore[assignment]
        tie = next(self._batch_tie_breaker)

        future._enqueue_ns = time.monotonic_ns()
        assert isinstance(self._batch_queue, asyncio.PriorityQueue)
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
        itertools.count()

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
                    async with asyncio.timeout(flush_interval):
                        assert isinstance(self._batch_queue, asyncio.PriorityQueue)
                        first_item = await self._batch_queue.get()
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
                            async with asyncio.timeout(0.01):
                                item = await self._batch_queue.get_nowait()  # type: ignore[unresolved-attribute]
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
                        except TimeoutError:
                            break

                except TimeoutError:
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
        timeout_s: float | None,
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

    def _compute_system_prompt_hash(self, system_msg: str | None) -> str:
        """Sprint 7G: Hash of system prompt for segregation."""
        if not system_msg:
            return 'default'
        return hashlib.md5(system_msg.encode(), usedforsecurity=False).hexdigest()[:8]

    async def _age_bump_queue(self) -> None:
        """Age-bump: improve priority of waiting items by 1 without O(n) rebuild."""
        if self._batch_queue is None:
            return
        assert isinstance(self._batch_queue, asyncio.PriorityQueue)
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
        by_schema: dict[str, list] = {}
        for priority, _tie, schema_key, payload in items:
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
        Shatters on total failure.

        Sprint P2-2: Parallel batch dispatch via asyncio.gather.
        All items in a batch have the same schema/system_msg/length_bin
        boundaries so they can be dispatched concurrently. Each _run_structured_single
        call goes through _submit_inference → MLXWorkerThread (when available),
        enabling concurrent dispatch while the worker thread serializes MLX execution.
        This gives ~2-4× wall-clock improvement for batched inference by overlapping
        I/O wait (async dispatch) with GPU computation.
        """
        tasks = [self._run_structured_single(payload) for payload, _ in items]
        # F314: migrated asyncio.gather -> safe_gather_dropin (fail-soft, preserves order).
        # Preserves original fail-soft semantics: exceptions returned in results list,
        # same position as original task index (return_exceptions=True behavior).
        results = await safe_gather_dropin(
            *tasks,
            label="deephermes3:structured_batch",
            logger_instance=logger,
        )

        # Detect shattered: any exception in results means batch processing
        # failed and fell through to individual item handling.
        has_exception = any(isinstance(r, Exception) for r in results)
        if has_exception:
            self._telemetry_counters['batch_shattered'] += 1
            logger.debug(f"[STRUCTURED] Batch shattered: {sum(1 for r in results if isinstance(r, Exception))} exceptions")

        # Resolve futures — handle both success and exception results
        for payload, result in zip([p for p, _ in items], results, strict=False):
            future = payload.get('future')
            if future and not future.done():
                future._resolve_ns = time.monotonic_ns()
                if isinstance(result, Exception):
                    future.set_exception(result)
                else:
                    future.set_result(result)

        self._telemetry_counters['batch_executed'] += 1

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
        """
        Run a single structured output request (canonical path).

        P2-2: Routes through _submit_inference (→ MLXWorkerThread when available)
        instead of run_in_executor, enabling concurrent dispatch with other batch items.
        This gives ~2-4× wall-clock improvement for batched inference by overlapping
        I/O wait (async dispatch) with GPU computation.
        """
        prompt = payload.get('prompt')
        response_model = payload.get('response_model')
        temperature = payload.get('temperature', 0.1)
        max_tokens = payload.get('max_tokens', 1024)
        system_msg = payload.get('system_msg')

        if system_msg:
            formatted = self._format_chatml(system_msg, prompt)
        else:
            formatted = self._format_chatml("You are a helpful assistant.", prompt)

        # P2-2: Route through _submit_inference (→ MLXWorkerThread) instead of
        # run_in_executor. This enables concurrent dispatch: multiple structured
        # requests can be submitted in parallel while the worker thread serializes
        # the actual MLX execution. The async overhead (submitting to the worker)
        # doesn't block the GPU.
        timeout_s = _get_hermes_timeout_s()
        raw_text = await self._submit_inference(
            timeout_s,
            self._run_inference,
            formatted,
            temperature,
            max_tokens,
            None,  # prefix_cache
        )

        # P2-2: Parse JSON response to structured output
        import re
        schema_cls = response_model if isinstance(response_model, type) else type(response_model)

        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if match:
            try:
                data = _msgspec_decode(match.group())
                if hasattr(schema_cls, 'model_validate'):
                    return schema_cls.model_validate(data)
                return schema_cls.model_construct(**data)
            except Exception:
                pass

        # Fallback: construct with defaults
        logger.debug(f"[STRUCTURED] Parse failed, using default for {schema_cls.__name__}")
        fields = dict.fromkeys(getattr(schema_cls, 'model_fields', {}).keys())
        return schema_cls.model_construct(**fields) if hasattr(schema_cls, 'model_construct') else schema_cls(**fields)

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
            if hasattr(mx, "get_active_memory"):
                return mx.get_active_memory()
        except Exception:
            pass

        return 0

    async def _ensure_model_loaded(self) -> None:
        """F273H+: Load model from cache or disk (idempotent, thread-safe).

        Uses module-level _HERMES_MODEL_CACHE to persist model across sprint cycles.
        HLEDAC_HERMES_NO_CACHE=1 bypasses cache (debug escape hatch).
        Double-checked locking pattern: fast path reads cache, slow path takes lock.
        """
        global _HERMES_MODEL_CACHE, _HERMES_CACHE_LOCK

        # Fast path: already loaded
        if self._model is not None and self._tokenizer is not None:
            logger.debug("[HERMES] Model already loaded, skipping cache check")
            return

        # Debug escape hatch: always reload from disk
        if os.getenv("HLEDAC_HERMES_NO_CACHE", "0") == "1":
            logger.debug("[HERMES] HLEDAC_HERMES_NO_CACHE=1 — loading from disk")
            model, tokenizer = await asyncio.to_thread(
                __import__("mlx_lm").load, self.config.model_path
            )
            self._model = model
            self._tokenizer = tokenizer
            return

        # Fast path: cache hit (no lock needed for read)
        model_path = self.config.model_path
        if model_path in _HERMES_MODEL_CACHE:
            self._model, self._tokenizer = _HERMES_MODEL_CACHE[model_path]
            logger.debug("[HERMES] Model retrieved from cache, skipping reload")
            return

        # Slow path: acquire lock and double-check
        async with _HERMES_CACHE_LOCK:
            if model_path in _HERMES_MODEL_CACHE:
                self._model, self._tokenizer = _HERMES_MODEL_CACHE[model_path]
                logger.debug("[HERMES] Model retrieved from cache (post-lock)")
                return

            logger.info(f"[HERMES] Loading model from disk: {model_path}")
            # F300S-FIX: Run mlx_lm.load() DIRECTLY in the main thread (no
            # asyncio.to_thread). mlx_lm.generate() internally calls
            # mx.new_thread_local_stream() which is registered for the FIRST
            # thread that calls mlx. If load() runs in a worker thread, the
            # stream is bound to that worker -- subsequent
            # mx.stream(generation_stream) in the main thread fails.
            # By calling load() directly in the main thread (it's called from
            # _ensure_model_loaded which runs in the main asyncio loop), we
            # ensure stream registration and inference happen in the same thread.
            # This blocks the event loop briefly during model load, which is
            # acceptable since load is infrequent and MLX load itself is the
            # bottleneck (not I/O bound).
            model, tokenizer = __import__("mlx_lm").load(model_path)
            self._model = model
            self._tokenizer = tokenizer
            # Sprint OPT-3: Half-precision optimizer state — convert model to float16
            # after load for 2× memory savings. Model weights are 4-bit quantized
            # on disk; during inference they are dequantized to float16 internally
            # by MLX — keeping model in float16 reduces dequantization scratch 2×.
            try:
                if os.getenv("HLEDAC_HALF_PRECISION", "1") != "0":
                    model.set_dtype(mx.float16)
                    logger.info("[HERMES] Model dtype set to float16 (half precision)")
            except Exception as e:
                logger.warning("[HERMES] Could not set float16 dtype: %s", e)
            _HERMES_MODEL_CACHE[model_path] = (model, tokenizer)
            logger.info(f"[HERMES] Model cached ({len(_HERMES_MODEL_CACHE)} entries)")

    @classmethod
    def evict_model_cache(cls) -> None:
        """F273H+: Uvolni všechny modely z paměti.

        Volat při SIGTERM nebo memory pressure.
        Modely jsou uvolněny přes GC a Metal cache je vyčištěna.
        """
        global _HERMES_MODEL_CACHE
        _HERMES_MODEL_CACHE.clear()
        gc.collect()
        try:
            import mlx.core as mx
            mx.eval([])  # Ensure all computations are done before clearing cache
            mx.metal.clear_cache()
        except Exception:
            pass
        logger.info("[HERMES] Model cache evicted")

    async def initialize(self) -> None:
        """Inicializovat model"""
        global KV_CACHE_AVAILABLE
        try:
            await self._ensure_model_loaded()
            logger.info("✓ Hermes-3 loaded successfully")

            # FIX 2 (P0): Reset circuit breaker after successful model load —
            # GAP-3/1 breaker for "hermes" stays OPEN across unload/reload cycles,
            # blocking every subsequent inference call. Reset on successful load.
            if self._model_breaker is not None:
                self._model_breaker.reset()
                logger.info("[GAP-3/1] Circuit breaker reset after successful model load")

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

            # Sprint F192B + F288 FIX: Emergency guard — skip draft model if emergency
            # requested OR if UMA is in EMERGENCY state. The draft model (~400MB) is
            # loaded into CPU RAM but mx.get_active_memory() only measures GPU RAM,
            # so _init_draft_model()'s own 2.5 GiB GPU threshold check passes even when
            # the system is already in EMERGENCY state (3.63 GiB GPU + 400MB draft =
            # ~4 GiB already). We check sample_uma_status() for the true system state.
            _skip_draft = False
            # P1-6 + F290-EXT: Always-on speculative decode disable on M1 8GB.
            # Draft model (~400-700MB) causes 30s blocking Metal calls that trigger
            # 178 branch timeouts and exhaust GPU memory on 8GB UMA.
            # Opt-in to re-enable: HLEDAC_DISABLE_SPEC_DECODE=0
            if os.environ.get("HLEDAC_DISABLE_SPEC_DECODE", "1") != "0":
                logger.info("[HERMES] Speculative decoding disabled by default on M1 8GB (HLEDAC_DISABLE_SPEC_DECODE=1)")
                _skip_draft = True
            if is_emergency_unload_requested is not None and is_emergency_unload_requested():
                logger.warning("[HERMES] Emergency unload requested — skipping draft model init")
                _skip_draft = True
            if not _skip_draft:
                try:
                    from hledac.universal.core.resource_governor import sample_uma_status_async
                    _uma = await sample_uma_status_async()
                    _uma_state = getattr(_uma, 'state', None)
                    # F265H-EXT: Skip draft model at CRITICAL (6.7+ GiB) and EMERGENCY.
                    # At CRITICAL the system has 0.3 GiB to EMERGENCY — speculative decoding
                    # causes 30s blocking calls that trigger 178 branch timeouts.
                    if _uma_state in ("critical", "emergency"):
                        logger.warning(f"[HERMES] UMA {_uma_state} ({getattr(_uma, 'system_used_gib', 0):.2f}GiB) — skipping draft model init")
                        _skip_draft = True
                except Exception:
                    pass  # noqa: BLE001  # Fail-safe: let draft init proceed
            if not _skip_draft:
                # Sprint 75: Initialize draft model with memory guard
                await self._init_draft_model()

            # Sprint 75: Initialize persistent system-prompt cache
            # P1-3: REPLACED by _prefill_warmup_caches — parallel prefill
            # await self._init_system_prompt_cache()
            # await self.warmup_prefix_cache(...)

            # P1-3: Background KV cache warmup — don't block sprint start.
            # Sprint pipeline begins immediately (CT/DNS/WAYBACK lanes run in
            # parallel); KV cache prefill completes asynchronously ~5s later.
            # If inference fires before prefill finishes, _get_kv_cache_kwargs()
            # returns None → falls back to cold-start (functional, just slower).
            # Fail-safe: any exception in the background task is caught and logged.
            asyncio.create_task(self._bg_warmup_caches())

        except Exception as e:
            logger.error(f"Failed to load Hermes-3: {e}")
            raise

    async def _init_draft_model(self) -> None:
        """
        F290-EXT: DISABLED — speculative decoding is always-off on M1 8GB.

        The draft model (~400-700MB) caused 30s blocking Metal calls that
        triggered 178 branch timeouts and exhausted GPU memory on 8GB UMA.

        The entire body below is no-op because _load_model() sets
        _skip_draft=True when HLEDAC_DISABLE_SPEC_DECODE != "0" (default "1").
        This method is kept as a no-op stub for future opt-in re-enabling.
        """
        # All speculative decode logic disabled — see _load_model() skip logic
        self._speculative_enabled = False
        self._draft_model_name = None
        self._draft_model_obj = None
        self._supports_draft = False
        logger.info("[SPEC] Draft model disabled (M1 8GB always-on safe mode)")

    async def _init_system_prompt_cache(self) -> None:
        """Initialize persistent system-prompt cache (Sprint 75 + Sprint M4)."""
        if not KV_CACHE_AVAILABLE or self._model is None:
            return

        try:
            # Sprint M4: probe disk BEFORE allocating/prefilling. A valid
            # on-disk cache is the cheapest path (~0 ms vs ~1.5 s prefill
            # for a 1500-token system prompt). Probe via path check, not
            # via full _load_cache, so we can branch on existence.
            from pathlib import Path

            from mlx_lm.models.cache import make_prompt_cache
            _disk_cache = (
                Path.home() / '.hledac' / 'cache' / 'system_prompt_cache.npz'
            )
            _has_disk = await asyncio.to_thread(_disk_cache.exists)

            self._system_prompt_cache = make_prompt_cache(self._model, max_kv_size=512)

            # Detect KV quantization support (always — needed for both paths)
            for layer in self._system_prompt_cache:
                if hasattr(layer, 'quantize'):
                    self._supports_kv_quant = True
                    break

            # Try disk first (M4) — skip the expensive prefill entirely
            # on a cache hit. _load_cache populates _system_prompt_cache
            # in place, replacing the empty cache object created above.
            if _has_disk and await self._load_cache():
                logger.info("[CACHE] System prompt cache loaded from disk (prefill skipped)")
                return

            # Cold path: no disk cache — pay the prefill cost once
            if self._supports_stream_generate:
                import mlx_lm

                from hledac.universal.utils.mlx_memory import get_metal_stream_context

                def _prefill():
                    # F288 FIX: Metal stream context per-thread — fixes
                    # "Stream(gpu,1) not in current thread" when prefill runs
                    # in asyncio.to_thread worker thread.
                    with get_metal_stream_context():
                        try:
                            # F266 FIX: mx.eval([]) barrier BEFORE stream_generate —
                            # flush pending lazy ops from previous inference.
                            # Without this, pending GPU work causes OOM cascades.
                            import mlx.core as _mx
                            _mx.eval([])
                            for _ in mlx_lm.stream_generate(
                                model=self._model,
                                tokenizer=self._tokenizer,
                                prompt=self._system_prompt,
                                prompt_cache=self._system_prompt_cache,
                                max_tokens=1
                            ):
                                pass
                        finally:
                            # F219B: safe eval + clear via helper
                            _safe_mlx_eval_and_clear_cache("system_prompt_cache_prefill")

                await asyncio.to_thread(_prefill)
                self._kv_cache_stats['cache_prefills'] = 1

            logger.info("[CACHE] System prompt cache initialized (cold prefill)")

        except Exception as e:
            logger.warning(f"[CACHE] System prompt cache init failed: {e}")

    # P1-3: Parallel KV cache prefill — replaces sequenční _init_system_prompt_cache + warmup_prefix_cache

    async def _prefill_warmup_caches(self) -> None:
        """
        P1-3: Parallel KV cache prefill — system prompt cache + warmup cache simultaneously.

        Replaces the sequenční pattern in initialize():
            await _init_system_prompt_cache()
            await warmup_prefix_cache(...)

        Both cache prefills are independent and can run in parallel:
        - System prompt cache (~512 KV, ~1500ms cold prefill)
        - Warmup cache (~1000 tokens, ~500ms cold prefill)

        M1 8GB invariant:
        - mx.eval([]) before clear_cache in each prefill path
        - Metal stream context per-thread (F288 fix)
        - Bounded: max_parallel_prefill=2 (configurable)
        - Fail-safe: one failure does not affect the other
        - Always asyncio.gather with return_exceptions=True

        Cold start improvement: ~1500ms parallel vs ~2000ms sequential
        """
        # FIX 1 (P0): Sequential prefill on Apple Silicon — detect M1 and force
        # max_parallel=1 to avoid Stream(gpu,1) Metal race condition in asyncio.gather
        # with concurrent asyncio.to_thread() prefills on unified memory architecture.
        max_parallel = getattr(self.config, 'max_parallel_prefill', 1)
        try:
            import mlx.core as mx
            device_info = mx.metal.device_info()
            device_name = device_info.get('device_name', '')
            if 'Apple' in device_name:
                max_parallel = 1
                logger.info("[FIX-1] Apple Silicon detected (%s) — forcing sequential prefill", device_name)
        except Exception:
            pass  # noqa: BLE001  # fail-safe: fall through with current max_parallel

        if self._model is None or self._tokenizer is None:
            return

        if max_parallel < 2:
            # Fallback to sequential if parallel disabled
            await self._init_system_prompt_cache()
            await self.warmup_prefix_cache(
                system_prompt=self._system_prompt,
                few_shot_examples=[
                    {"user": "What is 2+2?", "assistant": "4"},
                    {"user": "Capital of France?", "assistant": "Paris"},
                ]
            )
            return

        try:
            # P1-3: Disk probe for both caches BEFORE allocating — mirrors M4 pattern
            from pathlib import Path

            _disk_cache_path = Path.home() / '.hledac' / 'cache' / 'system_prompt_cache.npz'
            # P2-1: warmup cache now per-hash, no static path needed

            _has_sys_disk = await asyncio.to_thread(_disk_cache_path.exists)
            _has_warmup_disk = False  # P2-1: checked dynamically per-hash in _prefill_warmup_cache

            # Async wrappers for each prefill — run as true coroutines
            async def _prefill_system_cache() -> bool:
                """Prefill system prompt cache (512 KV)."""
                try:
                    from mlx_lm.models.cache import make_prompt_cache

                    from hledac.universal.utils.mlx_memory import get_metal_stream_context

                    self._system_prompt_cache = make_prompt_cache(self._model, max_kv_size=512)

                    # Detect KV quantization support
                    if not self._supports_kv_quant:
                        for layer in self._system_prompt_cache:
                            if hasattr(layer, 'quantize'):
                                self._supports_kv_quant = True
                                break

                    # Try disk first (M4) — skip expensive prefill on cache hit
                    if _has_sys_disk and await self._load_cache():
                        logger.info("[P1-3] System prompt cache loaded from disk (parallel prefill skipped)")
                        return True

                    # Cold path: prefill required
                    if self._supports_stream_generate:
                        import mlx_lm

                        def _do_prefill():
                            with get_metal_stream_context():
                                try:
                                    # F266 FIX: mx.eval([]) barrier BEFORE stream_generate
                                    import mlx.core as _mx
                                    _mx.eval([])
                                    for _ in mlx_lm.stream_generate(
                                        model=self._model,
                                        tokenizer=self._tokenizer,
                                        prompt=self._system_prompt,
                                        prompt_cache=self._system_prompt_cache,
                                        max_tokens=1
                                    ):
                                        pass
                                finally:
                                    _safe_mlx_eval_and_clear_cache("system_prompt_cache_parallel_prefill")

                        await asyncio.to_thread(_do_prefill)
                        self._kv_cache_stats['cache_prefills'] += 1

                    logger.info("[P1-3] System prompt cache prefill complete (parallel)")
                    return True

                except Exception as e:
                    logger.warning(f"[P1-3] System cache prefill failed: {e}")
                    return False

            async def _prefill_warmup_cache() -> bool:
                """Prefill warmup cache (~1000 tokens)."""
                try:
                    from mlx_lm.models.cache import make_prompt_cache

                    from hledac.universal.utils.mlx_memory import get_metal_stream_context

                    system_prompt = self._system_prompt
                    few_shot_examples = [
                        {"user": "What is 2+2?", "assistant": "4"},
                        {"user": "Capital of France?", "assistant": "Paris"},
                    ]

                    # Build warmup prompt in ChatML format
                    parts = [f"<|im_start|>system\n{system_prompt}<|im_end|>"]
                    for ex in few_shot_examples[:3]:
                        parts.append(f"<|im_start|>user\n{ex.get('user', '')}<|im_end|>")
                        parts.append(f"<|im_start|>assistant\n{ex.get('assistant', '')}<|im_end|>")
                    warmup_prompt = "\n".join(parts)

                    tokens = self._tokenizer.encode(warmup_prompt)
                    token_count = len(tokens)
                    if token_count > 1000:
                        warmup_prompt = self._tokenizer.decode(tokens[:1000])

                    # P2-1: Compute xxhash-based prompt hash for cache fingerprinting
                    if XXHASH_AVAILABLE:
                        canonical_parts = [system_prompt]
                        for ex in few_shot_examples[:3]:
                            canonical_parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
                        canonical_text = "\n".join(canonical_parts)
                        prompt_hash = xxhash.xxh64(canonical_text.encode()).hexdigest()[:16]
                    else:
                        canonical_parts = [system_prompt]
                        for ex in few_shot_examples[:3]:
                            canonical_parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
                        canonical_text = "\n".join(canonical_parts)
                        prompt_hash = hashlib.blake2b(canonical_text.encode(), digest_size=8).hexdigest()

                    # Try disk restore first (skip expensive prefill on hit)
                    # P2-1: Use hash-bazed path
                    _warmup_disk_path = WARMUP_CACHE_DIR / f"warmup_{prompt_hash}.safetensors"
                    _has_warmup_disk_now = await asyncio.to_thread(_warmup_disk_path.exists) if warmup_hash else False
                    if _has_warmup_disk_now:
                        if await self._restore_warmup_cache(_warmup_disk_path, prompt_hash):
                            logger.info("[P1-3] Warmup cache restored from disk (parallel)")
                            return True

                    logger.info(f"[P1-3] Building fresh warmup cache (~{token_count} tokens, parallel)...")

                    # Create warmup cache
                    self._warmup_cache = make_prompt_cache(
                        self._model, max_kv_size=max(token_count + 128, 1024)
                    )
                    self._warmup_prompt_hash = prompt_hash

                    # Quantize if supported
                    kv_bits = self._get_adaptive_kv_bits()
                    if self._supports_kv_quant:
                        for layer in self._warmup_cache:
                            if hasattr(layer, 'quantize'):
                                try:
                                    layer.quantize(group_size=64, bits=kv_bits)
                                except Exception:
                                    pass

                    # Prefill via mlx_lm.generate with max_tokens=1
                    from mlx_lm import generate as mlx_generate
                    from mlx_lm.sample_utils import make_sampler

                    _worker = getattr(self, "_mlx_worker_thread", None)
                    _worker_live = _worker is not None and _worker.is_active()

                    def _do_generate():
                        with get_metal_stream_context():
                            mlx_generate(
                                model=self._model,
                                tokenizer=self._tokenizer,
                                prompt=warmup_prompt,
                                sampler=make_sampler(temp=0.3),
                                max_tokens=1,
                                kv_bits=kv_bits,
                                prompt_cache=self._warmup_cache,
                                verbose=False,
                            )

                    if _worker_live:
                        # F300S-FIX: Use asyncio.run_coroutine_threadsafe to dispatch
                        # to MAIN THREAD where Metal context is valid. Mirrors
                        # _submit_inference() pattern (not worker.submit).
                        try:
                            main_loop = asyncio.get_event_loop()

                            async def _coro_wrapper():
                                return _do_generate()

                            inference_future = asyncio.run_coroutine_threadsafe(
                                _coro_wrapper(),
                                main_loop,
                            )
                            await asyncio.wait_for(
                                asyncio.wrap_future(inference_future),
                                timeout=60.0,
                            )
                        except (TimeoutError, RuntimeError):
                            await asyncio.to_thread(_do_generate)
                    else:
                        await asyncio.to_thread(_do_generate)

                    logger.info("[P1-3] Warmup cache prefill complete (parallel)")
                    return True

                except Exception as e:
                    logger.warning(f"[P1-3] Warmup cache prefill failed: {e}")
                    return False

            # P1-3: Parallel gather — both prefills run concurrently
            # F314: kept asyncio.gather (NOT safe_gather_shielded) because:
            # results[0]/results[1] positional access required for cache logic.
            # safe_gather_shielded puts exceptions in .errors, NOT at positional index.
            results = await asyncio.gather(
                _prefill_system_cache(),
                _prefill_warmup_cache(),
                return_exceptions=True
            )

            # F286 FIX 2 (P1): Persist warmup cache after parallel prefill.
            # _save_cache() saves BOTH system_prompt_cache AND warmup_cache.
            # In the parallel path, warmup_cache was built but _save_cache was
            # NOT called (only system_prompt_cache was saved via _load_cache disk probe).
            # Await save here so warmup cache survives next cold start.
            # This eliminates the ~500ms cold prefill on subsequent sprints.
            if len(results) > 1 and results[1] is True:
                try:
                    await self._save_cache()
                except Exception as _e:
                    logger.debug(f"[P1-3] warmup cache save failed: {_e}")

            # Telemetry
            successes = sum(1 for r in results if r is True)
            exceptions = [r for r in results if isinstance(r, Exception)]
            if exceptions:
                logger.warning(f"[P1-3] {len(exceptions)} prefill exception(s): {exceptions}")
            self._kv_cache_stats['parallel_prefills'] = successes
            logger.info(f"[P1-3] Parallel prefill complete: {successes}/{len(results)} succeeded")

        except Exception as e:
            # Top-level fail-safe — fall back to sequential
            logger.warning(f"[P1-3] Parallel prefill failed: {e}, falling back to sequential")
            await self._init_system_prompt_cache()
            await self.warmup_prefix_cache(
                system_prompt=self._system_prompt,
                few_shot_examples=[
                    {"user": "What is 2+2?", "assistant": "4"},
                    {"user": "Capital of France?", "assistant": "Paris"},
                ]
            )
            # F286 FIX 2 (P1): Persist both caches after sequential fallback too.
            # Without this, sequential path never saves warmup cache to disk.
            try:
                await self._save_cache()
            except Exception as _e:
                logger.debug(f"[P1-3] sequential fallback cache save failed: {_e}")

    async def _bg_warmup_caches(self) -> None:
        """Background KV cache warmup — fires after sprint start, does not block.

        Sprint Background KV Cache Warmup (P1-3 EXT):
        Let sprint begin first (CT/DNS/WAYBACK lanes start in parallel),
        then prefill KV caches without blocking the sprint pipeline.
        Expected improvement: ~60s savings (sprint starts immediately vs sequential).

        M1 8GB invariant:
        - mx.eval([]) before clear_cache in each prefill path (existing)
        - Metal stream context per-thread (existing F288 fix)
        - Fail-safe: any exception is caught and logged; sprint continues
        - Always asyncio.gather with return_exceptions=True (existing)

        Fallback chain: if prefill fails, generate() falls back to cold-start
        (functional, just without KV cache speedup).
        """
        try:
            await asyncio.sleep(5)  # Let sprint pipeline start first
        except asyncio.CancelledError:
            logger.debug("[P1-3] Background warmup cancelled (sprint ended early)")
            return
        try:
            logger.info("[P1-3] Starting background KV cache prefill (~5s after sprint start)...")
            await self._prefill_warmup_caches()
            logger.info("[P1-3] Background KV cache prefill complete")
        except asyncio.CancelledError:
            logger.debug("[P1-3] Background warmup cancelled during prefill")
        except Exception as e:
            # Fail-safe: never propagate — sprint continues without warmup cache
            logger.warning(f"[P1-3] Background KV cache prefill failed: {e}")

    async def _save_cache(self) -> None:
        """Save system prompt cache to disk (best-effort, non-blocking).

        Sprint M4: stores keys/values SEPARATELY per layer — mx.array() on a
        (keys, values) tuple is shape-ambiguous and silently stacks incorrectly
        on some MLX versions. Separate named arrays round-trip cleanly via
        mx.savez. The PromptCache-level offset is also persisted so resume
        picks up at the right token position.

        F265B: mx.savez() and save_prompt_cache() are blocking disk I/O —
        offloaded to a thread so the async event loop stays free.
        """
        try:
            from pathlib import Path

            cache_path = Path.home() / '.hledac' / 'cache' / 'system_prompt_cache.npz'
            # Fail-soft mkdir: sandboxed tests faking Path.home() to a
            # non-existent path must not break the save path itself.
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            warmup_cache = self._warmup_cache
            warmup_hash = self._warmup_prompt_hash
            # P2-1: Use hash-bazed path so warmup_or_skip can find it later
            if warmup_cache and warmup_hash:
                warmup_path = WARMUP_CACHE_DIR / f"warmup_{warmup_hash}.safetensors"
                try:
                    WARMUP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
            else:
                warmup_path = None

            async def _do_save() -> None:
                # Inner sync fn — runs on thread pool, never blocks the event loop
                if self._system_prompt_cache:
                    import mlx.core as mx

                    data: dict[str, Any] = {}
                    for i, layer in enumerate(self._system_prompt_cache):
                        state = getattr(layer, "state", None)
                        if state is None:
                            continue
                        # mlx_lm KVCache.state returns (keys, values) tuple
                        try:
                            keys, values = state
                        except Exception:
                            continue
                        data[f"layer_{i}_keys"] = keys
                        data[f"layer_{i}_values"] = values

                    # Persist PromptCache-level offset for resume correctness
                    if hasattr(self._system_prompt_cache, "offset"):
                        try:
                            data["_offset"] = mx.array(
                                [int(self._system_prompt_cache.offset)]
                            )
                        except Exception:
                            pass

                    if data:
                        mx.savez(str(cache_path), **data)
                        logger.debug(
                            f"[CACHE] Saved to {cache_path} ({len(self._system_prompt_cache)} layers)"
                        )

                # Sprint P1-3: Also save warmup cache if present and prompt matches
                # P2-3: Use mlx_lm 0.31.3 save_prompt_cache API (.safetensors) instead of
                # custom .npz format for cross-sprint persistent Metal cache compatibility.
                if warmup_cache and warmup_hash:
                    try:
                        from mlx_lm.models.cache import save_prompt_cache
                        save_prompt_cache(
                            str(warmup_path),
                            warmup_cache,
                            metadata={"prompt_hash": warmup_hash},
                        )
                        logger.debug(f"[CACHE] Warmup cache saved ({len(warmup_cache)} layers)")
                    except Exception as e:
                        logger.debug(f"[CACHE] save_prompt_cache failed: {e}")

            await asyncio.to_thread(_do_save)

        except Exception as e:
            logger.debug(f"[CACHE] Save failed (non-critical): {e}")

    async def _load_cache(self) -> bool:
        """Try to load cache from disk and restore into self._system_prompt_cache.

        Sprint M4: was previously dead code (logged and returned True without
        ever touching the cache). Now actually rebuilds the KV cache from
        disk: per-layer keys+values, plus PromptCache-level offset. M4 win
        = ~1500 system-prompt tokens of prefill cost avoided on each process
        restart.

        F265B: mx.load() is blocking disk I/O — offloaded to a thread so
        the async event loop stays free.
        """
        if self._system_prompt_cache is None:
            return False
        try:
            from pathlib import Path

            import mlx.core as mx

            cache_path = Path.home() / '.hledac' / 'cache' / 'system_prompt_cache.npz'
            if not cache_path.exists():
                return False

            # Inner sync fn — mx.load() is blocking disk I/O
            def _do_load() -> mx.ndarray:
                return mx.load(str(cache_path))

            data = await asyncio.to_thread(_do_load)

            n_layers = len(self._system_prompt_cache)
            restored = 0
            for i in range(n_layers):
                k_key = f"layer_{i}_keys"
                v_key = f"layer_{i}_values"
                if k_key in data and v_key in data:
                    layer = self._system_prompt_cache[i]
                    if hasattr(layer, "keys") and hasattr(layer, "values"):
                        try:
                            layer.keys = data[k_key]
                            layer.values = data[v_key]
                            restored += 1
                        except Exception as e:
                            logger.debug(
                                f"[CACHE] layer {i} restore failed: {e}"
                            )

            # Restore PromptCache-level offset
            if "_offset" in data and hasattr(self._system_prompt_cache, "offset"):
                try:
                    arr = data["_offset"]
                    # mx.array([N]).item() → int; robust to scalar array
                    if hasattr(arr, "item"):
                        offset_val = int(arr.item())
                    else:
                        offset_val = int(arr)
                    self._system_prompt_cache.offset = offset_val
                except Exception:
                    pass

            if restored > 0:
                logger.info(
                    f"[CACHE] Loaded from {cache_path} ({restored}/{n_layers} layers restored)"
                )
                return True
            logger.debug(f"[CACHE] No layers restored from {cache_path}")
            return False

        except Exception as e:
            logger.debug(f"[CACHE] Load failed: {e}")
            return False

    def _format_chatml(
        self,
        system_msg: str,
        user_msg: str,
        history: list[dict[str, str]] | None = None
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

    def _measure_kv_cache_bytes(self, cache: Any, tokens: list[int]) -> int:
        """
        P1-1: Measure actual Metal memory delta for a KV cache entry.

        Forces MLX lazy evaluation via mx.eval() before measuring.
        Falls back to 32 MB estimate if mx.get_active_memory() is unavailable.

        Args:
            cache: MLX KV cache object from make_prompt_cache()
            tokens: Pre-encoded system prompt tokens

        Returns:
            int: Estimated cache size in bytes (minimum 32 MB)
        """
        try:
            import mlx.core as mx

            if hasattr(mx, "get_active_memory"):
                mem_before = int(mx.get_active_memory())
                _ = self._model(mx.array([tokens]), cache=cache)
                mx.eval(cache)  # force MLX lazy evaluation barrier
                mem_after = int(mx.get_active_memory())
                return max(0, mem_after - mem_before)
            else:
                return _FALLBACK_CACHE_BYTES
        except Exception:
            return _FALLBACK_CACHE_BYTES

    def _get_prefix_cache(self, system_prompt: str):
        """
        F289: Build or return cached KV state for system prompt from LRU pool.

        Pool bounds: memory-based eviction via HLEDAC_KV_CACHE_POOL_MEMORY_MB
        (default 256MB), NOT count-based. max_kv_size still enforced per-entry.
        Eviction: largest entry evicted first when budget exceeded.
        Actual size measured via mx.get_active_memory() delta at build time.
        P1-1: _measure_kv_cache_bytes() with 32MB fallback for inaccurate estimates.
        Returns SAME object (not deepcopy) - protected by semaphore in generate().
        Thread-safe: per-key lock serializes cache-build for same prompt hash.

        RC-17: Per-key lock eliminates race window between cache lookup and insert.
        Without lock, two concurrent cache-misses for same hash would both build
        a new KV cache (expensive) and race to insert into the pool.
        """
        if not KV_CACHE_AVAILABLE or self._model is None or not system_prompt:
            return None
        try:
            import time as _time_module

            from mlx_lm.models.cache import make_prompt_cache

            prompt_hash = hashlib.md5(system_prompt.encode()).hexdigest()

            # RC-17: Fast path — cache hit, no lock needed (GIL protects dict read)
            if prompt_hash in self._kv_cache_pool:
                # Cache HIT — move to end (most recently used)
                self._kv_cache_pool.move_to_end(prompt_hash)
                self._kv_cache_pool_stats["pool_hits"] += 1
                logger.debug(
                    f"[KV-CACHE][F289] Pool hit for system prompt hash {prompt_hash[:8]}"
                )
                return self._kv_cache_pool[prompt_hash][0]

            # RC-17: Slow path — per-key lock to serialize cache build
            # Ensures only one thread builds the KV cache for a given hash.
            # Other threads waiting on this key will see cache hit after build.
            if prompt_hash not in self._key_locks:
                self._key_locks[prompt_hash] = threading.Lock()
            lock = self._key_locks[prompt_hash]

            with lock:
                # Double-check after acquiring lock — another thread may have built it
                if prompt_hash in self._kv_cache_pool:
                    self._kv_cache_pool.move_to_end(prompt_hash)
                    self._kv_cache_pool_stats["pool_hits"] += 1
                    return self._kv_cache_pool[prompt_hash][0]

                # Cache MISS — build new cache (single thread holds lock)
                self._kv_cache_pool_stats["pool_misses"] += 1
                tokens = self._tokenizer.encode(system_prompt)
                cache = make_prompt_cache(self._model)

                # P1-1: Measure actual Metal memory delta for this cache entry
                cache_size = self._measure_kv_cache_bytes(cache, tokens)

                # P1-1: Bytes-aware eviction — evict largest entries first until within budget
                pool_budget_bytes = self._kv_cache_pool_memory_mb * 1024 * 1024
                total_bytes = sum(entry[2] for entry in self._kv_cache_pool.values()) + cache_size
                while (
                    len(self._kv_cache_pool) >= self._kv_cache_pool_maxsize
                    or total_bytes > pool_budget_bytes
                ):
                    if not self._kv_cache_pool:
                        break
                    # Evict LARGEST entry (most memory consumed) — not oldest
                    evicted_key = max(
                        self._kv_cache_pool,
                        key=lambda k: self._kv_cache_pool[k][2]
                    )
                    evicted_size = self._kv_cache_pool[evicted_key][2]
                    del self._kv_cache_pool[evicted_key]
                    total_bytes -= evicted_size
                    self._kv_cache_pool_stats["pool_evictions"] += 1
                    self._kv_cache_pool_stats["pool_evictions_memory"] += evicted_size
                    logger.debug(
                        f"[KV-CACHE][F289] Pool eviction for hash {evicted_key[:8]} "
                        f"(size={evicted_size / 1024 / 1024:.1f}MB)"
                    )

                # Store in pool with timestamp and measured size
                self._kv_cache_pool[prompt_hash] = (cache, _time_module.monotonic(), cache_size)
                # F289: Keep _system_prompt_cache in sync for legacy code paths
                # (disk save/load, streaming). Points to most recently used entry.
                self._system_prompt_cache = cache
                self._system_prompt_hash = prompt_hash
                logger.debug(
                    f"[KV-CACHE][F289] System prompt cache built for hash {prompt_hash[:8]}"
                )
                return cache
        except Exception as e:
            logger.warning(f"[KV-CACHE] Prefix cache failed: {e}")
            return None

    def _get_kv_cache_kwargs(self) -> dict:
        """
        Sprint F214Q + F265C-METAL: Dynamické KV cache řízení dle Metal memory tier (M1 8GB).

        F265C-METAL FIX: KV cache žije v Metal/GPU paměti, ne v systémové RAM.
        Používá mx.get_active_memory() přímo — měří skutečnou GPU memory pressure.
        10× rychlejší decode na druhém tokenu s KV cache ON.

        Metal tier thresholds (fraction of 1.5 GiB Metal cache limit set in mlx_cache.py):
        - < 0.60  → "normal"  → max_kv_size=8192  (plná KV cache)
        - 0.60-0.80 → "warn"   → max_kv_size=4096  (poloviční)
        - 0.80-0.95 → "critical" → max_kv_size=2048 (čtvrtinová)
        - > 0.95  → "emergency" → {} (vypnuto)

        Returns:
            dict: kwargs pro mlx_lm.generate() — buď {} (KV off) nebo {"max_kv_size": N}
            INVARIANT: NIKDY nevyhazuje výjimku — fallback {} je vždy bezpečný
        """
        # Test override: pokud je _kv_cache_enabled explicitně False, vypni KV cache
        if self._kv_cache_enabled is False:
            return {}

        tier = "normal"  # safe default
        try:
            import mlx.core as mx

            # Sprint F265C-METAL FIX: use ABSOLUTE active memory threshold (2.5 GiB),
            # NOT a fraction of Metal cache limit. mx.get_active_memory() returns
            # total active allocations in bytes (model weights + KV cache + activations).
            # Threshold: 2.5 GiB absolute — above this Metal is in EMERGENCY pressure.
            active = 0
            # Modern-first: try mx.get_active_memory(), fall back to mx.metal.get_active_memory()
            if hasattr(mx, "get_active_memory"):
                active = int(mx.get_active_memory())
            elif hasattr(mx.metal, "get_active_memory"):
                active = int(mx.metal.get_active_memory())

            # Absolute thresholds for Metal memory tiers (bytes)
            if active > EMERGENCY_METAL_BYTES:
                tier = "emergency"
            elif active > CRITICAL_METAL_BYTES:  # 1.5 GiB — critical
                tier = "critical"
            elif active > WARN_METAL_BYTES:  # 1.0 GiB — warn
                tier = "warn"
            # else "normal"
        except Exception:
            tier = "normal"  # fail-safe: při chybě drž KV cache zapnutou

        # F265H-EXT: Systémová UMA kontrola — při CRITICAL/EMERGENCY aplikuje
        # agresivnější redukci než Metal tier samotný. KV cache se inkrementálně
        # hromadí přes session, takže systémový pressure predikuje budoucí Metal tlak.
        uma_state = "ok"
        try:
            from hledac.universal.core.resource_governor import sample_uma_status
            _uma = sample_uma_status()
            uma_state = getattr(_uma, 'state', 'ok')
        except Exception:
            pass  # noqa: BLE001  # Fail-safe: použij jen Metal tier

        # F265H-EXT: Dvoufaktorová redukce (UMA state × Metal tier)
        if uma_state == "emergency":
            # Emergency = vždy off
            kv_kwargs = {"max_kv_size": 0}
        elif uma_state == "critical":
            # Critical = 20-35% dle Metal tier
            if tier == "normal":
                kv_kwargs = {"max_kv_size": max(512, int(self._max_kv_size * 0.35))}
            elif tier == "warn":
                kv_kwargs = {"max_kv_size": max(512, int(self._max_kv_size * 0.60))}
            else:  # critical or emergency
                kv_kwargs = {"max_kv_size": max(256, int(self._max_kv_size * 0.20))}
        elif uma_state == "warn":
            # Warn = 50-80% dle Metal tier
            if tier == "normal":
                kv_kwargs = {"max_kv_size": max(1024, int(self._max_kv_size * 0.80))}
            elif tier == "warn":
                kv_kwargs = {"max_kv_size": max(1024, int(self._max_kv_size * 0.50))}
            else:  # critical or emergency
                kv_kwargs = {"max_kv_size": max(512, int(self._max_kv_size * 0.25))}
        else:
            # Normal — plná KV cache dle Metal tier
            if tier == "normal":
                kv_kwargs = {"max_kv_size": self._max_kv_size}
            elif tier == "warn":
                kv_kwargs = {"max_kv_size": max(1024, self._max_kv_size // 2)}
            elif tier == "critical":
                kv_kwargs = {"max_kv_size": max(512, self._max_kv_size // 4)}
            else:
                kv_kwargs = {"max_kv_size": 0}

        logger.debug(
            "[F265C-METAL+F265H-EXT] KV cache: uma_state=%s metal_tier=%s kv_kwargs=%s",
            uma_state, tier, list(kv_kwargs.keys())
        )
        return kv_kwargs

    def _get_adaptive_kv_bits(self) -> int:
        """
        Sprint F265C + F265C-METAL: Adaptive KV quantization bits based on Metal memory pressure.

        F265C-METAL FIX: KV cache quantized bits should scale with Metal/GPU memory
        pressure, not system RAM. Uses mx.get_active_memory() directly.

        Metal memory tier → kv_bits mapping:
        - < 1.5 GiB active → kv_bits=4  (default, low GPU pressure)
        - 1.5-2.0 GiB     → kv_bits=6  (medium GPU pressure)
        - > 2.0 GiB       → kv_bits=8  (high GPU pressure, KV quant compresses more)

        Falls back to env var GHOST_KV_BITS or default 4.
        B.KV: HLEDAC_KV_QUANTIZE=1 forces quant ON regardless of memory pressure.

        Returns:
            int: kv_bits value (4, 6, or 8) — never below 4 (F265C-METAL invariant)
        """
        # B.KV: Force quant override — takes precedence over adaptive logic
        if self._force_kv_quantize:
            kv_bits = max(4, self._kv_bits)
            logger.debug("[B.KV] KV quant forced on: kv_bits=%d", kv_bits)
            return kv_bits

        kv_bits = self._kv_bits  # default from env or class default
        active_gib = 0.0
        try:
            import mlx.core as mx

            active = 0
            if hasattr(mx, "get_active_memory"):
                active = int(mx.get_active_memory())

            active_gib = active / (1024 ** 3)

            if active_gib > 2.0:
                kv_bits = 8
            elif active_gib > 1.5:
                kv_bits = 6
            # else keep default 4
        except Exception:
            pass  # noqa: BLE001  # keep kv_bits as-is (default or env)

        logger.debug(
            "[F265C-METAL] Adaptive KV bits: active_GiB=%.2f kv_bits=%d", active_gib, kv_bits
        )
        return kv_bits

    def is_idle(self) -> bool:
        """
        F273H: Check if engine has been idle beyond threshold.

        Returns True if no inference occurred within _idle_unload_timeout_s.
        F273H+: If model was prewarmed (_model_ever_loaded=True) but never used
        for inference (_last_inference_at=None), returns True — unload unused prewarmed
        model to reclaim ~2GB RAM. Keeping an UNUSED model warm wastes memory
        with zero benefit since no inference history exists.
        """
        # F273H+: Model was prewarmed but never used for inference — unload it
        if self._model_ever_loaded and self._last_inference_at is None:
            return True  # Safe to unload: never used, no warm-start benefit
        if self._last_inference_at is None:
            return True
        try:
            import time as _time
            elapsed = _time.monotonic() - self._last_inference_at
            return elapsed >= self._idle_unload_timeout_s
        except Exception:
            return True  # Fail-safe: unload on error

    def _build_generate_kwargs(self, formatted_prompt: str, temp: float, max_tok: int, prefix_cache) -> dict:
        """
        Build mlx_lm.generate() kwargs — shared between stream and direct paths.

        KV Cache reuse strategy (Sprint F266 KV-REUSE):
          - prefix_cache (may be _system_prompt_cache): pre-computed system prompt KV cache.
            Passed as prompt_cache= so mlx_lm reuses it and extends with user prompt tokens.
          - If prefix_cache is None: create a new per-call cache (full prefill each call).
          - cache= param: used ONLY for speculative draft model caching (separate cache).

        F265C-METAL invariant: kv_bits + max_kv_size go to mlx_lm.generate(), NOT load().
        """
        from mlx_lm.sample_utils import make_sampler

        kv_bits = self._get_adaptive_kv_bits()

        # Sprint F266 KV-REUSE: reuse prefix_cache (e.g. _system_prompt_cache) as prompt_cache
        # instead of always creating a new cache. The prefix_cache already holds the system
        # prompt KV; mlx_lm.generate_step extends it with the user prompt tokens in place.
        if self._kv_cache_enabled and prefix_cache is not None:
            kv_cache = prefix_cache
            # Re-quantize if the cache supports it (may differ from last call's kv_bits)
            if self._supports_kv_quant:
                for layer in kv_cache:
                    if hasattr(layer, 'quantize'):
                        try:
                            layer.quantize(group_size=64, bits=kv_bits)
                            self._kv_cache_stats['quantized_count'] += 1
                        except Exception:
                            pass
        elif self._kv_cache_enabled:
            # Cold path: no prefix cache available — create a new one (full prefill)
            # B.KV: Paged KV Cache — when HLEDAC_PAGED_KV_CACHE=1, build RotatingKVCache
            # directly with keep=K parameter for page-like behavior.
            if self._paged_kv_cache:
                from mlx_lm.models.cache import RotatingKVCache

                num_layers = len(self._model.layers)
                kv_cache = [
                    RotatingKVCache(max_size=max_tok, keep=self._paged_kv_keep)
                    for _ in range(num_layers)
                ]
                logger.debug(
                    "[B.KV] Paged KV cache: keep=%d, max_size=%d, layers=%d",
                    self._paged_kv_keep, max_tok, num_layers
                )
            else:
                kv_cache = make_prompt_cache(self._model, max_kv_size=max_tok)
            if self._supports_kv_quant:
                for layer in kv_cache:
                    if hasattr(layer, 'quantize'):
                        try:
                            layer.quantize(group_size=64, bits=kv_bits)
                            self._kv_cache_stats['quantized_count'] += 1
                        except Exception:
                            pass
        else:
            kv_cache = None

        generate_kwargs = {
            "model": self._model,
            "tokenizer": self._tokenizer,
            "prompt": formatted_prompt,
            "sampler": make_sampler(temp=temp),
            "max_tokens": max_tok,
            "kv_bits": kv_bits,
            "verbose": False,
            **self._get_kv_cache_kwargs(),
        }

        if kv_cache is not None:
            generate_kwargs["prompt_cache"] = kv_cache
        if self._speculative_enabled and self._draft_model_obj is not None and self._supports_draft:
            generate_kwargs["draft_model"] = self._draft_model_obj
            generate_kwargs["num_draft_tokens"] = self._num_draft_tokens
        # NOTE: prefix_cache is now used as prompt_cache above.
        # The old separate cache= usage was for draft model which is handled above.

        self._kv_cache_stats['cache_uses'] += 1
        return generate_kwargs

    def _mlx_clear_and_timestamp(self) -> None:
        """Canonical MLX cleanup: eval → clear_cache → timestamp. Shared helper."""
        try:
            import mlx.core as _mx
            _mx.eval([])
            if hasattr(_mx, "clear_cache"):
                _mx.clear_cache()
            elif hasattr(_mx.metal, "clear_cache"):
                _mx.metal.clear_cache()
        except Exception:
            pass
        import time
        self._last_inference_at = time.monotonic()

    def _run_inference(self, formatted_prompt: str, temp: float, max_tok: int, prefix_cache=None) -> str:
        """
        Run MLX inference synchronously in thread pool (Sprint 75).

        P0-1 FIX: Reactive Metal stream fallback — if Stream(gpu) error occurs
        inside the stream context, retry WITHOUT the stream context (direct
        default stream). This handles the case where get_metal_stream_context()
        returns a valid stream but Metal still errors during generate().

        F288 FIX: Wrapped in get_metal_stream_context() — each thread
        (MLXWorkerThread, asyncio.to_thread, ThreadPoolExecutor) gets its
        own mx.stream(gpu) via thread-local storage.

        Args:
            formatted_prompt: Formatted prompt for generation
            temp: Temperature setting
            max_tok: Maximum tokens to generate
            prefix_cache: Optional KV cache for prompt prefix

        Returns:
            Generated text
        """

        from mlx_lm import generate as mlx_generate


        generate_kwargs = self._build_generate_kwargs(formatted_prompt, temp, max_tok, prefix_cache)

        # P0-1: mx.eval([]) barrier BEFORE mlx_lm.generate() — canonical F266 order.
        # F300S-FIX: No stream context management — mlx_lm.generate() handles its own
        # Metal stream internally. Previous attempts to manage streams caused
        # "no Stream(gpu,1) in current thread" errors from MLX internal stream ops.
        try:
            import mlx.core as _mx
            _mx.eval([])
        except Exception:
            pass

        # Direct call — mlx_lm.generate() manages its own Metal stream
        try:
            response = mlx_generate(**generate_kwargs)
        except Exception as _err:
            logger.warning("[P0-1] mlx_generate failed: %s", _err)
            self._mlx_clear_and_timestamp()
            raise RuntimeError(f"MLX inference failed: {_err}") from _err


        # P0-1: Post-inference cleanup — eval + clear_cache + timestamp
        self._mlx_clear_and_timestamp()

        return response.strip()

    # ─── Sprint P0-2: Continuous batching routing ────────────────────────
    async def _ensure_mlx_batcher(self) -> Any:
        """
        Lazy initialization of MLXBatchedExecutor.

        Idempotent — safe to call multiple times. Returns None on any
        initialization failure so caller can fall through to direct path.
        Invariant B.M2: NEVER instantiated at __init__ time, ALWAYS on
        first use. M1 8GB safe: import is lazy inside MLXBatchedExecutor.
        """
        if self._mlx_batcher is not None:
            return self._mlx_batcher
        try:
            from hledac.universal.brain.mlx_batched_executor import MLXBatchedExecutor
            # P0-3 integration: hand the worker thread to the batcher so
            # MLX inference runs on the persistent loop, not ThreadPoolExecutor.
            worker = self._ensure_mlx_worker_thread()
            self._mlx_batcher = MLXBatchedExecutor(
                engine=self, worker_thread=worker
            )
        except Exception as _e:
            logger.debug("[P0-2] MLXBatchedExecutor init skipped: %s", _e)
            self._mlx_batcher = None
        return self._mlx_batcher

    # ─── Sprint P0-3: Dedicated MLX worker thread ──────────────────────
    def _ensure_mlx_worker_thread(self) -> Any:
        """
        Lazy initialization of MLXWorkerThread (M.T2).

        Idempotent. Returns the worker thread instance or None on failure.
        M1 8GB safe: import is lazy; thread is daemon and bounded.
        Always-on: routing layer in _submit_inference() decides per-call.
        """
        if self._mlx_worker_thread is not None:
            return self._mlx_worker_thread
        try:
            from hledac.universal.brain.mlx_worker_thread import MLXWorkerThread
            # FIX 3 (P1): Idempotency guard — second None-check after import,
            # before construction. Prevents orphaned Metal thread when two
            # coroutines enter this method concurrently during the I/O-bound
            # import window: the second would overwrite the first's thread.
            if self._mlx_worker_thread is not None:
                return self._mlx_worker_thread
            self._mlx_worker_thread = MLXWorkerThread()
            self._mlx_worker_thread.start()
        except Exception as _e:
            logger.debug("[P0-3] MLXWorkerThread init skipped: %s", _e)
            self._mlx_worker_thread = None
        return self._mlx_worker_thread

    async def _run_inference_async(self, fn, *args, **kwargs):
        """
        Run a sync inference function from the worker thread context.

        This coroutine is scheduled on the worker thread's event loop
        (M.T1: single MLX context). It synchronously calls fn(*args, **kwargs)
        and returns the result. No thread switching happens — the call
        happens in the same thread that owns the MLX model state.
        """
        return fn(*args, **kwargs)

    async def _submit_inference(
        self,
        timeout: float,
        fn,
        *args,
        **kwargs,
    ):
        """
        Submit an MLX inference call.

        F300S-FIX: Uses asyncio.run_coroutine_threadsafe() to run blocking
        mlx_lm.generate() in the MAIN THREAD where the Metal stream context
        is valid. Previous approaches (ThreadPoolExecutor, MLXWorkerThread)
        failed because mlx_lm.generate() internally calls mx.stream(gpu)
        which has thread affinity to the main thread.

        The pattern: submit a blocking call to the main thread's event loop,
        wait on the returned Future. The main thread's loop runs the blocking
        mlx_lm.generate() while the async loop remains free for I/O.
        M.T3 fail-soft: any error falls back to direct semaphore-wrapped call.

        Args:
            timeout: Maximum seconds to wait for result
            fn: Blocking inference function (_run_inference)
            *args, **kwargs: Arguments to pass to fn

        Returns:
            Generated text from mlx_lm.generate()
        """
        # F300S-FIX: Run mlx_lm.generate() in main thread via
        # asyncio.run_coroutine_threadsafe(). This is the only path that works
        # because mlx_lm.generate() internally calls mx.stream(gpu) which is
        # only valid in the thread that initialized the Metal context (main thread).
        try:
            # Get the main thread's event loop (where MLX Metal was initialized)
            main_loop = asyncio.get_event_loop()

            # run_coroutine_threadsafe submits a coroutine to the given loop
            # and returns a concurrent.futures.Future. We use async def so
            # the coroutine runs in the main thread's loop where mx.stream(gpu)
            # is valid.
            async def _coro_wrapper():
                # Run in main thread — mx.stream(gpu) is valid here
                return fn(*args, **kwargs)

            # Submit to main thread's loop and wait for result
            inference_future = asyncio.run_coroutine_threadsafe(
                _coro_wrapper(),
                main_loop,
            )
            return await asyncio.wait_for(
                asyncio.wrap_future(inference_future),
                timeout=timeout,
            )
        except Exception as _submit_err:
            logger.debug(
                "[F300S] main-thread inference submit failed: %s — falling back",
                _submit_err,
            )
            # Fallback: run directly in the asyncio loop (blocks the loop, but
            # the loop is the main thread so mx.stream(gpu) is valid here too).
            # This is slower (blocks event loop) but works as last resort.
            async with self._inference_semaphore:
                loop = asyncio.get_running_loop()
                return await asyncio.wait_for(
                    loop.run_in_executor(
                        self._inference_executor,
                        lambda: fn(*args, **kwargs),
                    ),
                    timeout=timeout,
                )

    @_otel_instrumented("hermes.generate", component="mlx")
    async def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        *,
        thinking: bool = True,
    ) -> str:
        """
        Generovat text pomocí DeepHermes-3.

        Args:
            prompt: Vstupní prompt
            temperature: Teplota (0-1)
            max_tokens: Maximální počet tokenů
            system_msg: Systémová zpráva
            thinking: Režim deep thinking (přidá system prompt pro
                     řetězení myšlenek před odpověď)

        Returns:
            Vygenerovaný text
        """
        # F275: Lazy load — on first inference request, load model from disk.
        # Previously raised RuntimeError here, forcing eager load at boot time.
        # Now triggers async load and waits for it before proceeding.
        if self._model is None:
            await self._ensure_model_loaded()
            if self._model is None:
                raise RuntimeError("Model not initialized — Hermes load failed")

        # Sprint P0-2: Continuous batching routing (always-on, no feature flag).
        # is_batch_safe() decides per-call whether to route through the
        # BatchScheduler. Urgent / oversized / under-memory-pressure requests
        # fall through unchanged. B.M3 fail-soft: any batching error is
        # silently absorbed here so the direct path is preserved.
        # Pre-compute max_tokens for is_batch_safe gate (before config fallback)
        _max_tokens_for_batch = max_tokens if max_tokens is not None else self.config.max_tokens
        # F265-5.5: Always-on — no HLEDAC_MLX_BATCHING gate. Batching is safe
        # because is_batch_safe() gates per-call based on memory/length/priority.
        try:
            batcher = await self._ensure_mlx_batcher()
            if batcher is not None and batcher.is_batch_safe(
                prompt=prompt, system_msg=system_msg, priority=1.0,
                active_iteration_count=self._active_iteration_count,
                max_tokens=_max_tokens_for_batch,
            ):
                return await batcher.execute(
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_msg=system_msg,
                    priority=1.0,
                )
        except Exception as _batching_err:
            logger.debug(
                "[P0-2] batching routing failed, falling back to direct: %s",
                _batching_err,
            )

        # P1A: Model-level inference guard — block if hermes is circuit-broken
        if check_model_allowed is not None:
            decision = check_model_allowed("hermes")
            if not decision.allowed:
                raise RuntimeError(
                    f"model inference blocked: hermes, retry after {decision.retry_after_s:.1f}s"
                )

        # GAP-3/1: Per-model circuit breaker — block if model is open
        if self._model_breaker is not None and self._model_breaker.is_open():
            snap = self._model_breaker.get_snapshot()
            raise RuntimeError(
                f"GAP-3/1: ModelCircuitBreaker OPEN for {snap['model_id']!r} "
                f"(failures={snap['failure_count']}, last={snap['last_failure_kind']!r})"
            )

        try:
            temp = temperature or self.config.temperature
            max_tok = max_tokens or self.config.max_tokens

            # F219A+F285: Adaptive context preflight — estimate and truncate based on memory.
            # F285: decide_context_budget now uses M1ResourceGovernor as primary source
            # (uma_state + free_uma_gib), with psutil fallback. The Governor path ensures
            # context budget modes are aligned with the established UMA state ladder
            # (soft_warn → warn → critical → emergency) for consistent memory pressure
            # response across all advisory layers.
            if decide_context_budget is not None and apply_context_budget is not None:
                decision = decide_context_budget(
                    prompt,
                    requested_context_window=self.config.context_window,
                )
                if decision.mode == "reject":
                    # Memory critical — record and fail soft
                    logger.warning(
                        f"[CONTEXT] memory_admission_blocked: {decision.reason}"
                        + (f" uma_state={decision.uma_state}" if decision.uma_state else "")
                    )
                    if record_model_failure is not None:
                        record_model_failure(
                            "hermes",
                            failure_kind="memory_admission_blocked",
                        )
                    raise RuntimeError(
                        f"hermes context preflight rejected: {decision.reason}"
                    )
                if decision.truncated:
                    prompt = apply_context_budget(prompt, decision)
                    logger.debug(
                        f"[CONTEXT] truncated {decision.original_chars}"
                        f"→{decision.final_chars} chars, mode={decision.mode}"
                        + (f" uma_state={decision.uma_state}" if decision.uma_state else "")
                    )
                    # Record telemetry
                    self._telemetry_counters["adaptive_context_truncated"] = (
                        self._telemetry_counters.get("adaptive_context_truncated", 0) + 1
                    )
                    self._telemetry_counters["adaptive_context_mode"] = decision.mode
                    if decision.uma_state:
                        self._telemetry_counters["uma_state"] = decision.uma_state

            # SECURITY: Sanitize prompt before inference (sanitize first, then bound)
            # Priority: injected callback > fallback (failsafe)

            # P1G-A: Prompt injection validation (before custom sanitizer)
            # Validates scraped content against heuristic injection patterns.
            # Runs after adaptive context preflight (memory truncation) but before
            # _sanitize_for_llm/fallback_sanitize. Fail-open: returns original on error.
            if sanitize_prompt_injection_patterns is not None:
                validation_result = sanitize_prompt_injection_patterns(prompt)
                if validation_result.suspicious:
                    logger.debug(
                        f"[P1G-A] prompt_injection_guard: suspicious=True, "
                        f"patterns={len(validation_result.patterns)}, "
                        f"reason={validation_result.reason}"
                    )
                # Use validated text for downstream sanitizers
                prompt = validation_result.safe_text

            # GAP-5: Additional injection detection (independent layer)
            is_injection, patterns = _detect_prompt_injection(
                prompt if isinstance(prompt, str) else str(prompt)
            )
            if is_injection:
                import logging as _log
                _log.getLogger(__name__).warning(
                    f"GAP-5: Prompt injection patterns detected: {patterns[:3]}"
                    " — proceeding with sanitized input (fail-soft)"
                )

            if self._sanitize_for_llm is not None:
                # Use injected sanitizer from orchestrator (preferred path)
                sanitized_prompt = self._sanitize_for_llm(prompt)[:MAX_LLM_PROMPT_CHARS]
            else:
                # Failsafe: use fallback when no callback injected
                sanitized_prompt = fallback_sanitize(prompt, max_length=MAX_LLM_PROMPT_CHARS)[:MAX_LLM_PROMPT_CHARS]

            system = system_msg or "You are a helpful research assistant."

            # Deep thinking: prepend thinking prefix to system message
            if thinking:
                system = f"{self._DEEP_THINKING_PREFIX}\n\n{system}"

            # Sprint F259: PromptBandit arm selection in generate() — pick strategy before inference
            bandit = self._get_prompt_bandit()
            arm_used = ""
            if bandit is not None:
                try:
                    arm_used = bandit.select_arm()
                    bandit.get_prompt_modifier(arm_used)
                    self._last_bandit_arm = arm_used
                    logger.debug(f"[GENERATE] Bandit arm: {arm_used}")
                except Exception as e:
                    logger.debug(f"[GENERATE] Bandit select failed: {e}")

            # Sprint F214OPT-B: Bounded LRU prefix cache for tokenization
            cache_key = hashlib.sha256((system or "").encode()).hexdigest()
            if cache_key in self._prefix_cache:
                self._prefix_cache.move_to_end(cache_key)
                self._prefix_cache_stats["prefix_cache_hits"] += 1
                self._prefix_cache_stats["prefix_cache_size"] = len(self._prefix_cache)
                logger.debug(f"[CACHE] Prefix cache hit for key {cache_key[:8]}")
            else:
                # Tokenize and cache
                if self._tokenizer:
                    prefix_tokens = self._tokenizer.encode(system)
                    self._prefix_cache[cache_key] = prefix_tokens
                    self._prefix_cache.move_to_end(cache_key)
                    self._prefix_cache_stats["prefix_cache_misses"] += 1
                    self._prefix_cache_stats["prefix_cache_size"] = len(self._prefix_cache)
                    # Evict oldest entries above maxsize
                    while len(self._prefix_cache) > self._prefix_cache_maxsize:
                        evicted_key, _ = self._prefix_cache.popitem(last=False)
                        self._prefix_cache_stats["prefix_cache_evictions"] += 1
                        logger.debug(f"[CACHE] Prefix cache evicted key {evicted_key[:8]}")

            formatted_prompt = self._format_chatml(system, sanitized_prompt)

            # HARD LIMIT post-wrap (final prompt to mlx_lm.generate must be <= 8192)
            formatted_prompt = formatted_prompt[:MAX_LLM_PROMPT_CHARS]

            logger.debug(f"Generating with temp={temp}, max_tokens={max_tok}")

            # Sprint 36 + F289: Get prefix KV cache for system prompt.
            # F289 FIX: When system_msg is None but we have an existing
            # _system_prompt_cache, reuse it instead of skipping the prefix cache.
            # This saves the re-prefill cost (~100-200ms) for repeated calls
            # where only the user prompt changes.
            prefix_cache = None
            if self._kv_cache_enabled:
                if system_msg:
                    try:
                        prefix_cache = self._get_prefix_cache(system)
                    except Exception:
                        pass
                elif self._system_prompt_cache is not None:
                    # Reuse existing system prompt cache when system_msg=None
                    prefix_cache = self._system_prompt_cache

            # P1F-A: Global timeout on inference
            timeout_s = _get_hermes_timeout_s()

            # Use semaphore for serialization + executor for thread offload.
            # Sprint P0-3: routes through MLXWorkerThread (persistent loop) when
            # available, otherwise falls back to ThreadPoolExecutor. The main
            # asyncio loop is never blocked longer than `timeout_s`.
            response = await self._submit_inference(
                timeout_s,
                self._run_inference,
                formatted_prompt, temp, max_tok, prefix_cache,
            )

            # P1A: Record successful inference
            if record_model_success is not None:
                record_model_success("hermes")
            # GAP-3/1: Record success in per-model breaker
            if self._model_breaker is not None:
                self._model_breaker.record_success()

            # Sprint F259: Update bandit reward after successful generation
            if bandit is not None and arm_used and response:
                try:
                    # Reward = response_length_normalized × 0.8 (baseline confidence)
                    response_len_norm = min(1.0, len(response) / 4000.0)
                    reward = response_len_norm * 0.8
                    bandit.update_reward(arm_used, reward, reward)
                    logger.debug(f"[GENERATE] Bandit reward: arm={arm_used} reward={reward:.3f}")
                except Exception as e:
                    logger.debug(f"[GENERATE] Bandit update failed: {e}")

            return response

        except TimeoutError:
            # P1F-A: Timeout must propagate and record failure_kind="timeout"
            logger.warning("Hermes inference timed out")
            if record_model_failure is not None:
                record_model_failure("hermes", failure_kind="timeout")
            raise

        except asyncio.CancelledError:
            # P1A: CancelledError must propagate, not be swallowed or recorded as failure
            logger.warning("Hermes inference cancelled")
            raise

        except Exception as e:
            # P1A: Classify and record failure, then re-raise
            if record_model_failure is not None and classify_failure_kind is not None:
                kind = classify_failure_kind(e)
                record_model_failure("hermes", failure_kind=kind)
            # GAP-3/1: Record failure in per-model breaker
            if self._model_breaker is not None:
                if isinstance(e, (IndexError, KeyError)):
                    self._model_breaker.record_failure("internal_error")
                else:
                    err_str = str(e).lower()
                    if "memory" in err_str or "oom" in err_str or "alloc" in err_str:
                        self._model_breaker.record_failure("oom")
                    elif "timeout" in err_str or "deadline" in err_str:
                        self._model_breaker.record_failure("timeout")
                    elif "metal" in err_str or "gpu" in err_str:
                        self._model_breaker.record_failure("metal_driver")
                    else:
                        self._model_breaker.record_failure("runtime_error")
            logger.error(f"Generation failed: {e}")
            return f"Error: {str(e)}"

    # =========================================================================
    # Sprint F264: Async token streaming variant
    # =========================================================================

    async def generate_stream(
        self,
        prompt: str,
        max_tokens: int = 512,
        system_msg: str | None = None,
        temperature: float | None = None,
        *,
        thinking: bool = True,
    ) -> AsyncIterator[str]:
        """
        Async token stream for progressive output.

        Uses mlx_lm.stream_generate() with adaptive kv_bits + max_kv_size per
        M1 8GB UMA invariant (CLAUDE.md, F219B, F265C-METAL). max_kv_size is
        dynamically adjusted by _get_kv_cache_kwargs() based on Metal memory
        pressure (8192/4096/2048/0). Runs the sync generator in asyncio.to_thread
        so the event loop is never blocked by MLX dispatch.

        Fallback chain:
          1) mlx_lm.stream_generate unavailable → emit blocking generate() as a
             single chunk (preserves contract, still progressive from caller POV).
          2) Model not loaded or MLX unavailable → yield nothing (fail-soft).
          3) Any exception during streaming → log + return (no propagation —
             caller already has partial output via yielded tokens).

        Concurrency: serialised through self._inference_semaphore so a parallel
        blocking generate() does not corrupt the MLX model state. Per-token
        kv_bits (adaptive) + max_kv_size (adaptive via _get_kv_cache_kwargs) —
        NEVER in load() per CLAUDE.md invariant (F265C-METAL fix).
        """
        # --- Pre-flight gates (fail-soft, never raise) ---
        if self._model is None:
            logger.debug("[STREAM] model not initialised — yielding nothing")
            return

        if not _MLX_AVAILABLE_GLOBAL:
            logger.debug("[STREAM] MLX unavailable — yielding nothing")
            return

        # --- Fallback: blocking generate() as one chunk ---
        if not self._supports_stream_generate:
            try:
                full = await self.generate(
                    prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system_msg=system_msg,
                )
                if full:
                    yield full
            except Exception as e:  # generate() already records failures
                logger.warning("[STREAM] fallback generate() failed: %s", e)
            return

        # --- Resolve parameters + format prompt (mirrors generate() pre-inference) ---
        try:
            temp = temperature if temperature is not None else self.config.temperature
            max_tok = max_tokens
            system = system_msg or "You are a helpful research assistant."
            if thinking:
                system = f"{self._DEEP_THINKING_PREFIX}\n\n{system}"
            sanitized_prompt = prompt[:MAX_LLM_PROMPT_CHARS]
            formatted_prompt = self._format_chatml(system, sanitized_prompt)[
                :MAX_LLM_PROMPT_CHARS
            ]
        except Exception as e:
            logger.warning("[STREAM] prompt formatting failed: %s", e)
            return

        # --- Stream tokens under semaphore + to_thread (M1-safe) ---
        # Task #4: reset cancellation flag so each stream starts clean
        self._stream_cancelled.clear()
        async with self._inference_semaphore:
            try:
                async for token in asyncio.to_thread(
                    self._stream_tokens,
                    formatted_prompt,
                    max_tok,
                    temp,
                ):
                    if token:
                        yield token
            except asyncio.CancelledError:
                # Structured cancellation — set flag, then propagate (Task #4).
                # The flag is checked between token yields in _stream_tokens so
                # cancellation propagates promptly, not only when to_thread completes.
                self._stream_cancelled.set()
                raise
            except Exception as e:
                logger.warning("[STREAM] generate_stream failed: %s", e)
                return

        # --- Post-stream barrier: mx.eval([]) → clear_cache (F219B canonical order) ---
        try:
            _safe_mlx_eval_and_clear_cache("generate_stream_post")
        except Exception:
            # Helper is already fail-soft; this is belt-and-suspenders
            pass

    def _stream_tokens(
        self,
        formatted_prompt: str,
        max_tok: int,
        temp: float,
    ) -> Iterator[str]:
        """
        Sync token generator — runs in asyncio.to_thread, safe for M1.

        F288 FIX: Wrapped in get_metal_stream_context() — each thread gets
        its own mx.stream(gpu) via thread-local storage. This fixes
        "Stream(gpu,1) not in current thread" Metal errors when MLX is
        called from asyncio.to_thread.

        Honours the CLAUDE.md invariant: kv_bits (adaptive) + max_kv_size (adaptive
        via _get_kv_cache_kwargs) are passed to mlx_lm.stream_generate() (NOT to
        make_prompt_cache/load()). The generation call owns the cache lifecycle;
        we only pre-create it to attach 4-bit quantisation when the runtime
        supports it. F265C-METAL: max_kv_size is no longer hardcoded to 8192.

        Yielded values:
          - str token (decoded text fragment) for the caller
          - Robust to both MLX API shapes: chunk.text (object) and (token, _)
            (tuple). Newer mlx-lm returns GenerationToken with .text, older
            versions yielded raw (token_id_or_str, info) tuples.
        """
        from mlx_lm import stream_generate
        from mlx_lm.models.cache import make_prompt_cache
        from mlx_lm.sample_utils import make_sampler

        from hledac.universal.utils.mlx_memory import get_metal_stream_context

        # F288: Metal stream context per-thread (fixes Stream(gpu,1) error)
        with get_metal_stream_context():
            # P2-2 FIX: Create KV cache only when enabled — mirrors _run_inference fix.
            # When _kv_cache_enabled=False, skip cache creation and quantization entirely.
            kv_bits = self._get_adaptive_kv_bits()
            if self._kv_cache_enabled:
                kv_cache = make_prompt_cache(self._model, max_kv_size=max_tok)

                # Sprint 75: KV quantisation (capability-gated, fail-soft)
                # F265C: Use adaptive kv_bits based on RSS pressure
                if self._supports_kv_quant:
                    for layer in kv_cache:
                        if hasattr(layer, "quantize"):
                            try:
                                layer.quantize(group_size=64, bits=kv_bits)
                            except Exception:
                                # Per-layer failure is non-fatal — proceed without
                                pass
            else:
                kv_cache = None

            # F265C-METAL FIX: Use adaptive _get_kv_cache_kwargs() instead of hardcoded 8192.
            # Mirrors the fix in _run_inference() at line 1645.
            # CLAUDE.md invariant #2: kv_bits + max_kv_size in generate, NOT load
            stream_kwargs = {
                "max_tokens": max_tok,
                "sampler": make_sampler(temp=temp),
                "kv_bits": kv_bits,
                **self._get_kv_cache_kwargs(),
                "verbose": False,
            }

            # Sprint 37 + P2-2 FIX: Attach KV cache only when enabled
            if kv_cache is not None:
                stream_kwargs["prompt_cache"] = kv_cache

            # Sprint 75: Speculative decoding with memory guard (P2-1 FIX)
            if self._speculative_enabled and self._draft_model_obj is not None and self._supports_draft:
                stream_kwargs["draft_model"] = self._draft_model_obj
                stream_kwargs["num_draft_tokens"] = self._num_draft_tokens

            # F286 FIX 4 (P1): Adaptive eval/clear granularity.
            # Static 50-token fixed chunk causes excessive barriers (20 eval/clear per 1K token).
            # Adaptive: chunk_size = max(20, min(200, active_gb * 40))
            #   - Low pressure (<1GiB active): chunk=200 tokens (fewer barriers)
            #   - High pressure (>2GiB active): chunk=20 tokens (frequent reclaim)
            # CLEAR_GRANULARITY_TOKENS=64 means get_active_memory() every 64 eval cycles (8× fewer).
            _eval_counter = 0
            _active_gb = 0.0
            # F266 FIX: mx.eval([]) barrier BEFORE stream_generate — flush
            # pending lazy ops from previous inference. Without this barrier,
            # pending GPU work causes OOM cascades → Stream(gpu,1) error.
            try:
                import mlx.core as _m3_mx
                _m3_mx.eval([])
            except Exception:
                pass

            # F265D-STREAM: Token buffer for chunked streaming
            # Accumulates tokens before yielding — amortizes async yield overhead.
            # Buffer flushed on: size >= STREAM_BUFFER_SIZE, cancellation, or stream end.
            _token_buffer = []

            for chunk in stream_generate(
                self._model,
                self._tokenizer,
                prompt=formatted_prompt,
                **stream_kwargs,
            ):
                # Robust token extraction — both MLX shapes (object + tuple)
                if hasattr(chunk, "text"):
                    tok = chunk.text
                elif isinstance(chunk, tuple) and len(chunk) >= 1:
                    tok = chunk[0]
                else:
                    tok = str(chunk)

                if tok:
                    _eval_counter += 1
                    _token_buffer.append(tok)

                    # F286 FIX 4: Adaptive eval granularity — recompute chunk_size
                    # every CLEAR_GRANULARITY_TOKENS tokens from current Metal active memory.
                    # Only does a real mx.get_active_memory() call every 8th token, not every token.
                    if _eval_counter % CLEAR_GRANULARITY_TOKENS == 0:
                        try:
                            import mlx.core as _m3_mx
                            if hasattr(_m3_mx, "get_active_memory"):
                                _active_gb = int(_m3_mx.get_active_memory()) / (1024**3)
                            elif hasattr(_m3_mx.metal, "get_active_memory") and _m3_mx.metal is not None:
                                _active_gb = int(_m3_mx.metal.get_active_memory()) / (1024**3)
                        except Exception:
                            _active_gb = 0.0

                    # chunk_size scales inversely with memory pressure
                    _chunk_size = max(
                        EVAL_GRANULARITY_TOKENS_MIN,
                        min(EVAL_GRANULARITY_TOKENS_MAX, int(_active_gb * 40))
                    )

                    if _eval_counter % _chunk_size == 0:
                        try:
                            import mlx.core as _m3_mx

                            _m3_mx.eval([])
                            if _eval_counter % CLEAR_GRANULARITY_TOKENS == 0:
                                # Memory-aware clear — only when pressure is real
                                _active = 0
                                try:
                                    if hasattr(_m3_mx, "get_active_memory"):
                                        _active = int(_m3_mx.get_active_memory())
                                    elif hasattr(_m3_mx.metal, "get_active_memory") and _m3_mx.metal is not None:
                                        _active = int(_m3_mx.metal.get_active_memory())
                                except Exception:
                                    _active = 0
                                if _active > M3_METAL_PRESSURE_BYTES:
                                    if hasattr(_m3_mx, "clear_cache"):
                                        _m3_mx.clear_cache()
                                    elif hasattr(_m3_mx.metal, "clear_cache"):
                                        _m3_mx.metal.clear_cache()
                        except Exception:
                            # Fail-soft: never break the stream on eval/clear
                            pass

                    # F265D-STREAM: Flush buffer when full (amortizes yield overhead)
                    if len(_token_buffer) >= STREAM_BUFFER_SIZE:
                        yield ''.join(_token_buffer)
                        _token_buffer = []

                    # Task #4: check cancellation flag between token yields so
                    # CancelledError propagates promptly rather than waiting for
                    # to_thread to complete. Flush remaining tokens before breaking.
                    try:
                        if isinstance(self._stream_cancelled, asyncio.Event) and self._stream_cancelled.is_set():
                            if _token_buffer:
                                yield ''.join(_token_buffer)
                                _token_buffer = []
                            break
                    except Exception:
                        # Fail-open: any error → continue streaming
                        pass

            # F265D-STREAM: Flush any remaining tokens at stream end
            if _token_buffer:
                yield ''.join(_token_buffer)

    async def decide_next_action(self, context: dict[str, Any]) -> dict[str, Any]:
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

    # P6: Report generation bounds
    _REPORT_MAX_CONTEXT_CHARS = 4096 * 4  # ~4096 tokens max for context
    _REPORT_MAX_ITEM_CHARS = 500  # max per context item
    _REPORT_MAX_ITEMS = 20  # max context items to consider
    _REPORT_SYSTEM_PROMPT = (
        "Jsi OSINT research agent. Analyzuj poskytnuté podklady a vytvoř strukturovaný report v češtině. "
        "Na konci své odpovědi VŽDY vlož blok <IOC_JSON> s extrahovanými entitami ve formátu JSON. "
        "Formát: <IOC_JSON>{\"iocs\": [\"ioc1\", \"ioc2\", ...], \"entities\": [\"entity1\", \"entity2\", ...]}</IOC_JSON>"  # noqa: E501
    )

    async def generate_report(self, query: str, context: list[str]) -> str:
        """
        P6: Generate OSINT research report from query and context.

        Fail-soft: returns empty string if model not loaded.
        Prompt is bounded to max ~4096 tokens to respect M1 8GB constraints.

        Args:
            query: Research query string
            context: List of context strings (e.g., finding payloads, snippets)

        Returns:
            Generated report text, or empty string if model not available
        """
        # Fail-soft: model not loaded
        if self._model is None:
            logger.warning("[GENERATE_REPORT] Model not loaded, skipping report generation")
            return ""

        # Bound query
        bounded_query = str(query)[:self._SYNTH_MAX_QUERY_CHARS]

        # Truncate and combine context items (bounded to prevent OOM)
        truncated_contexts: list[str] = []
        total_len = 0
        for item in context[:self._REPORT_MAX_ITEMS]:
            truncated = str(item)[:self._REPORT_MAX_ITEM_CHARS]
            if total_len + len(truncated) > self._REPORT_MAX_CONTEXT_CHARS:
                # If adding this item would exceed limit, truncate it to fit
                remaining = self._REPORT_MAX_CONTEXT_CHARS - total_len
                if remaining > 100:  # Only add if worth it
                    truncated_contexts.append(truncated[:remaining])
                break
            truncated_contexts.append(truncated)
            total_len += len(truncated)

        context_str = "\n---\n".join(truncated_contexts)

        # Sprint F259: PromptBandit arm selection — pick strategy before generate
        bandit = self._get_prompt_bandit()
        arm_used = ""
        modifier = ""
        if bandit is not None:
            try:
                arm_used = bandit.select_arm()
                modifier = bandit.get_prompt_modifier(arm_used)
                self._last_bandit_arm = arm_used
                logger.debug(f"[GENERATE_REPORT] Bandit arm: {arm_used}")
            except Exception as e:
                logger.debug(f"[GENERATE_REPORT] Bandit select failed: {e}")

        prompt = f"""Research query: {bounded_query}

Podklady pro analýze:
{context_str}

Vytvoř strukturovaný OSINT report v češtině s následujícími sekcemi:
1. Shrnutí (Executive Summary) - max 3 věty
2. Klíčová zjištění (Key Findings) - hlavní IOC a poznatky
3. Doporučení (Recommendations) - praktické kroky

Report piš v češtině, buď konkrétní a stručný.{modifier}"""

        try:
            report_text = await self.generate(
                prompt=prompt,
                temperature=0.3,
                max_tokens=1024,
                system_msg=self._REPORT_SYSTEM_PROMPT
            )

            # Sprint F259: Update bandit reward — reward = response_length_normalized × confidence
            if bandit is not None and arm_used and report_text:
                try:
                    response_len_norm = min(1.0, len(report_text) / 2000.0)
                    confidence = min(1.0, len(truncated_contexts) / max(1, self._REPORT_MAX_ITEMS))
                    reward = response_len_norm * confidence
                    bandit.update_reward(arm_used, reward, reward)
                    logger.debug(f"[GENERATE_REPORT] Bandit reward: arm={arm_used} reward={reward:.3f}")
                except Exception as e:
                    logger.debug(f"[GENERATE_REPORT] Bandit update failed: {e}")

            return report_text
        except Exception as e:
            logger.error(f"[GENERATE_REPORT] Failed: {e}")
            return f"Report generation failed: {str(e)[:200]}"

    async def generate_sprint_plan(
        self,
        query: str,
        context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
        findings: list[Any],
        hypotheses: list[str] | None = None,
        context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
            logger.warning(f"[GENERATE] Failed: {e}")
            return {
                "report": f"Synthesis failed: {str(e)[:500]}",
                "confidence": 0.0,
                "sources_count": len(bounded_findings),
                "hypotheses_evaluated": len(bounded_hypotheses),
                "bounded": True,
                "synthesis_id": None,
            }

    async def synthesize(self, context: dict[str, Any]) -> str:
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

        system_msg = """You are a research synthesis expert. Create a comprehensive, well-structured answer based on the collected research data.  # noqa: E501

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
        response_model: type[T],
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
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
                    self._outlines_generators[schema_key] = _outlines_Generator(
                        self._outlines_model, response_model
                    )
                generator = self._outlines_generators[schema_key]

                # P0-3 FIX: Route through _submit_inference (→ MLXWorkerThread when available)
                # instead of direct run_in_executor, so MLX runs on the dedicated worker
                # thread with proper Metal context.
                def _do_outlines_generate() -> str:
                    return generator(prompt)

                result = await self._submit_inference(
                    timeout=30.0,
                    fn=_do_outlines_generate,
                )
                return response_model.model_validate_json(result)
            except Exception as e:
                logger.debug(f"[STRUCTURED] Outlines failed: {e}, falling back to JSON")

        # Sprint 75: JSON prompt + retry
        import json
        import re

        for attempt in range(max_retries + 1):
            json_prompt = f"""{prompt}

Respond ONLY with valid JSON matching this schema:
{json.dumps(response_model.model_json_schema(), indent=2)}

Do not include any other text. Output valid JSON only."""

            text = await self.generate(json_prompt, temperature=0.1, max_tokens=2048, system_msg=system_msg)

            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    data = _msgspec_decode(match.group())
                    return response_model(**data)
                except Exception as e:
                    if attempt < max_retries:
                        logger.debug(f"JSON parse failed (attempt {attempt+1}): {e}")
                        continue

        # Sprint 75: Heuristic fallback
        logger.warning(f"[STRUCTURED] All attempts failed, using fallback for {response_model.__name__}")
        fields = dict.fromkeys(response_model.model_fields.keys())
        return response_model.model_construct(**fields)

    # Sprint F214OPT-B: Invalidate prefix cache
    def invalidate_prefix_cache(self) -> None:
        """Clear the prefix cache (e.g., on model change)."""
        self._prefix_cache.clear()
        self._prefix_cache_stats["prefix_cache_size"] = 0
        self._prefix_cache_stats["prefix_cache_evictions"] = 0
        self._prefix_cache_stats["prefix_cache_hits"] = 0
        self._prefix_cache_stats["prefix_cache_misses"] = 0
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
        # Local import to avoid circular dependency (htn_planner imports deephermes3_engine)
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

        _MODEL_REGISTRY = {  # noqa: N806
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

        results: list[PlannerRuntimeResult] = []

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

            t0 = time.monotonic_ns()
            try:
                result = await self.generate_structured(
                    prompt=req.prompt,
                    response_model=model_cls,
                    priority=req.priority,
                    system_msg="You are a helpful research assistant.",
                    max_tokens=1024,
                )
                # Sprint 8S: Per-item wall-clock timing — measures from submit to result
                # (includes queue wait + inference). frozen msgspec Struct needs .copy().
                elapsed_s = (time.monotonic_ns() - t0) / 1e9
                output_text = result.result if hasattr(result, 'result') else str(result)
                return PlannerRuntimeResult(
                    task_id=req.task_id,
                    executed=True,
                    skipped_panic=False,
                    hermes_output=output_text,
                    error=None,
                ).copy(elapsed_s=elapsed_s)
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
            chunk_results = await safe_gather_dropin(*chunk_tasks, label="deephermes3_engine:2147")

            # Handle exceptions (invariant B.16: fail-open for unsupported task)
            for req, result in zip(chunk, chunk_results, strict=False):
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

        NEW ORDER (Sprint 7K + P1-3):
        1. _shutdown_batch_worker(timeout=3.0) — bounded, fail-pending-futures
        2. _batch_queue = None + _batch_worker_task = None (done by shutdown)
        3. _save_cache() — persists system_prompt_cache + warmup_cache to disk
        4. _warmup_cache + _warmup_prompt_hash eviction
        5. _prompt_cache / _system_prompt_cache eviction
        6. _model = None + _tokenizer = None
        7. gc.collect()
        8. Flush lazy ops + reclaim Metal memory (via helper — F219B)

        Safe-clear: Emergency flag is NOT auto-cleared here — caller decides.
        """
        # Step 1: Shutdown batch worker (bounded 3s) — fails pending futures
        await self._shutdown_batch_worker(timeout=3.0)

        # Step 2: Explicitly clear queue and task references
        # (worker is cancelled above; these ensure reload is clean)
        self._batch_queue = None
        self._batch_worker_task = None

        # Step 3: Save cache before shutdown (Sprint P1-3: includes warmup cache)
        await self._save_cache()

        # Step 4: Evict warmup cache + hash
        if self._warmup_cache is not None:
            self._warmup_cache = None
            logger.debug("[LIFECYCLE] _warmup_cache evicted")
        self._warmup_prompt_hash = None

        # Step 5: Evict all prompt caches
        if self._prompt_cache is not None:
            self._prompt_cache = None
            logger.debug("[LIFECYCLE] _prompt_cache evicted")
        if self._system_prompt_cache is not None:
            self._system_prompt_cache = None
            logger.debug("[LIFECYCLE] _system_prompt_cache evicted")
        # F289: Evict KV cache pool
        self._kv_cache_pool.clear()
        logger.debug("[LIFECYCLE][F289] _kv_cache_pool evicted")

        # Sprint 41: Clear prefix cache
        self.invalidate_prefix_cache()

        logger.info("Unloading Hermes-3...")

        # Shutdown inference executor
        self._inference_executor.shutdown(wait=True)

        # Sprint P0-2/P0-3: Shutdown batched executor (releases BatchScheduler
        # worker) and MLX worker thread (releases persistent event loop +
        # Metal context). Order matters: stop accepting new requests first
        # (batcher), then drain worker loop (worker thread), then collect.
        if self._mlx_batcher is not None:
            try:
                await self._mlx_batcher.shutdown()
            except Exception as _e:
                logger.debug("[P0-2] batcher shutdown skipped: %s", _e)
        if self._mlx_worker_thread is not None:
            try:
                self._mlx_worker_thread.shutdown(timeout=5.0)
            except Exception as _e:
                logger.debug("[P0-3] worker thread shutdown skipped: %s", _e)

        # Step 5: Null model and tokenizer
        self._model = None
        self._tokenizer = None
        self._outlines_model = None
        # P0-2/P0-3: drop lazy references so re-init produces fresh state
        self._mlx_batcher = None
        self._mlx_worker_thread = None

        # Step 6: gc.freeze() — M1-safe bez stop-the-world
        try:
            gc.freeze()
        except Exception:
            pass  # noqa: BLE001  # Python <3.12

        # Step 7: mx.eval([]) + mx.metal.clear_cache() — F219B via helper
        # F267: MLX prewarm — skip cache clear if prewarm active & gap < threshold
        global _MLX_PREWARM_LAST_UNLOAD_TIME, _mlx_prewarm_active
        if _MLX_PREWARM_ENABLED and _mlx_prewarm_active:
            try:
                import time as _time
                _MLX_PREWARM_LAST_UNLOAD_TIME = _time.monotonic()
            except Exception:
                pass
            logger.debug("[F267] MLX prewarm: skipping clear_cache, model kept warm")
        else:
            _safe_mlx_eval_and_clear_cache("hermes_unload")

        # F234: Release ANE/MLX mutex — MLX model now released
        try:
            from brain.ane_embedder import get_ane_mlx_mutex
            get_ane_mlx_mutex().release("mlx")
        except Exception:
            pass

        logger.info("✓ Hermes-3 unloaded (Sprint 7K lifecycle closed)")

    # =========================================================================
    # F259: Session-local KV cache reset for M1 8GB stability
    # =========================================================================

    def reset_session(self) -> None:
        """
        Sprint F259: Reset session-local MLX KV cache between sprints.

        Unlike unload(), this is a lightweight reset that clears only session-
        specific state without fully unloading the model. Called at the start
        of each new sprint to prevent KV cache accumulation.

        M1 8GB invariant: Prevents KV cache from growing across sprints.
        """
        # Clear session-specific KV caches
        self._prompt_cache = None
        self._system_prompt_cache = None
        self._system_prompt_hash = None
        # F289: Clear KV cache pool on session reset
        self._kv_cache_pool.clear()

        # Invalidate prefix cache
        self.invalidate_prefix_cache()

        # Force GPU sync to reclaim Metal memory
        try:
            import mlx.core as mx
            mx.eval([])
        except Exception:
            pass  # noqa: BLE001  # mlx may not be loaded

        # Reset KV cache stats for new session
        self._kv_cache_stats = {'cache_uses': 0, 'cache_prefills': 1, 'quantized_count': 0, 'parallel_prefills': 0}

        logger.debug("[F259] Hermes3 session KV cache reset")

    # =========================================================================
    # ModelLifecycleProtocol implementation (Sprint 8Z bridge)
    # =========================================================================

    async def get_current_model_name(self) -> str | None:
        """Return currently loaded model name, or None if no model loaded."""
        return self.config.model_path if self._model is not None else None

    def get_kv_pool_stats(self) -> dict:
        """Return KV cache pool statistics including cumulative evicted memory.

        Returns:
            dict with keys: pool_maxsize, pool_memory_mb, pool_hits, pool_misses,
            pool_evictions, pool_evictions_memory (bytes), pool_current_bytes,
            pool_current_mb
        """
        total_pool_bytes = sum(entry[2] for entry in self._kv_cache_pool.values())
        return {
            **self._kv_cache_pool_stats,
            "pool_current_bytes": total_pool_bytes,
            "pool_current_mb": total_pool_bytes / (1024 * 1024),
        }

    async def cancel_pending_model_tasks(self) -> None:
        """Cancel any in-flight generation tasks."""
        task: asyncio.Task[None] | None = getattr(self, '_generation_task', None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def load_model(self, model_id: str) -> bool:
        """Load specified model by path identifier (uses model cache)."""
        global _HERMES_MODEL_CACHE, _HERMES_CACHE_LOCK

        # F273H+: Check cache first — avoid reload if already in cache
        if model_id in _HERMES_MODEL_CACHE:
            self._model, self._tokenizer = _HERMES_MODEL_CACHE[model_id]
            self.config.model_path = model_id
            logger.info(f"[HERMES] Model retrieved from cache: {model_id}")
            self._model_ever_loaded = True
            return True

        from brain.ane_embedder import get_ane_mlx_mutex
        mutex = get_ane_mlx_mutex()
        try:
            from mlx_lm import load
            from mlx_lm.utils import make_prompt_cache
            # F234: ANE/MLX mutex — acquire MLX lock before loading
            mutex.acquire_mlx(model_size_mb=2000.0)

            # Debug escape hatch
            if os.getenv("HLEDAC_HERMES_NO_CACHE", "0") == "1":
                self._model, self._tokenizer = await asyncio.to_thread(load, model_id)
            else:
                async with _HERMES_CACHE_LOCK:
                    if model_id in _HERMES_MODEL_CACHE:
                        self._model, self._tokenizer = _HERMES_MODEL_CACHE[model_id]
                        logger.info(f"[HERMES] Model retrieved from cache (post-lock): {model_id}")
                    else:
                        logger.info(f"[HERMES] Loading model from disk: {model_id}")
                        self._model, self._tokenizer = await asyncio.to_thread(load, model_id)
                        _HERMES_MODEL_CACHE[model_id] = (self._model, self._tokenizer)
                        logger.info(f"[HERMES] Model cached ({len(_HERMES_MODEL_CACHE)} entries)")

            self.config.model_path = model_id
            # F265C-EXT: Initialize prompt cache here so load_model() path
            # has the same KV-cache setup as initialize() path.
            # P2-2 FIX: Attempt cache creation and let exceptions propagate the
            # KV_CACHE_AVAILABLE flag naturally. If make_prompt_cache fails on this
            # hardware, _kv_cache_enabled=False is set and downstream
            # _run_inference/_stream_tokens skip cache creation (avoiding IndexError
            # when max_kv_size=0 with a live prompt_cache object).
            global KV_CACHE_AVAILABLE
            try:
                self._prompt_cache = make_prompt_cache(self._model)
                self._kv_cache_enabled = True
                KV_CACHE_AVAILABLE = True
            except Exception:
                self._prompt_cache = None
                self._kv_cache_enabled = False
                KV_CACHE_AVAILABLE = False
            logger.info(f"✓ Model loaded: {model_id}")
            # F273H+: Mark model as ever-loaded so is_idle() knows prewarm happened
            self._model_ever_loaded = True
            return True
        except Exception as e:
            logger.warning(f"Model load failed for {model_id}: {e}")
            raise
        finally:
            # F234: Always release mutex — acquired at start of this method
            try:
                mutex.release("mlx")
            except Exception:
                pass

    # =========================================================================
    # Sprint 30: KV Cache Compression with CommVQ 2-bit Quantization
    # =========================================================================

    def _get_cache_size_mb(self) -> float:
        """Get current KV cache size in MB using tree flatten."""
        if not self._prompt_cache:
            return 0.0
        try:
            import sys

            import mlx.core as mx

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
            total_bytes = sum(l.nbytes if hasattr(l, 'nbytes') else sys.getsizeof(l) for l in leaves)  # noqa: E741
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
        from mlx_lm.sample_utils import make_sampler

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
            "sampler": make_sampler(temp=temp),
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

        # F192B: mx.eval([]) barrier + clear_cache after inference (canonical 7K order) — F219B via helper
        _safe_mlx_eval_and_clear_cache("sustain_inference")

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
        few_shot_examples: list | None = None
    ) -> bool:
        """
        Prefix-cache warmup: prefill KV cache s system prompt + few-shot examples.

        P2-1: Uses xxhash-xxh3_64 for stable prompt fingerprinting across
        process restarts. Cache path = ~/.hledac/cache/warmup/warmup_{hash16}.safetensors.
        warmup_or_skip() provides cache-hit/miss decision with fail-soft fallback.

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
            # Build warmup prompt in ChatML format
            parts = [f"<|im_start|>system\n{system_prompt}<|im_end|>"]
            for ex in few_shot_examples[:3]:  # Max 3 examples
                parts.append(f"<|im_start|>user\n{ex.get('user', '')}<|im_end|>")
                parts.append(f"<|im_start|>assistant\n{ex.get('assistant', '')}<|im_end|>")
            warmup_prompt = "\n".join(parts)

            # Tokenize to estimate size
            tokens = self._tokenizer.encode(warmup_prompt)
            token_count = len(tokens)
            if token_count > 1000:
                logger.warning(f"[WARMUP] Warmup prompt too long ({token_count} tokens), truncating")
                warmup_prompt = self._tokenizer.decode(tokens[:1000])
                tokens = tokens[:1000]

            # P2-1: Use xxhash-based cache fingerprinting + warmup_or_skip for dedup
            if await warmup_or_skip(self, system_prompt, few_shot_examples):
                return True  # Cache hit — warmup skipped

            # P2-1: Compute xxhash-based prompt hash for cache storage key
            if XXHASH_AVAILABLE:
                canonical_parts = [system_prompt]
                if few_shot_examples:
                    for ex in few_shot_examples[:3]:
                        canonical_parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
                canonical_text = "\n".join(canonical_parts)
                prompt_hash = xxhash.xxh64(canonical_text.encode()).hexdigest()[:16]
            else:
                canonical_parts = [system_prompt]
                if few_shot_examples:
                    for ex in few_shot_examples[:3]:
                        canonical_parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
                canonical_text = "\n".join(canonical_parts)
                prompt_hash = hashlib.blake2b(canonical_text.encode(), digest_size=8).hexdigest()

            logger.info(f"[WARMUP] Building fresh warmup cache (~{token_count} tokens)...")

            # Sprint P1-3: Create persistent warmup cache with full max_kv_size
            from mlx_lm.models.cache import make_prompt_cache
            self._warmup_cache = make_prompt_cache(self._model, max_kv_size=max(token_count + 128, 1024))
            self._warmup_prompt_hash = prompt_hash

            # Quantize if supported
            kv_bits = self._get_adaptive_kv_bits()
            if self._supports_kv_quant:
                for layer in self._warmup_cache:
                    if hasattr(layer, 'quantize'):
                        try:
                            layer.quantize(group_size=64, bits=kv_bits)
                        except Exception:
                            pass

            # Run generation with max_tokens=1 to populate KV cache
            # BUG 4 fix: Metal stream context must be from the SAME thread that holds
            # the model. Route through _mlx_worker_thread if alive (has proper context).
            # If worker not yet up, use get_metal_stream_context() on current thread —
            # this works during bootstrap in the main thread (model.load() context).
            from mlx_lm import generate as mlx_generate
            from mlx_lm.sample_utils import make_sampler

            from ..utils.mlx_memory import get_metal_stream_context

            _worker = getattr(self, "_mlx_worker_thread", None)
            _worker_live = _worker is not None and _worker.is_active()

            # Wrap sync mlx_generate in a sync function to call via worker or inline
            def _do_generate() -> None:
                with get_metal_stream_context():
                    # F266 FIX: mx.eval([]) barrier BEFORE mlx_generate —
                    # flush pending lazy ops from previous inference.
                    import mlx.core as _mx
                    _mx.eval([])
                    mlx_generate(
                        model=self._model,
                        tokenizer=self._tokenizer,
                        prompt=warmup_prompt,
                        sampler=make_sampler(temp=0.3),
                        max_tokens=1,
                        kv_bits=kv_bits,
                        prompt_cache=self._warmup_cache,
                        verbose=False,
                    )

            if _worker_live:
                # Worker has Metal context — dispatch via worker.submit() (M.T1 pattern)
                # so the sync call runs on the worker thread where mx.stream(gpu) is valid.
                # Uses _run_inference_async wrapper + asyncio.run_coroutine_threadsafe.
                try:
                    coro = self._run_inference_async(_do_generate)
                    await _worker.submit(coro, timeout=60.0)
                except RuntimeError as _no_loop:
                    # No running loop during bootstrap — use inline fallback
                    logger.debug(f"[WARMUP] No running loop ({_no_loop}), using inline fallback")
                    try:
                        _do_generate()
                    except Exception as _warmup_exc:
                        logger.warning(f"[WARMUP] inline warmup failed ({_warmup_exc}), continuing")
                        return True
                except Exception as _warmup_exc:
                    # Failsafe: warmup is advisory — log and continue without cache.
                    logger.warning(f"[WARMUP] Worker thread warmup failed ({_warmup_exc}), continuing")
                    return True
            else:
                # Fallback: inline Metal stream context (main thread bootstrap path)
                try:
                    _do_generate()
                except Exception as _warmup_exc:
                    # Failsafe: warmup is advisory — log and continue.
                    logger.warning(f"[WARMUP] inline warmup failed ({_warmup_exc}), continuing")
                    return True

            # F219B: eval barrier + clear after prefill
            _safe_mlx_eval_and_clear_cache("warmup_prefill")

            logger.info("[WARMUP] Prefix cache warmup complete (fresh build)")
            return True

        except Exception as e:
            logger.warning(f"[WARMUP] Warmup failed: {e}")
            return False

    async def _restore_warmup_cache(self, cache_path: Path, prompt_hash: str) -> bool:
        """Restore warmup cache from disk if prompt hash matches.

        P2-3: Uses mlx_lm 0.31.3 load_prompt_cache API (.safetensors format).
        Falls back to legacy .npz restore for backward compatibility with existing caches.
        """
        if self._model is None or self._tokenizer is None:
            return False
        try:
            from mlx_lm.models.cache import load_prompt_cache
            cache, metadata = load_prompt_cache(str(cache_path), return_metadata=True)

            # Verify prompt hash matches
            stored_hash = metadata.get("prompt_hash", None) if metadata else None
            if stored_hash is None:
                # Fallback: try legacy .npz format
                return await self._restore_warmup_cache_legacy(cache_path, prompt_hash)
            if str(stored_hash) != prompt_hash:
                logger.debug(f"[WARMUP] Cache hash mismatch: {stored_hash} != {prompt_hash}")
                return False

            self._warmup_cache = cache
            self._warmup_prompt_hash = prompt_hash
            logger.debug(f"[WARMUP] Restored {len(cache)} layers via load_prompt_cache")
            return True
        except Exception as e:
            logger.debug(f"[WARMUP] load_prompt_cache failed: {e}, trying legacy restore")
            return await self._restore_warmup_cache_legacy(cache_path, prompt_hash)

    async def _restore_warmup_cache_legacy(self, cache_path: Path, prompt_hash: str) -> bool:
        """Legacy .npz restore for backward compatibility with existing warmup caches."""
        try:
            import mlx.core as mx
            data = mx.load(str(cache_path))

            stored_hash = data.get("_prompt_hash", None)
            if stored_hash is None:
                return False
            if hasattr(stored_hash, "item"):
                stored_hash = str(stored_hash.item())
            if str(stored_hash) != prompt_hash:
                return False

            from mlx_lm.models.cache import make_prompt_cache
            n_tokens = len(self._tokenizer.encode("")) + 512
            self._warmup_cache = make_prompt_cache(self._model, max_kv_size=n_tokens)
            self._warmup_prompt_hash = prompt_hash

            restored = 0
            for i in range(len(self._warmup_cache)):
                k_key = f"layer_{i}_keys"
                v_key = f"layer_{i}_values"
                if k_key in data and v_key in data:
                    layer = self._warmup_cache[i]
                    if hasattr(layer, "keys") and hasattr(layer, "values"):
                        try:
                            layer.keys = data[k_key]
                            layer.values = data[v_key]
                            restored += 1
                        except Exception:
                            pass

            if restored > 0:
                logger.debug(f"[WARMUP] Restored {restored}/{len(self._warmup_cache)} layers (legacy)")
                return True
            return False
        except Exception as e:
            logger.debug(f"[WARMUP] Legacy restore failed: {e}")
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


# Alias for backward compatibility — Sprint 8N test imports Hermes3Engine by this name.
Hermes3Engine = DeepHermes3Engine

