"""P2-3: Pipeline Stage Protocol — AsyncIterator-based Stage Chain with TaskGroup Boundaries.

Role: Definuje Stage protokol a StageContext pro řetězec AsyncIterator[Item] s AIMD
a bounded queues mezi fázemi.






Architecture:
    DiscoveryStage → DedupStage → FetchStage → MatchStage → EnrichStage → StoreStage
    (každá fáze: AsyncIterator vstup → AsyncIterator výstup, AIMD controller, bounded queue)

Invarianty:
- Always-on: žádné feature flagy pro nové fáze
- Bounded: každá stage queue má explicitní maxsize
- Fail-safe: stage vrací prázdný iterator při chybě, neexception
- TaskGroup cancellation: Ctrl-C → všechny stagesGraceful shutdown
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar
from collections.abc import Awaitable, Callable

import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct
from _core import aclose

if TYPE_CHECKING:
    from typing import Protocol
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

T_in = TypeVar("T_in")
T_out = TypeVar("T_out")


# ----------------------------------------------------------------------
# Stage Metrics
# ----------------------------------------------------------------------


class StageMetrics(Struct):
    """Per-stage metrics for observability.

    Sbírá: počet zpracovaných itemů, dropnuté itemy, chyby,
    AIMD window, fronta velikost, latency.
    """

    stage_name: str
    processed: int = 0
    dropped: int = 0
    errors: int = 0
    queue_size: int = 0
    aimd_window: float = 0.0
    latency_ms: float = 0.0
    started_at: float = 0.0

    def record_processed(self, n: int = 1) -> None:
        self.processed += n

    def record_dropped(self, n: int = 1) -> None:
        self.dropped += n

    def record_error(self, n: int = 1) -> None:
        self.errors += n

    def update_queue_size(self, size: int) -> None:
        self.queue_size = size

    def update_aimd_window(self, window: float) -> None:
        self.aimd_window = window

    def update_latency(self, latency_ms: float) -> None:
        self.latency_ms = latency_ms

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage_name,
            "processed": self.processed,
            "dropped": self.dropped,
            "errors": self.errors,
            "queue_size": self.queue_size,
            "aimd_window": self.aimd_window,
            "latency_ms": round(self.latency_ms, 2),
            "elapsed_s": round(self.elapsed_s, 2),
        }


# ----------------------------------------------------------------------
# Stage Context
# ----------------------------------------------------------------------


class StageContext(Struct):
    """Sdílený kontext mezi všemi stages.

    Předává se při vytvoření pipeline. Obsahuje všechny externí
    závislosti (store, engine, graph, atd.) bez potřeby closure.
    """

    query: str
    store: Any | None = None
    graph: Any | None = None
    hermes_engine: Any | None = None
    memory_manager: Any | None = None
    vector_store: Any | None = None
    session_id: str | None = None
    sprint_id: str = ""
    uma_state: str = "ok"
    fetch_concurrency: int = 8
    fetch_timeout_s: float = 35.0
    fetch_max_bytes: int = 2_000_000
    max_results: int = 10
    metrics: dict[str, StageMetrics] = msgspec.field(default_factory=dict)

    def get_metrics(self, stage_name: str) -> StageMetrics:
        if stage_name not in self.metrics:
            self.metrics[stage_name] = StageMetrics(stage_name=stage_name)
        return self.metrics[stage_name]

    @property
    def is_critical_uma(self) -> bool:
        return self.uma_state in ("critical", "emergency")

    @property
    def is_emergency_uma(self) -> bool:
        return self.uma_state == "emergency"


# ----------------------------------------------------------------------
# Bounded Stage Queue
# ----------------------------------------------------------------------


@dataclass(slots=True)
class BoundedStageQueue(Generic[T_out]):
    """asyncio.Queue s bounded maxsize a drop metrikou.

    Rozdíl od plain asyncio.Queue:
    - put_nowait() při full queue vrací False (drop, ne block)
    - Drop counter pro telemetry
    - Stage name pro loggování
    - UMA-aware dynamická adaptace maxsize (P1-8)

    Fail-safe: nikdy neblokuje producenta, nikdy nehazuje exception.

    UMA adaptace (P1-8):
        ok:          base maxsize (např. 512 pro enrich_out)
        soft_warn:   75% base
        warn:        50% base
        critical:    25% base
        emergency:   12.5% base (min 1)

    při shrink: drain oldest, keep newest (drop-oldest na head)
    """

    maxsize: int
    stage_name: str
    _base_maxsize: int = field(default=0, repr=False)
    _uma_state: str = field(default="ok", repr=False)
    # D6 FIX: Default maxsize=512 instead of unbounded (maxsize=0).
    # __post_init__ will replace with self.maxsize anyway, but this avoids
    # creating a temporary unbounded queue object.
    _queue: asyncio.Queue[T_out] = field(default_factory=lambda: asyncio.Queue(maxsize=512))
    _dropped: int = field(default=0, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # UMA state → multiplier pro maxsize (P1-8)
    _MAXSIZE_TABLE: dict[str, float] = field(
        default_factory=lambda: {
            "ok": 1.0,
            "soft_warn": 0.75,
            "warn": 0.5,
            "critical": 0.25,
            "emergency": 0.125,
        },
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_base_maxsize", self.maxsize)
        object.__setattr__(self, "_queue", asyncio.Queue(maxsize=self.maxsize))

    def set_uma_state(self, state: str) -> None:
        """Adapt queue size to UMA pressure (P1-8).

        Voláno z hlavního TaskGroup runneru při změně UMA stavu.
        Synchroní — lock je držen jen během drain+recreate operace,
        což je velmi krátké (<1ms). Concurrent put() operace nejsou
        touto metodou ovlivněny (používají vlastní lock přes put_nowait).

        Args:
            state: "ok" | "soft_warn" | "warn" | "critical" | "emergency"

        """
        if state == self._uma_state:
            return

        multiplier = self._MAXSIZE_TABLE.get(state, 1.0)
        new_max = max(1, int(self._base_maxsize * multiplier))

        if new_max == self._queue.maxsize:
            object.__setattr__(self, "_uma_state", state)
            return

        # DRAIN: vyprázdni frontu, keep newest new_max items (drop oldest)
        items: list[T_out] = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        # Keep newest (tail = most recent), drop oldest (head = earliest)
        keep = items[-new_max:] if len(items) > new_max else items

        # RECREATE queue s novým maxsize
        new_queue = asyncio.Queue[T_out](maxsize=new_max)
        for item in keep:
            try:
                new_queue.put_nowait(item)
            except asyncio.QueueFull:
                break

        object.__setattr__(self, "_queue", new_queue)
        object.__setattr__(self, "_uma_state", state)

        dropped_by_shrink = len(items) - len(keep)
        if dropped_by_shrink > 0:
            logger.info(
                "BoundedStageQueue[%s]: UMA=%s, maxsize %d→%d, drained %d oldest",
                self.stage_name,
                state,
                new_max,
                new_max,
                dropped_by_shrink,
    )
        else:
            logger.debug(
                "BoundedStageQueue[%s]: UMA=%s, maxsize grew to %d",
                self.stage_name,
                state,
                new_max,
    )

    async def put(self, item: T_out) -> bool:
        """Vloží item do fronty.

        Serializuje concurrent put() volání přes asyncio.Lock — více korutin
        volajících put() současně by mohlo způsobit race condition v put_nowait,
        protože asyncio.Queue negeneruje yield-point během put_nowait a tudíž
        by se korutiny mohly při čekání na GIL přepínat uprostřed operace.

        Returns:
            True pokud item byl vložen (wasn't dropped).
            False pokud queue full (item dropped).

        """
        async with self._lock:
            try:
                self._queue.put_nowait(item)
                return True
            except asyncio.QueueFull:
                self._dropped += 1
                logger.debug(
                    "BoundedStageQueue[%s]: dropped item (queue full, size=%d)",
                    self.stage_name,
                    self.maxsize,
    )
                return False

    async def get(self) -> T_out:
        """Blokuje dokud není item dostupný."""
        return await self._queue.get()

    def qsize(self) -> int:
        return self._queue.qsize()

    @property
    def dropped(self) -> int:
        return self._dropped

    def reset_dropped(self) -> int:
        """Reset dropped counter, vrací předchozí hodnotu."""
        return self._dropped  # non-async, no lock needed

    def is_full(self) -> bool:
        return self._queue.full()

    def is_empty(self) -> bool:
        return self._queue.empty()


# ----------------------------------------------------------------------
# Stage Protocol
# ----------------------------------------------------------------------


class Stage(Generic[T_in, T_out], Protocol):
    """Protokol pro jednu pipeline fázi.

    Každá stage:
    1. Je AsyncIterator — produkuje output pro další stage
    2. Má vlastní AIMD controller
    3. Má bounded input queue
    4. Je fail-safe (vrací prázdný iterator při chybě)

    Implementace musí být reentrant-safe (může být cancelled a resumption).
    """

    name: str

    async def run(
        self,
        input_queue: BoundedStageQueue[T_in] | None,
        output_queue: BoundedStageQueue[T_out],
        ctx: StageContext,
    ) -> None:
        """Hlavní run method — zpracovává input a produkuje output.

        Args:
            input_queue: BoundedStageQueue pro vstup (None pro první stage)
            output_queue: BoundedStageQueue pro výstup
            ctx: StageContext sdílený přes celý pipeline

        Returns:
            None (output jde přes output_queue)

        """
        ...

    async def aclose(self) -> None:
        """Graceful shutdown — zavře resources, flushne buffer."""
        ...


# ----------------------------------------------------------------------
# StageResult
# ----------------------------------------------------------------------


class StageResult(Struct):
    """Výsledek běhu jedné stage pro telemetry."""

    stage_name: str
    processed: int = 0
    dropped: int = 0
    errors: int = 0
    latency_ms: float = 0.0
    aimd_window: float = 0.0
