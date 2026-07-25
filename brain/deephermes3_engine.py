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
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
import msgspec
from pathlib import Path
from typing import Any, TypeVar
from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_ok, safe_wait_for
from hledac.universal.core.sync_bridge import stream_via_queue
from hledac.universal.utils.cache import PyCacheDict
from hledac.universal.utils.lru_cache import LRUCache
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode, encode_fast as _msgspec_encode_fast
from hledac.universal.utils.import_resolver import lazy, lazy_callable
from brain._hermes_cache import hermes_cache
_otel_primary = lazy('otel.instrumented')
_otel_fallback = lazy('hledac.universal.telemetry.instrumented')

def _otel_resolver() -> Any:
    """Resolve otel.instrumented with chained fallback."""
    result = _otel_primary()
    if result is not None:
        return result
    return _otel_fallback()
_otel_instrumented = _otel_resolver()
T = TypeVar('T')
_xxh3_func: Callable[[str], str] | None = None
_xxh3_func_batch: Callable[..., list[str]] | None = None

def _get_xxh3_hex(data: str) -> str:
    """Return 16-char xxh3-64 hex fingerprint via Rust backend."""
    global _xxh3_func
    if _xxh3_func is None:
        try:
            from core.rust_backend import rust
            _xxh3_func = rust.hash.ContentHasher.xxh3_64_hex
        except Exception:
            return hashlib.blake2b(data.encode(), digest_size=8).hexdigest()
    try:
        return _xxh3_func(data.encode())
    except Exception:
        return hashlib.blake2b(data.encode(), digest_size=8).hexdigest()

def _get_xxh3_hex_batch(items: list[str]) -> list[str]:
    """
    Sprint F320: Batch xxh3-64 hex — Rust rayon path ~10× faster for N≥50.

    Falls back to serial blake2b per item when Rust unavailable.
    """
    global _xxh3_func_batch
    if _xxh3_func_batch is None:
        try:
            from core.rust_backend import rust
            _xxh3_func_batch = rust.hash.batch_xxh3_64_hex
        except Exception:
            _xxh3_func_batch = None
    if _xxh3_func_batch is not None:
        try:
            return _xxh3_func_batch([s.encode() for s in items])
        except Exception:
            pass
    return [hashlib.blake2b(s.encode(), digest_size=8).hexdigest() for s in items]
WARMUP_CACHE_DIR = Path.home() / '.hledac' / 'cache' / 'warmup'

def _get_warmup_cache_path(system_prompt: str, few_shot_examples: list | None=None) -> Path:
    """Compute cache file path from system_prompt fingerprint (xxhash-xxh3_64, first 16 chars).

    P2-1: Uses xxhash-xxh3_64 instead of MLX float operations for stable hashing
    across process restarts and model unload/reload cycles.
    """
    parts = [system_prompt]
    if few_shot_examples:
        for ex in few_shot_examples[:3]:
            parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
    canonical = '\n'.join(parts)
    prompt_hash = _get_xxh3_hex(canonical)
    WARMUP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return WARMUP_CACHE_DIR / f'warmup_{prompt_hash}.safetensors'

async def warmup_or_skip(engine: DeepHermes3Engine, system_prompt: str, few_shot_examples: list | None=None) -> bool:
    """Skip warmup if unexpired cache exists for this prompt fingerprint.

    P2-1: Returns True if cache hit (warmup skipped), False if cache miss
    (fresh warmup required). Fail-soft: any error triggers fresh warmup.

    Uses xxhash-xxh3_64 for stable, fast hashing (NEON-optimized on M1).
    """
    cache_path = _get_warmup_cache_path(system_prompt, few_shot_examples)
    if not cache_path.exists():
        return False
    expected_hash = cache_path.stem.removeprefix('warmup_')
    try:
        if await engine._restore_warmup_cache(cache_path, expected_hash):
            logger.info(f'[WARMUP] Cache hit: {cache_path.name} (hash={expected_hash[:8]})')
            return True
    except Exception:
        pass
    try:
        cache_path.unlink(missing_ok=True)
    except Exception:
        pass
    return False
_fallback_sanitize_resolver = lazy('..security.pii_gate.fallback_sanitize')

def fallback_sanitize(text: str, max_length: int=8192) -> str:
    """Standalone stub when security.pii_gate unavailable."""
    resolver = _fallback_sanitize_resolver()
    if resolver is not None:
        return resolver(text, max_length)
    return text[:max_length] if text else ''
is_emergency_unload_requested = lazy('..model_lifecycle.is_emergency_unload_requested')
sanitize_prompt_injection_patterns = lazy('.prompt_injection_validator.sanitize_prompt_injection_patterns')
import re as _re_pi

def _get_metal_tier_thresholds() -> tuple[int, int, int]:
    """
    Sprint F265-METAL (Issue #4): Adaptive Metal tier thresholds.

    Probes get_metal_limit_bytes_py() from Rust adaptive_scheduler which internally
    calls Python get_dynamic_metal_cache_limit() — the MEM-2 dynamic ceiling.
    Computes thresholds as fractions of that dynamic limit:
      emergency = limit * 1.75  (active > 1.75× limit → emergency)
      critical = limit * 1.05  (active > 1.05× limit → critical)
      warn     = limit * 0.70  (active > 0.70× limit → warn)
      normal   = below warn

    Fallback: uses the static constants below if Rust call fails.
    """
    try:
        from hledac.universal import rust_extensions as _rust  # type: ignore[attr-defined]
        limit_bytes = _rust.get_metal_limit_bytes_py()
        if limit_bytes > 0:
            return (int(limit_bytes * 1.75), int(limit_bytes * 1.05), int(limit_bytes * 0.7))
    except Exception:
        pass
    return (2684354560, 1610612736, 1073741824)
_mx_resolver = lazy('mlx.core')
MLX_AVAILABLE = _mx_resolver() is not None
mx = _mx_resolver() if MLX_AVAILABLE else None
_FALLBACK_CACHE_BYTES: int = 32 * 1024 * 1024
from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_ok, safe_wait_for, parallel
_INJECTION_PATTERNS: list = [_re_pi.compile('ignore\\s+(?:all\\s+)?previous\\s+(?:instructions?|commands?)', _re_pi.I), _re_pi.compile('(?:system|prompt)\\s*:\\s*you\\s+are\\s+(?:now\\s+)?a', _re_pi.I), _re_pi.compile('#{3,}\\s*system\\s*[:\\s]', _re_pi.I), _re_pi.compile('<\\|system\\|>', _re_pi.I), _re_pi.compile('\\bROLE\\s*:\\s*(?:admin|root|superuser)', _re_pi.I), _re_pi.compile('(?:jailbreak|DAN|do\\s+anything\\s+now)', _re_pi.I), _re_pi.compile('```\\s*system', _re_pi.I)]

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
check_model_allowed = lazy_callable('hledac.universal.brain.model_inference_guard.check_model_allowed')
classify_failure_kind = lazy('hledac.universal.brain.model_inference_guard.classify_failure_kind')
record_model_failure = lazy('hledac.universal.brain.model_inference_guard.record_model_failure')
record_model_success = lazy('hledac.universal.brain.model_inference_guard.record_model_success')
logger = logging.getLogger(__name__)
_outlines_resolver = lazy('outlines')
_outlines_module = _outlines_resolver()
OUTLINES_AVAILABLE = _outlines_module is not None
if OUTLINES_AVAILABLE:
    _outlines_Generator = getattr(_outlines_module, 'Generator', None)
    if _outlines_Generator is None:
        OUTLINES_AVAILABLE = False
        logger.warning('outlines not installed — grammar-constrained decoding disabled')
else:
    logger.warning('outlines not installed — grammar-constrained decoding disabled')
KV_CACHE_AVAILABLE = False
_hermes = hermes_cache()

def _maybe_evict_hermes_cache(reason: str) -> bool:
    """
    Backward-compatible wrapper — delegates to singleton.

    P0-04 fix: eviction now happens under RLock (no race), and the singleton's
    background monitor also triggers evictions on critical memory pressure
    independent of insert-time checks.
    """
    cache = hermes_cache()
    with cache._lock:
        count = len(cache._model_cache)
    if count == 0:
        return False
    result = cache._evict_model_internal()
    if result is not None:
        logger.debug(f'[HERMES] LRU eviction ({reason}): {result}')
        return True
    return False
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
        raw = float(os.environ.get('HLEDAC_HERMES_TIMEOUT_S', HERMES_TIMEOUT_DEFAULT_S))
        if raw <= 0:
            return HERMES_TIMEOUT_DEFAULT_S
        return max(HERMES_TIMEOUT_MIN_S, min(raw, HERMES_TIMEOUT_MAX_S))
    except (ValueError, TypeError):
        return HERMES_TIMEOUT_DEFAULT_S
_DSPY_AVAILABLE = False
try:
    import dspy
    from .dspy_signatures import DarkQuerySignature, HypothesisSignature, is_dspy_available
    _DSPY_AVAILABLE = is_dspy_available()
except ImportError:
    DarkQuerySignature = None
    HypothesisSignature = None
    _DSPY_AVAILABLE = False
HLEDAC_ENABLE_DSPY = os.environ.get('HLEDAC_ENABLE_DSPY', '0') == '1' and _DSPY_AVAILABLE
_MLX_PREWARM_ENABLED = os.environ.get('HLEDAC_MLX_PREWARM', '0') == '1'
_MLX_PREWARM_LAST_UNLOAD_TIME: float | None = None
_MLX_PREWARM_SKIP_THRESHOLD_S = 60.0
_mlx_prewarm_active: bool = False

def _safe_mlx_eval_and_clear_cache(reason: str) -> dict:
    """
    Issue #20+31 FIX: Settle lazy MLX ops and clear Metal cache.

    Canonical order (GHOST_INVARIANTS.md:80):
      gc.collect() -> mx.eval([]) -> mx.clear_cache() -> gc.collect()

    Args:
        reason: Telemetry label for this clear event.

    Returns:
        dict with keys: cleared (bool), reason (str), error (str or None)
    """
    result = {'cleared': False, 'reason': reason, 'error': None}
    try:
        import mlx.core as _mx
        import gc
        gc.collect()
        try:
            _mx.eval([])
        except Exception as _e:
            result['error'] = f'eval_failed:{_e}'
        try:
            if hasattr(_mx, 'clear_cache'):
                _mx.clear_cache()
                result['cleared'] = True
        except Exception as _e:
            result['error'] = f"{result['error']};clear_cache_failed:{_e}" if result['error'] else f'clear_cache_failed:{_e}'
        gc.collect()
    except Exception as _e:
        result['error'] = f'import_failed:{_e}'
    return result
try:
    from ..utils.mlx_cache import MLX_AVAILABLE as _MLX_AVAILABLE_GLOBAL
except ImportError:
    try:
        import mlx.core as mx
        _MLX_AVAILABLE_GLOBAL = True
    except ImportError:
        _MLX_AVAILABLE_GLOBAL = False
MAX_LLM_PROMPT_CHARS = 8192
MAX_PENDING_FUTURES = 256
EVAL_GRANULARITY_TOKENS_MIN = 32
EVAL_GRANULARITY_TOKENS_MAX = 256
CLEAR_GRANULARITY_TOKENS = 256
EVAL_EVERY_N_TOKENS = 256
M3_METAL_PRESSURE_BYTES = 2 * 1024 * 1024 * 1024
STREAM_BUFFER_SIZE = 32
STREAM_MIN_BUFFER = 8

class DeepHermesConfig(msgspec.Struct):
    """Konfigurace pro DeepHermes-3"""
    model_path: str = 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit'
    temperature: float = 0.3
    max_tokens: int = 2048
    context_window: int = 8192
    max_parallel_prefill: int = 1

class _DecisionOutput(msgspec.Struct, frozen=True):
    """Decision output for research agent — GC-free msgspec.Struct."""
    action: str
    reasoning: str
    params: dict[str, str] = msgspec.field(default_factory=dict)
    complete: bool = False

class _SynthesisOutput(msgspec.Struct, frozen=True):
    """Synthesis output — GC-free msgspec.Struct."""
    report: str
    confidence: float = 0.0

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
    match = _re_pi.search('<think>(.*?)</think>', response, _re_pi.DOTALL)
    if match:
        thinking = match.group(1).strip()
        answer = response[match.end():].strip()
    else:
        thinking = ''
        answer = response.strip()
    return {'thinking': thinking, 'answer': answer}

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
    # Duplicate __slots__ removed - see line 366ium_pressure_depth', '_batch_queue', '_batch_tie_breaker', '_batch_worker_shutting_down', '_batch_worker_task', '_compile_executor', '_compile_in_progress', '_draft_model_name', '_generation_since_clear', '_draft_model_obj', '_ema_alpha', '_flush_cycle_count', '_force_kv_quantize', '_idle_unload_timeout_s', '_inference_executor', '_inference_semaphore', '_key_locks', '_kv_bits', '_kv_cache_enabled', '_kv_cache_pool', '_kv_cache_pool_maxsize', '_kv_cache_pool_memory_mb', '_kv_cache_pool_stats', '_lazy_ops_eval_count', '_kv_cache_stats', '_last_age_bump', '_last_bandit_arm', '_last_clear_at', '_last_gpu_memory', '_last_inference_at', '_lora_adapter_path', '_lora_cache_stats', '_max_kv_size', '_mlx_batcher', '_mlx_scheduler', '_mlx_worker_thread', '_model', '_model_breaker', '_model_ever_loaded', '_num_draft_tokens', '_outlines_generators', '_outlines_model', '_paged_kv_cache', '_paged_kv_keep', '_pending_futures', '_post_executor', '_prep_executor', '_prefix_cache', '_prefix_cache_maxsize', '_prefix_cache_stats', '_prompt_bandit', '_prompt_cache', '_sanitize_for_llm', '_session_cache_maxsize', '_session_cache_memory_mb', '_session_cache_pool', '_session_cache_stats', '_speculative_enabled', '_stream_cancelled', '_supports_draft', '_supports_kv_quant', '_supports_stream_generate', '_system_prompt', '_system_prompt_cache', '_system_prompt_hash', '_telemetry_counters', '_telemetry_ema', '_tokenizer', '_warmup_cache', '_warmup_prompt_hash', 'config')
    _DEEP_THINKING_PREFIX = 'You are a deep thinking AI, you may use extremely long chains of thought to deeply consider the problem and deliberate with yourself via systematic reasoning processes to help come to a correct solution prior to answering. You should enclose your thoughts and internal monologue inside <think> </think> tags, and then provide your solution or response to the problem.'

    __slots__ = ('_active_iteration_count', '_age_bump_interval', '_batch_default_flush_interval', '_batch_flush_interval', '_batch_high_pressure_depth', '_batch_max_size', '_batch_medium_pressure_depth', '_batch_queue', '_batch_tie_breaker', '_batch_worker_shutting_down', '_batch_worker_task', '_compile_executor', '_compile_in_progress', '_draft_model_name', '_generation_since_clear', '_draft_model_obj', '_ema_alpha', '_flush_cycle_count', '_force_kv_quantize', '_idle_unload_timeout_s', '_inference_executor', '_inference_semaphore', '_key_locks', '_kv_bits', '_kv_cache_enabled', '_kv_cache_pool', '_kv_cache_pool_maxsize', '_kv_cache_pool_memory_mb', '_kv_cache_pool_stats', '_lazy_ops_eval_count', '_kv_cache_stats', '_last_age_bump', '_last_bandit_arm', '_last_clear_at', '_last_gpu_memory', '_last_inference_at', '_lora_adapter_path', '_lora_cache_stats', '_max_kv_size', '_mlx_batcher', '_mlx_scheduler', '_mlx_worker_thread', '_model', '_model_breaker', '_model_ever_loaded', '_num_draft_tokens', '_outlines_generators', '_outlines_model', '_paged_kv_cache', '_paged_kv_keep', '_pending_futures', '_post_executor', '_prep_executor', '_prefix_cache', '_prefix_cache_maxsize', '_prefix_cache_stats', '_prompt_bandit', '_prompt_cache', '_sanitize_for_llm', '_session_cache_maxsize', '_session_cache_memory_mb', '_session_cache_pool', '_session_cache_stats', '_speculative_enabled', '_stream_cancelled', '_supports_draft', '_supports_kv_quant', '_supports_stream_generate', '_system_prompt', '_system_prompt_cache', '_system_prompt_hash', '_telemetry_counters', '_telemetry_ema', '_tokenizer', '_warmup_cache', '_warmup_prompt_hash', 'config')

    def __init__(self, model_path: str | None=None, sanitize_for_llm: Callable[[str], str] | None=None):
        """
        Initialize DeepHermes3Engine.

        Args:
            model_path: Path to model (default from config)
            sanitize_for_llm: Optional callback for LLM input sanitization.
                               If provided, used instead of fallback_sanitize.
                               Signature: Callable[[str], str]
        """
        self.config = DeepHermesConfig(model_path=model_path or DeepHermesConfig.model_path)
        self._sanitize_for_llm = sanitize_for_llm
        self._model = None
        self._tokenizer = None
        self._kv_cache_enabled = False
        self._prompt_cache = None
        self._max_kv_size = 8192
        self._kv_bits = int(os.getenv('GHOST_KV_BITS', '4'))
        self._paged_kv_cache = os.getenv('HLEDAC_PAGED_KV_CACHE', '0') == '1'
        _raw_keep = os.getenv('HLEDAC_PAGED_KV_KEEP', '')
        self._paged_kv_keep: int
        try:
            self._paged_kv_keep = max(0, int(_raw_keep)) if _raw_keep.strip() else 4
        except (ValueError, TypeError):
            self._paged_kv_keep = 4
        self._force_kv_quantize = os.getenv('HLEDAC_KV_QUANTIZE', '0') == '1'
        self._outlines_model = None
        self._outlines_generators = {}
        self._draft_model_obj = None
        self._draft_model_name = None
        self._speculative_enabled = False
        self._num_draft_tokens = 4
        self._supports_stream_generate = False
        self._supports_draft = False
        self._supports_kv_quant = False
        self._kv_cache_stats = {'cache_uses': 0, 'cache_prefills': 1, 'quantized_count': 0, 'parallel_prefills': 0}
        self._system_prompt_cache = None
        self._last_inference_at: float | None = None
        self._generation_since_clear: int = 0  # L-04: throttle mx.clear_cache()
        self._last_clear_at: float | None = None
        self._model_ever_loaded: bool = False
        self._system_prompt = 'You are a helpful research assistant.'
        _raw_max_kv = os.environ.get('HLEDAC_KV_CACHE_POOL_MAXSIZE', '')
        try:
            _kv_max = int(_raw_max_kv) if _raw_max_kv.strip() else None
            self._kv_cache_pool_maxsize: int = max(1, _kv_max) if _kv_max is not None else 4
        except (ValueError, TypeError):
            self._kv_cache_pool_maxsize: int = 4
        _raw_mem = os.environ.get('HLEDAC_KV_CACHE_POOL_MEMORY_MB', '')
        try:
            _mem_mb = int(_raw_mem) if _raw_mem.strip() else None
            self._kv_cache_pool_memory_mb: int = max(32, _mem_mb) if _mem_mb is not None else 256
        except (ValueError, TypeError):
            self._kv_cache_pool_memory_mb: int = 256
        self._kv_cache_pool: LRUCache[str, tuple[Any, float, int]] = LRUCache(max_size=self._kv_cache_pool_maxsize)
        self._kv_cache_pool_stats = {'pool_maxsize': self._kv_cache_pool_maxsize, 'pool_memory_mb': self._kv_cache_pool_memory_mb, 'pool_hits': 0, 'pool_misses': 0, 'pool_evictions': 0, 'pool_evictions_memory': 0}
        self._key_locks: PyCacheDict[str, threading.Lock] = PyCacheDict(1024, 300.0)
        _raw_session_mem = os.getenv('HLEDAC_SESSION_CACHE_MEMORY_MB', '')
        try:
            _session_mem_mb = int(_raw_session_mem) if _raw_session_mem.strip() else None
            self._session_cache_memory_mb: int = max(32, _session_mem_mb) if _session_mem_mb is not None else 128
        except (ValueError, TypeError):
            self._session_cache_memory_mb: int = 128
        _raw_session_max = os.getenv('HLEDAC_SESSION_CACHE_MAXSIZE', '')
        try:
            _session_max = int(_raw_session_max) if _raw_session_max.strip() else None
            self._session_cache_maxsize: int = max(1, _session_max) if _session_max is not None else 8
        except (ValueError, TypeError):
            self._session_cache_maxsize: int = 8
        self._session_cache_pool: LRUCache[str, tuple[Any, str, float, int]] = LRUCache(max_size=self._session_cache_maxsize)
        self._session_cache_stats = {'session_cache_hits': 0, 'session_cache_misses': 0, 'session_cache_evictions': 0, 'session_cache_memory_mb': self._session_cache_memory_mb, 'session_cache_maxsize': self._session_cache_maxsize}
        _raw_max = os.environ.get('HLEDAC_HERMES_PREFIX_CACHE_MAXSIZE', '')
        try:
            _max = int(_raw_max) if _raw_max.strip() else None
            self._prefix_cache_maxsize: int = max(1, _max) if _max is not None else 64
        except (ValueError, TypeError):
            self._prefix_cache_maxsize: int = 64
        self._prefix_cache: LRUCache[str, Any] = LRUCache(max_size=self._prefix_cache_maxsize)
        self._idle_unload_timeout_s: float = float(os.getenv('HLEDAC_IDLE_UNLOAD_TIMEOUT_S', '1800.0'))
        self._prefix_cache_stats = {'prefix_cache_maxsize': self._prefix_cache_maxsize, 'prefix_cache_size': 0, 'prefix_cache_evictions': 0, 'prefix_cache_hits': 0, 'prefix_cache_misses': 0}
        self._inference_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Issue #14: CPU prep || GPU exec pipeline — 3-stage overlap
        # Stage 1 (prep): format_chatml + tokenization — CPU-bound, parallel across prompts
        # Stage 2 (GPU): mlx_lm.generate() — single Metal command queue, serial
        # Stage 3 (post): JSON parse + model_validate — CPU-bound, parallel across prompts
        self._prep_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=3, thread_name_prefix='hermes_prep'
        )
        self._post_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix='hermes_post'
        )
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        self._inference_semaphore = get_semaphore_for_testing(ConcurrencyCategory.MLX_INFERENCE)
        self._mlx_batcher: Any = None
        self._mlx_worker_thread: Any = None
        self._mlx_scheduler: Any = None
        self._compile_in_progress: bool = False
        self._compile_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._lora_adapter_path: str | None = None
        self._lora_cache_stats = {'lora_cache_hits': 0, 'lora_cache_misses': 0, 'lora_cache_evictions': 0, 'lora_applications': 0}
        self._active_iteration_count: int = 0
        self._stream_cancelled: asyncio.Event = asyncio.Event()
        self._batch_queue = None
        self._batch_worker_task: asyncio.Task | None = None
        self._batch_max_size = 8
        self._batch_default_flush_interval = 2.0
        self._batch_flush_interval = self._batch_default_flush_interval
        self._batch_medium_pressure_depth = 64
        self._batch_high_pressure_depth = 192
        self._telemetry_ema = {'enqueue_to_dispatch_ms': 0.0, 'dispatch_to_result_ms': 0.0, 'batch_size': 0, 'queue_depth': 0}
        self._telemetry_counters = {'batch_submitted': 0, 'batch_executed': 0, 'batch_fallback_single': 0, 'schema_mismatch_flushes': 0, 'length_bin_mismatch_flushes': 0, 'batch_shattered': 0, 'prompt_mismatch_flushes': 0, 'emergency_guard_triggered': 0, 'emergency_batch_rejected': 0, 'emergency_single_rejected': 0, 'emergency_pending_failed': 0, 'adaptive_flush_default_entries': 0, 'adaptive_flush_medium_entries': 0, 'adaptive_flush_fast_entries': 0, 'metal_pressure_fast_flush': 0, 'cache_invalidation_count': 0}
        self._pending_futures = set()
        self._ema_alpha = 0.3
        self._flush_cycle_count = 0
        self._age_bump_interval = 3
        self._last_age_bump = 0
        self._warmup_cache: Any = None
        self._warmup_prompt_hash: str | None = None
        self._batch_worker_shutting_down = False
        self._last_gpu_memory: int = 0
        self._model_breaker: ModelCircuitBreaker | None = None
        try:
            from transport.circuit_breaker import ModelCircuitBreaker
            self._model_breaker = ModelCircuitBreaker(model_id='hermes')
        except Exception:
            pass
        self._prompt_bandit = None
        self._last_bandit_arm: str | None = None

    def _get_prompt_bandit(self):
        """Lazy init PromptBandit (avoid heavy import at module load)."""
        if self._prompt_bandit is None:
            try:
                from hledac.universal.brain.prompt_bandit import PromptBandit
                self._prompt_bandit = PromptBandit(lambda_reg=0.01, persist_path=str(Path.home() / '.hledac' / 'hermes_prompt_bandit.json'))
                logger.debug('PromptBandit initialized for Hermes3Engine')
            except ImportError:
                self._prompt_bandit = None
                logger.debug('PromptBandit not available')
        return self._prompt_bandit

    def init_model_breaker(self, model_id: str) -> None:
        """GAP-3/1: Initialize per-model circuit breaker."""
        from transport.circuit_breaker import ModelCircuitBreaker
        self._model_breaker = ModelCircuitBreaker(model_id=model_id)

    @property
    def model(self) -> Any:
        """Canonical model reference — shares the loaded model with callers.

        M-05 fix: MemoryLayer previously loaded a separate BF16 Hermes-3 via
        mlx_lm.load() directly, causing ~6GB Metal allocation independent of
        this engine. Now MemoryLayer._load_model() delegates to this engine's
        shared _model reference via this property.
        """
        return self._model

    @property
    def tokenizer(self) -> Any:
        """Canonical tokenizer reference shared with this engine."""
        return self._tokenizer

    async def _ensure_batch_worker(self) -> None:
        """Ensure batch worker is started (lazy start)."""
        if self._batch_worker_task is None:
            self._batch_queue = asyncio.PriorityQueue(maxsize=256)
            import itertools
            self._batch_tie_breaker = itertools.count()
            self._pending_futures = set()
            self._batch_worker_shutting_down = False
            self._batch_worker_task = safe_create_task(self._batch_worker())
            logger.debug('Batch worker started')

    async def _shutdown_batch_worker(self, timeout: float=3.0) -> None:
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
        for fut in list(self._pending_futures):
            if not fut.done():
                fut.set_exception(RuntimeError('emergency_unload_requested'))
                self._telemetry_counters['emergency_pending_failed'] += 1
        self._pending_futures.clear()
        self._batch_worker_shutting_down = True
        self._batch_worker_task.cancel()
        try:
            async with asyncio.timeout(timeout):
                await asyncio.shield(self._batch_worker_task)
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            self._batch_worker_task = None
            self._batch_queue = None
            raise
        self._batch_worker_task = None
        self._batch_queue = None
        logger.debug('Batch worker shutdown complete (Sprint 7K)')

    async def _submit_structured_batch(self, prompt: str, response_model: type, priority: float=1.0, temperature: float=0.1, max_tokens: int=1024, system_msg: str | None=None) -> Any:
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
        if is_emergency_unload_requested is not None and is_emergency_unload_requested():
            self._telemetry_counters['emergency_batch_rejected'] += 1
            raise RuntimeError('emergency_unload_requested')
        import itertools
        await self._ensure_batch_worker()
        schema_key = response_model.__name__
        payload: dict = {'type': 'structured', 'prompt': prompt, 'response_model': response_model, 'temperature': temperature, 'max_tokens': max_tokens, 'system_msg': system_msg, 'future': None}
        future = asyncio.Future()
        payload['future'] = future
        if len(self._pending_futures) >= MAX_PENDING_FUTURES:
            done_futures = [f for f in self._pending_futures if f.done()]
            if done_futures:
                self._pending_futures.discard(done_futures[0])
            else:
                raise RuntimeError('pending_futures overflow')
        self._pending_futures.add(future)

        def _safe_discard(f: asyncio.Future) -> None:
            try:
                self._pending_futures.discard(f)
            except Exception:
                pass
        future.add_done_callback(_safe_discard)
        if not hasattr(self.__class__, '_batch_tie_breaker'):
            self.__class__._batch_tie_breaker = itertools.count()
        tie = next(self._batch_tie_breaker)
        future._enqueue_ns = time.monotonic_ns()  # type: ignore[attr-defined]
        assert isinstance(self._batch_queue, asyncio.PriorityQueue)
        await self._batch_queue.put((priority, tie, schema_key, payload))
        self._telemetry_ema['enqueue_to_dispatch_ms'] = self._ema_alpha * 0.0 + (1 - self._ema_alpha) * self._telemetry_ema.get('enqueue_to_dispatch_ms', 0.0)
        return future

    async def _batch_worker(self) -> None:
        """Background worker that processes batches with schema-awareness + prompt/length segregation."""
        import itertools
        itertools.count()
        while True:
            if is_emergency_unload_requested is not None and is_emergency_unload_requested():
                for fut in list(self._pending_futures):
                    if not fut.done():
                        fut.set_exception(RuntimeError('emergency_unload_requested'))
                        self._telemetry_counters['emergency_pending_failed'] += 1
                self._pending_futures.clear()
                break
            if getattr(self, '_batch_worker_shutting_down', False):
                for fut in list(self._pending_futures):
                    if not fut.done():
                        fut.set_exception(RuntimeError('engine_unloaded'))
                self._pending_futures.clear()
                break
            try:
                items = []
                current_schema_key = None
                current_prompt_hash = None
                current_length_bin = None
                flush_interval = self._current_flush_interval()
                if flush_interval >= 1.9:
                    self._telemetry_counters['adaptive_flush_default_entries'] += 1
                elif flush_interval >= 0.9:
                    self._telemetry_counters['adaptive_flush_medium_entries'] += 1
                else:
                    self._telemetry_counters['adaptive_flush_fast_entries'] += 1
                try:
                    async with asyncio.timeout(flush_interval):
                        assert isinstance(self._batch_queue, asyncio.PriorityQueue)
                        first_item = await self._batch_queue.get()
                    current_schema_key = first_item[2]
                    items.append(first_item)
                    first_payload = first_item[3]
                    first_prompt = first_payload.get('prompt', '')
                    first_system_msg = first_payload.get('system_msg')
                    current_prompt_hash = self._compute_system_prompt_hash(first_system_msg)
                    current_length_bin = self._compute_length_bin(first_prompt)
                    while len(items) < self._batch_max_size:
                        try:
                            async with asyncio.timeout(0.01):
                                item = await self._batch_queue.get_nowait()
                            item_schema = item[2]
                            item_payload = item[3]
                            item_prompt = item_payload.get('prompt', '')
                            item_system_msg = item_payload.get('system_msg')
                            item_prompt_hash = self._compute_system_prompt_hash(item_system_msg)
                            item_length_bin = self._compute_length_bin(item_prompt)
                            if item_schema != current_schema_key:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['schema_mismatch_flushes'] += 1
                                break
                            if item_prompt_hash != current_prompt_hash:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['prompt_mismatch_flushes'] += 1
                                break
                            if item_length_bin != current_length_bin:
                                await self._batch_queue.put(item)
                                self._telemetry_counters['length_bin_mismatch_flushes'] += 1
                                break
                            items.append(item)
                        except TimeoutError:
                            break
                except TimeoutError:
                    continue
                self._flush_cycle_count += 1
                if self._flush_cycle_count - self._last_age_bump >= self._age_bump_interval:
                    self._last_age_bump = self._flush_cycle_count
                    await self._age_bump_queue()
                self._telemetry_ema['queue_depth'] = self._batch_queue.qsize()
                t0 = time.monotonic()
                await self._process_batch(items)
                dispatch_ms = (time.monotonic() - t0) * 1000
                self._telemetry_ema['batch_size'] = len(items)
                self._telemetry_ema['dispatch_to_result_ms'] = self._ema_alpha * dispatch_ms + (1 - self._ema_alpha) * self._telemetry_ema['dispatch_to_result_ms']
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f'Batch worker error: {e}')

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

    def _is_batch_safe(self, response_model: Any, priority: float, stream: bool, timeout_s: float | None) -> bool:
        """
        Sprint 7G: Batch-safe eligibility check.

        Routing criteria:
        - schema type must be detectable (msgspec or pydantic)
        - not streaming
        - not urgent priority (priority == 0)
        - timeout must allow for batching (>= 2x flush interval)
        """
        if stream:
            return False
        if priority == 0:
            return False
        if response_model is None:
            return False
        if timeout_s is not None and timeout_s <= self._current_flush_interval() * 2:
            return False
        schema_cls = response_model if isinstance(response_model, type) else type(response_model)
        if not hasattr(schema_cls, '__struct_fields__') and (not hasattr(schema_cls, 'model_validate_json')):
            return False
        return True

    def _compute_length_bin(self, prompt: str) -> str:
        """Sprint 7G: Length binning — short/medium/long to prevent padding waste."""
        tokens_est = len(prompt) // 4
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
        by_schema: dict[str, list] = {}
        for priority, _tie, schema_key, payload in items:
            if schema_key not in by_schema:
                by_schema[schema_key] = []
            by_schema[schema_key].append((payload, priority))
        for schema_key, group in by_schema.items():
            try:
                if group[0][0].get('type') == 'structured':
                    await self._process_structured_batch(group)
                elif group[0][0].get('type') == 'generate':
                    for payload, _ in group:
                        future = payload.get('future')
                        if future and (not future.done()):
                            future.set_result({'processed': True})
            except Exception as e:
                logger.debug(f'Batch process error for schema {schema_key}: {e}')

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
        _batch_result = await parallel(tasks, policy="log", ctx='deephermes3:structured_batch')
        results = _batch_result.ok
        has_exception = any((isinstance(r, Exception) for r in results))
        if has_exception:
            self._telemetry_counters['batch_shattered'] += 1
            logger.debug(f'[STRUCTURED] Batch shattered: {sum((1 for r in results if isinstance(r, Exception)))} exceptions')
        for payload, result in zip([p for p, _ in items], results, strict=False):
            future = payload.get('future')
            if future and (not future.done()):
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

        Issue #14: CPU prep || GPU exec pipeline.
        Stage 1 (prep): _format_chatml in prep thread pool (parallel across prompts).
        Stage 2 (GPU): _submit_inference via MLXWorkerThread (serial).
        Stage 3 (post): JSON parse + model_validate in post thread pool (parallel).

        Each stage overlaps with GPU execution — when prompt N is being
        generated, prompt N+1 is being prepped and prompt N-1 is being parsed.
        """
        prompt = payload.get('prompt')
        response_model = payload.get('response_model')
        temperature = payload.get('temperature', 0.1)
        max_tokens = payload.get('max_tokens', 1024)
        system_msg = payload.get('system_msg')

        # Stage 1: CPU prep — parallel across prompts (prep pool, 3 workers)
        loop = asyncio.get_running_loop()
        system = system_msg or 'You are a helpful assistant.'

        def _sync_prep() -> str:
            # Issue #14: sanitize before formatting (full prep pipeline)
            raw_sanitized = self._sanitize_for_llm(prompt) if self._sanitize_for_llm else prompt
            sanitized = raw_sanitized[:MAX_LLM_PROMPT_CHARS]
            return self._format_chatml(system_msg=system, user_msg=sanitized)

        formatted_prompt = await loop.run_in_executor(self._prep_executor, _sync_prep)

        # Stage 2: GPU exec — serial (MLX single command queue)
        # M-03: Tokenize once — avoids double encode in _build_generate_kwargs
        try:
            batch_prompt_tokens = self._tokenizer.encode(formatted_prompt)
        except Exception:
            batch_prompt_tokens = None
        timeout_s = _get_hermes_timeout_s()
        # M-01: _run_inference returns (response, kv_cache_after); discard cache here
        raw_text, _ = await self._submit_inference(
            timeout_s, self._run_inference, formatted_prompt, temperature, max_tokens, None, None, batch_prompt_tokens
        )

        # Stage 3: CPU post — parallel across prompts (post pool, 2 workers)
        import re as _re
        schema_cls = response_model if isinstance(response_model, type) else type(response_model)

        def _parse_structured() -> Any:
            match = _re.search(r'\{.*\}', raw_text, _re.DOTALL)
            if match:
                try:
                    data = _msgspec_decode(match.group())
                    if hasattr(schema_cls, 'model_validate'):
                        return schema_cls.model_validate(data)  # type: ignore[union-attr]
                    return schema_cls.model_construct(**data)  # type: ignore[union-attr]
                except Exception:
                    pass
            logger.debug(f'[STRUCTURED] Parse failed, using default for {schema_cls.__name__}')
            fields = dict.fromkeys(getattr(schema_cls, 'model_fields', {}).keys())
            return schema_cls.model_construct(**fields) if hasattr(schema_cls, 'model_construct') else schema_cls(**fields)  # type: ignore[union-attr]

        return await loop.run_in_executor(self._post_executor, _parse_structured)
    async def flush_all(self, timeout: float=5.0) -> int:
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
            if hasattr(mx, 'get_active_memory'):
                return mx.get_active_memory()
        except Exception:
            pass
        return 0

    async def _ensure_model_loaded(self) -> None:
        """F273H+: Load model from cache or disk (idempotent, thread-safe).

        P0-04: Uses HermesModelCache singleton — single RLock for all access,
        active background pressure monitor corrects passive-only insert-time eviction.
        HLEDAC_HERMES_NO_CACHE=1 bypasses cache (debug escape hatch).

        C2-FIX: mlx_lm.load() is blocking I/O (disk read + Metal kernel compilation).
        Wrapped in asyncio.to_thread() to avoid blocking the event loop.
        """
        if self._model is not None and self._tokenizer is not None:
            logger.debug('[HERMES] Model already loaded, skipping cache check')
            return
        if os.getenv('HLEDAC_HERMES_NO_CACHE', '0') == '1':
            logger.debug('[HERMES] HLEDAC_HERMES_NO_CACHE=1 — loading from disk')
            model, tokenizer = await asyncio.to_thread(
                __import__('mlx_lm').load, self.config.model_path
            )
            self._model = model
            self._tokenizer = tokenizer
            return
        cache = hermes_cache()
        model_path = self.config.model_path
        result = cache.get_model(model_path)
        if result is not None:
            self._model, self._tokenizer = result
            logger.debug('[HERMES] Model retrieved from cache (LRU updated), skipping reload')
            return
        logger.info(f'[HERMES] Loading model from disk: {model_path}')
        model, tokenizer = await asyncio.to_thread(
            __import__('mlx_lm').load, model_path
        )
        self._model = model
        self._tokenizer = tokenizer
        try:
            if os.getenv('HLEDAC_HALF_PRECISION', '1') != '0':
                model.set_dtype(mx.float16)  # type: ignore[union-attr]
                logger.info('[HERMES] Model dtype set to float16 (half precision)')
        except Exception as e:
            logger.warning('[HERMES] Could not set float16 dtype: %s', e)
        cache.put_model(model_path, model, tokenizer)
        mc, lc = len(cache)  # type: ignore[arg-type]
        logger.info(f'[HERMES] Model cached ({mc} models, {lc} loras)')
        cache.start_monitor()
        self._compile_in_progress = True
        self._compile_model_warmup(model, tokenizer)

    def _compile_model_warmup(self, model: Any, tokenizer: Any) -> None:
        """
        Issue #29 + P2-FIX: Trigger MLX JIT compilation via dummy forward pass.

        mx.compile() forces the MLX JIT compiler to compile the model's forward
        graph on the first call. Without this warmup, the first real generate()
        call takes 10-30× longer as compilation happens during inference.

        P2-FIX: Fire-and-forget via dedicated ThreadPoolExecutor (1 thread).
        The compile runs in background while _ensure_model_loaded() returns immediately.
        _compile_in_progress flag stays True until compile thread completes;
        generate() lazy-waits for it via asyncio.sleep() loop.

        F300S-FIX constraint: mlx_lm.load() must run in main thread (MLX stream
        registration). mx.compile() has no such constraint — any thread with Metal
        context can run it. _compile_executor thread calls get_metal_stream_context()
        just like _run_inference does (F288 fix).
        """
        try:
            import mlx.core as mx
            sample_tokens = [tokenizer.bos_id or 1] * 4
            dummy_input = mx.array([sample_tokens])
            if self._compile_executor is None:
                self._compile_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='hermes_compile')

            def _do_compile() -> None:
                """Fire-and-forget compile — sets flag when done."""
                try:
                    with get_metal_stream_context():  # type: ignore[name-defined]
                        mx.eval([])
                        try:
                            compiled_model = mx.compile(model)
                            _ = compiled_model(dummy_input)
                            mx.eval(_)
                        except Exception:
                            _ = model(dummy_input)
                            mx.eval(_)
                    logger.info('[HERMES] MLX compile warmup complete (JIT cache compiled)')
                except Exception as _e:
                    logger.warning(f'[HERMES] Compile warmup failed: {_e}')
                finally:
                    self._compile_in_progress = False
            self._compile_executor.submit(_do_compile)
        except Exception as _e:
            self._compile_in_progress = False
            logger.warning(f'[HERMES] MLX compile warmup failed: {_e} (first inference will be slower)')

    @classmethod
    def evict_model_cache(cls) -> None:
        """F273H+: Uvolni všechny modely z paměti.

        P0-04: Delegates to HermesModelCache singleton — clears both model
        and LoRA caches, runs canonical MLX cleanup (gc.collect → mx.eval → clear_cache).
        Volat při SIGTERM nebo memory pressure.
        """
        cache = hermes_cache()
        cache.clear_models()
        cache.clear_loras()
        logger.info('[HERMES] Model + LoRA cache evicted via singleton')

    async def initialize(self) -> None:
        """Inicializovat model"""
        global KV_CACHE_AVAILABLE
        try:
            await self._ensure_model_loaded()
            logger.info('✓ Hermes-3 loaded successfully')
            if self._model_breaker is not None:
                self._model_breaker.reset()
                logger.info('[GAP-3/1] Circuit breaker reset after successful model load')
            if KV_CACHE_AVAILABLE:
                try:
                    from mlx_lm.utils import make_prompt_cache  # type: ignore[attr-defined]
                    self._prompt_cache = make_prompt_cache(self._model)
                    self._kv_cache_enabled = True
                    KV_CACHE_AVAILABLE = True
                    logger.info('✓ Prompt cache initialized (MLX)')
                except Exception as e:
                    logger.warning(f'Prompt cache init failed: {e}, continuing without it')
                    self._prompt_cache = None
                    self._kv_cache_enabled = False
            else:
                logger.info('[HERMES] KV_CACHE not available – KV cache disabled')
                self._prompt_cache = None
                self._kv_cache_enabled = False
            if OUTLINES_AVAILABLE:
                try:
                    self._outlines_model = outlines.from_mlxlm(self._model, self._tokenizer)  # type: ignore[name-defined]
                    logger.info('✓ Outlines model initialized')
                except Exception as e:
                    logger.warning(f'Outlines init failed: {e}, continuing without it')
                    self._outlines_model = None
            _skip_draft = False
            if os.environ.get('HLEDAC_DISABLE_SPEC_DECODE', '1') != '0':
                logger.info('[HERMES] Speculative decoding disabled by default on M1 8GB (HLEDAC_DISABLE_SPEC_DECODE=1)')
                _skip_draft = True
            if is_emergency_unload_requested is not None and is_emergency_unload_requested():
                logger.warning('[HERMES] Emergency unload requested — skipping draft model init')
                _skip_draft = True
            if not _skip_draft:
                try:
                    from hledac.universal.core.resource_governor import sample_uma_status_async
                    _uma = await sample_uma_status_async()
                    _uma_state = getattr(_uma, 'state', None)
                    if _uma_state in ('critical', 'emergency'):
                        logger.warning(f"[HERMES] UMA {_uma_state} ({getattr(_uma, 'system_used_gib', 0):.2f}GiB) — skipping draft model init")
                        _skip_draft = True
                except Exception:
                    pass
            if not _skip_draft:
                await self._init_draft_model()
            safe_create_task(self._bg_warmup_caches())
        except Exception as e:
            logger.error(f'Failed to load Hermes-3: {e}')
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
        self._speculative_enabled = False
        self._draft_model_name = None
        self._draft_model_obj = None
        self._supports_draft = False
        logger.info('[SPEC] Draft model disabled (M1 8GB always-on safe mode)')

    async def _init_system_prompt_cache(self) -> None:
        """Initialize persistent system-prompt cache (Sprint 75 + Sprint M4)."""
        if not KV_CACHE_AVAILABLE or self._model is None:
            return
        try:
            from pathlib import Path
            from mlx_lm.models.cache import make_prompt_cache
            _disk_cache = Path.home() / '.hledac' / 'cache' / 'system_prompt_cache.npz'
            _has_disk = await asyncio.to_thread(_disk_cache.exists)
            self._system_prompt_cache = make_prompt_cache(self._model, max_kv_size=512)
            for layer in self._system_prompt_cache:
                if hasattr(layer, 'quantize'):
                    self._supports_kv_quant = True
                    break
            if _has_disk and await self._load_cache():
                logger.info('[CACHE] System prompt cache loaded from disk (prefill skipped)')
                return
            if self._supports_stream_generate:
                import mlx_lm
                from hledac.universal.utils.mlx_memory import get_metal_stream_context
                from hledac.universal.core.mlx_inference_lock import _get_mlx_inference_lock

                def _prefill():
                    _mlx_lock = _get_mlx_inference_lock()
                    with get_metal_stream_context():
                        try:
                            import mlx.core as _mx
                            _mx.eval([])
                            with _mlx_lock:
                                for _ in mlx_lm.stream_generate(model=self._model, tokenizer=self._tokenizer, prompt=self._system_prompt, prompt_cache=self._system_prompt_cache, max_tokens=1):  # type: ignore[arg-type]
                                    pass
                        finally:
                            _safe_mlx_eval_and_clear_cache('system_prompt_cache_prefill')
                await asyncio.to_thread(_prefill)
                self._kv_cache_stats['cache_prefills'] = 1
            logger.info('[CACHE] System prompt cache initialized (cold prefill)')
        except Exception as e:
            logger.warning(f'[CACHE] System prompt cache init failed: {e}')

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
        max_parallel = getattr(self.config, 'max_parallel_prefill', 1)
        try:
            import mlx.core as mx
            device_info = mx.metal.device_info()
            device_name = device_info.get('device_name', '')
            if 'Apple' in device_name:
                max_parallel = 1
                logger.info('[FIX-1] Apple Silicon detected (%s) — forcing sequential prefill', device_name)
        except Exception:
            pass
        if self._model is None or self._tokenizer is None:
            return
        if max_parallel < 2:
            await self._init_system_prompt_cache()
            await self.warmup_prefix_cache(system_prompt=self._system_prompt, few_shot_examples=[{'user': 'What is 2+2?', 'assistant': '4'}, {'user': 'Capital of France?', 'assistant': 'Paris'}])
            return
        try:
            from pathlib import Path
            _disk_cache_path = Path.home() / '.hledac' / 'cache' / 'system_prompt_cache.npz'
            _has_sys_disk = await asyncio.to_thread(_disk_cache_path.exists)
            _has_warmup_disk = False

            async def _prefill_system_cache() -> bool:
                """Prefill system prompt cache (512 KV)."""
                try:
                    from mlx_lm.models.cache import make_prompt_cache
                    from hledac.universal.utils.mlx_memory import get_metal_stream_context
                    self._system_prompt_cache = make_prompt_cache(self._model, max_kv_size=512)
                    if not self._supports_kv_quant:
                        for layer in self._system_prompt_cache:
                            if hasattr(layer, 'quantize'):
                                self._supports_kv_quant = True
                                break
                    if _has_sys_disk and await self._load_cache():
                        logger.info('[P1-3] System prompt cache loaded from disk (parallel prefill skipped)')
                        return True
                    if self._supports_stream_generate:
                        import mlx_lm

                        def _do_prefill():
                            _mlx_lock = _get_mlx_inference_lock()  # type: ignore[name-defined]
                            with get_metal_stream_context():  # type: ignore[name-defined]
                                try:
                                    import mlx.core as _mx
                                    _mx.eval([])
                                    with _mlx_lock:
                                        for _ in mlx_lm.stream_generate(model=self._model, tokenizer=self._tokenizer, prompt=self._system_prompt, prompt_cache=self._system_prompt_cache, max_tokens=1):  # type: ignore[arg-type]
                                            pass
                                finally:
                                    _safe_mlx_eval_and_clear_cache('system_prompt_cache_parallel_prefill')
                        await asyncio.to_thread(_do_prefill)
                        self._kv_cache_stats['cache_prefills'] += 1
                    logger.info('[P1-3] System prompt cache prefill complete (parallel)')
                    return True
                except Exception as e:
                    logger.warning(f'[P1-3] System cache prefill failed: {e}')
                    return False

            async def _prefill_warmup_cache() -> bool:
                """Prefill warmup cache (~1000 tokens)."""
                try:
                    from mlx_lm.models.cache import make_prompt_cache
                    from hledac.universal.utils.mlx_memory import get_metal_stream_context
                    system_prompt = self._system_prompt
                    few_shot_examples = [{'user': 'What is 2+2?', 'assistant': '4'}, {'user': 'Capital of France?', 'assistant': 'Paris'}]
                    parts = [f'<|im_start|>system\n{system_prompt}<|im_end|>']
                    for ex in few_shot_examples[:3]:
                        parts.append(f"<|im_start|>user\n{ex.get('user', '')}<|im_end|>")
                        parts.append(f"<|im_start|>assistant\n{ex.get('assistant', '')}<|im_end|>")
                    warmup_prompt = '\n'.join(parts)
                    tokens = self._tokenizer.encode(warmup_prompt)  # type: ignore[union-attr]
                    token_count = len(tokens)
                    if token_count > 1000:
                        warmup_prompt = self._tokenizer.decode(tokens[:1000])  # type: ignore[union-attr]
                    canonical_parts = [system_prompt]
                    for ex in few_shot_examples[:3]:
                        canonical_parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
                    canonical_text = '\n'.join(canonical_parts)
                    prompt_hash = _get_xxh3_hex(canonical_text)
                    _warmup_disk_path = WARMUP_CACHE_DIR / f'warmup_{prompt_hash}.safetensors'
                    _has_warmup_disk_now = await asyncio.to_thread(_warmup_disk_path.exists) if prompt_hash else False
                    if _has_warmup_disk_now:
                        if await self._restore_warmup_cache(_warmup_disk_path, prompt_hash):
                            logger.info('[P1-3] Warmup cache restored from disk (parallel)')
                            return True
                    logger.info(f'[P1-3] Building fresh warmup cache (~{token_count} tokens, parallel)...')
                    self._warmup_cache = make_prompt_cache(self._model, max_kv_size=max(token_count + 128, 1024))
                    self._warmup_prompt_hash = prompt_hash
                    kv_bits = self._get_adaptive_kv_bits()
                    if self._supports_kv_quant:
                        for layer in self._warmup_cache:
                            if hasattr(layer, 'quantize'):
                                try:
                                    layer.quantize(group_size=64, bits=kv_bits)
                                except Exception:
                                    pass
                    from mlx_lm import generate as mlx_generate
                    from mlx_lm.sample_utils import make_sampler
                    _worker = getattr(self, '_mlx_worker_thread', None)
                    _worker_live = _worker is not None and _worker.is_active()

                    def _do_generate():
                        with get_metal_stream_context():
                            mlx_generate(model=self._model, tokenizer=self._tokenizer, prompt=warmup_prompt, sampler=make_sampler(temp=0.3), max_tokens=1, kv_bits=kv_bits, prompt_cache=self._warmup_cache, verbose=False)
                    if _worker_live:
                        try:
                            main_loop = asyncio.get_running_loop()

                            async def _coro_wrapper():
                                return _do_generate()
                            inference_future = asyncio.run_coroutine_threadsafe(_coro_wrapper(), main_loop)
                            await safe_wait_for(asyncio.wrap_future(inference_future), timeout=60.0, label='deephermes_main_thread')
                        except (TimeoutError, RuntimeError):
                            await asyncio.to_thread(_do_generate)
                    else:
                        await asyncio.to_thread(_do_generate)
                    logger.info('[P1-3] Warmup cache prefill complete (parallel)')
                    return True
                except Exception as e:
                    logger.warning(f'[P1-3] Warmup cache prefill failed: {e}')
                    return False
            _result = await parallel([_prefill_system_cache(), _prefill_warmup_cache()], taskgroup=True, policy='collect', ctx='deephermes3:parallel_prefill')
            results = _result.ok
            if len(results) > 1 and results[1] is True:
                try:
                    await self._save_cache()
                except Exception as _e:
                    logger.debug(f'[P1-3] warmup cache save failed: {_e}')
            successes = sum((1 for r in results if r is True))
            exceptions = [r for r in results if isinstance(r, Exception)]
            if exceptions:
                logger.warning(f'[P1-3] {len(exceptions)} prefill exception(s): {exceptions}')
            self._kv_cache_stats['parallel_prefills'] = successes
            logger.info(f'[P1-3] Parallel prefill complete: {successes}/{len(results)} succeeded')
        except Exception as e:
            logger.warning(f'[P1-3] Parallel prefill failed: {e}, falling back to sequential')
            await self._init_system_prompt_cache()
            await self.warmup_prefix_cache(system_prompt=self._system_prompt, few_shot_examples=[{'user': 'What is 2+2?', 'assistant': '4'}, {'user': 'Capital of France?', 'assistant': 'Paris'}])
            try:
                await self._save_cache()
            except Exception as _e:
                logger.debug(f'[P1-3] sequential fallback cache save failed: {_e}')

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
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.debug('[P1-3] Background warmup cancelled (sprint ended early)')
            return
        try:
            logger.info('[P1-3] Starting background KV cache prefill (~5s after sprint start)...')
            await self._prefill_warmup_caches()
            logger.info('[P1-3] Background KV cache prefill complete')
        except asyncio.CancelledError:
            logger.debug('[P1-3] Background warmup cancelled during prefill')
        except Exception as e:
            logger.warning(f'[P1-3] Background KV cache prefill failed: {e}')

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
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            warmup_cache = self._warmup_cache
            warmup_hash = self._warmup_prompt_hash
            if warmup_cache and warmup_hash:
                warmup_path = WARMUP_CACHE_DIR / f'warmup_{warmup_hash}.safetensors'
                try:
                    WARMUP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
            else:
                warmup_path = None

            async def _do_save() -> None:
                if self._system_prompt_cache:
                    import mlx.core as mx
                    data: dict[str, Any] = {}
                    for i, layer in enumerate(self._system_prompt_cache):
                        state = getattr(layer, 'state', None)
                        if state is None:
                            continue
                        try:
                            keys, values = state
                        except Exception:
                            continue
                        data[f'layer_{i}_keys'] = keys
                        data[f'layer_{i}_values'] = values
                    if hasattr(self._system_prompt_cache, 'offset'):
                        try:
                            data['_offset'] = mx.array([int(self._system_prompt_cache.offset)])  # type: ignore[union-attr]
                        except Exception:
                            pass
                    if data:
                        mx.savez(str(cache_path), **data)
                        logger.debug(f'[CACHE] Saved to {cache_path} ({len(self._system_prompt_cache)} layers)')
                if warmup_cache and warmup_hash:
                    try:
                        from mlx_lm.models.cache import save_prompt_cache
                        save_prompt_cache(str(warmup_path), warmup_cache, metadata={'prompt_hash': warmup_hash})
                        logger.debug(f'[CACHE] Warmup cache saved ({len(warmup_cache)} layers)')
                    except Exception as e:
                        logger.debug(f'[CACHE] save_prompt_cache failed: {e}')
            await asyncio.to_thread(_do_save)
        except Exception as e:
            logger.debug(f'[CACHE] Save failed (non-critical): {e}')

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

            def _do_load() -> mx.ndarray:
                return mx.load(str(cache_path))
            data = await asyncio.to_thread(_do_load)
            n_layers = len(self._system_prompt_cache)
            restored = 0
            for i in range(n_layers):
                k_key = f'layer_{i}_keys'
                v_key = f'layer_{i}_values'
                if k_key in data and v_key in data:
                    layer = self._system_prompt_cache[i]
                    if hasattr(layer, 'keys') and hasattr(layer, 'values'):
                        try:
                            layer.keys = data[k_key]
                            layer.values = data[v_key]
                            restored += 1
                        except Exception as e:
                            logger.debug(f'[CACHE] layer {i} restore failed: {e}')
            if '_offset' in data and hasattr(self._system_prompt_cache, 'offset'):
                try:
                    arr = data['_offset']
                    if hasattr(arr, 'item'):
                        offset_val = int(arr.item())
                    else:
                        offset_val = int(arr)
                    self._system_prompt_cache.offset = offset_val  # type: ignore[union-attr]
                except Exception:
                    pass
            if restored > 0:
                logger.info(f'[CACHE] Loaded from {cache_path} ({restored}/{n_layers} layers restored)')
                return True
            logger.debug(f'[CACHE] No layers restored from {cache_path}')
            return False
        except Exception as e:
            logger.debug(f'[CACHE] Load failed: {e}')
            return False

    def _format_chatml(self, system_msg: str, user_msg: str, history: list[dict[str, str]] | None=None) -> str:
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
        parts.append(f'<|im_start|>system\n{system_msg}<|im_end|>')
        if history:
            for entry in history:
                role = entry.get('role', 'user')
                content = entry.get('content', '')
                parts.append(f'<|im_start|>{role}\n{content}<|im_end|>')
        parts.append(f'<|im_start|>user\n{user_msg}<|im_end|>')
        parts.append('<|im_start|>assistant\n')
        return '\n'.join(parts)

    @staticmethod
    def _prep_generate(
        prompt: str,
        system: str,
        sanitize_fn: Callable[[str], str] | None,
        tokenizer: Any,
        prefix_cache: dict,
        prefix_cache_maxsize: int,
        prefix_cache_stats: dict,
        max_prompt_chars: int,
    ) -> tuple[str, Any, Any, list[int]]:
        """
        Issue #14: Stage 1 — CPU prep for generate() (runs in prep thread pool).

        Combines sanitization, deep thinking prefix, prefix cache lookup,
        ChatML formatting, and tokenization. All CPU-bound work for one
        prompt, run in the same prep worker thread.

        Returns (formatted_prompt, prefix_cache, tokenizer, prefix_tokens).

        Thread-safe: reads from shared state (no writes except cache append
        via move_to_end/popitem which are GIL-protected dict ops).
        """
        sanitized = (sanitize_fn(prompt) if sanitize_fn else None) or fallback_sanitize(prompt, max_length=max_prompt_chars)
        sanitized = sanitized[:max_prompt_chars]
        formatted = self._format_chatml(system_msg=system, user_msg=sanitized)
        formatted = formatted[:max_prompt_chars]
        prefix_tokens = None
        cache_key = hashlib.sha256((system or '').encode()).hexdigest()
        if cache_key in prefix_cache:
            # LRUCache.__getitem__ below automatically marks as MRU
            prefix_cache_stats['prefix_cache_hits'] = prefix_cache_stats.get('prefix_cache_hits', 0) + 1
            prefix_cache_stats['prefix_cache_size'] = len(prefix_cache)
            logger.debug(f'[CACHE] Prefix cache hit for key {cache_key[:8]}')
        elif tokenizer:
            try:
                prefix_tokens = tokenizer.encode(system)
                # LRUCache.__setitem__ inserts at MRU position automatically
                prefix_cache[cache_key] = prefix_tokens
                prefix_cache_stats['prefix_cache_misses'] = prefix_cache_stats.get('prefix_cache_misses', 0) + 1
                prefix_cache_stats['prefix_cache_size'] = len(prefix_cache)
                while len(prefix_cache) > prefix_cache_maxsize:
                    evicted_key, _ = prefix_cache.popitem(last=False)  # type: ignore[call-arg]
                    prefix_cache_stats['prefix_cache_evictions'] = prefix_cache_stats.get('prefix_cache_evictions', 0) + 1
                    logger.debug(f'[CACHE] Prefix cache evicted key {evicted_key[:8]}')
            except Exception:
                pass
        return formatted, None, tokenizer, prefix_tokens  # type: ignore[return-value]

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
            if hasattr(mx, 'get_active_memory'):
                mem_before = int(mx.get_active_memory())
                _ = self._model(mx.array([tokens]), cache=cache)  # type: ignore[operator]
                mx.eval(cache)
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
        if not KV_CACHE_AVAILABLE or self._model is None or (not system_prompt):
            return None
        try:
            import time as _time_module
            from mlx_lm.models.cache import make_prompt_cache
            prompt_hash = hashlib.md5(system_prompt.encode()).hexdigest()
            if prompt_hash in self._kv_cache_pool:
                # LRUCache.__getitem__ automatically marks as MRU
                self._kv_cache_pool_stats['pool_hits'] += 1
                logger.debug(f'[KV-CACHE][F289] Pool hit for system prompt hash {prompt_hash[:8]}')
                return self._kv_cache_pool[prompt_hash][0]
            lock = self._key_locks.get(prompt_hash)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[prompt_hash] = lock
            with lock:
                if prompt_hash in self._kv_cache_pool:
                    # LRUCache.__getitem__ automatically marks as MRU
                    self._kv_cache_pool_stats['pool_hits'] += 1
                    return self._kv_cache_pool[prompt_hash][0]
                self._kv_cache_pool_stats['pool_misses'] += 1
                tokens = self._tokenizer.encode(system_prompt)  # type: ignore[union-attr]
                cache = make_prompt_cache(self._model)
                cache_size = self._measure_kv_cache_bytes(cache, tokens)
                pool_budget_bytes = self._kv_cache_pool_memory_mb * 1024 * 1024
                total_bytes = sum((entry[2] for entry in self._kv_cache_pool.values())) + cache_size
                while len(self._kv_cache_pool) >= self._kv_cache_pool_maxsize or total_bytes > pool_budget_bytes:
                    if not self._kv_cache_pool:
                        break
                    evicted_key = max(self._kv_cache_pool, key=lambda k: self._kv_cache_pool[k][2])
                    evicted_size = self._kv_cache_pool[evicted_key][2]
                    self._kv_cache_pool.pop(evicted_key)
                    total_bytes -= evicted_size
                    self._kv_cache_pool_stats['pool_evictions'] += 1
                    self._kv_cache_pool_stats['pool_evictions_memory'] += evicted_size
                    logger.debug(f'[KV-CACHE][F289] Pool eviction for hash {evicted_key[:8]} (size={evicted_size / 1024 / 1024:.1f}MB)')
                self._kv_cache_pool[prompt_hash] = (cache, _time_module.monotonic(), cache_size)
                self._system_prompt_cache = cache
                self._system_prompt_hash = prompt_hash
                logger.debug(f'[KV-CACHE][F289] System prompt cache built for hash {prompt_hash[:8]}')
                return cache
        except Exception as e:
            logger.warning(f'[KV-CACHE] Prefix cache failed: {e}')
            return None

    def _get_session_cache(self, formatted_prompt: str) -> tuple[Any, str] | None:
        """
        F266-U3: Session KV cache lookup — returns (kv_cache, prompt_hash) for cache hit.

        Session cache enables cross-request reuse within a single engine session.
        Unlike _get_prefix_cache (system prompt only), this caches user prompts.

        Cache key = xxhash of formatted_prompt (fast, stable across restarts).
        LRU eviction when pool exceeds memory budget or max entries.

        Thread-safe via GIL (dict operations are atomic for dict reads).

        Returns:
            Tuple of (kv_cache, prompt_hash) on hit, None on miss.
        """
        if not self._kv_cache_enabled or not formatted_prompt:
            return None
        try:
            prompt_hash = _get_xxh3_hex(formatted_prompt)
            if prompt_hash in self._session_cache_pool:
                # LRUCache.__getitem__ automatically marks as MRU
                self._session_cache_stats['session_cache_hits'] += 1
                # M-01: compute live hit ratio for telemetry
                total = self._session_cache_stats['session_cache_hits'] + self._session_cache_stats['session_cache_misses']
                self._session_cache_stats['session_cache_hit_ratio'] = float(self._session_cache_stats['session_cache_hits'] / total) if total > 0 else 0.0
                logger.debug(f'[SESSION-CACHE] Hit for prompt hash {prompt_hash[:8]}')
                return (self._session_cache_pool[prompt_hash][0], prompt_hash)
            self._session_cache_stats['session_cache_misses'] += 1
            total = self._session_cache_stats['session_cache_hits'] + self._session_cache_stats['session_cache_misses']
            self._session_cache_stats['session_cache_hit_ratio'] = self._session_cache_stats['session_cache_hits'] / total if total > 0 else 0.0
            return None
        except Exception as e:
            logger.debug(f'[SESSION-CACHE] Lookup failed: {e}')
            return None

    def _store_session_cache(self, formatted_prompt: str, kv_cache: Any, cache_size: int) -> None:
        """
        F266-U3: Store KV cache in session pool after inference.

        Evicts largest entries when pool exceeds memory budget or max entries.
        Called after each generate() completes to cache the result KV state.

        Args:
            formatted_prompt: Full formatted prompt (for hash key)
            kv_cache: MLX KV cache object to store
            cache_size: Measured size in bytes via _measure_kv_cache_bytes
        """
        if not self._kv_cache_enabled:
            return
        # M-01: reject None — storing None defeats session cache (every hit = full prefill)
        if kv_cache is None:
            logger.debug('[SESSION-CACHE] Skipping store: kv_cache is None')
            return
        try:
            prompt_hash = _get_xxh3_hex(formatted_prompt)
            if prompt_hash in self._session_cache_pool:
                # LRUCache.__setitem__ will update and mark as MRU
                return
            pool_budget_bytes = self._session_cache_memory_mb * 1024 * 1024
            total_bytes = sum((entry[3] for entry in self._session_cache_pool.values())) + cache_size
            while len(self._session_cache_pool) >= self._session_cache_maxsize or total_bytes > pool_budget_bytes:
                if not self._session_cache_pool:
                    break
                evicted_key = max(self._session_cache_pool, key=lambda k: self._session_cache_pool[k][3])
                evicted_size = self._session_cache_pool[evicted_key][3]
                self._session_cache_pool.pop(evicted_key)
                total_bytes -= evicted_size
                self._session_cache_stats['session_cache_evictions'] += 1
                logger.debug(f'[SESSION-CACHE] Evicted hash {evicted_key[:8]} (size={evicted_size / 1024 / 1024:.1f}MB)')
            self._session_cache_pool[prompt_hash] = (kv_cache, prompt_hash, time.monotonic(), cache_size)
            logger.debug(f'[SESSION-CACHE] Stored for hash {prompt_hash[:8]} (size={cache_size / 1024 / 1024:.1f}MB)')
        except Exception as e:
            logger.debug(f'[SESSION-CACHE] Store failed: {e}')

    def _get_kv_cache_kwargs(self, input_tokens: int | None=None, max_tokens: int | None=None) -> dict:
        """
        Sprint F214Q + F265C-METAL + O1: Adaptive KV cache sizing for M1 8GB.

        O1 OPTIMIZATION: KV cache size = min(input_tokens + headroom, memory_adjusted_cap).
        Short prompts (low input_tokens) → small cache is sufficient.
        Long prompts (high input_tokens) → cache must be large enough to hold the full context.

        Memory-pressure tier thresholds (Metal active memory fraction of 1.5 GiB):
        - < 0.60  → "normal"  → max_kv_size = min(input+headroom, 8192)
        - 0.60-0.80 → "warn"   → max_kv_size = min(input+headroom, 4096)
        - 0.80-0.95 → "critical" → max_kv_size = min(input+headroom, 2048)
        - > 0.95  → "emergency" → {} (KV off)

        O1 adaptive headroom formula:
          headroom = min(max_tokens or 512, 1024)
          min_cache = input_tokens + headroom  (guarantees output space)
          cap = memory-tier cap (8192/4096/2048/0)
          max_kv_size = min(min_cache, cap)

        Example: input=512, max_tokens=512, normal tier → min_cache=1536, cap=8192 → 1536

        Args:
            input_tokens: Počet tokenů vstupního promptu (po tokenizaci).
                          Pokud None, použije se legacy behavior (ignores input length).
            max_tokens: Maximální očekávaný počet output tokenů.
                        Pokud None, použije se 512 jako default.

        Returns:
            dict: kwargs pro mlx_lm.generate() — {} (KV off) nebo {"max_kv_size": N}
            INVARIANT: NIKDY nevyhazuje výjimku — fallback {} je vždy bezpečný
        """
        if self._kv_cache_enabled is False:
            return {}
        tier = 'normal'
        try:
            import mlx.core as mx
            active = 0
            if hasattr(mx, 'get_active_memory'):
                active = int(mx.get_active_memory())
            elif hasattr(mx.metal, 'get_active_memory'):
                active = int(mx.metal.get_active_memory())
            emergency_bytes, critical_bytes, warn_bytes = _get_metal_tier_thresholds()
            if active > emergency_bytes:
                tier = 'emergency'
            elif active > critical_bytes:
                tier = 'critical'
            elif active > warn_bytes:
                tier = 'warn'
        except Exception:
            tier = 'normal'
        uma_state = 'ok'
        try:
            from hledac.universal.core.resource_governor import sample_uma_status
            _uma = sample_uma_status()
            uma_state = getattr(_uma, 'state', 'ok')
        except Exception:
            pass
        _in_tokens = input_tokens if input_tokens is not None else 0
        _max_tok = max_tokens if max_tokens is not None else 512
        _headroom = min(_max_tok, 1024)
        _min_cache = _in_tokens + _headroom
        if uma_state == 'emergency':
            base_size = 0
        elif uma_state == 'critical':
            if tier == 'normal':
                base_size = max(512, int(self._max_kv_size * 0.35))
            elif tier == 'warn':
                base_size = max(512, int(self._max_kv_size * 0.6))
            else:
                base_size = max(256, int(self._max_kv_size * 0.2))
        elif uma_state == 'warn':
            if tier == 'normal':
                base_size = max(1024, int(self._max_kv_size * 0.8))
            elif tier == 'warn':
                base_size = max(1024, int(self._max_kv_size * 0.5))
            else:
                base_size = max(512, int(self._max_kv_size * 0.25))
        elif tier == 'normal':
            base_size = self._max_kv_size
        elif tier == 'warn':
            base_size = max(1024, self._max_kv_size // 2)
        elif tier == 'critical':
            base_size = max(512, self._max_kv_size // 4)
        else:
            base_size = 0
        if base_size == 0:
            final_size = 0
        else:
            final_size = max(_min_cache, base_size)
        kv_kwargs = {'max_kv_size': final_size} if final_size > 0 else {}
        logger.debug('[O1+F265C-METAL+F265H-EXT] KV cache: input_tokens=%d max_tokens=%d min_cache=%d uma_state=%s metal_tier=%s base=%d final=%d', _in_tokens, _max_tok, _min_cache, uma_state, tier, base_size, final_size)
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
        if self._force_kv_quantize:
            kv_bits = max(4, self._kv_bits)
            logger.debug('[B.KV] KV quant forced on: kv_bits=%d', kv_bits)
            return kv_bits
        kv_bits = self._kv_bits
        active_gib = 0.0
        try:
            import mlx.core as mx
            active = 0
            if hasattr(mx, 'get_active_memory'):
                active = int(mx.get_active_memory())
            active_gib = active / 1024 ** 3
            if active_gib > 2.0:
                kv_bits = 8
            elif active_gib > 1.5:
                kv_bits = 6
        except Exception:
            pass
        logger.debug('[F265C-METAL] Adaptive KV bits: active_GiB=%.2f kv_bits=%d', active_gib, kv_bits)
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
        if self._model_ever_loaded and self._last_inference_at is None:
            return True
        if self._last_inference_at is None:
            return True
        try:
            import time as _time
            elapsed = _time.monotonic() - self._last_inference_at
            return elapsed >= self._idle_unload_timeout_s
        except Exception:
            return True

    def _build_generate_kwargs(self, formatted_prompt: str, temp: float, max_tok: int, prefix_cache, adapter_path: str | None=None, prompt_tokens: list[int] | None=None) -> dict:
        """
        Build mlx_lm.generate() kwargs — shared between stream and direct paths.

        KV Cache reuse strategy (Sprint F266 KV-REUSE):
          - prefix_cache (may be _system_prompt_cache): pre-computed system prompt KV cache.
            Passed as prompt_cache= so mlx_lm reuses it and extends with user prompt tokens.
          - If prefix_cache is None: create a new per-call cache (full prefill each call).
          - cache= param: used ONLY for speculative draft model caching (separate cache).

        F265C-METAL invariant: kv_bits + max_kv_size go to mlx_lm.generate(), NOT load().

        LoRA (Sprint LoRA-1): when adapter_path is set, use the LoRA-fused model
        from _lora_cache. When None, use base model. KV cache size is halved
        when LoRA is active to compensate for LoRA Metal SRAM footprint.

        M-03 FIX: prompt_tokens (pre-computed token list) avoids double tokenization.
        If prompt_tokens is provided, _get_kv_cache_kwargs() uses its length directly
        instead of re-encoding formatted_prompt. Caller is responsible for tokenizing
        once and passing the tokens list.
        """
        from mlx_lm.sample_utils import make_sampler
        kv_bits = self._get_adaptive_kv_bits()
        if self._kv_cache_enabled and prefix_cache is not None:
            kv_cache = prefix_cache
            if self._supports_kv_quant:
                for layer in kv_cache:
                    if hasattr(layer, 'quantize'):
                        try:
                            layer.quantize(group_size=64, bits=kv_bits)
                            self._kv_cache_stats['quantized_count'] += 1
                        except Exception:
                            pass
        elif self._kv_cache_enabled:
            if self._paged_kv_cache:
                from mlx_lm.models.cache import RotatingKVCache
                num_layers = len(self._model.layers)
                kv_cache = [RotatingKVCache(max_size=max_tok, keep=self._paged_kv_keep) for _ in range(num_layers)]
                logger.debug('[B.KV] Paged KV cache: keep=%d, max_size=%d, layers=%d', self._paged_kv_keep, max_tok, num_layers)
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
        _active_model = self._model
        _active_tokenizer = self._tokenizer
        _active_adapter: str | None = None
        if adapter_path is not None:
            cache = hermes_cache()
            _lora_tuple = cache.get_lora(adapter_path)
            if _lora_tuple is not None:
                _active_model, _active_tokenizer = _lora_tuple
                _active_adapter = adapter_path
                self._lora_cache_stats['lora_applications'] += 1
        # M-03: Use pre-computed prompt_tokens if available (avoids double encode).
        # Note: when prompt_tokens is already computed, LoRA tokenizer swap is not needed
        # since we use the tokens directly — LoRA affects only new tokenization.
        _input_tokens_count: int | None = len(prompt_tokens) if prompt_tokens is not None else None
        _kv_kwargs = self._get_kv_cache_kwargs(input_tokens=_input_tokens_count, max_tokens=max_tok)
        if _active_adapter is not None and 'max_kv_size' in _kv_kwargs:
            _orig_size = _kv_kwargs['max_kv_size']
            _kv_kwargs['max_kv_size'] = max(2048, _orig_size // 2)
            logger.debug(f"[LoRA] KV cache reduced: {_orig_size} → {_kv_kwargs['max_kv_size']} (adapter={_active_adapter})")
        # M-03: Pass prompt_tokens directly to mlx_lm.generate() — avoids re-tokenizing
        _prompt_arg: str | list[int] = prompt_tokens if prompt_tokens is not None else formatted_prompt
        generate_kwargs = {'model': _active_model, 'tokenizer': _active_tokenizer, 'prompt': _prompt_arg, 'sampler': make_sampler(temp=temp), 'max_tokens': max_tok, 'kv_bits': kv_bits, 'verbose': False, **_kv_kwargs}
        if kv_cache is not None:
            generate_kwargs['prompt_cache'] = kv_cache
        if self._speculative_enabled and self._draft_model_obj is not None and self._supports_draft:
            generate_kwargs['draft_model'] = self._draft_model_obj
            generate_kwargs['num_draft_tokens'] = self._num_draft_tokens
        self._kv_cache_stats['cache_uses'] += 1
        return generate_kwargs

    # L-04: Throttling constants — mx.clear_cache() called at most every N generations
    # or when memory pressure is HIGH/CRITICAL. Avoids 1-20ms per-call penalty.
    _CLEAR_INTERVAL: int = 20  # generations between clears (M1 8GB: ~20 inferences window)

    # M-08: Cache invalidation — called before and after model swap
    def _invalidate_all_prompt_caches(self, reason: str) -> None:
        """
        M-08 FIX: Invalidate all prompt/KV caches on model swap.

        Root cause: load_model() resetovalo pouze _prompt_cache, ale
        _system_prompt_cache, _warmup_cache, _kv_cache_pool, _session_cache_pool
        zůstávaly s referencemi na starý model → stale Metal allocations,
        potenciální kompatibilita cache ↔ nový model (různé dimenze/quantization).

        Volá se z load_model() na ZAČÁTKU (před načtením nového modelu)
        i NA KONCI (pro jistotu po každém model swap).

        Args:
            reason: Telemetrie label pro debugging (např. "model_swap_start", "model_swap_end")
        """
        self._prompt_cache = None
        self._system_prompt_cache = None
        self._warmup_cache = None
        self._warmup_prompt_hash = None
        self._kv_cache_pool.clear()
        self._session_cache_pool.clear()
        self._mlx_clear_and_timestamp(force_clear=True)
        self._telemetry_counters['cache_invalidation_count'] += 1
        logger.debug(f'[M-08] All prompt caches invalidated: {reason}')

    def _mlx_clear_and_timestamp(self, force_clear: bool = False) -> None:
        """
        L-04 FIX: Throttled MLX Metal cache clear.

        Clears Metal allocator cache only when:
          - force_clear=True  (timeout retry path — memory may be fragmented)
          - _generation_since_clear >= _CLEAR_INTERVAL
          - UMA pressure state is HIGH or CRITICAL

        This eliminates the 1-20ms per-call penalty from unconditional clear,
        while preserving the original sequence: gc.collect() -> mx.eval([]) ->
        mx.clear_cache() -> gc.collect().

        Args:
            force_clear: Force clear even if throttle threshold not met.
                         Used by timeout-retry path where fragmentation risk
                         outweighs cache preservation benefit.
        """
        _should_clear = force_clear
        if not _should_clear:
            if self._generation_since_clear >= self._CLEAR_INTERVAL:
                _should_clear = True
            else:
                # Check memory pressure — HIGH/CRITICAL requires immediate relief
                try:
                    from hledac.universal.core.resource_governor import sample_uma_status
                    _uma = sample_uma_status()
                    _uma_state = getattr(_uma, 'state', 'ok')
                    if _uma_state in ('high', 'critical'):
                        _should_clear = True
                except Exception:
                    pass  # fail-open: don't clear if sampling fails

        # Always update timestamp and counter regardless of whether we clear
        import time as _time
        self._last_inference_at = _time.monotonic()
        self._generation_since_clear = 0  # reset counter after recording
        self._last_clear_at = self._last_inference_at

        if not _should_clear:
            return

        try:
            import mlx.core as _mx
            import gc as _gc
            _gc.collect()
            _mx.eval([])
            if hasattr(_mx, 'clear_cache'):
                _mx.clear_cache()
            _gc.collect()
        except Exception:
            pass

    async def apply_lora_adapter_async(self, adapter_path: str | None) -> None:
        """
        Set or swap the active LoRA adapter (lazy-load with bounded LRU cache).

        P0-04: Uses HermesModelCache singleton for both models and LoRA adapters.
        Single RLock — works from asyncio loop thread and ThreadPoolExecutor.
        Active background monitor handles critical memory pressure independently.

        C2-FIX: mlx_lm.lora.load_lora_model() is blocking I/O.
        Wrapped in asyncio.to_thread() to avoid blocking the event loop.

        Args:
            adapter_path: Path to LoRA adapter safetensors file, or None to use base model.
        """
        if adapter_path == self._lora_adapter_path:
            return
        if adapter_path is None:
            self._lora_adapter_path = None
            logger.debug('[LoRA] Switched to base model (no adapter)')
            return
        cache = hermes_cache()
        lora_result = cache.get_lora(adapter_path)
        if lora_result is not None:
            self._lora_adapter_path = adapter_path
            self._lora_cache_stats['lora_cache_hits'] += 1
            logger.debug(f'[LoRA] Cache hit (LRU updated): {adapter_path}')
            return
        try:
            import mlx_lm
            logger.info(f'[LoRA] Loading adapter: {adapter_path}')
            lora_model, lora_tokenizer = await asyncio.to_thread(
                mlx_lm.lora.load_lora_model, self._model, adapter_path
            )
            cache.put_lora(adapter_path, lora_model, lora_tokenizer)
            self._lora_adapter_path = adapter_path
            self._lora_cache_stats['lora_cache_misses'] += 1
            logger.info(f'[LoRA] Adapter loaded and cached: {adapter_path}')
        except Exception as _e:
            logger.warning(f'[LoRA] Failed to load adapter {adapter_path}: {_e}')
            self._lora_adapter_path = None

    def apply_lora_adapter(self, adapter_path: str | None) -> None:
        """Sync wrapper for apply_lora_adapter_async (for non-async contexts)."""
        if adapter_path == self._lora_adapter_path:
            return
        if adapter_path is None:
            self._lora_adapter_path = None
            logger.debug('[LoRA] Switched to base model (no adapter)')
            return
        cache = hermes_cache()
        lora_result = cache.get_lora(adapter_path)
        if lora_result is not None:
            self._lora_adapter_path = adapter_path
            self._lora_cache_stats['lora_cache_hits'] += 1
            logger.debug(f'[LoRA] Cache hit (LRU updated): {adapter_path}')
            return
        try:
            import mlx_lm
            logger.info(f'[LoRA] Loading adapter: {adapter_path}')
            lora_model, lora_tokenizer = mlx_lm.lora.load_lora_model(self._model, adapter_path)
            cache.put_lora(adapter_path, lora_model, lora_tokenizer)
            self._lora_adapter_path = adapter_path
            self._lora_cache_stats['lora_cache_misses'] += 1
            logger.info(f'[LoRA] Adapter loaded and cached: {adapter_path}')
        except Exception as _e:
            logger.warning(f'[LoRA] Failed to load adapter {adapter_path}: {_e}')
            self._lora_adapter_path = None

    def unload_lora_adapter(self) -> None:
        """Evict all LoRA adapters from cache and reset active adapter.

        P0-04: Delegates to HermesModelCache singleton (clear_loras).
        """
        cache = hermes_cache()
        cache.clear_loras()
        self._lora_adapter_path = None
        logger.debug('[LoRA] All adapters unloaded')

    def get_lora_active_adapter(self) -> str | None:
        """Return the currently active LoRA adapter path, or None for base model."""
        return self._lora_adapter_path

    def get_lora_stats(self) -> dict:
        """Return LoRA cache telemetry (P0-04)."""
        cache = hermes_cache()
        return {**self._lora_cache_stats, 'lora_active': self._lora_adapter_path, 'lora_cache_size': cache.lora_count}

    def _get_lora_kwargs(self) -> dict:
        """
        Return mlx_lm.generate() kwargs for active LoRA adapter.

        When _lora_adapter_path is set, mlx_lm.generate() applies the LoRA
        transform at inference time (no separate model copy needed).

        Memory: When LoRA is active, reduce max_kv_size from 8192→4096 to
        compensate for LoRA adapter Metal SRAM footprint (~50-200MB).

        Returns:
            dict with adapter_path key, or empty dict when no LoRA active.
        """
        if self._lora_adapter_path is None:
            return {}
        self._lora_cache_stats['lora_applications'] += 1
        return {'adapter_path': self._lora_adapter_path}

    def _get_lora_kv_size(self, base_kv_kwargs: dict) -> dict:
        """
        Adjust KV cache size when LoRA adapter is active.

        LoRA adapters occupy ~50-200 MB Metal SRAM. Reduce max_kv_size
        from 8192→4096 (or from current adaptive value → half) to stay
        within M1 8GB memory budget.

        Returns modified kv_kwargs dict with reduced max_kv_size.
        """
        if self._lora_adapter_path is None:
            return base_kv_kwargs
        if 'max_kv_size' not in base_kv_kwargs:
            return base_kv_kwargs
        current_size = base_kv_kwargs.get('max_kv_size', 8192)
        reduced_size = max(2048, current_size // 2)
        logger.debug(f'[LoRA] KV cache reduced: {current_size} → {reduced_size} (LoRA active)')
        return {**base_kv_kwargs, 'max_kv_size': reduced_size}

    def _run_inference(self, formatted_prompt: str, temp: float, max_tok: int, prefix_cache=None, adapter_path: str | None=None, prompt_tokens: list[int] | None=None) -> tuple[str, Any]:
        """
        Run MLX inference synchronously in thread pool (Sprint 75).

        M-01 FIX: Returns (response, kv_cache_after) so the caller can store
        the populated KV cache. Previously returned only response.strip(),
        losing the post-prefill kv_cache object and defeating session caching
        (every cache hit still required full prefill on reuse).

        P0-1 FIX: Reactive Metal stream fallback — if Stream(gpu) error occurs
        inside the stream context, retry WITHOUT the stream context (direct
        default stream). This handles the case where get_metal_stream_context()
        returns a valid stream but Metal still errors during generate().

        F288 FIX: Wrapped in get_metal_stream_context() — each thread
        (MLXWorkerThread, asyncio.to_thread, ThreadPoolExecutor) gets its
        own mx.stream(gpu) via thread-local storage.

        LoRA (Sprint LoRA-1): adapter_path triggers LoRA model from cache
        in _build_generate_kwargs.

        M-03 FIX: prompt_tokens (pre-computed token list) avoids double tokenization.
        If provided, mlx_lm.generate() receives token list directly instead of
        re-encoding the string prompt.

        Args:
            formatted_prompt: Formatted prompt for generation
            temp: Temperature setting
            max_tok: Maximum tokens to generate
            prefix_cache: Optional KV cache for prompt prefix
            adapter_path: Optional LoRA adapter path (resolved from _lora_cache)
            prompt_tokens: Pre-computed token list (M-03 fix — avoids double encode)

        Returns:
            Tuple of (generated text, kv_cache object after prefill).
            The kv_cache carries the populated KV state and is what must be
            stored in the session cache — NOT the input prefix_cache.
        """
        from mlx_lm import generate as mlx_generate
        # L-01: Globální MLX Metal lock — serializuje všechny mlx_lm.generate() volání
        from hledac.universal.core.mlx_inference_lock import _get_mlx_inference_lock

        generate_kwargs = self._build_generate_kwargs(formatted_prompt, temp, max_tok, prefix_cache, adapter_path=adapter_path, prompt_tokens=prompt_tokens)
        # M-01: capture kv_cache for return — needed by caller for session cache store
        kv_cache_after = generate_kwargs.get('prompt_cache')
        _mlx_lock = _get_mlx_inference_lock()
        try:
            import mlx.core as _mx
            _mx.eval([])
        except Exception:
            pass
        try:
            with _mlx_lock:
                response = mlx_generate(**generate_kwargs)
        except Exception as _err:
            logger.warning('[P0-1] mlx_generate failed: %s', _err)
            self._mlx_clear_and_timestamp(force_clear=True)  # L-04: force clear after error (fragmented cache)
            raise RuntimeError(f'MLX inference failed: {_err}') from _err
        self._generation_since_clear += 1  # L-04: increment BEFORE throttled clear
        self._mlx_clear_and_timestamp()  # L-04: throttled — clears only when threshold reached or HIGH pressure
        # M-01: return (response, kv_cache_after) so caller can store populated cache
        return response.strip(), kv_cache_after

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
            worker = self._ensure_mlx_worker_thread()
            self._mlx_batcher = MLXBatchedExecutor(engine=self, worker_thread=worker)
        except Exception as _e:
            logger.debug('[P0-2] MLXBatchedExecutor init skipped: %s', _e)
            self._mlx_batcher = None
        return self._mlx_batcher

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
            if self._mlx_worker_thread is not None:
                return self._mlx_worker_thread
            self._mlx_worker_thread = MLXWorkerThread()
            self._mlx_worker_thread.start()
        except Exception as _e:
            logger.debug('[P0-3] MLXWorkerThread init skipped: %s', _e)
            self._mlx_worker_thread = None
        return self._mlx_worker_thread

    async def _ensure_mlx_scheduler(self) -> Any:
        """
        Lazy initialization of MLXUnifiedScheduler.

        ISSUE-120 FIX: MLXUnifiedScheduler coordinates all MLX compute (LLM inference +
        embedding encode) on M1 with priority lanes. Previously defined but never
        instantiated — now wired as optional coordinator in generate() path.

        Idempotent. Returns the scheduler instance or None on failure.
        M1 8GB safe: imports are lazy; scheduler is lightweight wrapper.

        Architecture:
            MLXUnifiedScheduler (coordinator)
            ├── DeepHermes3Engine (this instance) — LLM inference
            ├── MLXBatchedExecutor — batched inference
            ├── MLXWorkerThread — persistent loop
            └── MLXEmbedder — embedding encode

        Routing in generate():
            1. Try MLXUnifiedScheduler.submit_inference() when available
            2. Fall back to MLXBatchedExecutor.execute() if scheduler unavailable
            3. Final fallback to _submit_inference() direct path

        Always-on: scheduler is optional; fail-soft ensures direct path works.
        """
        if self._mlx_scheduler is not None:
            return self._mlx_scheduler
        try:
            from hledac.universal.core.mlx_unified_scheduler import MLXUnifiedScheduler
            from hledac.universal.core.mlx_unified_scheduler import LanePriority
            worker = self._ensure_mlx_worker_thread()
            batcher = await self._ensure_mlx_batcher()
            self._mlx_scheduler = MLXUnifiedScheduler(llm_engine=self, worker_thread=worker, batcher=batcher)
            await self._mlx_scheduler.start()
            logger.debug('[ISSUE-120] MLXUnifiedScheduler initialized')
        except Exception as _e:
            logger.debug('[ISSUE-120] MLXUnifiedScheduler init skipped: %s', _e)
            self._mlx_scheduler = None
        return self._mlx_scheduler

    async def _run_inference_async(self, fn, *args, **kwargs):
        """
        Run a sync inference function from the worker thread context.

        This coroutine is scheduled on the worker thread's event loop
        (M.T1: single MLX context). It synchronously calls fn(*args, **kwargs)
        and returns the result. No thread switching happens — the call
        happens in the same thread that owns the MLX model state.
        """
        return fn(*args, **kwargs)

    async def _submit_inference(self, timeout: float, fn, *args, **kwargs):
        """
        Submit an MLX inference call.

        P0-2 FIX: Routing order (priority):
          1. MLXWorkerThread (P0-3): dedicated worker, non-blocking main loop.
             Worker has its own Metal stream context (initialized at thread start).
             If worker is busy or unavailable, fall through.
          2. Main-thread run_coroutine_threadsafe (F300S-FIX): Metal context valid
             in main thread. Used when worker is busy. Risk: if main thread is
             already running mlx_lm.generate(), second concurrent call times out
             because _inference_semaphore blocks (single slot). This is safe —
             semaphore serialize prevents concurrent MLX calls.
          3. ThreadPoolExecutor fallback (last resort): blocks event loop but works
             when both worker and main thread paths fail.

        Retry with exponential backoff on timeout:
          - Primary path: mlx_lm.generate() on M1 can fail transiently when the
            system is under memory pressure (Metal allocation timeouts, KV cache
            eviction during generation).
          - Retry up to 2 times with 5s delay between attempts.
          - On repeated timeout: record model failure and propagate TimeoutError.

        Args:
            timeout: Maximum seconds to wait for result
            fn: Blocking inference function (_run_inference)
            *args, **kwargs: Arguments to pass to fn

        Returns:
            Generated text from mlx_lm.generate()
        """
        worker = self._ensure_mlx_worker_thread()
        if worker is not None and worker.is_active():
            try:
                result = await worker.submit(self._run_inference_async(fn, *args, **kwargs), timeout=timeout)
                return result
            except (TimeoutError, RuntimeError) as _worker_err:
                logger.debug('[P0-2] worker submit failed (%s) — trying main thread path', _worker_err)
            except Exception as _worker_err:
                logger.debug('[P0-2] worker submit unexpected error (%s) — trying main thread path', _worker_err)
        _retries = 2
        _base_delay = 2.0
        for _attempt in range(_retries + 1):
            try:
                main_loop = asyncio.get_running_loop()

                async def _coro_wrapper():
                    return fn(*args, **kwargs)
                inference_future = asyncio.run_coroutine_threadsafe(_coro_wrapper(), main_loop)
                return await safe_wait_for(asyncio.wrap_future(inference_future), timeout=timeout, label='deephermes_inference')
            except TimeoutError:
                if _attempt < _retries:
                    logger.warning('[P0-2] main-thread inference timeout (attempt %d/%d), retrying in %.1fs', _attempt + 1, _retries + 1, _base_delay)
                    await asyncio.sleep(_base_delay)
                    _base_delay *= 1.5
                    self._mlx_clear_and_timestamp(force_clear=True)  # L-04: force clear after timeout (fragmented KV cache)
                    continue
                logger.warning('[P0-2] main-thread inference timeout after %d attempts — propagating', _retries + 1)
                raise
            except Exception as _submit_err:
                logger.debug('[P0-2] main-thread submit failed (attempt %d): %s — falling back', _attempt + 1, _submit_err)
                if _attempt >= _retries:
                    break
                await asyncio.sleep(_base_delay)
                _base_delay *= 1.5
                continue
        async with self._inference_semaphore:
            loop = asyncio.get_running_loop()
            return await safe_wait_for(loop.run_in_executor(self._inference_executor, lambda: fn(*args, **kwargs)), timeout=timeout, label='deephermes_executor')

    @_otel_instrumented('hermes.generate', component='mlx')
    async def generate(self, prompt: str, temperature: float | None=None, max_tokens: int | None=None, system_msg: str | None=None, *, thinking: bool=True, adapter_path: str | None=None) -> str:
        """
        Generovat text pomocí DeepHermes-3.

        Args:
            prompt: Vstupní prompt
            temperature: Teplota (0-1)
            max_tokens: Maximální počet tokenů
            system_msg: Systémová zpráva
            thinking: Režim deep thinking (přidá system prompt pro
                     řetězení myšlenek před odpověď)
            adapter_path: Optional LoRA adapter path for fine-tuned inference.
                          When set, loads (or retrieves from cache) the LoRA adapter
                          and routes inference through it. KV cache is reduced
                          (8192→4096) to compensate for LoRA Metal SRAM footprint.
                          Pass None to use base model (default).

        Returns:
            Vygenerovaný text
        """
        if self._model is None:
            await self._ensure_model_loaded()
            if self._model is None:
                raise RuntimeError('Model not initialized — Hermes load failed')
        while self._compile_in_progress:
            await asyncio.sleep(0.1)
        _max_tokens_for_batch = max_tokens if max_tokens is not None else self.config.max_tokens
        try:
            scheduler = await self._ensure_mlx_scheduler()
            if scheduler is not None:
                from hledac.universal.core.mlx_unified_scheduler import LanePriority
                return await scheduler.submit_inference(prompt=prompt, temperature=temperature, max_tokens=max_tokens or 1024, system_msg=system_msg, priority=LanePriority.INTERACTIVE)
        except Exception as _scheduler_err:
            logger.debug('[ISSUE-120] Scheduler routing failed, falling back to batcher: %s', _scheduler_err)
        try:
            batcher = await self._ensure_mlx_batcher()
            if batcher is not None and batcher.is_batch_safe(prompt=prompt, system_msg=system_msg, priority=1.0, active_iteration_count=self._active_iteration_count, max_tokens=_max_tokens_for_batch):
                return await batcher.execute(prompt=prompt, temperature=temperature, max_tokens=max_tokens, system_msg=system_msg, priority=1.0)
        except Exception as _batching_err:
            logger.debug('[P0-2] batching routing failed, falling back to direct: %s', _batching_err)
        if check_model_allowed is not None:
            decision = check_model_allowed('hermes')
            if not decision.allowed:
                raise RuntimeError(f'model inference blocked: hermes, retry after {decision.retry_after_s:.1f}s')
        if self._model_breaker is not None and self._model_breaker.is_open():
            snap = self._model_breaker.get_snapshot()
            raise RuntimeError(f"GAP-3/1: ModelCircuitBreaker OPEN for {snap['model_id']!r} (failures={snap['failure_count']}, last={snap['last_failure_kind']!r})")
        try:
            temp = temperature or self.config.temperature
            max_tok = max_tokens or self.config.max_tokens
            if decide_context_budget is not None and apply_context_budget is not None:
                decision = decide_context_budget(prompt, requested_context_window=self.config.context_window)
                if decision.mode == 'reject':
                    logger.warning(f'[CONTEXT] memory_admission_blocked: {decision.reason}' + (f' uma_state={decision.uma_state}' if decision.uma_state else ''))
                    if record_model_failure is not None:
                        record_model_failure('hermes', failure_kind='memory_admission_blocked')
                    raise RuntimeError(f'hermes context preflight rejected: {decision.reason}')
                if decision.truncated:
                    prompt = apply_context_budget(prompt, decision)
                    logger.debug(f'[CONTEXT] truncated {decision.original_chars}→{decision.final_chars} chars, mode={decision.mode}' + (f' uma_state={decision.uma_state}' if decision.uma_state else ''))
                    self._telemetry_counters['adaptive_context_truncated'] = self._telemetry_counters.get('adaptive_context_truncated', 0) + 1
                    self._telemetry_counters['adaptive_context_mode'] = decision.mode
                    if decision.uma_state:
                        self._telemetry_counters['uma_state'] = decision.uma_state
            if sanitize_prompt_injection_patterns is not None:
                validation_result = sanitize_prompt_injection_patterns(prompt)
                if validation_result.suspicious:
                    logger.debug(f'[P1G-A] prompt_injection_guard: suspicious=True, patterns={len(validation_result.patterns)}, reason={validation_result.reason}')
                prompt = validation_result.safe_text
            is_injection, patterns = _detect_prompt_injection(prompt if isinstance(prompt, str) else str(prompt))
            if is_injection:
                import logging as _log
                _log.getLogger(__name__).warning(f'GAP-5: Prompt injection patterns detected: {patterns[:3]} — proceeding with sanitized input (fail-soft)')
                        # Issue #14: Stage 1 — CPU prep in thread pool (parallel across prompts)
            loop = asyncio.get_running_loop()
            bandit = self._get_prompt_bandit()
            arm_used = ''
            if bandit is not None:
                try:
                    arm_used = bandit.select_arm()
                    bandit.get_prompt_modifier(arm_used)
                    self._last_bandit_arm = arm_used
                    logger.debug(f'[GENERATE] Bandit arm: {arm_used}')
                except Exception as e:
                    logger.debug(f'[GENERATE] Bandit select failed: {e}')
            sanitized_for_prep = prompt if isinstance(prompt, str) else str(prompt)
            system_for_prep = system_msg or 'You are a helpful research assistant.'
            if thinking:
                system_for_prep = f'{self._DEEP_THINKING_PREFIX}\n\n{system_for_prep}'
            formatted_prompt, _, tokenizer, prefix_tokens = await loop.run_in_executor(
                self._prep_executor,
                self._prep_generate,
                sanitized_for_prep,
                system_for_prep,
                self._sanitize_for_llm,
                self._tokenizer,
                self._prefix_cache,
                self._prefix_cache_maxsize,
                self._prefix_cache_stats,
                MAX_LLM_PROMPT_CHARS,
            )
            if adapter_path is not None:
                await self.apply_lora_adapter_async(adapter_path)
            logger.debug(f'Generating with temp={temp}, max_tokens={max_tok}, lora={adapter_path}')
            prefix_cache = None
            if self._kv_cache_enabled:
                if system_msg:
                    try:
                        prefix_cache = self._get_prefix_cache(system_msg)
                    except Exception:
                        pass
                elif self._system_prompt_cache is not None:
                    prefix_cache = self._system_prompt_cache
            session_result = self._get_session_cache(formatted_prompt)
            if session_result is not None:
                cached_kv, _ = session_result
                prefix_cache = cached_kv
                logger.debug('[SESSION-CACHE] Using cached KV for inference')
            # M-03: Tokenize once here — avoids double encode in _build_generate_kwargs
            try:
                gen_prompt_tokens = self._tokenizer.encode(formatted_prompt)
            except Exception:
                gen_prompt_tokens = None
            timeout_s = _get_hermes_timeout_s()
            # M-01: _run_inference returns (response, populated_kv_cache)
            # — store the populated cache, NOT the input prefix_cache
            # M-03: Pass prompt_tokens to avoid double encode
            response, populated_kv = await self._submit_inference(timeout_s, self._run_inference, formatted_prompt, temp, max_tok, prefix_cache, adapter_path, gen_prompt_tokens)
            if self._kv_cache_enabled and session_result is None:
                try:
                    estimated_size = len(formatted_prompt) * 64
                    self._store_session_cache(formatted_prompt, populated_kv, estimated_size)
                except Exception:
                    pass
            if record_model_success is not None:
                record_model_success('hermes')
            if self._model_breaker is not None:
                self._model_breaker.record_success()
            if bandit is not None and arm_used and response:
                try:
                    response_len_norm = min(1.0, len(response) / 4000.0)
                    reward = response_len_norm * 0.8
                    bandit.update_reward(arm_used, reward, reward)
                    logger.debug(f'[GENERATE] Bandit reward: arm={arm_used} reward={reward:.3f}')
                except Exception as e:
                    logger.debug(f'[GENERATE] Bandit update failed: {e}')
            return response
        except TimeoutError:
            logger.warning('Hermes inference timed out')
            if record_model_failure is not None:
                record_model_failure('hermes', failure_kind='timeout')
            raise
        except asyncio.CancelledError:
            logger.warning('Hermes inference cancelled')
            raise
        except Exception as e:
            if record_model_failure is not None and classify_failure_kind is not None:
                kind = classify_failure_kind(e)
                record_model_failure('hermes', failure_kind=kind)
            if self._model_breaker is not None:
                if isinstance(e, (IndexError, KeyError)):
                    self._model_breaker.record_failure('internal_error')
                else:
                    err_str = str(e).lower()
                    if 'memory' in err_str or 'oom' in err_str or 'alloc' in err_str:
                        self._model_breaker.record_failure('oom')
                    elif 'timeout' in err_str or 'deadline' in err_str:
                        self._model_breaker.record_failure('timeout')
                    elif 'metal' in err_str or 'gpu' in err_str:
                        self._model_breaker.record_failure('metal_driver')
                    else:
                        self._model_breaker.record_failure('runtime_error')
            logger.error(f'Generation failed: {e}')
            return f'Error: {str(e)}'

    async def generate_stream(self, prompt: str, max_tokens: int=512, system_msg: str | None=None, temperature: float | None=None, *, thinking: bool=True) -> AsyncIterator[str]:
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
        if self._model is None:
            logger.debug('[STREAM] model not initialised — yielding nothing')
            return
        if not _MLX_AVAILABLE_GLOBAL:
            logger.debug('[STREAM] MLX unavailable — yielding nothing')
            return
        if not self._supports_stream_generate:
            try:
                full = await self.generate(prompt, temperature=temperature, max_tokens=max_tokens, system_msg=system_msg)
                if full:
                    yield full
            except Exception as e:
                logger.warning('[STREAM] fallback generate() failed: %s', e)
            return
        try:
            temp = temperature if temperature is not None else self.config.temperature
            max_tok = max_tokens
            system = system_msg or 'You are a helpful research assistant.'
            if thinking:
                system = f'{self._DEEP_THINKING_PREFIX}\n\n{system}'
            sanitized_prompt = prompt[:MAX_LLM_PROMPT_CHARS]
            # Issue #14: Stage 1 — CPU prep in thread pool (parallel with GPU of prior prompt)
            def _sync_stream_prep() -> str:
                sanitized = self._sanitize_for_llm(sanitized_prompt) if self._sanitize_for_llm else sanitized_prompt
                return self._format_chatml(system, sanitized)[:MAX_LLM_PROMPT_CHARS]
            loop = asyncio.get_running_loop()
            formatted_prompt = await loop.run_in_executor(self._prep_executor, _sync_stream_prep)
            # M-03: Tokenize once in thread pool — avoids double encode in _stream_tokens
            try:
                stream_prompt_tokens = await loop.run_in_executor(
                    None,  # default executor
                    lambda: self._tokenizer.encode(formatted_prompt)
                )
            except Exception:
                stream_prompt_tokens = None
        except Exception as e:
            logger.warning('[STREAM] prompt formatting failed: %s', e)
            return
        self._stream_cancelled.clear()
        session_result = self._get_session_cache(formatted_prompt)
        stream_prefix_cache = None
        if session_result is not None:
            cached_kv, _ = session_result
            stream_prefix_cache = cached_kv
            logger.debug('[SESSION-CACHE] Stream using cached KV')
        async with self._inference_semaphore:
            try:
                async for token in stream_via_queue(
                    self._stream_tokens, formatted_prompt, max_tok, temp, stream_prefix_cache, stream_prompt_tokens
                ):
                    if token:
                        yield token
            except asyncio.CancelledError:
                self._stream_cancelled.set()
                raise
            except Exception as e:
                logger.warning('[STREAM] generate_stream failed: %s', e)
                return
        try:
            _safe_mlx_eval_and_clear_cache('generate_stream_post')
        except Exception:
            pass

    def _stream_tokens(self, formatted_prompt: str, max_tok: int, temp: float, prefix_cache: Any=None, prompt_tokens: list[int] | None=None) -> Iterator[str]:
        """
        Sync token generator — runs in asyncio.to_thread, safe for M1.

        F288 FIX: Wrapped in get_metal_stream_context() — each thread gets
        its own mx.stream(gpu) via thread-local storage. This fixes
        "Stream(gpu,1) not in current thread" Metal errors when MLX is
        called from asyncio.to_thread.

        F266-U3: prefix_cache param enables cross-request KV reuse. When provided
        (from session cache pool), mlx_lm.stream_generate() extends the existing KV
        instead of recomputing from scratch.

        Honours the CLAUDE.md invariant: kv_bits (adaptive) + max_kv_size (adaptive
        via _get_kv_cache_kwargs) are passed to mlx_lm.stream_generate() (NOT to
        make_prompt_cache/load()). The generation call owns the cache lifecycle;
        we only pre-create it to attach 4-bit quantisation when the runtime
        supports it. F265C-METAL: max_kv_size is no longer hardcoded to 8192.

        M-03 FIX: prompt_tokens (pre-computed token list) avoids double tokenization.
        When prompt_tokens is provided, mlx_lm.stream_generate() receives it directly
        instead of re-encoding the string prompt. _get_kv_cache_kwargs uses len(prompt_tokens)
        directly, avoiding redundant encode() call.

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
        with get_metal_stream_context():
            kv_bits = self._get_adaptive_kv_bits()
            if self._kv_cache_enabled:
                if prefix_cache is not None:
                    kv_cache = prefix_cache
                    logger.debug('[STREAM] Reusing cached KV from session pool')
                elif self._paged_kv_cache:
                    # M-07: RotatingKVCache — zero-copy rotation, no mid-stream realloc
                    from mlx_lm.models.cache import RotatingKVCache
                    num_layers = len(self._model.layers)
                    kv_cache = [RotatingKVCache(max_size=max_tok, keep=self._paged_kv_keep) for _ in range(num_layers)]
                    logger.debug('[STREAM] RotatingKVCache: keep=%d, max_size=%d, layers=%d', self._paged_kv_keep, max_tok, num_layers)
                    if self._supports_kv_quant:
                        for layer in kv_cache:
                            if hasattr(layer, 'quantize'):
                                try:
                                    layer.quantize(group_size=64, bits=kv_bits)
                                    self._kv_cache_stats['quantized_count'] += 1
                                except Exception:
                                    pass
                else:
                    kv_cache = make_prompt_cache(self._model, max_kv_size=max_tok)
                    if self._supports_kv_quant:
                        for layer in kv_cache:
                            if hasattr(layer, 'quantize'):
                                try:
                                    layer.quantize(group_size=64, bits=kv_bits)
                                except Exception:
                                    pass
            else:
                kv_cache = None
            # M-03: Use pre-computed prompt_tokens if available (avoids double encode)
            _input_tokens_count: int | None = len(prompt_tokens) if prompt_tokens is not None else None
            stream_kwargs = {'max_tokens': max_tok, 'sampler': make_sampler(temp=temp), 'kv_bits': kv_bits, **self._get_kv_cache_kwargs(input_tokens=_input_tokens_count, max_tokens=max_tok), 'verbose': False}
            if kv_cache is not None:
                stream_kwargs['prompt_cache'] = kv_cache
            if self._speculative_enabled and self._draft_model_obj is not None and self._supports_draft:
                stream_kwargs['draft_model'] = self._draft_model_obj
                stream_kwargs['num_draft_tokens'] = self._num_draft_tokens
            _eval_counter = 0
            _active_gb = 0.0
            try:
                import mlx.core as _m3_mx
                _m3_mx.eval([])
            except Exception:
                pass
            _token_buffer = []
            _tokens_generated = 0
            # M-03: Use pre-computed prompt_tokens if available — avoids re-encoding
            _prompt_arg: str | list[int] = prompt_tokens if prompt_tokens is not None else formatted_prompt
            for chunk in stream_generate(self._model, self._tokenizer, prompt=_prompt_arg, **stream_kwargs):
                if hasattr(chunk, 'text'):
                    tok = chunk.text
                elif isinstance(chunk, tuple) and len(chunk) >= 1:
                    tok = chunk[0]
                else:
                    tok = str(chunk)
                if tok:
                    _eval_counter += 1
                    _tokens_generated += 1
                    _token_buffer.append(tok)
                    if _eval_counter % CLEAR_GRANULARITY_TOKENS == 0:
                        try:
                            import mlx.core as _m3_mx
                            if hasattr(_m3_mx, 'get_active_memory'):
                                _active_gb = int(_m3_mx.get_active_memory()) / 1024 ** 3
                            elif hasattr(_m3_mx.metal, 'get_active_memory') and _m3_mx.metal is not None:
                                _active_gb = int(_m3_mx.metal.get_active_memory()) / 1024 ** 3
                        except Exception:
                            _active_gb = 0.0
                    _chunk_size = max(EVAL_GRANULARITY_TOKENS_MIN, min(EVAL_GRANULARITY_TOKENS_MAX, int(_active_gb * 40)))
                    if _eval_counter % EVAL_EVERY_N_TOKENS == 0:
                        try:
                            import mlx.core as _m3_mx
                            _m3_mx.eval([])
                            self._lazy_ops_eval_count += 1
                            if _eval_counter % CLEAR_GRANULARITY_TOKENS == 0:
                                _active = 0
                                try:
                                    if hasattr(_m3_mx, 'get_active_memory'):
                                        _active = int(_m3_mx.get_active_memory())
                                    elif hasattr(_m3_mx.metal, 'get_active_memory') and _m3_mx.metal is not None:
                                        _active = int(_m3_mx.metal.get_active_memory())
                                except Exception:
                                    _active = 0
                                if _active > M3_METAL_PRESSURE_BYTES:
                                    import gc
                                    gc.collect()
                                    _m3_mx.eval([])
                                    if hasattr(_m3_mx, 'clear_cache'):
                                        _m3_mx.clear_cache()
                                    gc.collect()
                        except Exception:
                            pass
                    if len(_token_buffer) >= STREAM_BUFFER_SIZE:
                        yield ''.join(_token_buffer)
                        _token_buffer = []
                    try:
                        if isinstance(self._stream_cancelled, asyncio.Event) and self._stream_cancelled.is_set():
                            if _token_buffer:
                                yield ''.join(_token_buffer)
                                _token_buffer = []
                            break
                    except Exception:
                        pass
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
        query = context.get('query', '')
        step = context.get('step', 0)
        max_steps = context.get('max_steps', 20)
        history = context.get('history', [])
        system_msg = 'You are a research orchestrator. Decide the next action to progress the research.\n\nAvailable actions:\n- search: Search for information\n- google: Google search\n- download: Download a file\n- deep_read: Read content from URL (secure)\n- research_paper: Search academic papers\n- osint_discovery: Discover hidden sources\n- archive_fallback: Check Wayback Machine\n- fact_check: Verify a claim\n- synthesize: Complete research and synthesize findings\n\nRespond in JSON format:\n{\n  "action": "action_name",\n  "params": {"key": "value"},\n  "reasoning": "why this action",\n  "complete": false\n}\n\nSet "complete": true when research is sufficiently comprehensive.'
        history_str = _msgspec_encode_fast(history[-3:]).decode() if history else 'No previous actions'
        prompt = f'Research query: {query}\nStep: {step}/{max_steps}\n\nHistory:\n{history_str}\n\nWhat should be the next action?'
        decision_model = await self.generate_structured(prompt, _DecisionOutput, system_msg=system_msg, temperature=0.2)
        return msgspec.to_dict(decision_model)
    _PLAN_MAX_QUERY_CHARS = 2048
    _PLAN_MAX_HISTORY_ITEMS = 5
    _PLAN_MAX_CONTEXT_CHARS = 4096
    _SYNTH_MAX_QUERY_CHARS = 1024
    _SYNTH_MAX_FINDINGS = 50
    _SYNTH_MAX_FINDING_CHARS = 800
    _SYNTH_MAX_HYPOTHESES = 10
    _SYNTH_MAX_OUTPUT_CHARS = 8192
    _REPORT_MAX_CONTEXT_CHARS = 4096 * 4
    _REPORT_MAX_ITEM_CHARS = 500
    _REPORT_MAX_ITEMS = 20
    _REPORT_SYSTEM_PROMPT = 'Jsi OSINT research agent. Analyzuj poskytnuté podklady a vytvoř strukturovaný report v češtině. Na konci své odpovědi VŽDY vlož blok <IOC_JSON> s extrahovanými entitami ve formátu JSON. Formát: <IOC_JSON>{"iocs": ["ioc1", "ioc2", ...], "entities": ["entity1", "entity2", ...]}</IOC_JSON>'

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
        if self._model is None:
            logger.warning('[GENERATE_REPORT] Model not loaded, skipping report generation')
            return ''
        bounded_query = str(query)[:self._SYNTH_MAX_QUERY_CHARS]
        truncated_contexts: list[str] = []
        total_len = 0
        for item in context[:self._REPORT_MAX_ITEMS]:
            truncated = str(item)[:self._REPORT_MAX_ITEM_CHARS]
            if total_len + len(truncated) > self._REPORT_MAX_CONTEXT_CHARS:
                remaining = self._REPORT_MAX_CONTEXT_CHARS - total_len
                if remaining > 100:
                    truncated_contexts.append(truncated[:remaining])
                break
            truncated_contexts.append(truncated)
            total_len += len(truncated)
        context_str = '\n---\n'.join(truncated_contexts)
        bandit = self._get_prompt_bandit()
        arm_used = ''
        modifier = ''
        if bandit is not None:
            try:
                arm_used = bandit.select_arm()
                modifier = bandit.get_prompt_modifier(arm_used)
                self._last_bandit_arm = arm_used
                logger.debug(f'[GENERATE_REPORT] Bandit arm: {arm_used}')
            except Exception as e:
                logger.debug(f'[GENERATE_REPORT] Bandit select failed: {e}')
        prompt = f'Research query: {bounded_query}\n\nPodklady pro analýze:\n{context_str}\n\nVytvoř strukturovaný OSINT report v češtině s následujícími sekcemi:\n1. Shrnutí (Executive Summary) - max 3 věty\n2. Klíčová zjištění (Key Findings) - hlavní IOC a poznatky\n3. Doporučení (Recommendations) - praktické kroky\n\nReport piš v češtině, buď konkrétní a stručný.{modifier}'
        try:
            report_text = await self.generate(prompt=prompt, temperature=0.3, max_tokens=1024, system_msg=self._REPORT_SYSTEM_PROMPT)
            if bandit is not None and arm_used and report_text:
                try:
                    response_len_norm = min(1.0, len(report_text) / 2000.0)
                    confidence = min(1.0, len(truncated_contexts) / max(1, self._REPORT_MAX_ITEMS))
                    reward = response_len_norm * confidence
                    bandit.update_reward(arm_used, reward, reward)
                    logger.debug(f'[GENERATE_REPORT] Bandit reward: arm={arm_used} reward={reward:.3f}')
                except Exception as e:
                    logger.debug(f'[GENERATE_REPORT] Bandit update failed: {e}')
            return report_text
        except Exception as e:
            logger.error(f'[GENERATE_REPORT] Failed: {e}')
            return f'Report generation failed: {str(e)[:200]}'

    async def generate_sprint_plan(self, query: str, context: dict[str, Any] | None=None) -> dict[str, Any]:
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
        if self._model is None:
            return {'action': 'initialize', 'params': {'reason': 'model_not_loaded'}, 'reasoning': 'Hermes model not initialized', 'complete': False, 'plan_id': None, 'bounded': False}
        ctx = context or {}
        step = min(ctx.get('step', 0), 9999)
        max_steps = min(ctx.get('max_steps', 20), 9999)
        history = (ctx.get('history', []) or [])[-self._PLAN_MAX_HISTORY_ITEMS:]
        goals = ctx.get('goals', '')
        bounded_query = str(query)[:self._PLAN_MAX_QUERY_CHARS]
        query_was_truncated = len(str(query)) > self._PLAN_MAX_QUERY_CHARS
        bounded_history = []
        for h in history:
            if not isinstance(h, dict):
                h = {'action': str(h)[:200] if h else ''}
            entry = {'action': str(h.get('action', ''))[:200], 'result': str(h.get('result', ''))[:300] if h.get('result') else None}
            bounded_history.append(entry)
        runtime_ctx = {'query': bounded_query, 'step': step, 'max_steps': max_steps, 'history': bounded_history}
        if goals:
            runtime_ctx['goals'] = str(goals)[:self._PLAN_MAX_CONTEXT_CHARS]
        try:
            result = await self.decide_next_action(runtime_ctx)
            if not isinstance(result, dict):
                result = {'action': None, 'params': {}, 'reasoning': str(result), 'complete': False}
            for key in ('action', 'params', 'reasoning', 'complete'):
                if key not in result:
                    result[key] = None if key != 'complete' else False
            result['bounded'] = query_was_truncated
            result['plan_id'] = f'plan_{int(time.time() * 1000)}'
            return result
        except Exception as e:
            logger.warning(f'[SPRINT_PLAN] Failed: {e}')
            return {'action': 'error', 'params': {'error': str(e)[:200]}, 'reasoning': 'generate_sprint_plan failed', 'complete': False, 'plan_id': None, 'bounded': query_was_truncated}

    async def synthesize_findings(self, query: str, findings: list[Any], hypotheses: list[str] | None=None, context: dict[str, Any] | None=None) -> dict[str, Any]:
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
        if self._model is None:
            return {'report': 'Model not loaded', 'confidence': 0.0, 'sources_count': 0, 'hypotheses_evaluated': 0, 'bounded': False, 'synthesis_id': None}
        bounded_query = str(query)[:self._SYNTH_MAX_QUERY_CHARS]
        query_truncated = len(str(query)) > self._SYNTH_MAX_QUERY_CHARS
        bounded_findings = []
        for f in findings[:self._SYNTH_MAX_FINDINGS]:
            if isinstance(f, dict):
                finding_str = _msgspec_encode_fast(f).decode()[:self._SYNTH_MAX_FINDING_CHARS]
            else:
                finding_str = str(f)[:self._SYNTH_MAX_FINDING_CHARS]
            bounded_findings.append(finding_str)
        findings_truncated = len(findings) > self._SYNTH_MAX_FINDINGS
        bounded_hypotheses = []
        if hypotheses:
            bounded_hypotheses = [str(h)[:500] for h in hypotheses[:self._SYNTH_MAX_HYPOTHESES]]
        hypotheses_truncated = len(hypotheses or []) > self._SYNTH_MAX_HYPOTHESES
        history = (context or {}).get('history', [])
        goals = (context or {}).get('goals', '')
        runtime_ctx = {'query': bounded_query, 'history': history[-10:] if history else [], 'data': bounded_findings}
        if goals:
            runtime_ctx['goals'] = str(goals)[:self._SYNTH_MAX_CONTEXT_CHARS]
        try:
            raw_report = await self.synthesize(runtime_ctx)
            bounded_report = str(raw_report)[:self._SYNTH_MAX_OUTPUT_CHARS]
            output_truncated = len(str(raw_report)) > self._SYNTH_MAX_OUTPUT_CHARS
            confidence = min(1.0, len(bounded_findings) / max(1, self._SYNTH_MAX_FINDINGS))
            return {'report': bounded_report, 'confidence': confidence, 'sources_count': len(bounded_findings), 'hypotheses_evaluated': len(bounded_hypotheses), 'bounded': query_truncated or findings_truncated or hypotheses_truncated or output_truncated, 'synthesis_id': f'synth_{int(time.time() * 1000)}'}
        except Exception as e:
            logger.warning(f'[GENERATE] Failed: {e}')
            return {'report': f'Synthesis failed: {str(e)[:500]}', 'confidence': 0.0, 'sources_count': len(bounded_findings), 'hypotheses_evaluated': len(bounded_hypotheses), 'bounded': True, 'synthesis_id': None}

    async def synthesize(self, context: dict[str, Any]) -> str:
        """
        Syntetizovat výsledky výzkumu do finální odpovědi.

        Args:
            context: Kontext s nasbíranými daty

        Returns:
            Syntetizovaná odpověď
        """
        query = context.get('query', '')
        history = context.get('history', [])
        data = context.get('data', [])
        system_msg = 'You are a research synthesis expert. Create a comprehensive, well-structured answer based on the collected research data.  # noqa: E501\n\nYour answer should:\n- Be thorough and detailed\n- Cite sources where possible\n- Acknowledge limitations or gaps\n- Be objective and balanced\n- Use markdown formatting'
        data_summary = []
        for i, item in enumerate(data[-10:], 1):
            data_summary.append(f'{i}. {_msgspec_encode_fast(item).decode()[:500]}')
        history_str = _msgspec_encode_fast(history).decode()[:2000]
        prompt = f'Research Query: {query}\n\nCollected Data:\n{chr(10).join(data_summary)}\n\nExecution History:\n{history_str}\n\nSynthesize a comprehensive research report answering the query.'
        synthesis_model = await self.generate_structured(prompt, _SynthesisOutput, system_msg=system_msg, max_tokens=4096)
        return synthesis_model.report

    async def generate_structured(self, prompt: str, response_model: type[T], temperature: float | None=None, max_tokens: int | None=None, system_msg: str | None=None, max_retries: int=2, priority: float=1.0) -> T:
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
        if is_emergency_unload_requested is not None and is_emergency_unload_requested():
            self._telemetry_counters['emergency_guard_triggered'] += 1
            raise RuntimeError('emergency_unload_requested')
        timeout_s = max_tokens / 10.0 if max_tokens else None
        if self._is_batch_safe(response_model, priority, stream=False, timeout_s=timeout_s):
            try:
                self._telemetry_counters['batch_submitted'] += 1
                future = await self._submit_structured_batch(prompt=prompt, response_model=response_model, priority=priority, temperature=temperature or 0.1, max_tokens=max_tokens or 1024, system_msg=system_msg)
                result = await future
                schema_cls = response_model if isinstance(response_model, type) else type(response_model)
                if hasattr(schema_cls, '__struct_fields__'):
                    return result
                else:
                    if isinstance(result, schema_cls):
                        return result
                    return schema_cls.model_construct(**result) if isinstance(result, dict) else result
            except Exception as e:
                logger.debug(f'[STRUCTURED] Batch path failed: {e}, falling back to direct')
                self._telemetry_counters['batch_fallback_single'] += 1
        if OUTLINES_AVAILABLE and self._outlines_model is not None and (self._model is not None):
            try:
                schema_key = response_model.__name__
                if schema_key not in self._outlines_generators:
                    self._outlines_generators[schema_key] = _outlines_Generator(self._outlines_model, response_model)
                generator = self._outlines_generators[schema_key]

                def _do_outlines_generate() -> str:
                    return generator(prompt)
                result = await self._submit_inference(timeout=30.0, fn=_do_outlines_generate)
                return response_model.model_validate_json(result)
            except Exception as e:
                logger.debug(f'[STRUCTURED] Outlines failed: {e}, falling back to JSON')
        import re
        for attempt in range(max_retries + 1):
            schema_str = _msgspec_encode_fast(response_model.model_json_schema()).decode()
            json_prompt = f'{prompt}\n\nRespond ONLY with valid JSON matching this schema:\n{schema_str}\n\nDo not include any other text. Output valid JSON only.'
            text = await self.generate(json_prompt, temperature=0.1, max_tokens=2048, system_msg=system_msg)
            match = re.search('\\{.*\\}', text, re.DOTALL)
            if match:
                try:
                    data = _msgspec_decode(match.group())
                    return response_model(**data)
                except Exception as e:
                    if attempt < max_retries:
                        logger.debug(f'JSON parse failed (attempt {attempt + 1}): {e}')
                        continue
        logger.warning(f'[STRUCTURED] All attempts failed, using fallback for {response_model.__name__}')
        fields = dict.fromkeys(response_model.model_fields.keys())
        return response_model.model_construct(**fields)

    def invalidate_prefix_cache(self) -> None:
        """Clear the prefix cache (e.g., on model change)."""
        self._prefix_cache.clear()
        self._prefix_cache_stats['prefix_cache_size'] = 0
        self._prefix_cache_stats['prefix_cache_evictions'] = 0
        self._prefix_cache_stats['prefix_cache_hits'] = 0
        self._prefix_cache_stats['prefix_cache_misses'] = 0
        logger.info('[CACHE] Prefix cache invalidated')
    _BRIDGE_CHUNK_SIZE = 10

    async def execute_planner_requests(self, requests, response_models=None):
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
        from hledac.universal.planning.htn_planner import PlannerRuntimeResult
        if self._model is None:
            return [PlannerRuntimeResult(task_id=r.task_id, executed=False, skipped_panic=False, hermes_output=None, error='model_not_loaded') for r in requests]
        import msgspec

        class GenericResult(msgspec.Struct, kw_only=True):
            result: str = ''
            confidence: float = 0.5

        class FetchResult(GenericResult):
            url: str = ''

        class DeepReadResult(GenericResult):
            url: str = ''
            depth: int = 1

        class AnalyseResult(GenericResult):
            source: str = ''

        class SynthesizeResult(GenericResult):
            sources: list[str] = msgspec.field(default_factory=list)

        class BranchResult(GenericResult):
            branches: int = 1

        class ExplainResult(GenericResult):
            topic: str = ''

        class HypothesisResult(GenericResult):
            hypothesis: str = ''
        _MODEL_REGISTRY = {'FetchResult': FetchResult, 'DeepReadResult': DeepReadResult, 'AnalyseResult': AnalyseResult, 'SynthesizeResult': SynthesizeResult, 'BranchResult': BranchResult, 'ExplainResult': ExplainResult, 'HypothesisResult': HypothesisResult, 'GenericResult': GenericResult}
        if response_models is None:
            response_models = _MODEL_REGISTRY
        results: list[PlannerRuntimeResult] = []

        async def execute_single(req) -> PlannerRuntimeResult:
            """Execute a single PlannerRuntimeRequest via generate_structured."""
            if req.is_panic_deprioritized:
                return PlannerRuntimeResult(task_id=req.task_id, executed=False, skipped_panic=True, hermes_output=None, error=None)
            model_cls = response_models.get(req.response_model_name, GenericResult)
            t0 = time.monotonic_ns()
            try:
                result = await self.generate_structured(prompt=req.prompt, response_model=model_cls, priority=req.priority, system_msg='You are a helpful research assistant.', max_tokens=1024)
                elapsed_s = (time.monotonic_ns() - t0) / 1000000000.0
                output_text = result.result if hasattr(result, 'result') else str(result)
                return PlannerRuntimeResult(task_id=req.task_id, executed=True, skipped_panic=False, hermes_output=output_text, error=None).copy(elapsed_s=elapsed_s)
            except Exception as exc:
                return PlannerRuntimeResult(task_id=req.task_id, executed=False, skipped_panic=False, hermes_output=None, error=str(exc))
        for i in range(0, len(requests), self._BRIDGE_CHUNK_SIZE):
            chunk = requests[i:i + self._BRIDGE_CHUNK_SIZE]
            chunk_tasks = [execute_single(req) for req in chunk]
            _chunk_result = await parallel(chunk_tasks, policy="log", ctx='deephermes3_engine:2147')
            chunk_results = _chunk_result.ok
            for req, result in zip(chunk, chunk_results, strict=False):
                if isinstance(result, Exception):
                    results.append(PlannerRuntimeResult(task_id=req.task_id, executed=False, skipped_panic=False, hermes_output=None, error=f'bridge_exception:{result}'))
                else:
                    results.append(result)
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
        await self._shutdown_batch_worker(timeout=3.0)
        self._batch_queue = None
        self._batch_worker_task = None
        await self._save_cache()
        if self._warmup_cache is not None:
            self._warmup_cache = None
            logger.debug('[LIFECYCLE] _warmup_cache evicted')
        self._warmup_prompt_hash = None
        if self._prompt_cache is not None:
            self._prompt_cache = None
            logger.debug('[LIFECYCLE] _prompt_cache evicted')
        if self._system_prompt_cache is not None:
            self._system_prompt_cache = None
            logger.debug('[LIFECYCLE] _system_prompt_cache evicted')
        self._kv_cache_pool.clear()
        logger.debug('[LIFECYCLE][F289] _kv_cache_pool evicted')
        self.invalidate_prefix_cache()
        logger.info('Unloading Hermes-3...')
        self._inference_executor.shutdown(wait=True)
        # Issue #14: shutdown thread pools (prep + post)
        if hasattr(self, '_prep_executor') and self._prep_executor is not None:
            self._prep_executor.shutdown(wait=False)
            self._prep_executor = None
        if hasattr(self, '_post_executor') and self._post_executor is not None:
            self._post_executor.shutdown(wait=False)
            self._post_executor = None
        if self._compile_executor is not None:
            self._compile_executor.shutdown(wait=False)
            self._compile_executor = None
        if self._mlx_batcher is not None:
            try:
                await self._mlx_batcher.shutdown()
            except Exception as _e:
                logger.debug('[P0-2] batcher shutdown skipped: %s', _e)
        if self._mlx_worker_thread is not None:
            try:
                self._mlx_worker_thread.shutdown(timeout=5.0)
            except Exception as _e:
                logger.debug('[P0-3] worker thread shutdown skipped: %s', _e)
        self._model = None
        self._tokenizer = None
        self._outlines_model = None
        self._mlx_batcher = None
        self._mlx_worker_thread = None
        try:
            gc.freeze()
        except Exception:
            pass
        global _MLX_PREWARM_LAST_UNLOAD_TIME, _mlx_prewarm_active
        if _MLX_PREWARM_ENABLED and _mlx_prewarm_active:
            try:
                import time as _time
                _MLX_PREWARM_LAST_UNLOAD_TIME = _time.monotonic()
            except Exception:
                pass
            logger.debug('[F267] MLX prewarm: skipping clear_cache, model kept warm')
        else:
            _safe_mlx_eval_and_clear_cache('hermes_unload')
        _mlx_prewarm_active = False
        try:
            from brain.ane_embedder import get_ane_mlx_mutex
            get_ane_mlx_mutex().release('llm')
        except Exception:
            pass
        logger.info('✓ Hermes-3 unloaded (Sprint 7K lifecycle closed)')

    def reset_session(self) -> None:
        """
        Sprint F259: Reset session-local MLX KV cache between sprints.

        Unlike unload(), this is a lightweight reset that clears only session-
        specific state without fully unloading the model. Called at the start
        of each new sprint to prevent KV cache accumulation.

        M1 8GB invariant: Prevents KV cache from growing across sprints.
        """
        self._prompt_cache = None
        self._system_prompt_cache = None
        self._system_prompt_hash = None
        self._kv_cache_pool.clear()
        self._session_cache_pool.clear()
        self.invalidate_prefix_cache()
        try:
            import mlx.core as mx
            mx.eval([])
        except Exception:
            pass
        self._kv_cache_stats = {'cache_uses': 0, 'cache_prefills': 1, 'quantized_count': 0, 'parallel_prefills': 0}
        self._session_cache_stats = {'session_cache_hits': 0, 'session_cache_misses': 0, 'session_cache_evictions': 0, 'session_cache_memory_mb': self._session_cache_memory_mb, 'session_cache_maxsize': self._session_cache_maxsize}
        logger.debug('[F259] Hermes3 session KV cache reset')

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
        total_pool_bytes = sum((entry[2] for entry in self._kv_cache_pool.values()))
        return {**self._kv_cache_pool_stats, 'pool_current_bytes': total_pool_bytes, 'pool_current_mb': total_pool_bytes / (1024 * 1024)}

    def get_inference_stats(self) -> dict:
        """Krok 1.2: Return MLX lazy ops counters and GPU memory metrics.

        Returns:
            dict with keys:
            - lazy_ops_eval_count: total mx.eval([]) calls across all streaming generations
            - gpu_memory_active_bytes: current active GPU memory (0 if unavailable)
            - gpu_memory_active_gb: current active GPU memory in GiB
            - metal_pressure_fast_flush: count of GPU-pressure-triggered fast flushes
            - pending_lazy_ops_estimate: rough estimate of accumulated lazy ops
              (lazy_ops_eval_count * avg_tokens_per_eval cycle)
        """
        _active_bytes = 0
        if _MLX_AVAILABLE_GLOBAL:
            try:
                import mlx.core as _mx
                if hasattr(_mx, 'get_active_memory'):
                    _active_bytes = int(_mx.get_active_memory())
                elif hasattr(_mx.metal, 'get_active_memory') and _mx.metal is not None:
                    _active_bytes = int(_mx.metal.get_active_memory())
            except Exception:
                pass
        return {'lazy_ops_eval_count': self._lazy_ops_eval_count, 'gpu_memory_active_bytes': _active_bytes, 'gpu_memory_active_gb': _active_bytes / 1024 ** 3, 'metal_pressure_fast_flush': self._telemetry_counters.get('metal_pressure_fast_flush', 0), 'pending_lazy_ops_estimate': self._lazy_ops_eval_count * EVAL_EVERY_N_TOKENS}

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
        """Load specified model by path identifier (P0-04: uses HermesModelCache singleton)."""
        # M-08: Invalidate all caches BEFORE loading new model (prevents stale Metal allocations)
        self._invalidate_all_prompt_caches(f'model_swap_start:{model_id}')
        cache = hermes_cache()
        result = cache.get_model(model_id)
        if result is not None:
            self._model, self._tokenizer = result
            self.config.model_path = model_id
            logger.info(f'[HERMES] Model retrieved from cache (LRU updated): {model_id}')
            self._model_ever_loaded = True
            # Re-initialize prompt cache for cached model (engine-specific, not in hermes_cache LRU)
            try:
                from mlx_lm.utils import make_prompt_cache
                self._prompt_cache = make_prompt_cache(self._model)
                self._kv_cache_enabled = True
            except Exception:
                self._prompt_cache = None
                self._kv_cache_enabled = False
            return True
        from brain.ane_embedder import get_ane_mlx_mutex
        mutex = get_ane_mlx_mutex()
        try:
            from mlx_lm import load
            from mlx_lm.utils import make_prompt_cache  # type: ignore[attr-defined]
            mutex.acquire_mlx(model_size_mb=2000.0)
            if os.getenv('HLEDAC_HERMES_NO_CACHE', '0') == '1':
                self._model, self._tokenizer = await asyncio.to_thread(load, model_id)
            else:
                logger.info(f'[HERMES] Loading model from disk: {model_id}')
                self._model, self._tokenizer = await asyncio.to_thread(load, model_id)
                cache.put_model(model_id, self._model, self._tokenizer)
                mc, lc = len(cache)  # type: ignore[arg-type]
                logger.info(f'[HERMES] Model cached ({mc} models, {lc} loras)')
            self.config.model_path = model_id
            global KV_CACHE_AVAILABLE
            try:
                self._prompt_cache = make_prompt_cache(self._model)
                self._kv_cache_enabled = True
                KV_CACHE_AVAILABLE = True
            except Exception:
                self._prompt_cache = None
                self._kv_cache_enabled = False
                KV_CACHE_AVAILABLE = False
            # M-08 NOTE: End-of-load invalidation NOT needed here because:
            # 1. _invalidate_all_prompt_caches() was already called at model_swap_start
            # 2. Fresh prompt_cache was just created via make_prompt_cache() above
            # 3. Stale KV/prompt from previous model already cleared by the start invalidation
            logger.info(f'✓ Model loaded: {model_id}')
            self._model_ever_loaded = True
            return True
        except Exception as e:
            logger.warning(f'Model load failed for {model_id}: {e}')
            raise
        finally:
            try:
                mutex.release('llm')
            except Exception:
                pass

    def _get_cache_size_mb(self) -> float:
        """Get current KV cache size in MB using tree flatten."""
        if not self._prompt_cache:
            return 0.0
        try:
            import sys
            import mlx.core as mx
            if isinstance(self._prompt_cache, tuple) and self._prompt_cache[0] == 'commvq_compressed':
                compressed_groups = self._prompt_cache[1]
                total_bytes = 0
                for centroids, indices in compressed_groups:
                    total_bytes += centroids.nbytes + indices.nbytes
                return total_bytes / (1024 * 1024)
            leaves = mx.tree_flatten(self._prompt_cache)
            total_bytes = sum((l.nbytes if hasattr(l, 'nbytes') else sys.getsizeof(l) for l in leaves))
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
            import mlx.core as mx
            try:
                mx.eval(self._prompt_cache)
                if hasattr(self._prompt_cache, 'dtype'):
                    if self._prompt_cache.dtype not in (mx.bfloat16, mx.float16, mx.float32):
                        logger.debug(f'[KV-CACHE] Skip: cache dtype is {self._prompt_cache.dtype}')
                        return False
            except Exception as e:
                logger.warning(f'[KV-CACHE] Cannot evaluate cache: {e}')
                return False
            compressed = commvq_quantize(self._prompt_cache, bits=2)
            if compressed is self._prompt_cache:
                logger.debug('[KV-CACHE] Quantization returned original (fail-safe)')
                return False
            old_size = self._get_cache_size_mb()
            self._prompt_cache = compressed
            mx.eval(self._prompt_cache)
            new_size = self._get_cache_size_mb()
            savings = (old_size - new_size) / old_size * 100 if old_size > 0 else 0
            logger.info(f'[KV-CACHE] Compressed: {old_size:.1f} MB -> {new_size:.1f} MB ({savings:.1f}% savings)')
            return True
        except Exception as e:
            logger.warning(f'[KV-CACHE] Compression failed: {e}')
            return False

    async def _prune_kv_cache(self) -> bool:
        """
        Sprint 37: Prune KV cache resetem offsetu pokud kontext > 1024 tokenů.
        mlx_lm PromptCache nepodporuje přímý token mask – offset je jediný bezpečný způsob.
        """
        if not self._kv_cache_enabled or self._prompt_cache is None:
            return False
        try:
            if not hasattr(self._prompt_cache, 'offset'):
                return False
            context_len = self._prompt_cache.offset
            if context_len <= 1024:
                return False
            new_offset = int(context_len * 0.8)
            self._prompt_cache.offset = new_offset
            logger.info(f'[PRUNE] Context {context_len} → {new_offset} tokens (saved {context_len - new_offset})')
            return True
        except Exception as e:
            logger.warning(f'[PRUNE] Failed: {e}, falling back to compression')
            return False

    @staticmethod
    def _build_sustain_generate_kwargs_for_test(generate_fn: Callable) -> dict:
        """
        Build MLX generate kwargs for sustain mode using runtime introspection.

        Uses GHOST_HERMES_SUSTAIN=1 env flag and inspects generate_fn signature
        to add only supported kwargs.
        """
        sustain_flag = os.getenv('GHOST_HERMES_SUSTAIN', '0')
        if sustain_flag != '1':
            return {}
        try:
            sig = inspect.signature(generate_fn)
            param_names = set(sig.parameters.keys())
            has_var_keyword = any((p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()))
        except Exception:
            param_names = set()
            has_var_keyword = False
        kwargs = {}
        if 'max_kv_size' in param_names or has_var_keyword:
            kwargs['max_kv_size'] = int(os.getenv('GHOST_KV_SIZE', '4096'))
        if 'kv_cache_type' in param_names:
            kwargs['kv_cache_type'] = 'rotating'
        if 'attention_sink_size' in param_names:
            kwargs['attention_sink_size'] = 4
        return kwargs

    def _run_sustain_inference(self, formatted_prompt: str, temp: float, max_tok: int) -> str:
        """Run MLX inference with sustain mode (M1 8GB optimization)."""
        from mlx_lm import generate as mlx_generate
        from mlx_lm.sample_utils import make_sampler
        try:
            from ..utils.mlx_memory import configure_mlx_limits, format_mlx_memory_snapshot, get_current_memory_tier, get_tier_config
            tier = get_current_memory_tier()
            config = get_tier_config(tier)
            configure_mlx_limits(cache_limit_mb=config['cache_mb'], memory_limit_mb=config['buffer_mb'])
            logger.debug(f'[SUSTAIN] PRE (tier={tier}): {format_mlx_memory_snapshot()}')
        except Exception as e:
            logger.debug(f'[SUSTAIN] MLX limits configure failed: {e}')
        sustain_kwargs = self._build_sustain_generate_kwargs_for_test(mlx_generate)
        generate_kwargs = {'model': self._model, 'tokenizer': self._tokenizer, 'prompt': formatted_prompt, 'sampler': make_sampler(temp=temp), 'max_tokens': max_tok, 'verbose': False}
        for k, v in sustain_kwargs.items():
            generate_kwargs[k] = v
        if os.getenv('GHOST_PREFIX_CACHE_EXPERIMENT', '0') == '1':
            try:
                from mlx_lm.models.cache import make_prompt_cache
                kv_cache = make_prompt_cache(self._model, max_kv_size=max_tok)
                generate_kwargs['prompt_cache'] = kv_cache
            except Exception as e:
                logger.debug(f'[SUSTAIN] prompt_cache experiment failed: {e}')
        response = mlx_generate(**generate_kwargs)
        _safe_mlx_eval_and_clear_cache('sustain_inference')
        try:
            from ..utils.mlx_memory import format_mlx_memory_snapshot
            logger.debug(f'[SUSTAIN] POST: {format_mlx_memory_snapshot()}')
        except Exception:
            pass
        return response.strip()

    async def warmup_prefix_cache(self, system_prompt: str='You are a helpful research assistant.', few_shot_examples: list | None=None) -> bool:
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
            logger.warning('[WARMUP] Model not loaded, skipping warmup')
            return False
        if few_shot_examples is None:
            few_shot_examples = [{'user': 'What is 2+2?', 'assistant': '4'}, {'user': 'Capital of France?', 'assistant': 'Paris'}]
        try:
            parts = [f'<|im_start|>system\n{system_prompt}<|im_end|>']
            for ex in few_shot_examples[:3]:
                parts.append(f"<|im_start|>user\n{ex.get('user', '')}<|im_end|>")
                parts.append(f"<|im_start|>assistant\n{ex.get('assistant', '')}<|im_end|>")
            warmup_prompt = '\n'.join(parts)
            tokens = self._tokenizer.encode(warmup_prompt)
            token_count = len(tokens)
            if token_count > 1000:
                logger.warning(f'[WARMUP] Warmup prompt too long ({token_count} tokens), truncating')
                warmup_prompt = self._tokenizer.decode(tokens[:1000])
                tokens = tokens[:1000]
            if await warmup_or_skip(self, system_prompt, few_shot_examples):
                return True
            if XXHASH_AVAILABLE:
                canonical_parts = [system_prompt]
                if few_shot_examples:
                    for ex in few_shot_examples[:3]:
                        canonical_parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
                canonical_text = '\n'.join(canonical_parts)
                prompt_hash = _get_xxh3_hex(canonical_text)
            else:
                canonical_parts = [system_prompt]
                if few_shot_examples:
                    for ex in few_shot_examples[:3]:
                        canonical_parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
                canonical_text = '\n'.join(canonical_parts)
                prompt_hash = hashlib.blake2b(canonical_text.encode(), digest_size=8).hexdigest()
            logger.info(f'[WARMUP] Building fresh warmup cache (~{token_count} tokens)...')
            from mlx_lm.models.cache import make_prompt_cache
            self._warmup_cache = make_prompt_cache(self._model, max_kv_size=max(token_count + 128, 1024))
            self._warmup_prompt_hash = prompt_hash
            kv_bits = self._get_adaptive_kv_bits()
            if self._supports_kv_quant:
                for layer in self._warmup_cache:
                    if hasattr(layer, 'quantize'):
                        try:
                            layer.quantize(group_size=64, bits=kv_bits)
                        except Exception:
                            pass
            from mlx_lm import generate as mlx_generate
            from mlx_lm.sample_utils import make_sampler
            from ..utils.mlx_memory import get_metal_stream_context
            _worker = getattr(self, '_mlx_worker_thread', None)
            _worker_live = _worker is not None and _worker.is_active()

            def _do_generate() -> None:
                with get_metal_stream_context():
                    import mlx.core as _mx
                    _mx.eval([])
                    mlx_generate(model=self._model, tokenizer=self._tokenizer, prompt=warmup_prompt, sampler=make_sampler(temp=0.3), max_tokens=1, kv_bits=kv_bits, prompt_cache=self._warmup_cache, verbose=False)
            if _worker_live:
                try:
                    coro = self._run_inference_async(_do_generate)
                    await _worker.submit(coro, timeout=60.0)
                except RuntimeError as _no_loop:
                    logger.debug(f'[WARMUP] No running loop ({_no_loop}), using inline fallback')
                    try:
                        _do_generate()
                    except Exception as _warmup_exc:
                        logger.warning(f'[WARMUP] inline warmup failed ({_warmup_exc}), continuing')
                        return True
                except Exception as _warmup_exc:
                    logger.warning(f'[WARMUP] Worker thread warmup failed ({_warmup_exc}), continuing')
                    return True
            else:
                try:
                    _do_generate()
                except Exception as _warmup_exc:
                    logger.warning(f'[WARMUP] inline warmup failed ({_warmup_exc}), continuing')
                    return True
            _safe_mlx_eval_and_clear_cache('warmup_prefill')
            logger.info('[WARMUP] Prefix cache warmup complete (fresh build)')
            return True
        except Exception as e:
            logger.warning(f'[WARMUP] Warmup failed: {e}')
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
            stored_hash = metadata.get('prompt_hash', None) if metadata else None
            if stored_hash is None:
                return await self._restore_warmup_cache_legacy(cache_path, prompt_hash)
            if str(stored_hash) != prompt_hash:
                logger.debug(f'[WARMUP] Cache hash mismatch: {stored_hash} != {prompt_hash}')
                return False
            self._warmup_cache = cache
            self._warmup_prompt_hash = prompt_hash
            logger.debug(f'[WARMUP] Restored {len(cache)} layers via load_prompt_cache')
            return True
        except Exception as e:
            logger.debug(f'[WARMUP] load_prompt_cache failed: {e}, trying legacy restore')
            return await self._restore_warmup_cache_legacy(cache_path, prompt_hash)

    async def _restore_warmup_cache_legacy(self, cache_path: Path, prompt_hash: str) -> bool:
        """Legacy .npz restore for backward compatibility with existing warmup caches."""
        try:
            import mlx.core as mx
            data = mx.load(str(cache_path))
            stored_hash = data.get('_prompt_hash', None)
            if stored_hash is None:
                return False
            if hasattr(stored_hash, 'item'):
                stored_hash = str(stored_hash.item())
            if str(stored_hash) != prompt_hash:
                return False
            from mlx_lm.models.cache import make_prompt_cache
            n_tokens = len(self._tokenizer.encode('')) + 512
            self._warmup_cache = make_prompt_cache(self._model, max_kv_size=n_tokens)
            self._warmup_prompt_hash = prompt_hash
            restored = 0
            for i, layer in enumerate(self._warmup_cache):
                k_key = f'layer_{i}_keys'
                v_key = f'layer_{i}_values'
                if k_key in data and v_key in data:
                    if hasattr(layer, 'keys') and hasattr(layer, 'values'):
                        try:
                            layer.keys = data[k_key]
                            layer.values = data[v_key]
                            restored += 1
                        except Exception:
                            pass
            if restored > 0:
                logger.debug(f'[WARMUP] Restored {restored}/{len(self._warmup_cache)} layers (legacy)')
                return True
            return False
        except Exception as e:
            logger.debug(f'[WARMUP] Legacy restore failed: {e}')
            return False

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
            import outlines.generate as og
            import msgspec

            class _ProbeSchema(msgspec.Struct):
                ok: bool
            gen = og.json(self._outlines_model, _ProbeSchema)
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
Hermes3Engine = DeepHermes3Engine