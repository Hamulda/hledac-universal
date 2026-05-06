# DEPRECATED/UNUSED — zero callers as of F214CLEAN (2026-05-06)
# Historical references exist in wiki/reports but no active imports.
# Kept for potential future ThreadPoolExecutor migration.
# Active ProcessPoolExecutor users (OUT OF SCOPE for F214CLEAN):
#   - orchestrator/global_scheduler.py
#   - utils/execution_optimizer.py
#   - discovery/rss_atom_adapter.py
#   - discovery/ti_feed_adapter.py
from concurrent.futures import ProcessPoolExecutor

executor = ProcessPoolExecutor()
