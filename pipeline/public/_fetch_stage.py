"""Fetch stage — per-URL HTTP fetch for public OSINT pipeline.

Responsibilities:
- Fetch URL content via curl_cffi / httpx

- Handle quality gates (pre-fetch skip, memory pressure)
- Manage concurrency (bounded semaphore)
- Track fetch errors and failure stages

Input: PageBatch (urls, titles, snippets, ranks, discovery_scores)
Output: FetchedBatch (urls, texts, text_lens, fetch_errors, failure_stages, redirects, ...)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import msgspec

from hledac.universal.pipeline._soa_types import FetchedBatch, PageBatch
from hledac.universal.utils.asyncx import parallel_ok
from core import aclose

logger = logging.getLogger(__name__)

# Re-use constants from live_public_pipeline
_DEFAULT_FETCH_TIMEOUT_S: float = 35.0
_DEFAULT_FETCH_MAX_BYTES: int = 2_000_000


class FetchStage:
    """Fetch stage: PageBatch → FetchedBatch.

    Fetches each URL in the PageBatch concurrently (bounded by semaphore).
    Delegates to public_fetch.py _fetch_and_process_page for actual fetch logic.
    """

    __slots__ = ("_fetch_timeout_s", "_fetch_max_bytes", "_fetch_concurrency")

    def __init__(
        self,
        fetch_timeout_s: float = _DEFAULT_FETCH_TIMEOUT_S,
        fetch_max_bytes: int = _DEFAULT_FETCH_MAX_BYTES,
        fetch_concurrency: int = 8,
    ) -> None:
        self._fetch_timeout_s = fetch_timeout_s
        self._fetch_max_bytes = fetch_max_bytes
        self._fetch_concurrency = fetch_concurrency

    @property
    def name(self) -> str:
        return "fetch"

    async def process(
        self, input_batch: PageBatch | None
    ) -> tuple[FetchedBatch, dict[str, Any]]:
        """Fetch URLs from a PageBatch.

        Args:
            input_batch: PageBatch with urls to fetch

        Returns:
            Tuple of (FetchedBatch, telemetry)

        """
        if input_batch is None or not input_batch.urls:
            return self._empty_batch(), {}

        urls = input_batch.urls
        semaphore = asyncio.Semaphore(self._fetch_concurrency)

        telemetry: dict[str, Any] = {
            "fetch_attempted": len(urls),
            "fetch_success": 0,
            "fetch_failed": 0,
            "fetch_skipped_memory_gate": 0,
            "fetch_skipped_quality_gate": 0,
        }

        # Launch all fetch tasks concurrently
        async def fetch_one(idx: int, url: str) -> dict[str, Any]:
            async with semaphore:
                return await _fetch_single_url(
                    url=url,
                    title=input_batch.titles[idx] if idx < len(input_batch.titles) else "",
                    snippet=input_batch.snippets[idx] if idx < len(input_batch.snippets) else "",
                    rank=input_batch.ranks[idx] if idx < len(input_batch.ranks) else -1,
                    discovery_score=(
                        input_batch.discovery_scores[idx]
                        if idx < len(input_batch.discovery_scores)
                        else None
                    ),
                    fetch_timeout_s=self._fetch_timeout_s,
                    fetch_max_bytes=self._fetch_max_bytes,
                )

        tasks = [fetch_one(i, url) for i, url in enumerate(urls)]
        # F3XX: parallel_ok() replaces asyncio.gather — returns list[T] in original order,
        # preserves I6/I7 invariants, exceptions are logged and dropped (fire-and-forget
        # with observation). For error counting we rely on downstream failure_stages.
        results = await parallel_ok(*tasks, label="fetch_stage")

        # Collect results into FetchedBatch arrays (results are in original task order)
        texts: list[str] = []
        text_lens: list[int] = []
        fetch_errors: list[str | None] = []
        failure_stages: list[str | None] = []
        redirects: list[str | None] = []
        js_skipped: list[str | None] = []
        fetch_blocked: list[str | None] = []

        for result in results:
            texts.append(result.get("text", ""))
            text_lens.append(result.get("text_len", 0))
            fetch_errors.append(result.get("error"))
            failure_stages.append(result.get("failure_stage"))
            redirects.append(result.get("redirect_target"))
            js_skipped.append(result.get("js_renderer_skipped_reason"))
            fetch_blocked.append(result.get("fetch_blocked_reason"))
            if result.get("error"):
                telemetry["fetch_failed"] += 1
            else:
                telemetry["fetch_success"] += 1
                if result.get("fetch_blocked_reason") == "uma_memory":
                    telemetry["fetch_skipped_memory_gate"] += 1
                elif result.get("fetch_blocked_reason") == "quality_skip":
                    telemetry["fetch_skipped_quality_gate"] += 1

        batch = FetchedBatch(
            urls=urls,
            texts=texts,
            text_lens=text_lens,
            fetch_errors=fetch_errors,
            failure_stages=failure_stages,
            redirects=redirects,
            js_renderer_skipped_reasons=js_skipped,
            fetch_blocked_reasons=fetch_blocked,
        )

        return batch, telemetry

    def _empty_batch(self) -> FetchedBatch:
        return FetchedBatch(
            urls=[],
            texts=[],
            text_lens=[],
            fetch_errors=[],
            failure_stages=[],
            redirects=[],
            js_renderer_skipped_reasons=[],
            fetch_blocked_reasons=[],
        )


async def _fetch_single_url(
    url: str,
    title: str,
    snippet: str,
    rank: int,
    discovery_score: float | None,
    fetch_timeout_s: float,
    fetch_max_bytes: int,
) -> dict[str, Any]:
    """Fetch a single URL and return result as dict.

    Delegates to public_fetch.py _fetch_and_process_page.
    """
    try:
        # Import here to avoid circular dependency
        from hledac.universal.pipeline.public_fetch import _fetch_and_process_page

        result: msgspec.Struct = await _fetch_and_process_page(
            semaphore=asyncio.Semaphore(1),  # already accounted for in semaphore
            query="",  # filled by caller
            hit_url=url,
            hit_title=title,
            hit_snippet=snippet,
            hit_rank=rank,
            fetch_timeout_s=fetch_timeout_s,
            fetch_max_bytes=fetch_max_bytes,
            store=None,  # fetch only, no store
            memory_manager=None,
            session_id=None,
            discovery_score=discovery_score,
            discovery_reason=None,
            vector_store=None,
            graph=None,
        )

        # Extract fields from PipelinePageResult
        text = getattr(result, "text", "") if hasattr(result, "text") else ""
        text_len = len(text) if text else 0
        error = getattr(result, "error", None)
        failure_stage = getattr(result, "failure_stage", None)
        redirect_target = getattr(result, "redirect_target", None)
        js_reason = getattr(result, "js_renderer_skipped_reason", None)
        blocked_reason = getattr(result, "fetch_blocked_reason", None)

        return {
            "text": text,
            "text_len": text_len,
            "error": error,
            "failure_stage": failure_stage,
            "redirect_target": redirect_target,
            "js_renderer_skipped_reason": js_reason,
            "fetch_blocked_reason": blocked_reason,
        }

    except Exception as exc:
        logger.warning(f"Fetch failed for {url}: {exc}")
        return {
            "text": "",
            "text_len": 0,
            "error": str(exc),
            "failure_stage": "exception",
            "redirect_target": None,
            "js_renderer_skipped_reason": None,
            "fetch_blocked_reason": None,
        }
