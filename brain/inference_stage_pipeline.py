"""
BoundedInferencePipeline — 3-stage async inference pipeline with backpressure.

Issue #17: The Hermes engine has _prep_executor, _inference_executor, and


_post_executor as separate thread pools, but they are called sequentially
per request with no overlap.  While request N is in GPU inference (1–30 s),
the CPU sits idle — the next prompt's prep could already be running.

Solution: A proper 3-stage bounded pipeline connected by asyncio.Queue
channels.  Each stage is a persistent set of async workers pulling from
an upstream channel and pushing to the next stage.  Bounded channel sizes
provide natural backpressure — when a downstream stage is slow, the
upstream worker blocks on put(), avoiding unbounded queue growth.

Architecture
------------

  [Input Queue: max 16]
      │
      ▼
  ┌─────────────────────────────┐
  │  Prep Stage  (3 workers)    │   _prep_executor (ThreadPoolExecutor)
  │  • sanitize + ChatML format  │
  │  • tokenize for prefix cache │
  └──────────────┬──────────────┘
                 │  formatted prompt + tokens
                 ▼
  ┌─────────────────────────────┐
  │  Inf Channel (max 4)        │   Bounded — 4 waiting = ~32 KB
  └──────────────┬──────────────┘
                 ▼
  ┌─────────────────────────────┐
  │  Inference Stage (1 worker) │   MLXWorkerThread (serial Metal)
  │  • mlx_lm.generate()        │
  └──────────────┬──────────────┘
                 │  raw text
                 ▼
  ┌─────────────────────────────┐
  │  Post Channel (max 3)       │   Bounded — 3 waiting = ~6 KB
  └──────────────┬──────────────┘
                 ▼
  ┌─────────────────────────────┐
  │  Post Stage  (2 workers)    │   _post_executor (ThreadPoolExecutor)
  │  • parse / validate          │
  │  • resolve Future            │
  └──────────────┬──────────────┘
                 ▼
              Result

Overlap
-------
Time →
Req 0:  [Prep 0] [===== Inference 0 =====] [Post 0]
Req 1:           [Prep 1] [===== Inference 1 =====] [Post 1]
Req 2:                     [Prep 2] [===== Inference 2 =====] [Post 2]

Without pipeline: total = sum of all stages
With pipeline:    total ≈ max(stage_sums) + prep_overhead

Backpressure Flow
-----------------
  submit() → input_queue.put()  ← blocks when ≥ 16 pending
  prep worker → prep_to_inf.put() ← blocks when ≥ 4 waiting for GPU
  inf worker → inf_to_post.put() ← blocks when ≥ 3 waiting for parsing

M1 8GB Bounds
-------------
  max_pending    = 16   → ~128 KB (prompts ≤ 8 KB each)
  prep_to_inf    =  4   →  ~32 KB (formatted prompts)
  inf_to_post    =  3   →   ~6 KB (raw text ~ 2 KB each)
  Total queue memory      < 200 KB
  Worker threads: 3 prep + 1 inf + 2 post = 6 (matches existing pool sizes)

Python 3.14+ Best Practices
----------------------------
  • Persistent async workers via asyncio.create_task()
  • Bounded asyncio.Queue for backpressure
  • Proper CancelledError propagation
  • no bare except: — always except Exception or specific
  • msgspec.Struct for zero-copy pipeline items (PEP 698 slots)

Always-on, fail-soft, no feature flag.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.asyncx import _check_gathered

if TYPE_CHECKING:
    from .deephermes3_engine import DeepHermes3Engine

logger = logging.getLogger(__name__)

# ── M1 8GB channel bounds ──────────────────────────────────────────────
_PREP_TO_INF_MAX: int = 4        # formatted prompts waiting for GPU
_INF_TO_POST_MAX: int = 3        # raw results waiting for parse
_INPUT_QUEUE_MAX: int = 16       # incoming requests
_PREP_WORKERS: int = 3           # matches _HERMES_PREP_WORKERS
_POST_WORKERS: int = 2           # matches _HERMES_POST_WORKERS
_INF_TIMEOUT_S: float = 120.0    # per-request inference timeout
_PIPELINE_SHUTDOWN_S: float = 5.0
MAX_PROMPT_CHARS: int = 8192


# ── Pipeline Item ──────────────────────────────────────────────────────

@dataclass(slots=True)
class PipelineItem:
    """A single request traversing the 3-stage pipeline.

    Fields mutate as the item flows through stages:
      Stage 1 (prep) writes:  formatted_prompt, prompt_tokens
      Stage 2 (inf)  writes:  raw_text
      Stage 3 (post) reads:   raw_text → resolves future
    """
    request_id: str
    future: asyncio.Future[str]

    # ── Stage 1 inputs ──
    prompt: str
    system_msg: str | None
    thinking: bool
    temperature: float
    max_tokens: int

    # ── Stage 1 output → Stage 2 input ──
    formatted_prompt: str | None = None
    prompt_tokens: list[int] | None = None
    _system_used: str | None = None  # enhanced system msg (with thinking prefix)

    # ── Stage 2 output → Stage 3 input ──
    raw_text: str | None = None

    # ── Telemetry ──
    enqueued_at: float = field(default_factory=time.monotonic)
    prepped_at: float = 0.0
    inferred_at: float = 0.0
    done_at: float = 0.0


# ── Bounded 3-Stage Pipeline ───────────────────────────────────────────

class BoundedInferencePipeline:
    """Bounded 3-stage async inference pipeline with explicit backpressure.

    Public API
    ----------
      async submit(prompt, ...) → str
          Submit a request; blocks until the input queue has space
          (backpressure), then returns the generated text.

      async submit_nonblocking(prompt, ...) → asyncio.Future[str]
          Submit without waiting for the result.

      get_stats() → dict
          Telemetry snapshot.

      async shutdown() → None
          Bounded shutdown — cancels workers, fails pending futures.

    Invariants
    ----------
      P.I1  Bounded channels — every asyncio.Queue has explicit maxsize.
      P.I2  Persistent workers — async tasks run for pipeline lifetime.
      P.I3  Fail-soft — errors resolve the Future, never crash the pipeline.
      P.I4  Serial inference — single inference worker, serial MLX.
      P.I5  M1 8GB safe — total queue memory < 200 KB.
      P.I6  Lazy start — workers created on first submit, not at __init__.

    Lifecycle
    ---------
      Created lazily by DeepHermes3Engine._ensure_inference_pipeline().
      Workers start on first submit().  Shutdown via engine.close().
    """

    __slots__ = (
        '_closed',
        '_engine',
        '_inf_task',
        '_inf_to_post',
        '_input_queue',
        '_post_tasks',
        '_prep_tasks',
        '_prep_to_inf',
        '_started',
        '_stats',
    )

    def __init__(self, engine: DeepHermes3Engine) -> None:
        self._engine: DeepHermes3Engine = engine

        # ── Bounded channels (backpressure) ──
        self._input_queue: asyncio.Queue[PipelineItem] = asyncio.Queue(
            maxsize=_INPUT_QUEUE_MAX
        )
        self._prep_to_inf: asyncio.Queue[PipelineItem] = asyncio.Queue(
            maxsize=_PREP_TO_INF_MAX
        )
        self._inf_to_post: asyncio.Queue[PipelineItem] = asyncio.Queue(
            maxsize=_INF_TO_POST_MAX
        )

        # ── Worker tasks (lazy, created on first submit) ──
        self._prep_tasks: list[asyncio.Task[None]] = []
        self._inf_task: asyncio.Task[None] | None = None
        self._post_tasks: list[asyncio.Task[None]] = []

        # ── State ──
        self._started: bool = False
        self._closed: bool = False

        # ── Telemetry ──
        self._stats: dict[str, int | float] = {
            'submitted': 0,
            'completed': 0,
            'failed': 0,
            'input_queue_depth': 0,
            'prep_to_inf_depth': 0,
            'inf_to_post_depth': 0,
            'prep_avg_ms': 0.0,
            'inf_avg_ms': 0.0,
            'post_avg_ms': 0.0,
            'total_avg_ms': 0.0,
        }

    # ── Public API ─────────────────────────────────────────────────

    async def submit(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        *,
        thinking: bool = True,
    ) -> str:
        """Submit a request and block until the result is ready.

        Backpressure: blocks on input_queue.put() when ≥ 16 pending.
        """
        future = await self.submit_nonblocking(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system_msg=system_msg,
            thinking=thinking,
        )
        return await future

    async def submit_nonblocking(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        *,
        thinking: bool = True,
    ) -> asyncio.Future[str]:
        """Submit a request; returns Future immediately.

        Non-blocking — the caller can do other work while the pipeline
        processes the request.  The returned Future resolves when the
        request completes all 3 stages.
        """
        if self._closed:
            raise RuntimeError('BoundedInferencePipeline is closed')

        if not self._started:
            await self._start_workers()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        item = PipelineItem(
            request_id=uuid.uuid4().hex[:12],
            future=future,
            prompt=prompt[:MAX_PROMPT_CHARS],
            system_msg=system_msg,
            thinking=thinking,
            temperature=temperature if temperature is not None else 0.1,
            max_tokens=max_tokens if max_tokens is not None else 1024,
        )

        # Backpressure: blocks when input queue is full
        await self._input_queue.put(item)
        self._stats['submitted'] = int(self._stats['submitted']) + 1
        self._stats['input_queue_depth'] = self._input_queue.qsize()

        return future

    def get_stats(self) -> dict[str, int | float]:
        """Return telemetry snapshot (non-intrusive read)."""
        s: dict[str, int | float] = dict(self._stats)
        s['input_queue_depth'] = self._input_queue.qsize()
        s['prep_to_inf_depth'] = self._prep_to_inf.qsize()
        s['inf_to_post_depth'] = self._inf_to_post.qsize()
        s['started'] = self._started
        s['closed'] = self._closed
        s['prep_workers'] = len(self._prep_tasks)
        s['post_workers'] = len(self._post_tasks)
        return s

    async def shutdown(self) -> None:
        """Bounded shutdown — cancel workers, drain/fail pending futures.

        Idempotent.  Uses asyncio.timeout to bound shutdown ≤ 5.0 s.
        """
        if self._closed:
            return
        self._closed = True

        # Fail all pending futures
        for queue in (self._input_queue, self._prep_to_inf, self._inf_to_post):
            while not queue.empty():
                try:
                    item: PipelineItem = queue.get_nowait()
                    if not item.future.done():
                        item.future.set_exception(
                            RuntimeError('pipeline shutdown')
                        )
                except asyncio.QueueEmpty:
                    break

        # Cancel worker tasks
        all_tasks: list[asyncio.Task[None]] = []
        all_tasks.extend(self._prep_tasks)
        if self._inf_task is not None:
            all_tasks.append(self._inf_task)
        all_tasks.extend(self._post_tasks)

        for t in all_tasks:
            if not t.done():
                t.cancel()

        if all_tasks:
            try:
                async with asyncio.timeout(_PIPELINE_SHUTDOWN_S):
                    gathered = await asyncio.gather(*all_tasks, return_exceptions=True)
                    _, errors = _check_gathered(gathered)
                    for err in errors:
                        logger.debug('[Pipeline] shutdown: task failed: %s', err)
            except TimeoutError:
                logger.debug(
                    '[Pipeline] shutdown timed out after %.1fs',
                    _PIPELINE_SHUTDOWN_S,
                )
            except Exception:  # noqa: BLE001
                pass

        self._prep_tasks.clear()
        self._inf_task = None
        self._post_tasks.clear()
        logger.debug('[Pipeline] shutdown complete')

    # ── Worker Lifecycle ───────────────────────────────────────────

    async def _start_workers(self) -> None:
        """Start all stage workers (idempotent, P.I6 lazy start).

        Uses asyncio.create_task() directly — NOT asyncio.TaskGroup —
        because workers are long-lived (run until pipeline shutdown).
        TaskGroup.__aexit__ would block waiting for workers to finish.
        """
        if self._started:
            return

        for i in range(_PREP_WORKERS):
            t = asyncio.create_task(
                self._prep_worker(i), name=f'pipeline:prep-{i}'
            )
            self._prep_tasks.append(t)

        self._inf_task = asyncio.create_task(
            self._inference_worker(), name='pipeline:inf'
        )

        for i in range(_POST_WORKERS):
            t = asyncio.create_task(
                self._post_worker(i), name=f'pipeline:post-{i}'
            )
            self._post_tasks.append(t)

        self._started = True
        logger.debug(
            '[Pipeline] workers started: %d prep, 1 inf, %d post',
            _PREP_WORKERS,
            _POST_WORKERS,
        )

    # ── Stage 1: Prep Workers ──────────────────────────────────────

    async def _prep_worker(self, worker_id: int) -> None:
        """Prep stage worker: sanitize → ChatML format → tokenize.

        Pulls from input_queue, runs CPU work in _prep_executor,
        pushes to prep_to_inf channel.  Blocks on downstream backpressure.
        """
        loop = asyncio.get_running_loop()
        while not self._closed:
            try:
                # Backpressure: blocks when input queue is empty
                item = await self._input_queue.get()
            except asyncio.CancelledError:
                break
            except RuntimeError:
                # Queue closed
                break

            try:
                t0 = time.monotonic()

                # Resolve system message
                system = item.system_msg or 'You are a helpful research assistant.'
                if item.thinking:
                    system = (
                        'You are a deep thinking AI, you may use extremely '
                        'long chains of thought to deeply consider the problem '
                        'and deliberate with yourself via systematic reasoning '
                        'processes to help come to a correct solution prior to '
                        'answering. You should enclose your thoughts and '
                        'internal monologue inside <think> </think> tags, and '
                        'then provide your solution or response to the problem.'
                        '\n\n' + system
                    )

                # Run prep in dedicated thread pool
                formatted, _, _, prefix_tokens = await loop.run_in_executor(
                    self._engine._prep_executor,
                    self._engine._prep_generate,
                    item.prompt,
                    system,
                    self._engine._sanitize_for_llm,
                    self._engine._tokenizer,
                    self._engine._prefix_cache,
                    self._engine._prefix_cache_maxsize,
                    self._engine._prefix_cache_stats,
                    MAX_PROMPT_CHARS,
                )

                item.formatted_prompt = formatted
                item.prompt_tokens = prefix_tokens
                item._system_used = system
                item.prepped_at = time.monotonic()

                prep_ms = (item.prepped_at - t0) * 1000
                self._update_ema('prep_avg_ms', prep_ms)

                # Backpressure: blocks when inference queue is full
                await self._prep_to_inf.put(item)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug('[Pipeline] prep-%d error: %s', worker_id, e)
                if not item.future.done():
                    item.future.set_exception(e)
                self._stats['failed'] = int(self._stats['failed']) + 1
            finally:
                self._input_queue.task_done()

    # ── Stage 2: Inference Worker ──────────────────────────────────

    async def _inference_worker(self) -> None:
        """Inference stage worker: single, serial MLX generate.

        Pulls from prep_to_inf, runs MLX inference via MLXWorkerThread,
        pushes to inf_to_post channel.  The single-worker design (P.I4)
        matches the single Metal command queue.
        """
        while not self._closed:
            try:
                item = await self._prep_to_inf.get()
            except asyncio.CancelledError:
                break
            except RuntimeError:
                break

            try:
                t0 = time.monotonic()

                # Tokenize the formatted prompt for LLM-02/03 guard
                if item.formatted_prompt is None:
                    if not item.future.done():
                        item.future.set_exception(
                            RuntimeError('formatted_prompt is None')
                        )
                    self._prep_to_inf.task_done()
                    self._stats['failed'] = int(self._stats['failed']) + 1
                    continue

                try:
                    gen_tokens = self._engine._tokenizer.encode(
                        item.formatted_prompt
                    )
                except Exception:
                    gen_tokens = None

                # LLM-02: token overflow truncation
                formatted = item.formatted_prompt
                if gen_tokens is not None:
                    _max_total = (
                        self._engine.config.context_window
                        - item.max_tokens
                        - 50
                    )
                    if len(gen_tokens) > _max_total:
                        _max_prompt_tokens = max(1, _max_total)
                        _max_chars = int(_max_prompt_tokens * 3.5)
                        truncated = item.prompt[:_max_chars]
                        truncated = truncated[:MAX_PROMPT_CHARS]
                        formatted = self._engine._format_chatml(
                            system_msg=(
                                item._system_used
                                or 'You are a helpful research assistant.'
                            ),
                            user_msg=truncated,
                        )
                        try:
                            gen_tokens = self._engine._tokenizer.encode(
                                formatted
                            )
                        except Exception:
                            gen_tokens = None
                        logger.warning(
                            '[Pipeline] TOKEN-OVERFLOW truncated → %d tokens',
                            len(gen_tokens) if gen_tokens else 0,
                        )

                # Resolve KV cache prefix
                prefix_cache = self._engine._resolve_kv_cache(
                    item.system_msg, formatted
                )

                # Submit inference via routing layer (handles MLXWorkerThread
                # / main-thread / ThreadPoolExecutor fallback automatically)
                timeout = _INF_TIMEOUT_S
                result = await self._engine._submit_inference(
                    timeout,
                    self._engine._run_inference,
                    formatted,
                    item.temperature,
                    item.max_tokens,
                    prefix_cache,
                    None,   # adapter_path
                    gen_tokens,
                    None,   # logits_processors
                )

                item.raw_text = result[0]  # (response, kv_cache_after)
                item.inferred_at = time.monotonic()

                inf_ms = (item.inferred_at - t0) * 1000
                self._update_ema('inf_avg_ms', inf_ms)

                # Post-inference cleanup (throttled Metal clear)
                self._engine._mlx_clear_and_timestamp()

                # Backpressure: blocks when post queue is full
                await self._inf_to_post.put(item)

            except asyncio.CancelledError:
                break
            except TimeoutError:
                logger.warning(
                    '[Pipeline] inference timeout for %s', item.request_id
                )
                if not item.future.done():
                    item.future.set_exception(
                        TimeoutError(f'inference timeout after {_INF_TIMEOUT_S}s')
                    )
                self._stats['failed'] = int(self._stats['failed']) + 1
            except Exception as e:
                logger.debug('[Pipeline] inference error: %s', e)
                if not item.future.done():
                    item.future.set_exception(e)
                self._stats['failed'] = int(self._stats['failed']) + 1
            finally:
                self._prep_to_inf.task_done()

    # ── Stage 3: Post Workers ──────────────────────────────────────

    async def _post_worker(self, worker_id: int) -> None:
        """Post stage worker: parse → validate → resolve Future.

        Pulls from inf_to_post, resolves text output directly
        to the Future.  Structured output parsing is handled by the
        structured generate path which doesn't route through this pipeline.
        """
        while not self._closed:
            try:
                item = await self._inf_to_post.get()
            except asyncio.CancelledError:
                break
            except RuntimeError:
                break

            try:
                t0 = time.monotonic()

                if item.raw_text is None:
                    if not item.future.done():
                        item.future.set_exception(
                            RuntimeError('raw_text is None')
                        )
                    self._stats['failed'] = int(self._stats['failed']) + 1
                    self._inf_to_post.task_done()
                    continue

                # For text output, resolve directly
                # (structured output parsing is handled by the structured
                #  generate path which doesn't go through this text pipeline)
                if not item.future.done():
                    item.future.set_result(item.raw_text)

                item.done_at = time.monotonic()
                total_ms = (item.done_at - item.enqueued_at) * 1000
                self._update_ema('total_avg_ms', total_ms)
                self._stats['completed'] = (
                    int(self._stats['completed']) + 1
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug('[Pipeline] post-%d error: %s', worker_id, e)
                if not item.future.done():
                    item.future.set_exception(e)
                self._stats['failed'] = int(self._stats['failed']) + 1
            finally:
                self._inf_to_post.task_done()

    # ── Helpers ────────────────────────────────────────────────────

    def _update_ema(self, key: str, value: float, alpha: float = 0.1) -> None:
        """Update an EMA telemetry counter."""
        prev = self._stats.get(key, 0.0)
        if isinstance(prev, (int, float)):
            self._stats[key] = alpha * value + (1 - alpha) * float(prev)

    def __repr__(self) -> str:
        return (
            f'BoundedInferencePipeline('
            f'started={self._started}, '
            f'closed={self._closed}, '
            f'submitted={self._stats.get("submitted", 0)}, '
            f'completed={self._stats.get("completed", 0)}, '
            f'failed={self._stats.get("failed", 0)}, '
            f'input_q={self._input_queue.qsize()}, '
            f'prep_q={self._prep_to_inf.qsize()}, '
            f'post_q={self._inf_to_post.qsize()}'
            f')'
        )


__all__ = ['BoundedInferencePipeline', 'PipelineItem']
