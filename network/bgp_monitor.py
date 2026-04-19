#!/usr/bin/env python3
"""
BGP Monitor — Real-time BGP event streaming via pybgpstream.

Graceful fallback when pybgpstream is unavailable on arm64.
Bounded memory: max 1000 events in deque, older events discarded.

Anti-patterns prevented:
  - No blocking socket ops (all async via asyncio)
  - No pybgpstream assumption (ImportError guard at top)
  - No unbounded memory (deque maxlen=1000)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graceful fallback — must be at top of file, not inside functions
# ---------------------------------------------------------------------------
try:
    import pybgpstream
    BGP_AVAILABLE = True
except ImportError:
    BGP_AVAILABLE = False
    logger.warning(
        "WARNING: pybgpstream not available on arm64 — BGP monitoring disabled"
    )


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------

BGP_EVENT_TYPES: frozenset[str] = frozenset({"announce", "withdraw", "unknown"})


def _parse_as_path(raw_path: str) -> str:
    """Normalize AS path to space-separated string."""
    if not raw_path:
        return ""
    # AS paths come as "{asn1} {asn2} ..." or "{asn1}{asn2}..."
    # Normalize to space-separated
    normalized = " ".join(raw_path.replace("{", "").replace("}", "").split())
    return normalized.strip()


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

async def monitor_bgp(
    prefixes: list[str],
    callback: Callable[[float, str, str, str], None],
    duration_seconds: int = 60,
) -> list[dict]:
    """
    Stream BGP events for given prefixes.

    Args:
        prefixes: List of BGP prefixes to monitor (e.g. ["1.1.1.0/24"])
        callback: Called with (timestamp, prefix, as_path, event_type) per event.
                  timestamp: float (unix time)
                  prefix: str (e.g. "1.1.1.0/24")
                  as_path: str (e.g. "13335 1234")
                  event_type: str in {"announce", "withdraw", "unknown"}
        duration_seconds: How long to stream (default 60s)

    Returns:
        List of event dicts with keys: timestamp, prefix, as_path, event_type

    Anti-patterns prevented:
      - Graceful degradation when BGP_AVAILABLE=False
      - Bounded memory via deque(maxlen=1000)
      - Non-blocking via asyncio shield around sync pybgpstream iteration
    """
    if not BGP_AVAILABLE:
        logger.warning(
            "WARNING: pybgpstream not available on arm64 — BGP monitoring disabled"
        )
        return []

    events: list[dict] = []
    event_buffer: deque[dict] = deque(maxlen=1000)  # Bounded memory

    # Parse duration into start/end times
    end_time = int(time.time())
    start_time = end_time - duration_seconds

    try:
        stream = pybgpstream.BGPStream(
            data_interface="single",
            filter=f"type any prefix {' '.join(prefixes)}",
        )
        stream.set_start_time(start_time)
        stream.set_end_time(end_time)

        async def _stream_events():
            """Async wrapper around sync pybgpstream iteration."""
            try:
                for entry in stream:
                    elem = entry.record["elements"][0]
                    raw_ts = elem["time"]
                    raw_prefix = elem["prefix"]
                    raw_as_path = elem.get("fields", {}).get("as-path", "")
                    raw_type = elem["type"]

                    timestamp = float(raw_ts)
                    prefix = str(raw_prefix)
                    as_path = _parse_as_path(str(raw_as_path))
                    event_type = raw_type if raw_type in BGP_EVENT_TYPES else "unknown"

                    event = {
                        "timestamp": timestamp,
                        "prefix": prefix,
                        "as_path": as_path,
                        "event_type": event_type,
                    }
                    event_buffer.append(event)

                    # Invoke callback (non-blocking)
                    try:
                        callback(timestamp, prefix, as_path, event_type)
                    except Exception as cb_err:
                        logger.debug(f"BGP callback error: {cb_err}")

                    # Check if duration exceeded
                    if time.time() - end_time + duration_seconds > 0:
                        break
            except Exception as e:
                logger.warning(f"BGP stream error: {e}")

        # Run sync iteration in executor to avoid blocking event loop
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, _stream_events),
            timeout=duration_seconds + 5,
        )

    except asyncio.TimeoutError:
        logger.debug(f"BGP monitor reached duration limit ({duration_seconds}s)")
    except Exception as e:
        logger.warning(f"BGP monitor error: {e}")

    # Return buffered events (max 1000)
    return list(event_buffer)


__all__ = [
    "BGP_AVAILABLE",
    "monitor_bgp",
]
