"""
core/policy.py — Central async bridging policy.

P1-1: Single source of truth for sync→async bridging.

WHY THIS FILE EXISTS
====================
asyncio.run() creates a new event loop on every call, which:
  1. Blocks M1 Metal GPU inference when called inside ThreadPoolExecutor
  2. Roots GC references from the previous loop, causing memory fragmentation
  3. Creates nested-loop pasts when combined with run_coroutine_threadsafe
     on an outer running loop

AUTHORIZED ENTRY POINTS
=======================
asyncio.run() is PERMITTED only in:
  1. `if __name__ == '__main__':` blocks — CLI entry points
  2. `__main__.py` modules — explicit program entry
  3. Test fixtures — isolated test event loops

FORBIDDEN IN PRODUCTION
=======================
asyncio.run() inside:
  - Regular module-level code
  - Class methods (sync or async)
  - ThreadPoolExecutor / run_in_executor callbacks
  - Signal handlers

COMPLIANCE
==========
All sync→async bridging in production code MUST use:
  from hledac.universal.utils.sync_bridge import run_sync_async

Or for CPU-bound sync work called from async code:
  from hledac.universal.utils.sync_bridge import to_thread

NO bare asyncio.run() in production modules (non-__main__).
"""
from hledac.universal.utils.sync_bridge import run_sync_async, to_thread

__all__ = ["run_sync_async", "to_thread"]
