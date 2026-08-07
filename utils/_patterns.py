"""
_shared_patterns.py — Centralizované vzory pro eliminaci kódových klonů.

Moderní Python 3.14+ implementace běžných vzorů optimalizovaných pro M1 8GB:
- Singleton s thread-safe lockem
- Async lifecycle helpers
- Result aggregation patterns
- Fail-safe decorators
- LMDB/context manager patterns

Autor: F320-refactor
Datum: 2026-08-07
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    ParamSpec,
    TypeVar,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    # Singleton
    "singleton_with_lock",
    # Async lifecycle
    "async_cleanup",
    "fail_safe_async",
    # Result aggregation
    "collect_results",
    "aggregate_with_score",
    # Fail-safe decorators
    "never_raises",
    "log_failures",
    # Telemetry helpers
    "elapsed_ms",
    "record_transition_safe",
    # Batch processing
    "backpressure_tier",
    "collect_batch_items",
    # LMDB helpers
    "safe_lmdb_close",
    "safe_close",
]

P = ParamSpec("P")
T = TypeVar("T")
R = TypeVar("R")


# ==============================================================================
# Singleton Pattern
# ==============================================================================


def singleton_with_lock(cls: type[T]) -> type[T]:
    """
    Decorator pro thread-safe singleton s lockem.

    Použití:
        @singleton_with_lock
        class MySingleton:
            def __init__(self) -> None:
                self._data: int = 0

    Místo:
        class MySingleton:
            _lock = threading.Lock()
            _instance: MySingleton | None = None

            def __new__(cls) -> MySingleton:
                with cls._lock:
                    if cls._instance is None:
                        instance = super().__new__(cls)
                        instance._data = 0
                        cls._instance = instance
                return cls._instance

    Výhody:
    - Méně kódu
    - Type-safe
    - Automatický lock per-class
    - M1 8GB: žádné extra alokace
    """
    _instances: dict[type, T] = {}
    _locks: dict[type, threading.Lock] = {}

    @wraps(cls)
    def get_instance(*args: P.args, **kwargs: P.kwargs) -> T:
        if cls not in _locks:
            _locks[cls] = threading.Lock()
        lock = _locks[cls]

        with lock:
            if cls not in _instances:
                _instances[cls] = object.__new__(cls)
                _instances[cls].__init__(*args, **kwargs)
            return _instances[cls]

    get_instance._is_singleton = True  # type: ignore[attr-defined]
    get_instance._original_class = cls  # type: ignore[attr-defined]

    return get_instance  # type: ignore[return-value]


class SingletonMeta(type):
    """
    Metaclass pro singleton s lockem (alternativa k decoratoru).

    Použití:
        class MySingleton(metaclass=SingletonMeta):
            _lock = threading.Lock()
            _instance: MySingleton | None = None
            # Nebo bez explicitního locku:
            # _singleton_lock: ClassVar[threading.Lock] = threading.Lock()
    """

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        lock_attr = "_singleton_lock"
        instance_attr = "_singleton_instance"

        lock = getattr(cls, lock_attr, None)
        if lock is None:
            lock = threading.Lock()
            setattr(cls, lock_attr, lock)

        with lock:
            instance = getattr(cls, instance_attr, None)
            if instance is None:
                instance = super().__call__(*args, **kwargs)
                setattr(cls, instance_attr, instance)
            return instance


# ==============================================================================
# Async Lifecycle Helpers
# ==============================================================================


async def async_cleanup(
    *components: Any,
    logger: Any = None,
    context: str = "",
) -> None:
    """
    Univerzální async cleanup pro více komponent.

    Použití:
        await async_cleanup(
            self.decomposer,
            self.extractor,
            logger=logger,
            context="HTNPlanner"
        )

    Místo:
        if self.decomposer is not None and hasattr(self.decomposer, 'unload'):
            try:
                await self.decomposer.unload()
            except Exception as e:
                logger.debug(f'...')

    Výhody:
    - Konzistentní error handling
    - Centralizované logging
    - Idempotentní
    """
    for component in components:
        if component is None:
            continue

        name = type(component).__name__
        method_name = None

        # Prefer unload() before close() (HL7 kompatibilní)
        for method in ("unload", "close", "cleanup", "shutdown"):
            if hasattr(component, method):
                method_name = method
                break

        if method_name:
            try:
                method = getattr(component, method_name)
                if asyncio.iscoroutinefunction(method):
                    await method()
                else:
                    method()
            except Exception as e:
                if logger:
                    logger.debug(f"{context} {name}.{method_name} error: {e}")
        else:
            if logger:
                logger.debug(f"{context} {name}: no cleanup method found")


def fail_safe_async(
    default: Any = None,
    reraise: bool = False,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """
    Decorator pro async metody které nikdy nevyhodí výjimku.

    Použití:
        @fail_safe_async(default=[])
        async def get_items(self) -> list[Item]:
            return await self._fetch_items()

    Místo:
        async def get_items(self) -> list[Item]:
            try:
                return await self._fetch_items()
            except Exception:
                return []

    Výhody:
    - DRY
    - Konzistentní chování
    - Snadná změna defaultu
    """

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return await func(*args, **kwargs)
            except Exception:
                if reraise:
                    raise
                return default

        return wrapper

    return decorator


# ==============================================================================
# Result Aggregation
# ==============================================================================


def collect_results(
    items: Sequence[T],
    processor: Callable[[T], R],
    *,
    timeout: float | None = None,
) -> list[R]:
    """
    Sekvenční sběr výsledků z processoru.

    Použití:
        results = collect_results(
            queries,
            lambda q: _python_query_duckdb(db_path, q)
        )

    Místo:
        results = []
        for sql in queries:
            result = _python_query_duckdb(db_path, sql)
            results.append(result)

    M1 8GB: Sekvenční je lepší pro I/O bound operace na limitovaném RAM.
    """
    return [processor(item) for item in items]


def aggregate_with_score(
    items: Sequence[T],
    scorer: Callable[[T], float],
    *,
    reverse: bool = True,
) -> list[tuple[T, float]]:
    """
    Rankování položek podle skóre.

    Použití:
        ranked = aggregate_with_score(
            candidates,
            lambda a: compute_eig(hypotheses, a)
        )

    Místo:
        scored = []
        for action in candidates:
            eig = self.compute_eig(hypotheses_set, action)
            scored.append((action, eig))
        scored.sort(key=lambda x: x[1], reverse=True)

    Výhody:
    - List comprehension pro M1 cache efficiency
    - Jednořádkové volání
    """
    scored = [(item, scorer(item)) for item in items]
    scored.sort(key=lambda x: x[1], reverse=reverse)
    return scored


def joint_probability(hypotheses: Sequence[dict[str, Any]], prob_key: str = "posterior_probability") -> float:
    """
    Výpočet joint probability z hypotéz.

    Použití:
        joint = joint_probability(hypotheses, "posterior_probability")

    Místo:
        joint_prob = 1.0
        for hypothesis in hypotheses:
            joint_prob *= hypothesis.posterior_probability
    """
    if not hypotheses:
        return 0.0
    product = 1.0
    for h in hypotheses:
        product *= h.get(prob_key, 0.0)
    return product


def compound_confidence(
    hops: Sequence[dict[str, Any]],
    confidence_key: str = "confidence",
    decay_factor: float = 0.9,
) -> float:
    """
    Compounded confidence s length penalty.

    Použití:
        conf = compound_confidence(hops, "confidence", 0.9)

    Místo:
        product_confidence = 1.0
        for hop in hops:
            product_confidence *= hop.confidence
        length_penalty = 0.9 ** (len(hops) - 1)
        return product_confidence * length_penalty
    """
    if not hops:
        return 1.0
    product = 1.0
    for hop in hops:
        product *= hop.get(confidence_key, 1.0)
    penalty = decay_factor ** (len(hops) - 1)
    return product * penalty


# ==============================================================================
# Fail-Safe Decorators
# ==============================================================================


def never_raises(
    default: R = None,  # type: ignore[assignment]
    reraise: bool = False,
    log_traceback: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator pro synchronní metody které nikdy nevyhodí výjimku.

    Použití:
        @never_raises(default=[])
        def get_routes(self, domain: str) -> list[RouteEdge]:
            return await self._get_routes_for_domain(domain)

    Výhody:
    - Konzistentní "never raises" sémantika
    - Volitelný logging
    - Type-safe default
    """
    import traceback
    import logging

    logger = logging.getLogger("hledac.patterns")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return func(*args, **kwargs)
            except Exception:
                if reraise:
                    raise
                if log_traceback:
                    logger.debug("never_raises %s: %s", func.__name__, traceback.format_exc())
                return default  # type: ignore[return-value]

        return wrapper

    return decorator


def log_failures(
    logger: Any,
    level: int = 10,  # DEBUG
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """
    Decorator pro logging výjimek bez jejich propagace.

    Použití:
        @log_failures(my_logger)
        async def risky_operation(self) -> Result:
            ...

    Místo:
        async def risky_operation(self) -> Result:
            try:
                return await self._do_it()
            except Exception as e:
                logger.debug(f'risky_operation error: {e}')
                raise  # nebo return None
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.log(level, "%s error: %s", func.__name__, e)
                raise

        return wrapper

    return decorator


# ==============================================================================
# Telemetry Helpers
# ==============================================================================


def elapsed_ms(started_at: float | None) -> float:
    """
    Výpočet uplynulého času v ms.

    Použití:
        elapsed = elapsed_ms(self._started_at)

    Místo:
        if self._started_at is None:
            return 0.0
        try:
            return (time.monotonic() - self._started_at) * 1000.0
        except Exception:
            return 0.0
    """
    if started_at is None:
        return 0.0
    try:
        return (time.monotonic() - started_at) * 1000.0
    except Exception:
        return 0.0


def record_transition_safe(
    telemetry: Any,
    from_phase: str,
    to_phase: str,
    component: str,
    started_at: float | None,
) -> None:
    """
    Safe telemetry transition recording.

    Použití:
        record_transition_safe(
            self._telemetry,
            from_phase="INIT",
            to_phase="RUNNING",
            component=self._component,
            started_at=self._started_at
        )
    """
    try:
        elapsed = elapsed_ms(started_at)
        telemetry.log_phase_transition(
            from_phase=from_phase,
            to_phase=to_phase,
            component=component,
            elapsed_ms=elapsed,
        )
    except Exception:  # noqa: BLE001
        pass


# ==============================================================================
# Batch Processing Helpers
# ==============================================================================


def backpressure_tier(
    queue_depth: int,
    high_pressure_threshold: int,
    medium_pressure_threshold: int,
) -> str:
    """
    Určení úrovně backpressure podle hloubky fronty.

    Použití:
        tier = backpressure_tier(
            queue_depth=qsize,
            high_pressure_threshold=100,
            medium_pressure_threshold=50
        )

    Místo:
        pressure_tier = "normal"
        if queue_depth > self._batch_high_pressure_depth:
            pressure_tier = "critical"
        elif queue_depth > self._batch_medium_pressure_depth:
            pressure_tier = "high"

    Výhody:
    - Oddělená logika pro snadnější testování
    - Konfigurovatelné thresholdy
    """
    if queue_depth > high_pressure_threshold:
        return "critical"
    if queue_depth > medium_pressure_threshold:
        return "high"
    return "normal"


async def collect_batch_items(
    queue: asyncio.PriorityQueue | asyncio.Queue,
    timeout_seconds: float,
    max_items: int,
    current_schema: Any = None,
    current_prompt_hash: Any = None,
    current_length_bin: Any = None,
) -> tuple[list, Any, Any, Any]:
    """
    Univerzální sběr batch položek z fronty.

    Použití:
        items, schema, hash, length = await collect_batch_items(
            queue=self._batch_queue,
            timeout_seconds=flush_interval,
            max_items=self._max_batch_size,
            current_schema=first_item[2],
            current_prompt_hash=first_item[3].get('hash'),
            current_length_bin=first_item[4]
        )

    Vrací:
        (items, schema_key, prompt_hash, length_bin)
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            first_item = await queue.get()
    except TimeoutError:
        return [], None, None, None

    items = [first_item]

    # Infer current values from first item if not provided
    if len(first_item) >= 3:
        current_schema = current_schema or first_item[2]
    if len(first_item) >= 4 and isinstance(first_item[3], dict):
        current_prompt_hash = current_prompt_hash or first_item[3].get("prompt_hash")
        current_length_bin = current_length_bin or first_item[3].get("length_bin")

    # Collect more items matching current batch criteria
    while len(items) < max_items:
        try:
            async with asyncio.timeout(0.01):
                item = await queue.get_nowait()
                # Check if item matches current batch
                if len(item) >= 3 and item[2] != current_schema:
                    await queue.put(item)
                    break
                items.append(item)
        except (TimeoutError, asyncio.QueueEmpty):
            break

    return items, current_schema, current_prompt_hash, current_length_bin


# ==============================================================================
# LMDB / Safe Close Helpers
# ==============================================================================


def safe_lmdb_close(env: Any, *, logger: Any = None, name: str = "LMDB") -> None:
    """
    Bezpečné uzavření LMDB prostředí.

    Použití:
        safe_lmdb_close(self._env, logger=logger, name="UNIFIED-LMDB")

    Místo:
        if self._env is not None:
            try:
                self._env.close()
            except Exception as exc:
                logger.debug("env.close() failed: %s", exc)
            self._env = None
    """
    if env is None:
        return
    try:
        env.close()
    except Exception as exc:
        if logger:
            logger.debug("[%s] env.close() failed: %s", name, exc)


def safe_close(
    *resources: Any,
    logger: Any = None,
    context: str = "",
) -> None:
    """
    Univerzální bezpečné uzavření resources.

    Použití:
        safe_close(
            self._persist_file,
            self._socket,
            logger=logger,
            context="Metrics"
        )

    Místo:
        if self._persist_file:
            try:
                self._persist_file.close()
            except Exception as e:
                logger.error(f'Error closing metrics: {e}')
            finally:
                self._persist_file = None

    Výhody:
    - Podporuje libovolný počet resources
    - Automatický finally reset na None
    - Konzistentní logging
    """
    for resource in resources:
        if resource is None:
            continue

        name = type(resource).__name__
        try:
            close_method = getattr(resource, "close", None)
            if close_method:
                close_method()
        except Exception as e:
            if logger:
                logger.debug("%s %s close error: %s", context, name, e)


@contextmanager
def finalizer_context(obj: Any, cleanup_method: str = "_cleanup"):
    """
    Context manager pro weakref.finalize pattern.

    Použití:
        self._finalizer = finalizer_context(self, "_cleanup")
        with self._finalizer:
            # obj je aktivní
            pass
        # při GC se zavolá obj._cleanup()

    Místo:
        import weakref
        def _finalize() -> None:
            self._cleanup()
        self._finalizer = weakref.finalize(self, _finalize)

    Výhody:
    - Čistší syntaxe
    - Automatické volání při GC
    """
    import weakref

    cleanup = getattr(obj, cleanup_method, None)
    if cleanup is None:
        raise ValueError(f"Object has no {cleanup_method} method")

    finalizer = weakref.finalize(obj, cleanup)
    try:
        yield finalizer
    finally:
        finalizer()


# ==============================================================================
# Path Extraction Helpers
# ==============================================================================


def extract_internal_paths(
    names: list[str],
    *,
    extensions: tuple[str, ...] = (".xml",),
    max_paths: int = 100,
) -> list[str]:
    """
    Extrakce interních cest z archivu.

    Použití:
        paths = extract_internal_paths(names)

    Místo:
        internal_paths = []
        for name in names:
            if name.endswith('.xml'):
                try:
                    xml_content = zf.read(name).decode('utf-8', errors='ignore')
                    paths = self._find_internal_paths(xml_content)
                    internal_paths.extend(paths)
                except Exception:
                    pass
        result['internal_paths'] = list(set(internal_paths))[:MAX_INTERNAL_PATHS]

    Výhody:
    - List comprehension pro efektivitu
    - Deduplikace přes set
    - Limit na max_paths
    """
    paths: list[str] = []
    for name in names:
        if name.endswith(extensions):
            paths.append(name)
    return list(set(paths))[:max_paths]


# ==============================================================================
# Memory Cleanup Helpers
# ==============================================================================


def memory_cleanup_fallback(
    *,
    mlx_cleanup: bool = True,
    malloc_relief: bool = True,
    logger: Any = None,
    level: str = "normal",
) -> None:
    """
    Univerzální memory cleanup pro M1 8GB systémy.

    Použití:
        memory_cleanup_fallback(logger=logger, level="critical")

    Místo:
        try:
            from utils import mlx_cache
            mlx_cache.mlx_cleanup_sync()
        except (ImportError, AttributeError) as e:
            logger.debug(f'Cleanup failed: {e}')
        try:
            from core.memory_cycle import malloc_zone_pressure_relief
            released = malloc_zone_pressure_relief()
        except (ImportError, AttributeError, OSError) as e:
            logger.debug(f'malloc relief failed: {e}')

    Výhody:
    - Fallback při chybějících dependencies
    - Různé úrovně agresivity
    - M1 optimalizované
    """
    if mlx_cleanup:
        try:
            from utils import mlx_cache

            if level == "aggressive":
                mlx_cache.mlx_cleanup_aggressive()
            else:
                mlx_cache.mlx_cleanup_sync()
            if logger:
                logger.info(f"[MEM-CLEANUP] MLX cleanup ({level}) completed")
        except (ImportError, AttributeError) as e:
            if logger:
                logger.debug(f"[MEM-CLEANUP] MLX cleanup failed: {e}")

    if malloc_relief:
        try:
            from core.memory_cycle import malloc_zone_pressure_relief

            released = malloc_zone_pressure_relief()
            if released > 0 and logger:
                logger.debug(f"[MEM-CLEANUP] malloc_zone released {released} bytes")
        except (ImportError, AttributeError, OSError) as e:
            if logger:
                logger.debug(f"[MEM-CLEANUP] malloc relief failed: {e}")


# ==============================================================================
# Re-export pro zpětnou kompatibilitu
# ==============================================================================

# Pro případné post-import úpravy existujících modulů
ORIGINAL_MODULES: dict[str, str] = {
    "core/rust_backend/__init__.py": "singleton pattern",
    "runtime/prewarm_daemon.py": "singleton pattern",
    "utils/cache.py": "cache size property",
    "planning/htn_planner.py": "async teardown",
    "recon/document_intelligence.py": "async close",
    "coordinators/monitoring_coordinator.py": "_do_cleanup",
    "layers/security_layer.py": "cleanup",
    "runtime/telemetry.py": "record methods",
    "core/rust_backend/query.py": "parallel queries",
    "tools/probe/probe_f214int_interpreter_pool.py": "candidate functions",
    "brain/deephermes3_engine.py": "_collect_batch",
    "brain/mlx_batch_coordinator.py": "_collect_batch",
    "brain/inference_engine.py": "calculate_joint_probability",
    "brain/_batch/batch_processor.py": "_process_batch",
    "utils/eig.py": "rank_actions",
    "knowledge/ioc_dedup_adapter.py": "close",
    "core/lmdb_unified.py": "_cleanup",
    "metrics_registry.py": "close",
    "layers/ghost_layer.py": "close",
    "utils/sketches.py": "close",
    "knowledge/proxy_routes.py": "record_route_failure",
    "transport/circuit_breaker.py": "domain_breaker_record_failure",
    "pipeline/_discovery_stage.py": "rescue/boostrap URLs",
    "tools/document_metadata_extractor.py": "internal paths",
    "transport/darknet_session_provider.py": "cleanup",
    "utils/uma_budget.py": "on_* callbacks",
}
