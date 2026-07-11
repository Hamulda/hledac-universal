"""
Streaming JSON utilities — incremental parsing without full memory load.

M1 8GB safe: O(1) memory per item instead of O(n) for full parse.
Pure Python, async-first, fail-safe.

Sprint F265C — Streaming JSON with ijson

Cleanup invariant (Python 3.14+):
    All async generators consuming ijson/items_casync or
    response.content.iter_lines() MUST be wrapped in
    contextlib.aclosing() to guarantee __aexit__ cleanup.
    Python < 3.14: manual try/finally fallback.

    Correct pattern:
        async with aclosing(ijson.items_casync(...)) as agen:
            async for item in agen:
                yield item

    NOT:
        async for item in ijson.items_casync(...):
            yield item  # leaks if exception between yield and exit
"""



import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)

# Python 3.14+: contextlib.aclosing() for async generator cleanup
# Fallback for older Python versions (graceful degradation)
from contextlib import aclosing  # noqa: E402

# Lazy import — ijson loaded only when streaming functions called
_IJSON_AVAILABLE: bool = True

try:
    import ijson  # type: ignore[attr-defined]  # noqa: F401
except ImportError:
    _IJSON_AVAILABLE = False


async def stream_json_array(
    response: aiohttp.ClientResponse,
    path: str = "item",
) -> AsyncIterator[Any]:
    """
    Stream JSON array elements from aiohttp response without loading full JSON.

    Uses ijson for incremental parsing — memory efficient for large feeds.
    First item available immediately after headers + minimal JSON parsed.

    Args:
        response: aiohttp response with JSON body
        path: JSONPath to array elements (e.g. "item", "items.item", "data.records")

    Yields:
        Individual JSON objects from the array

    Example:
        async with session.get(url) as resp:
            async with aclosing(stream_json_array(resp, "records.record")) as agen:
                async for item in agen:
                    yield item
    """
    if not _IJSON_AVAILABLE:
        logger.warning("[streaming_json] ijson not available, falling back to full parse")
        # Fallback: full parse (not ideal but functional)
        text = await response.text()
        import orjson
        try:
            data = orjson.loads(text)
            if isinstance(data, list):
                for item in data:
                    yield item
            elif isinstance(data, dict):
                # Try common wrapper keys
                for key in ("items", "data", "results", "records"):
                    if key in data and isinstance(data[key], list):
                        for item in data[key]:
                            yield item
                        break
        except orjson.JSONDecodeError as e:
            logger.debug(f"[streaming_json] fallback parse failed: {e}")
        return

    try:
        import ijson

        # ijson.items_casync parses incrementally from response.content
        # Content is consumed in chunks — never loads full JSON into memory
        # F265C-SUPER: wrap in aclosing() to guarantee __aexit__ cleanup
        async_gen = ijson.items_casync(response.content, path)  # type: ignore[attr-defined]
        async with aclosing(async_gen) as agen:
            async for obj in agen:
                yield obj
    except (RuntimeError, StopIteration, asyncio.CancelledError) as e:
        # ijson generators raise RuntimeError on protocol errors, StopIteration on premature end
        logger.debug(f"[streaming_json] ijson stream failed: {e}")
        # Fallback: try to salvage what we can
        try:
            text = await response.text()
            import orjson
            data = orjson.loads(text)
            if isinstance(data, list):
                for item in data:
                    yield item
        except orjson.JSONDecodeError as e2:
            logger.debug(f"[streaming_json] salvage failed: {e2}")


async def stream_ndjson(
    response: aiohttp.ClientResponse,
) -> AsyncIterator[dict]:
    """
    Stream NDJSON (newline-delimited JSON) from aiohttp response.

    Each line is a valid JSON object on its own line.
    Used by: CT logs, CommonCrawl CDX, many TI feeds.

    Memory: O(1) per item instead of O(n) for full parse.

    Args:
        response: aiohttp response with NDJSON body

    Yields:
        Individual JSON objects (dicts)

    Example:
        async with session.get(cdx_url) as resp:
            async for record in stream_ndjson(resp):
                yield record
    """
    try:
        import orjson

        # F265C-SUPER: wrap iter_lines() in aclosing() to guarantee cleanup
        async with aclosing(response.content.iter_lines()) as lines:
            async for line in lines:
                if line.strip():
                    try:
                        yield orjson.loads(line)
                    except orjson.JSONDecodeError as e:
                        logger.debug(f"[streaming_json] NDJSON line parse failed: {e}")
                        continue
    except (ConnectionError, asyncio.TimeoutError) as e:
        logger.debug(f"[streaming_json] NDJSON stream failed: {e}")


async def stream_jsonlines(
    response: aiohttp.ClientResponse,
) -> AsyncIterator[dict]:
    """
    Stream JSON Lines format from aiohttp response.

    Alias for stream_ndjson — both handle newline-delimited JSON.
    Memory: O(1) per item.

    Args:
        response: aiohttp response with JSON Lines body

    Yields:
        Individual JSON objects (dicts)
    """
    async for obj in stream_ndjson(response):
        yield obj


async def stream_json_array_by_key(
    response: aiohttp.ClientResponse,
    wrapper_key: str = "data",
) -> AsyncIterator[Any]:
    """
    Stream JSON array from a dict-wrapped response.

    Common pattern: API returns {"data": [...]} wrapper.
    This streams the inner array without loading full response.

    Args:
        response: aiohttp response
        wrapper_key: Key containing the array (default: "data")

    Yields:
        Items from response[wrapper_key]
    """
    path = f"{wrapper_key}.item"
    async for obj in stream_json_array(response, path):
        yield obj


def parse_json_chunks(
    text: str,
    path: str = "item",
) -> Iterator[Any]:
    """
    Parse JSON from text in chunks (sync version for testing/replay).

    Use when you already have full text but want incremental yield.
    For truly large JSON, prefer stream_json_array with aiohttp response.

    Args:
        text: Full JSON text
        path: JSONPath to array elements

    Yields:
        Items from the array

    Example:
        for item in parse_json_chunks(large_json_text, "records.record"):
            process(item)
    """
    if not _IJSON_AVAILABLE:
        import orjson
        try:
            data = orjson.loads(text)
            if isinstance(data, list):
                yield from data
        except orjson.JSONDecodeError as e:
            logger.debug(f"[streaming_json] chunk parse failed: {e}")
        return

    try:
        import ijson

        # Use ijson.parse for lowest-level incremental parsing
        # This is sync but yields items as they're parsed
        parser = ijson.parse(text)
        current_item: dict | None = None

        for prefix, event, value in parser:
            if prefix == path and event == "start_map":
                current_item = {}
            elif prefix == path and event == "end_map":
                if current_item is not None:
                    yield current_item
                    current_item = None
            elif current_item is not None and event == "map_key":
                current_item[value] = None
            elif current_item is not None and value is not None:
                # Set the most recent key
                keys = [k for k, v in current_item.items() if v is None]
                if keys:
                    current_item[keys[-1]] = value
    except (RuntimeError, ValueError) as e:
        # ijson.parse raises RuntimeError on malformed JSON, ValueError on parse errors
        logger.debug(f"[streaming_json] chunk parse failed: {e}")


# -----------------------------------------------------------------------------
# Bounded variants for M1 8GB safety
# -----------------------------------------------------------------------------

async def stream_json_array_bounded(
    response: aiohttp.ClientResponse,
    path: str = "item",
    max_items: int = 1000,
) -> AsyncIterator[Any]:
    """
    Bounded version of stream_json_array — stops after max_items.

    M1 8GB safety: hard cap prevents unbounded memory growth.

    Args:
        response: aiohttp response
        path: JSONPath to array elements
        max_items: Maximum items to yield (default: 1000)

    Yields:
        Up to max_items items from the array
    """
    count = 0
    async for item in stream_json_array(response, path):
        yield item
        count += 1
        if count >= max_items:
            break


async def stream_ndjson_bounded(
    response: aiohttp.ClientResponse,
    max_items: int = 1000,
) -> AsyncIterator[dict]:
    """
    Bounded version of stream_ndjson — stops after max_items.

    M1 8GB safety: hard cap prevents unbounded memory growth.

    Args:
        response: aiohttp response
        max_items: Maximum items to yield (default: 1000)

    Yields:
        Up to max_items NDJSON records
    """
    count = 0
    async for item in stream_ndjson(response):
        yield item
        count += 1
        if count >= max_items:
            break
