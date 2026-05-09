# DEPRECATED/UNUSED — zero callers as of F214CLEAN (2026-05-06)
# Historical references exist in wiki/reports but no active imports.
# Kept for potential future ThreadPoolExecutor migration.
# Active ProcessPoolExecutor users (OUT OF SCOPE for F214CLEAN):
#   - orchestrator/global_scheduler.py
#   - utils/execution_optimizer.py
#   - discovery/rss_atom_adapter.py
#   - discovery/ti_feed_adapter.py

"""
Worker pool module — import-safe lazy initialization.

No ProcessPoolExecutor processes are spawned on import.
Use get_executor() to get or create the singleton executor.
Use shutdown_worker_pool() to tear down the singleton.
"""

from concurrent.futures import ProcessPoolExecutor
from typing import Optional

# Module-level singleton — None until first get_executor() call
_executor: Optional[ProcessPoolExecutor] = None


def get_executor(max_workers: Optional[int] = None) -> ProcessPoolExecutor:
    """
    Get or create the shared ProcessPoolExecutor singleton.

    First call creates the executor. Subsequent calls return the same instance.

    Args:
        max_workers: Passed to ProcessPoolExecutor constructor.
                     If None, uses default (os.cpu_count()).

    Returns:
        The shared ProcessPoolExecutor singleton.
    """
    global _executor
    if _executor is None:
        _executor = ProcessPoolExecutor(max_workers=max_workers)
    return _executor


def shutdown_worker_pool(wait: bool = True) -> dict:
    """
    Shut down the worker pool singleton and return telemetry.

    Idempotent: safe to call even if pool was never created.

    Args:
        wait: If True, shutdown() waits for pending work to complete.
              If False, cancels pending work.

    Returns:
        Dict with telemetry: {
            "was_active": bool,      # True if executor existed before shutdown
            "executor_id": str,      # id() of the executor (for debugging)
            "wait": bool,            # value of wait parameter
        }
    """
    global _executor
    was_active = _executor is not None
    executor_id = id(_executor) if _executor is not None else None
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None
    return {
        "was_active": was_active,
        "executor_id": str(executor_id) if executor_id is not None else None,
        "wait": wait,
    }