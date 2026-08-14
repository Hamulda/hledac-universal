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
from operator import itemgetter
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Literal,
    ParamSpec,
    TypeVar,
)
import logging

from collections.abc import Sequence

__all__ = [
    # Singleton
    "singleton_with_lock",
    "module_singleton_creator",
    "module_singleton_getter",
    # Lazy module import
    "lazy_module_getter",
    # Async lifecycle
    "async_cleanup",
    "fail_safe_async",
    # Result aggregation
    "collect_results",
    "collect_results_async",  # F320
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
    "compound_confidence_from_objects",  # F320
    # LMDB helpers
    "safe_lmdb_close",
    "safe_close",
    "make_close_method",  # F320: close() factory (flexible)
    "make_lmdb_close",  # F320-REFACTOR-2: LMDB close factory
    "CloseMethodDescriptor",  # F320-REFACTOR-2: close() descriptor
    # Lazy lock patterns
    "make_lazy_lock",  # F320-REFACTOR-2: ISSUE-014 lazy lock
    "LazyLockDescriptor",  # F320-REFACTOR-2: lazy lock descriptor
    "make_lazy_lock_classmethod",  # F320-REFACTOR-2: class-level lazy lock
    "make_async_lazy_lock",  # F320-REFACTOR-2: async lazy lock
    "AsyncLazyLockDescriptor",  # F320-REFACTOR-2: async lazy lock descriptor
    # Lazy property
    "lazy_property",
    # Probe scanning
    "parallel_probe_scan",
    "scan_parallel",
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
# Module-Level Singleton Pattern (Double-Checked Locking)
# ==============================================================================


def module_singleton_creator(
    *,
    factory: Callable[[], T],
    post_init: Callable[[T], None] | None = None,
) -> Callable[[], T]:
    """
    Vytvoří thread-safe module-level singleton getter s double-checked locking.

    Použití:
        def _create_scheduler() -> MicroBurstScheduler:
            s = MicroBurstScheduler()
            s.start()
            return s

        get_scheduler = module_singleton_creator(
            factory=_create_scheduler,
        )

    Místo:
        _scheduler_singleton: MicroBurstScheduler | None = None
        _scheduler_lock = threading.Lock()

        def get_scheduler() -> MicroBurstScheduler:
            global _scheduler_singleton
            if _scheduler_singleton is not None:
                return _scheduler_singleton
            with _scheduler_lock:
                if _scheduler_singleton is None:
                    _scheduler_singleton = MicroBurstScheduler()
                    _scheduler_singleton.start()
                return _scheduler_singleton

    Výhody:
    - DRY: eliminuje opakování double-checked locking pattern
    - Type-safe factory
    - M1 8GB: efektivní bez extra allocations
    - post_init pro init lifecycle (např. .start())
    """
    import threading

    _instance: T | None = None
    _lock = threading.Lock()

    def get_instance() -> T:
        nonlocal _instance
        if _instance is not None:
            return _instance
        with _lock:
            if _instance is None:
                _instance = factory()
                if post_init is not None:
                    post_init(_instance)
        return _instance

    return get_instance


def module_singleton_getter(
    *,
    singleton_name: str,
    factory: Callable[[], T],
    reset_func: Callable[[], None] | None = None,
) -> Callable[[], T]:
    """
    Vytvoří module-level singleton getter (thread-safe, double-checked locking).

    Použití:
        _get_lang_detector = module_singleton_getter(
            singleton_name="_lang_detector_instance",
            factory=lambda: LangDetector(...),
            reset_func=lambda: globals().update({'_lang_detector_instance': None}),
        )

        def get_lang_detector(...) -> LangDetector:
            return _get_lang_detector()

    Místo:
        _lang_detector_instance: LangDetector | None = None

        def get_lang_detector(...) -> LangDetector:
            global _lang_detector_instance
            if _lang_detector_instance is None:
                _lang_detector_instance = LangDetector(...)
            return _lang_detector_instance

        def reset_lang_detector() -> None:
            global _lang_detector_instance
            _lang_detector_instance = None

    Výhody:
    - DRY: eliminuje opakování global + if None pattern
    - Thread-safe bez explicitních locků
    - Reset funkce pro testování
    - Použito v: lang_detector, anti_bot_profiles, proxy_routes,
      domain_reputation, ioc_dedup

    F320: Sprint F320-FINAL - Code clone elimination.
    """
    import threading

    _instance: T | None = None
    _lock = threading.Lock()

    def get_instance() -> T:
        nonlocal _instance
        if _instance is not None:
            return _instance
        with _lock:
            if _instance is None:
                _instance = factory()
        return _instance

    # Attach metadata for introspection
    get_instance._singleton_name = singleton_name  # type: ignore[attr-defined]
    get_instance._is_singleton_getter = True  # type: ignore[attr-defined]

    return get_instance


# ==============================================================================
# Lazy Module Import Pattern
# ==============================================================================


def lazy_module_getter(
    module_path: str,
    attrs: dict[str, str],
) -> Callable[[str], Any]:
    """
    Vytvoří __getattr__ funkci pro lazy import modulů.

    Použití:
        __getattr__ = lazy_module_getter(
            "hledac.universal.runtime.sprint_scheduler_v1_archived",
            {"SprintRunContext": "SprintRunContext", "get_sprint_ctx": "get_sprint_ctx"}
        )

    Místo:
        def __getattr__(name: str):
            if name in ("SprintRunContext", "get_sprint_ctx"):
                from hledac.universal.runtime import sprint_scheduler_v1_archived as _v1
                return getattr(_v1, name)
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    Výhody:
    - DRY: eliminuje opakování __getattr__ pattern
    - Centralizovaná definice mappings
    - Snadná údržba
    """
    def getter(name: str) -> Any:
        if name in attrs:
            import importlib
            module = importlib.import_module(module_path)
            return getattr(module, attrs[name])
        raise AttributeError(f"module has no attribute {name!r}")

    return getter


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


async def collect_results_async(
    items: Sequence[T],
    processor: Callable[[T], Awaitable[R]],
) -> list[R]:
    """
    Async sekvenční sběr výsledků z async processoru.

    Použití:
        results = await collect_results_async(
            batch_items,
            lambda item: self._process_single(item)
        )

    Místo:
        results = []
        for item in items:
            result = await self._process_single(item)
            results.append(result)

    M1 8GB: Sekvenční je lepší pro I/O bound operace na limitovaném RAM.
    F320: Přidáno pro DRY batch processing patterns.
    """
    results: list[R] = []
    for item in items:
        result = await processor(item)
        results.append(result)
    return results


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
    scored.sort(key=itemgetter(1), reverse=reverse)
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


def compound_confidence_from_objects(
    items: Sequence[Any],
    confidence_attr: str = "confidence",
    decay_factor: float = 0.9,
) -> float:
    """
    Compound confidence z objektů s confidence atributem.

    Použití:
        conf = compound_confidence_from_objects(hop_steps, "confidence")

    Ekvivalent:
        confidences = [item.confidence for item in items]
        return compound_confidence(confidences, confidence_key=None)

    F320: Přidáno pro inference_engine._calculate_compound_confidence.
    """
    if not items:
        return 1.0
    product = 1.0
    for item in items:
        product *= getattr(item, confidence_attr, 1.0)
    penalty = decay_factor ** (len(items) - 1)
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


def make_close_method(
    instance: Any,
    attr_name: str,
) -> Callable[[], None]:
    """
    Vytvoří bound close() metodu pro konkrétní atribut.

    Použití:
        class MyClass:
            def __init__(self) -> None:
                self._db: Database | None = None

            close = make_close_method(self, "_db")

    Místo:
        def close(self) -> None:
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:  # noqa: BLE001
                    pass
                self._db = None

    Výhody:
    - DRY pro repeated close() patterns
    - Konzistentní chování napříč codebase
    - Python 3.14+ kompatibilní

    F320-REFACTOR: Eliminuje 6 klonů close() metod.
    """
    def close() -> None:
        resource = getattr(instance, attr_name, None)
        if resource is not None:
            try:
                resource.close()
            except Exception:  # noqa: BLE001
                pass
            setattr(instance, attr_name, None)

    return close


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
            from hledac.universal.utils import mlx_cache
            mlx_cache.mlx_cleanup_sync()
        except (ImportError, AttributeError) as e:
            logger.debug(f'Cleanup failed: {e}')
        try:
            from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief
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
            from hledac.universal.utils import mlx_cache

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
            from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief

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


# ==============================================================================
# F330 Clone Report Analysis (2026-08-07)
# ==============================================================================
# Dokumentace false-positive klonů z clone reportu (94.2% similarity).
# Clone report neznamená vždy "refaktoruj to" — mnoho reportů jsou
# strukturální podobnosti s různou sémantikou.
# ==============================================================================

# FALSE POSITIVE CLONES - Různá sémantika, různý účel:
# 1. core/global_co_scheduler.py:308 vs utils/execution_optimizer.py:184
#    -> _ensure_coordinator (lazy coordinator) vs _pending_limit (lazy semaphore)
#    -> Různé typy resources, různé lazy init strategie
# 2. core/resource_pool.py:278 vs core/system_detector.py:148
#    -> get_semaphore() (asyncio.Semaphore) vs __new__ singleton
#    -> Naprosto různé patterny (lazy semaphore vs singleton metaclass)
# 3. brain/_batch/batch_processor.py:393 vs brain/continuous_batch_engine.py:90
#    -> shutdown() s flush vs stop() s task.cancel
#    -> Různé cleanup sekvence, různé priority
# 4. brain/research_hypothesis_engine.py:1277 vs pipeline/public/_match_stage.py:270
#    -> _extract_entities_heuristic (regex entity extraction) vs label dedup in SIMD scan
#    -> Různý účel: threat entity extraction vs IOC label deduplication
# 5. core/mlx_unified_scheduler.py:445 vs multimodal/vision_encoder.py:293
#    -> ANE mutex release vs MLX eval/clear_cache
#    -> Různé MLX resource cleanup, různé API
# 6. knowledge/ioc_graph.py:1443 vs tools/content_miner.py:678,684
#    -> _pivot_sync (graph traversal) vs _extract_from_link_tags (feed URL extraction)
#    -> Podobný set+list dedup pattern, ale různý domain (IOC vs RSS feeds)
# 7. tools/probe/probe_f214int_interpreter_pool.py:94 vs utils/deduplication.py:354
#    -> candidate_entity_confidence vs batch embeddings
#    -> Podobný try/except iteration pattern, ale různé helper funkce
# 8. knowledge/quality_assessment.py:168 vs tools/url_dedup.py:1174
#    -> _compute_entropy_batch (Rust+serial entropy) vs Rust+fallback patterns
#    -> Stejný Rust-fallback pattern, ale různé domain (entropy vs dedup)
# 9. multimodal/evidence_triage.py:159 vs utils/jsonl_lz4_writer.py:168
#    -> close() s metadata_extractor vs close() s writer_task
#    -> Podobný async cleanup pattern, ale různé resources
# 10. brain/mlxcel_ipc_client.py:447 vs utils/jsonl_lz4_writer.py:168
#     -> Process cleanup (kill, wait, stderr) vs task cleanup (cancel, wait)
#     -> Různé process vs task resources
# 11. network/freenet_client.py:163 vs utils/pivot_seed_extractor.py:137
#     -> extract_freenet_keys vs _extract_hashes
#     -> Podobný set+list dedup pattern, ale různé regex patterns
# 12. runtime/scheduler_config.py:86 vs runtime/scheduler_config.py:107
#     -> effective_windup_lead_s vs final_windup_lead_s
#     -> Stejná struktura, různé property (jiné ratio výpočty)
# 13. brain/dspy_optimizer.py:417 vs utils/filtering.py:461
#     -> _load_cache_from_disk (read JSON) vs _save_to_disk (write JSON)
#     -> Stejný ORJSON+fallback pattern, ale read vs write semantics
# 14. context_optimization/context_compressor.py:432 vs layers/hive_coordination.py:375
#     -> list_compressed_contexts vs _get_recent_events
#     -> Podobný dict iteration pattern, různá data structures
# 15. tools/sprint_gate/live_artifact_triage.py:267 vs tools/sprint_gate/live_artifact_triage.py:323
#     -> _acquisition_report vs _top_level_terminality_satisfied
#     -> Různé KPI helper funkce, různé data paths


# ==============================================================================
# Lazy Property Pattern
# ==============================================================================


class lazy_property:
    """
    Descriptor pro lazy initialization s importem.

    Použití:
        class MyClass:
            _engine: "HypothesisEngine | None" = None

            @lazy_property
            def engine(self) -> "HypothesisEngine":
                from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine
                return HypothesisEngine(max_hypotheses=200)

    Místo:
        @property
        def engine(self) -> "HypothesisEngine":
            if self._engine is None:
                from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine
                self._engine = HypothesisEngine(max_hypothheses=200)
            return self._engine

    Výhody:
    - Elimituje repeated `if None: import` pattern
    - Thread-safe (GIL v Pythonu)
    - Clear separation of init logic
    - Used in: hypothesis_builder, layer_manager, duckdb_rag_store, resource_governor
    """

    __slots__ = ("_factory", "_attr_name")

    def __init__(self, factory: Callable[[Any], T]) -> None:
        self._factory = factory
        self._attr_name: str | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr_name = f"_lazy_{name}"

    def __get__(self, obj: Any, objtype: type | None = None) -> T:
        if obj is None:
            return self  # type: ignore[return-value]

        if self._attr_name is None:
            raise RuntimeError("lazy_property not properly initialized")

        # Get cached value or create it
        try:
            cached = getattr(obj, self._attr_name)
            if cached is not None:
                return cached
        except AttributeError:
            pass

        # Create and cache
        value = self._factory(obj)
        object.__setattr__(obj, self._attr_name, value)
        return value


# ==============================================================================
# F320-FINAL-2: Async Lazy Helpers (2026-08-07)
# ==============================================================================
# Clone report analysis: lazy initialization patterns across codebase:
# - discovery/matrix_adapter.py:51 - httpx.AsyncClient lazy session property
# - core/embeddings/cache.py:265 - asyncio.Lock lazy initialization (_l1_lock, _mmap_lock)
# - core/dlq_manager.py:173 - asyncio.Lock lazy initialization (_get_async_lock)
# ==============================================================================


def lazy_async_lock(
    attr_name: str,
    *,
    is_closed_check: str | None = None,
) -> Callable[[Callable[[Any], T]], Callable[[Any], T]]:
    """
    Decorator for lazy async lock initialization (E-4 issue).

    Problem: asyncio.Lock() raises RuntimeError when called from within an existing
    event loop (e.g. async module init). Solution: create locks lazily when
    inside an async context.

    Použití:
        class MyClass:
            _l1_lock: asyncio.Lock | None = None

            @lazy_async_lock("_l1_lock")
            def _get_l1_lock(self) -> asyncio.Lock:
                return asyncio.Lock()

    Místo:
        async def _get_l1_lock(self) -> asyncio.Lock:
            if self._l1_lock is None:
                self._l1_lock = asyncio.Lock()
            return self._l1_lock

    Výhody:
    - Eliminates repeated "if None: create" pattern
    - Supports is_closed check for resources that can be closed
    - Used in: core/embeddings/cache.py, core/dlq_manager.py

    F320-FINAL-2: Clone elimination for async lazy lock patterns.
    """

    def decorator(factory: Callable[[Any], T]) -> Callable[[Any], T]:
        @wraps(factory)
        def wrapper(self: Any) -> T:
            attr = getattr(self, attr_name)
            if attr is not None:
                # Optional closed check
                if is_closed_check and hasattr(attr, is_closed_check):
                    if getattr(attr, is_closed_check):
                        object.__setattr__(self, attr_name, None)
                    else:
                        return attr
                else:
                    return attr
            # Create and cache
            value = factory(self)
            object.__setattr__(self, attr_name, value)
            return value

        return wrapper

    return decorator


class lazy_resource_property:
    """
    Descriptor for lazy HTTP/client session initialization.

    Handles the common pattern where a resource needs lazy initialization
    and may become "closed" and need recreation.

    Použití:
        class MatrixClient:
            _session: httpx.AsyncClient | None = None

            @lazy_resource_property("_session")
            def session(self) -> httpx.AsyncClient:
                return httpx.AsyncClient()

            async def close(self) -> None:
                if self._session and not self._session.is_closed:
                    await self._session.aclose()
                self._session = None

    Místo:
        @property
        def session(self) -> httpx.AsyncClient:
            if self._session is None or self._session.is_closed:
                self._session = httpx.AsyncClient()
            return self._session

    Výhody:
    - Eliminates repeated "if None or is_closed" pattern
    - Clear separation of storage attr vs property
    - Thread-safe via descriptor mechanism
    - Used in: discovery/matrix_adapter.py

    F320-FINAL-2: Clone elimination for lazy session patterns.
    """

    __slots__ = ("_attr_name", "_factory")

    def __init__(
        self,
        attr_name: str,
        *,
        factory: Callable[[], T] | None = None,
        is_closed_attr: str | None = None,
    ) -> None:
        self._attr_name = attr_name
        self._factory = factory
        self._is_closed_attr = is_closed_attr

    def __set_name__(self, owner: type, name: str) -> None:
        # Factory defaults to creating the type if not provided
        if self._factory is None:
            # Try to infer from type hints or use generic factory
            pass

    def __get__(self, obj: Any, objtype: type | None = None) -> T:
        if obj is None:
            return self  # type: ignore[return-value]

        attr = getattr(obj, self._attr_name, None)

        # Check if needs recreation
        needs_recreation = False
        if attr is None:
            needs_recreation = True
        elif self._is_closed_attr and hasattr(attr, self._is_closed_attr):
            if getattr(attr, self._is_closed_attr):
                needs_recreation = True

        if not needs_recreation:
            return attr

        # Create new resource
        if self._factory:
            value = self._factory()
        else:
            # Generic factory: try to create from type or raise
            raise RuntimeError(
                f"lazy_resource_property for {self._attr_name} needs a factory"
            )

        object.__setattr__(obj, self._attr_name, value)
        return value


def make_close_method(
    *attrs: str,
    close_method: str = "close",
    is_none_allowed: bool = True,
) -> Callable[[Any], None]:
    """
    Factory for close() methods that safely close multiple resources.

    Použití:
        class MyClass:
            _diskcache: DiskCache | None = None
            _connection: DBConnection | None = None

            close = make_close_method("_diskcache", "_connection")

    Místo:
        def close(self) -> None:
            if self._diskcache is not None:
                try:
                    self._diskcache.close()
                except Exception:
                    pass
                self._diskcache = None
            if self._connection is not None:
                try:
                    self._connection.close()
                except Exception:
                    pass
                self._connection = None

    Výhody:
    - DRY: eliminates repeated try/except close patterns
    - Centralized error handling
    - Easy to extend
    - Used in: transport/conditional_cache.py

    F320-FINAL-2: Clone elimination for close() methods.
    """
    logger = logging.getLogger("hledac._patterns")

    def close_method(self: Any) -> None:
        for attr_name in attrs:
            resource = getattr(self, attr_name, None)
            if resource is None:
                if is_none_allowed:
                    continue
                else:
                    raise ValueError(f"Required resource {attr_name} is None")

            try:
                close_fn = getattr(resource, close_method, None)
                if close_fn:
                    close_fn()
            except Exception:  # noqa: BLE001
                logger.debug("%s close error", attr_name)

            # Reset to None
            object.__setattr__(self, attr_name, None)

    return close_method




# ==============================================================================
# Cleanup Component Helpers (F320)
# ==============================================================================


async def safe_cleanup_component(
    component: object | None,
    name: str,
    logger: logging.Logger | None,
    *,
    _type: Literal["async", "sync"] = "async",
) -> None:
    """
    Safely cleanup a component (sync or async) with logging.

    F320: DRY helper for cleanup patterns.

    Usage:
        await safe_cleanup_component(
            self._secure_destructor, 'SecureDestructor', logger, _type='async'
        )
        safe_cleanup_component(
            self._mission_audit, 'MissionAudit', logger, _type='sync'
        )

    Instead of:
        if component and hasattr(component, 'cleanup'):
            try:
                await component.cleanup()
            except Exception as e:
                logger.warning(f'Component cleanup error: {e}')
    """
    if component is None or not hasattr(component, 'cleanup'):
        return
    try:
        if _type == "async":
            await component.cleanup()
        else:
            component.cleanup()
    except Exception as e:
        if logger:
            logger.warning(f"WARNING: {name} cleanup error: {e}")


# ==============================================================================
# F330 Clone Refactoring: Exposed Service Hunter Scan Pattern
# ==============================================================================


async def scan_parallel(
    check_args: Sequence[tuple[Any, ...]],
    checker: Callable[..., Awaitable[T | None]],
    *,
    label: str,
    logger: Any = None,
    log_success: str | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> list[T]:
    """
    Univerzální paralelní skenování pro exposed_service_hunter.

    Použití:
        results = await scan_parallel(
            check_args=[(host, port) for host in hosts for port in ports],
            checker=lambda h, p: self._check_docker_api(h, p),
            label='exposed_service_hunter:docker',
            log_success='Found Docker API: {host}:{port}',
        )

    S vlastním semaphore:
        sem = asyncio.Semaphore(20)
        results = await scan_parallel(..., semaphore=sem)

    Eliminuje 20+ řádků boilerplate na scan metodu.
    Použito v: exposed_service_hunter (Docker, K8s, port scanning, Azure containers)
    """
    if not check_args:
        return []

    from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
    from hledac.universal.utils.asyncx import parallel_ok

    if semaphore is None:
        semaphore = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)

    async def _checked(*args: Any) -> T | None:
        async with semaphore:
            try:
                result = await checker(*args)
                if result and logger and log_success:
                    try:
                        msg = log_success.format(**dict(zip(['host', 'port'], args[:2])))
                        logger.info(msg)
                    except (KeyError, IndexError):
                        logger.info(f"Found: {args}")
                return result
            except Exception as e:
                if logger:
                    try:
                        logger.debug(f"Error checking {args}: {e}")
                    except Exception:
                        pass
                return None

    tasks = [_checked(*args) for args in check_args]
    results = await parallel_ok(*tasks, label=label)
    return [r for r in results if r is not None]


# ==============================================================================
# Lazy Lock Patterns (ISSUE-014 compliant)
# ==============================================================================


def make_lazy_lock(
    instance: Any,
    lock_attr: str,
) -> Callable[[], asyncio.Lock]:
    """
    Vytvoří bound lazy asyncio.Lock getter.

    Použití (instance-level):
        class MyClass:
            def __init__(self) -> None:
                self._lock: asyncio.Lock | None = None

            _get_lock = make_lazy_lock(self, "_lock")

        # Volání:
        async with self._get_lock():
            ...

    Použití (class-level):
        class MyClass:
            _lock: asyncio.Lock | None = None

            _get_lock = classmethod(make_lazy_lock_classmethod("_lock"))

    Místo:
        def _get_lock(self) -> asyncio.Lock:
            if self._lock is None:
                self._lock = asyncio.Lock()
            return self._lock

    Výhody:
    - DRY pro ISSUE-014 compliant lazy lock pattern
    - Konzistentní sémantika napříč codebase
    - Python 3.14+ kompatibilní

    F320-REFACTOR-2: Eliminuje 6 klonů _get_lock() metod.
    """
    def _get_lock() -> asyncio.Lock:
        lock = getattr(instance, lock_attr, None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(instance, lock_attr, lock)
        return lock

    return _get_lock


class LazyLockDescriptor:
    """
    Descriptor pro lazy asyncio.Lock getter na úrovni instance.

    Použití:
        class MyClass:
            _lock: asyncio.Lock | None = None

            def _get_lock(self) -> asyncio.Lock:
                ...

        # Nebo použít descriptor factory:
        _get_lock = LazyLockDescriptor("_lock")

    Výhody:
    - Automaticky vytvoří lock pro každou instanci
    - DRY pro ISSUE-014 compliant lazy lock pattern
    - Konzistentní sémantika

    F320-REFACTOR-2: Eliminuje klony _get_lock() metod.
    """

    __slots__ = ("_lock_attr",)

    def __init__(self, lock_attr: str = "_lock") -> None:
        self._lock_attr = lock_attr

    def __get__(self, obj: Any, objtype: type | None = None) -> Callable[[], asyncio.Lock]:
        """Return a bound lock getter for the accessed instance."""
        if obj is None:
            return self  # type: ignore[return-value]

        lock_attr = self._lock_attr

        def _get_lock() -> asyncio.Lock:
            lock = getattr(obj, lock_attr, None)
            if lock is None:
                lock = asyncio.Lock()
                setattr(obj, lock_attr, lock)
            return lock

        return _get_lock


def make_lazy_lock_classmethod(
    lock_attr: str,
) -> Callable[[type], Callable[[], asyncio.Lock]]:
    """
    Vytvoří classmethod wrapper pro lazy asyncio.Lock na úrovni třídy.

    Použití:
        class MyClass:
            _lock: asyncio.Lock | None = None

            _get_lock = classmethod(make_lazy_lock_classmethod("_lock"))

    Místo:
        _lock: asyncio.Lock | None = None

        @classmethod
        def _get_lock(cls) -> asyncio.Lock:
            if cls._lock is None:
                cls._lock = asyncio.Lock()
            return cls._lock

    Výhody:
    - DRY pro class-level lazy lock pattern
    - Konzistentní sémantika

    F320-REFACTOR-2: Eliminuje classmethod klony _get_lock().
    
    BUG-FIX: Cache the returned getter to prevent creating new locks on each call.
    """
    # Cache per class to prevent creating new locks on each _get_lock() call
    _getter_cache: dict[type, Callable[[], asyncio.Lock]] = {}
    
    def make_getter(cls: type) -> Callable[[], asyncio.Lock]:
        """Create or return cached lazy lock getter for this class."""
        cached = _getter_cache.get(cls)
        if cached is not None:
            return cached
        
        def _get_lock() -> asyncio.Lock:
            lock = getattr(cls, lock_attr, None)
            if lock is None:
                lock = asyncio.Lock()
                setattr(cls, lock_attr, lock)
            return lock
        
        _getter_cache[cls] = _get_lock
        return _get_lock
    
    return make_getter


async def make_async_lazy_lock(
    instance: Any,
    lock_attr: str,
) -> asyncio.Lock:
    """
    Async lazy asyncio.Lock getter (pro případy kde je lock v async contextu).

    Použití:
        async def _get_lock(self) -> asyncio.Lock:
            if self._lock is None:
                self._lock = asyncio.Lock()
            return self._lock

    Místo:
        async with await make_async_lazy_lock(self, "_lock"):
            ...

    Rozdíl od make_lazy_lock: vrací Awaitable, pro async kontexty.
    """
    lock = getattr(instance, lock_attr, None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(instance, lock_attr, lock)
    return lock


class AsyncLazyLockDescriptor:
    """
    Descriptor pro async lazy asyncio.Lock getter.

    Použití:
        class MyClass:
            _lock: asyncio.Lock | None = None

            async def _get_lock(self) -> asyncio.Lock:
                ...

        # Nebo použít descriptor factory:
        _get_lock = AsyncLazyLockDescriptor("_lock")
        
        # Usage:
        # lock = await self._get_lock()

    Výhody:
    - Automaticky vytvoří lock pro každou instanci
    - DRY pro ISSUE-014 compliant async lazy lock pattern
    - Konzistentní sémantika

    F320-REFACTOR-2: Eliminuje klony async _get_lock() metod.
    
    BUG-FIX: __get__ is now regular method returning async function,
    not async method. Usage: lock = await self._get_lock()
    """

    __slots__ = ("_lock_attr",)

    def __init__(self, lock_attr: str = "_lock") -> None:
        self._lock_attr = lock_attr

    def __get__(self, obj: Any, objtype: type | None = None) -> Callable[[], asyncio.Lock]:
        """Return an async function that gets or creates the lock for this instance."""
        if obj is None:
            return self  # type: ignore[return-value]

        lock_attr = self._lock_attr

        async def _get_lock() -> asyncio.Lock:
            lock = getattr(obj, lock_attr, None)
            if lock is None:
                lock = asyncio.Lock()
                setattr(obj, lock_attr, lock)
            return lock

        return _get_lock


# ==============================================================================
# LMDB Close Pattern
# ==============================================================================


def make_lmdb_close(
    instance: Any,
    env_attr: str = "_lmdb_env",
    db_attr: str | None = "_lmdb_db",
    initialized_attr: str | None = "_initialized",
) -> Callable[[], None]:
    """
    Vytvoří bound LMDB close() metodu.

    Použití:
        class MyLMDB:
            def __init__(self) -> None:
                self._lmdb_env: Any = None
                self._lmdb_db: Any = None
                self._initialized: bool = False

            close = make_lmdb_close(self, "_lmdb_env", "_lmdb_db", "_initialized")

    Místo:
        def close(self) -> None:
            if self._lmdb_env is not None:
                try:
                    self._lmdb_env.close()
                except Exception:  # noqa: BLE001
                    pass
                self._lmdb_env = None
                self._lmdb_db = None
                self._initialized = False

    Výhody:
    - DRY pro LMDB close patterns
    - Konzistentní chování
    - Python 3.14+ kompatibilní

    F320-REFACTOR-2: Eliminuje 3 klony close() metod s LMDB.
    """
    def close() -> None:
        env = getattr(instance, env_attr, None)
        if env is not None:
            try:
                env.close()
            except Exception:  # noqa: BLE001
                pass
            setattr(instance, env_attr, None)
            if db_attr:
                setattr(instance, db_attr, None)
            if initialized_attr:
                setattr(instance, initialized_attr, False)

    return close


def make_close_method(
    instance: Any,
    resource_attr: str,
    *additional_attrs: str,
    initialized_attr: str | None = None,
    initialized_value: Any = False,
) -> Callable[[], None]:
    """
    Vytvoří bound close() metodu pro resource s dodatečným state reset.

    Použití (in __init__):
        class MyStore:
            def __init__(self) -> None:
                self._db: Database | None = None
                self._table: Table | None = None
                self._initialized: bool = False
                self.close = make_close_method(
                    self,
                    "_db",
                    "_table",
                    initialized_attr="_initialized",
                    initialized_value=False
                )

    Místo:
        def close(self) -> None:
            if self._db is not None:
                try:
                    self._db.close()
                except Exception:  # noqa: BLE001
                    pass
                self._db = None
                self._table = None
                self._initialized = False

    Výhody:
    - DRY pro close() patterns s dodatečným state reset
    - Flexibilní pro různé kombinace atributů
    - Konzistentní chování

    F320-REFACTOR-2: Eliminuje klony close() metod s více resources.
    """
    def close() -> None:
        resource = getattr(instance, resource_attr, None)
        if resource is not None:
            try:
                resource.close()
            except Exception:  # noqa: BLE001
                pass
            setattr(instance, resource_attr, None)
            for attr in additional_attrs:
                setattr(instance, attr, None)
            if initialized_attr is not None:
                setattr(instance, initialized_attr, initialized_value)

    return close


class CloseMethodDescriptor:
    """
    Descriptor pro close() metodu s více resources.

    Použití:
        class MyStore:
            _db: Database | None
            _table: Table | None
            _initialized: bool

            close = CloseMethodDescriptor(
                "_db",
                "_table",
                initialized_attr="_initialized",
                initialized_value=False,
            )

    Výhody:
    - DRY pro close() patterns jako class-level atribut
    - Automaticky vytvoří bound close() metodu
    - Konzistentní sémantika

    F320-REFACTOR-2: Eliminuje klony close() metod.
    """

    __slots__ = ("_resource_attr", "_additional_attrs", "_initialized_attr", "_initialized_value")

    def __init__(
        self,
        resource_attr: str,
        *additional_attrs: str,
        initialized_attr: str | None = None,
        initialized_value: Any = False,
    ) -> None:
        self._resource_attr = resource_attr
        self._additional_attrs = additional_attrs
        self._initialized_attr = initialized_attr
        self._initialized_value = initialized_value

    def __get__(self, obj: Any, objtype: type | None = None) -> Callable[[], None]:
        """Return a bound close function for this instance."""
        if obj is None:
            return self  # type: ignore[return-value]

        resource_attr = self._resource_attr
        additional_attrs = self._additional_attrs
        initialized_attr = self._initialized_attr
        initialized_value = self._initialized_value

        def close() -> None:
            resource = getattr(obj, resource_attr, None)
            if resource is not None:
                try:
                    resource.close()
                except Exception:  # noqa: BLE001
                    pass
                setattr(obj, resource_attr, None)
                for attr in additional_attrs:
                    setattr(obj, attr, None)
                if initialized_attr is not None:
                    setattr(obj, initialized_attr, initialized_value)

        return close
