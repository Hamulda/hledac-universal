"""
Pure async helper for reading chunk streams with a hard byte cap.

No transport layer coupling. No router involvement. No network I/O.
Used by both curl_cffi and httpx transport lanes.

Invariants:
- Bounded: respects max_bytes strictly; truncates in place on overflow.
- Fail-soft: returns (body, truncated) tuple — never raises on overflow.
- O(1) amortized append via bytearray.extend() — no O(n²) string concat.
- CancelledError propagates unchanged (transport contract).
- Bounded chunks counter (CHUNKS_BUDGET=8192) defends against pathological
  sources that emit millions of tiny chunks.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Defense against pathological sources — never read more than this many
# chunks regardless of byte cap. 8k × 8KB = 64MB upper bound, well below
# any reasonable transport cap, but high enough for streaming downloads.
CHUNKS_BUDGET: int = 8192


@dataclass(frozen=True, slots=True)
class BodyReadResult:
    """
    Bounded body-read outcome with enough context for FetchResult construction.

    Slots: M1 memory friendly, frozen: immutable, no accidental mutation.
    """
    body: bytes
    total_read: int  # bytes after truncation (== len(body) when truncated)
    truncated: bool
    chunks_consumed: int  # bounded by CHUNKS_BUDGET


async def _read_body_into(
    chunks: AsyncIterator[bytes],
    max_bytes: int,
) -> BodyReadResult:
    """
    Internal helper: read chunks into a bytearray, honoring max_bytes + CHUNKS_BUDGET.

    Returns BodyReadResult. Does NOT raise on overflow (returns truncated=True).
    Raises asyncio.CancelledError unchanged.

    max_bytes <= 0 means "no cap" — collect everything subject to CHUNKS_BUDGET.
    """
    content_bytes = bytearray()
    truncated = False
    chunks_consumed = 0

    if max_bytes <= 0:
        async for chunk in chunks:
            if chunks_consumed >= CHUNKS_BUDGET:
                logger.warning(
                    f"Body read hit CHUNKS_BUDGET={CHUNKS_BUDGET} without byte cap; "
                    f"truncating at {len(content_bytes)} bytes"
                )
                truncated = True
                break
            content_bytes.extend(chunk)
            chunks_consumed += 1
        return BodyReadResult(
            body=bytes(content_bytes),
            total_read=len(content_bytes),
            truncated=truncated,
            chunks_consumed=chunks_consumed,
        )

    async for chunk in chunks:
        if chunks_consumed >= CHUNKS_BUDGET:
            logger.warning(
                f"Body read hit CHUNKS_BUDGET={CHUNKS_BUDGET}; truncating at {max_bytes} bytes"
            )
            truncated = True
            break
        content_bytes.extend(chunk)
        chunks_consumed += 1
        if len(content_bytes) > max_bytes:
            del content_bytes[max_bytes:]  # truncate in-place, O(1) amortized
            logger.debug(f"Body truncated to {max_bytes} bytes after {chunks_consumed} chunks")
            truncated = True
            break

    return BodyReadResult(
        body=bytes(content_bytes),
        total_read=len(content_bytes),
        truncated=truncated,
        chunks_consumed=chunks_consumed,
    )


async def read_body_with_cap(
    chunks: AsyncIterator[bytes],
    max_bytes: int,
) -> tuple[bytes, bool]:
    """
    Read an async chunk stream up to a hard byte cap.

    Backward-compatible thin wrapper over _read_body_into().

    Args:
        chunks: Async iterator yielding body chunks (e.g. response.iter_content()).
        max_bytes: Hard cap on total bytes to collect.

    Returns:
        tuple[bytes, bool]: (body_bytes, truncated) where truncated is True
        if the cap was exceeded.

    Raises:
        asyncio.CancelledError: propagates unchanged.

    Behavior:
        - Uses bytearray.extend() for O(1) amortized append.
        - On exceeding max_bytes, truncates in-place: del content_bytes[max_bytes:].
        - Bounded by CHUNKS_BUDGET chunks even without byte cap.
        - CancelledError is re-raised (not caught), matching transport contract.
    """
    result = await _read_body_into(chunks, max_bytes)
    return result.body, result.truncated
