"""
Pure async helper for reading chunk streams with a hard byte cap.

No transport layer coupling. No router involvement. No network I/O.
Used by both curl_cffi and httpx transport lanes.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


async def read_body_with_cap(
    chunks: AsyncIterator[bytes],
    max_bytes: int,
) -> tuple[bytes, bool]:
    """
    Read an async chunk stream up to a hard byte cap.

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
        - CancelledError is re-raised (not caught), matching transport contract.
    """
    content_bytes = bytearray()
    truncated = False

    # max_bytes=0 means "no cap" — collect everything
    if max_bytes <= 0:
        async for chunk in chunks:
            content_bytes.extend(chunk)
        return bytes(content_bytes), False

    async for chunk in chunks:
        content_bytes.extend(chunk)
        if len(content_bytes) > max_bytes:
            del content_bytes[max_bytes:]  # truncate in-place
            logger.debug(f"Body truncated to {max_bytes} bytes")
            truncated = True
            break

    return bytes(content_bytes), truncated
